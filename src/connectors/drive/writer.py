"""Write-scoped Drive access, used only by the seeder.

Kept out of DriveClient on purpose. That client is read-only by construction —
it issues nothing but GETs and asks for a read-only scope — and that property is
worth more than the code it would save to merge the two. The ingestion path
cannot damage a customer's Drive even if it is wrong; only this module can, and
only the seeder imports it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from connectors.drive.auth import WRITE_SCOPE, ServiceAccountAuth
from connectors.drive.models import FOLDER_MIME

log = logging.getLogger("connectors.drive.writer")

API_BASE = "https://www.googleapis.com/drive/v3"
UPLOAD_BASE = "https://www.googleapis.com/upload/drive/v3"

SHARED_DRIVE_PARAMS = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}

MULTIPART_BOUNDARY = "pkg-seeder-boundary"

# How far `if_exists='version'` will count before giving up. See `_free_name`.
MAX_VERSIONED_NAMES = 50


class DriveWriteError(RuntimeError):
    pass


class DriveWriter:
    """Creates folders and files in a shared drive. Async context manager."""

    def __init__(self, key_path: str, *, timeout: float = 60.0) -> None:
        self._auth = ServiceAccountAuth(key_path, scopes=(WRITE_SCOPE,))
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def client_email(self) -> str:
        return self._auth.client_email

    async def __aenter__(self) -> DriveWriter:
        self._http = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _call(self, method: str, url: str, **kw: Any) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("DriveWriter must be used as an async context manager")

        params = {**SHARED_DRIVE_PARAMS, **kw.pop("params", {})}
        token = await self._auth.token(self._http)
        headers = {"Authorization": f"Bearer {token}", **kw.pop("headers", {})}

        response = await self._http.request(
            method, url, params=params, headers=headers, **kw
        )
        if response.status_code >= 400:
            raise DriveWriteError(
                f"{method} {url} -> HTTP {response.status_code}: {response.text[:300]}"
            )
        return response.json() if response.content else {}

    # ------------------------------------------------------------- lookups --

    async def find_child(self, parent_id: str, name: str) -> str | None:
        """Direct child by exact name, so seeding twice is not seeding double.

        Names are escaped rather than interpolated raw: an apostrophe in a
        fixture name would otherwise terminate the query string and turn a
        lookup into a syntax error.
        """
        escaped = name.replace("\\", "\\\\").replace("'", "\'")
        body = await self._call(
            "GET",
            f"{API_BASE}/files",
            params={
                "q": f"'{parent_id}' in parents and name = '{escaped}' and trashed = false",
                "corpora": "allDrives",
                "fields": "files(id,name)",
                "pageSize": 10,
            },
        )
        files = body.get("files", [])
        return files[0]["id"] if files else None

    # ------------------------------------------------------------ creation --

    async def create_folder(self, name: str, parent_id: str) -> str:
        existing = await self.find_child(parent_id, name)
        if existing:
            log.debug("folder %r already present", name)
            return existing

        body = await self._call(
            "POST",
            f"{API_BASE}/files",
            params={"fields": "id"},
            json={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
        )
        return body["id"]

    async def create_file(
        self,
        name: str,
        parent_id: str,
        content: str,
        *,
        upload_mime: str = "text/plain",
        target_mime: str | None = None,
        if_exists: str = "reuse",
    ) -> dict[str, str]:
        """Multipart create: metadata and bytes in one request.

        Setting `target_mime` to a Drive-native type makes Drive convert the
        upload — uploading text/plain as a Doc is how the fixtures get real
        native files, which are the ones that exercise the export path.

        `if_exists` decides what a name collision means, because the seeder and
        an action want opposite things from one. Seeding twice must not seed
        double, so the default is `reuse`: hand back what is already there and
        write nothing. An action asked to produce a document must never resolve
        to "your content was silently discarded", so it passes `fail` or
        `version` instead.

        Returns the created (or reused) file's `id` and `webViewLink`. A dict
        rather than a bare id because the link is what a later step posts into
        Slack, and fetching it separately would be a second round trip for
        something the create response can carry.
        """
        existing = await self.find_child(parent_id, name)
        if existing:
            if if_exists == "reuse":
                log.debug("file %r already present", name)
                return {"id": existing, "webViewLink": ""}
            if if_exists == "fail":
                raise DriveWriteError(
                    f"a file named {name!r} already exists in this folder"
                )
            name = await self._free_name(parent_id, name)

        metadata: dict[str, Any] = {"name": name, "parents": [parent_id]}
        if target_mime:
            metadata["mimeType"] = target_mime

        b = MULTIPART_BOUNDARY
        payload = (
            f"--{b}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata)}\r\n"
            f"--{b}\r\n"
            f"Content-Type: {upload_mime}\r\n\r\n"
            f"{content}\r\n"
            f"--{b}--"
        ).encode()

        body = await self._call(
            "POST",
            f"{UPLOAD_BASE}/files",
            params={"uploadType": "multipart", "fields": "id,webViewLink"},
            headers={"Content-Type": f"multipart/related; boundary={b}"},
            content=payload,
        )
        return {"id": body["id"], "webViewLink": body.get("webViewLink", "")}

    async def _free_name(self, parent_id: str, name: str) -> str:
        """`name` with the lowest numeric suffix that is not taken.

        Bounded rather than looped to exhaustion: a folder holding this many
        documents of one name is a runaway caller, and answering that with an
        error beats adding the two-thousandth copy.
        """
        stem, dot, extension = name.rpartition(".")
        base = stem if dot else name
        suffix = f".{extension}" if dot else ""
        for n in range(2, MAX_VERSIONED_NAMES + 2):
            candidate = f"{base} ({n}){suffix}"
            if not await self.find_child(parent_id, candidate):
                return candidate
        raise DriveWriteError(
            f"{MAX_VERSIONED_NAMES} files named like {name!r} already exist here"
        )

    async def update_content(
        self, file_id: str, content: str, *, upload_mime: str = "text/plain"
    ) -> str:
        """Replace a file's body, keeping its id, name, and parents.

        Multipart with empty metadata rather than `uploadType=media`. A native
        Doc will not accept raw media on its own — Drive needs the metadata part
        present to know it should convert the upload back into Doc format — and
        the media-only form silently turns a Doc into a plain text file.

        The id is stable across this, which is what makes it an edit rather than
        a delete and a create: existing links keep resolving and the poller sees
        a version bump on the node it already has.
        """
        b = MULTIPART_BOUNDARY
        payload = (
            f"--{b}\r\n"
            "Content-Type: application/json; charset=UTF-8\r\n\r\n"
            "{}\r\n"
            f"--{b}\r\n"
            f"Content-Type: {upload_mime}\r\n\r\n"
            f"{content}\r\n"
            f"--{b}--"
        ).encode()

        body = await self._call(
            "PATCH",
            f"{UPLOAD_BASE}/files/{file_id}",
            params={"uploadType": "multipart", "fields": "id"},
            headers={"Content-Type": f"multipart/related; boundary={b}"},
            content=payload,
        )
        return body.get("id", file_id)

    async def trash(self, file_id: str) -> None:
        """Move to the bin. Never a hard delete: a seeder that can permanently
        destroy Drive content is a footgun aimed at whoever's drive is
        configured."""
        await self._call(
            "PATCH",
            f"{API_BASE}/files/{file_id}",
            params={"fields": "id"},
            json={"trashed": True},
        )
