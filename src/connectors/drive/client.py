"""Drive v3 transport: auth, quota accounting, pagination, retries.

Only the methods this pipeline needs are exposed. Parsed models come back from
the typed helpers; `raw_*` variants hand back the untouched dict, because replay
re-projects from exactly what the API said rather than from our view of it.

Quota is metered in units, not requests (see ingestion-design.md §1): a list
costs 100 and a download 200, so a limiter that counts calls would be wrong by
40x depending on the mix. The limiter here counts units.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from connectors.drive.auth import ServiceAccountAuth
from connectors.drive.models import Change, DriveFile, Permission, SharedDrive

log = logging.getLogger("connectors.drive.client")

API_BASE = "https://www.googleapis.com/drive/v3"

# Published unit costs per operation class.
UNITS_READ = 5
UNITS_LIST = 100
UNITS_DOWNLOAD = 200

# Well under the 325,000/min/user cap. The ceiling exists to bound a runaway
# subtree walk, not to saturate the quota.
DEFAULT_UNITS_PER_MINUTE = 60_000

MAX_ATTEMPTS = 5
PAGE_SIZE = 200

# Every shared-drive call needs both flags. Without them the API answers as if
# only My Drive existed — an empty result rather than an error, which is the
# worst possible failure mode.
SHARED_DRIVE_PARAMS = {"supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}

# Enough to build a node without a second round trip. Every field costs response
# size, so this is the whole list and nothing more.
FILE_FIELDS = (
    "id,name,mimeType,parents,driveId,createdTime,modifiedTime,version,"
    "headRevisionId,md5Checksum,size,trashed,explicitlyTrashed,webViewLink,"
    "hasAugmentedPermissions,lastModifyingUser(displayName,emailAddress)"
)
PERMISSION_FIELDS = (
    "permissions(id,type,role,emailAddress,domain,deleted,"
    "permissionDetails(permissionType,role,inherited,inheritedFrom))"
)

# Errors Google returns as 403 that are throttling, not authorization. Retrying
# an actual authorization failure would just burn attempts.
RETRYABLE_403_REASONS = frozenset(
    {"rateLimitExceeded", "userRateLimitExceeded", "sharingRateLimitExceeded"}
)


class DriveError(RuntimeError):
    """A Drive API call failed in a way retries will not fix."""

    def __init__(self, path: str, status: int, reason: str, message: str) -> None:
        super().__init__(f"{path} failed: HTTP {status} {reason} — {message}")
        self.path = path
        self.status = status
        self.reason = reason


class QuotaLimiter:
    """Sliding one-minute window over quota units.

    In-process only. It is authoritative because all Drive access lives in the
    poller; workers never call Drive, so scaling workers does not scale the
    request rate. A second poller replica would need the shared Redis bucket.
    """

    def __init__(self, units_per_minute: int = DEFAULT_UNITS_PER_MINUTE) -> None:
        self._limit = units_per_minute
        self._spent: list[tuple[float, int]] = []
        self._lock = asyncio.Lock()

    async def acquire(self, units: int) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                self._spent = [(t, u) for t, u in self._spent if now - t < 60.0]
                if sum(u for _, u in self._spent) + units <= self._limit:
                    self._spent.append((now, units))
                    return
                oldest = self._spent[0][0]
                sleep_for = 60.0 - (now - oldest)
            log.debug("quota window full, sleeping %.1fs", sleep_for)
            await asyncio.sleep(max(sleep_for, 0.05))


class DriveClient:
    """Async Drive v3 client. Use as an async context manager."""

    def __init__(
        self,
        auth: ServiceAccountAuth,
        *,
        timeout: float = 60.0,
        units_per_minute: int = DEFAULT_UNITS_PER_MINUTE,
    ) -> None:
        self._auth = auth
        self._timeout = timeout
        self._limiter = QuotaLimiter(units_per_minute)
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> DriveClient:
        self._http = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ---------------------------------------------------------------- core --

    async def _request(
        self, path: str, *, units: int, params: dict[str, Any] | None = None
    ) -> httpx.Response:
        if self._http is None:
            raise RuntimeError("DriveClient must be used as an async context manager")

        query = {**SHARED_DRIVE_PARAMS, **(params or {})}
        query = {k: v for k, v in query.items() if v is not None}

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire(units)
            token = await self._auth.token(self._http)
            try:
                response = await self._http.get(
                    f"{API_BASE}{path}",
                    params=query,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                await self._backoff(attempt, f"transport error: {exc}")
                continue

            if response.status_code < 400:
                return response

            status, reason, message = _error_of(response)

            if status == 429 or (status == 403 and reason in RETRYABLE_403_REASONS):
                if attempt == MAX_ATTEMPTS:
                    raise DriveError(path, status, reason, message)
                # Drive rarely sends Retry-After; exponential backoff is the
                # documented guidance.
                await self._backoff(attempt, f"throttled ({reason or status})")
                continue

            if status >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise DriveError(path, status, reason, message)
                await self._backoff(attempt, f"HTTP {status}")
                continue

            raise DriveError(path, status, reason, message)

        raise DriveError(path, 0, "retries_exhausted", "")

    async def _get_json(
        self, path: str, *, units: int, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        return (await self._request(path, units=units, params=params)).json()

    @staticmethod
    async def _backoff(attempt: int, why: str) -> None:
        delay = min(2.0**attempt, 32.0)
        log.warning("%s; backing off %.1fs (attempt %d)", why, delay, attempt)
        await asyncio.sleep(delay)

    async def _paginate(
        self, path: str, key: str, *, units: int, **params: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield raw items across every page. Cursors are opaque — never parsed."""
        page_token: str | None = None
        while True:
            body = await self._get_json(
                path, units=units, params={**params, "pageToken": page_token}
            )
            for item in body.get(key, []):
                yield item
            page_token = body.get("nextPageToken")
            if not page_token:
                return

    # ------------------------------------------------------------- methods --

    async def get_drive(self, drive_id: str) -> SharedDrive:
        body = await self._get_json(
            f"/drives/{drive_id}", units=UNITS_READ, params={"fields": "id,name,createdTime"}
        )
        return SharedDrive.model_validate(body)

    async def get_file(self, file_id: str) -> tuple[DriveFile, dict[str, Any]]:
        body = await self._get_json(
            f"/files/{file_id}", units=UNITS_READ, params={"fields": FILE_FIELDS}
        )
        return DriveFile.model_validate(body), body

    async def list_files(
        self, drive_id: str, *, parent_id: str | None = None
    ) -> AsyncIterator[tuple[DriveFile, dict[str, Any]]]:
        """Files in a shared drive, optionally just one folder's direct children.

        `corpora=drive` scopes the query to this drive; without it the search
        spans everything the service account can see.
        """
        query = "trashed = false"
        if parent_id:
            query = f"'{parent_id}' in parents and {query}"

        async for raw in self._paginate(
            "/files",
            "files",
            units=UNITS_LIST,
            driveId=drive_id,
            corpora="drive",
            q=query,
            pageSize=PAGE_SIZE,
            fields=f"files({FILE_FIELDS}),nextPageToken",
        ):
            yield DriveFile.model_validate(raw), raw

    async def list_permissions(self, file_id: str) -> list[Permission]:
        """Grants on one item.

        The shared drive itself is addressed as a file here — there is no
        /drives/{id}/permissions endpoint, and calling one 404s.
        """
        perms: list[Permission] = []
        async for raw in self._paginate(
            f"/files/{file_id}/permissions",
            "permissions",
            units=UNITS_READ,
            pageSize=100,
            fields=f"{PERMISSION_FIELDS},nextPageToken",
        ):
            perms.append(Permission.model_validate(raw))
        return perms

    # -------------------------------------------------------------- changes --

    async def start_page_token(self, drive_id: str) -> str:
        body = await self._get_json(
            "/changes/startPageToken", units=UNITS_READ, params={"driveId": drive_id}
        )
        return body["startPageToken"]

    async def changes(
        self, drive_id: str, page_token: str
    ) -> tuple[list[Change], str]:
        """Drain the change feed from `page_token`.

        Returns every change plus the token to persist. The whole feed is drained
        before returning so the caller advances the watermark exactly once, after
        all of it has been published.
        """
        changes: list[Change] = []
        token = page_token
        while True:
            body = await self._get_json(
                "/changes",
                units=UNITS_LIST,
                params={
                    "pageToken": token,
                    "driveId": drive_id,
                    "pageSize": PAGE_SIZE,
                    "includeRemoved": "true",
                    "fields": f"changes(changeType,time,removed,fileId,driveId,file({FILE_FIELDS})),"
                    "nextPageToken,newStartPageToken",
                },
            )
            changes.extend(Change.model_validate(c) for c in body.get("changes", []))

            if body.get("nextPageToken"):
                token = body["nextPageToken"]
                continue
            return changes, body["newStartPageToken"]

    # -------------------------------------------------------------- content --

    async def export_text(self, file_id: str, export_mime: str) -> str:
        """Text of a native editor file.

        Native files have no bytes: alt=media on a Google Doc returns 403, so
        this endpoint is the only way to read one.
        """
        response = await self._request(
            f"/files/{file_id}/export",
            units=UNITS_DOWNLOAD,
            params={"mimeType": export_mime},
        )
        return response.text

    async def download_text(self, file_id: str) -> str:
        response = await self._request(
            f"/files/{file_id}", units=UNITS_DOWNLOAD, params={"alt": "media"}
        )
        return response.text


def _error_of(response: httpx.Response) -> tuple[int, str, str]:
    """Pull Google's structured error out, tolerating HTML error pages."""
    try:
        error = response.json().get("error", {})
    except ValueError:
        return response.status_code, "", response.text[:200]

    reasons = error.get("errors") or [{}]
    return (
        response.status_code,
        reasons[0].get("reason", error.get("status", "")),
        error.get("message", ""),
    )
