"""Drive records -> stream envelopes.

Gathers the file record, its direct permissions, and extracted body. Graph
shape is the worker's job. Body reads stay here: they cost 200 quota units.
"""

from __future__ import annotations

import logging
from collections import OrderedDict

from connectors.drive import envelopes
from connectors.drive.client import DriveClient, DriveError
from connectors.drive.content import BodySource, body_changed, body_source, clamp
from connectors.drive.models import Change, DriveFile, SharedDrive
from core.message import ChangeKind, Envelope

log = logging.getLogger("connectors.drive.events")

BODY_CACHE_MAX_ENTRIES = 2_000


class DriveEventMapper:
    """Turns Drive files and change records into envelopes."""

    def __init__(self, client: DriveClient) -> None:
        self._client = client
        self._bodies: OrderedDict[str, tuple[str, str]] = OrderedDict()

    async def drive_message(self, drive: SharedDrive) -> Envelope:
        permissions = await self._client.list_permissions(drive.id)
        return envelopes.drive_envelope(drive, permissions=permissions)

    async def item_message(
        self, file: DriveFile, *, change: ChangeKind = ChangeKind.CREATED
    ) -> Envelope:
        body, source = await self._read_body(file)

        permissions = []
        if file.hasAugmentedPermissions:
            permissions = await self._client.list_permissions(file.id)

        return envelopes.item_envelope(
            file,
            permissions=permissions,
            body=body,
            body_source=source.value,
            change=change,
        )

    def delete_message(self, file_id: str, *, was_folder: bool = False) -> Envelope:
        return envelopes.delete_envelope(file_id, was_folder=was_folder)

    async def from_change(self, change: Change) -> Envelope | None:
        if change.is_drive_change:
            return None

        if change.is_gone:
            return self.delete_message(
                change.fileId, was_folder=bool(change.file and change.file.is_folder)
            )

        if change.file is None:
            log.debug(
                "change %s carried no file record; treating as removed", change.fileId
            )
            return self.delete_message(change.fileId)

        return await self.item_message(change.file, change=ChangeKind.UPDATED)

    async def _read_body(self, file: DriveFile) -> tuple[str, BodySource]:
        source, export_mime = body_source(file)
        if source is BodySource.NONE:
            return "", source

        cached = self._bodies.get(file.id)
        if cached and not body_changed(file, cached[0]):
            self._bodies.move_to_end(file.id)
            return cached[1], source

        try:
            if source is BodySource.EXPORT and export_mime:
                text = await self._client.export_text(file.id, export_mime)
            elif source is BodySource.DOWNLOAD:
                text = await self._client.download_text(file.id)
            else:
                return "", BodySource.NONE
        except DriveError as exc:
            log.warning("body read failed for %s (%s): %s", file.id, file.name, exc)
            return "", BodySource.NONE

        body = clamp(text.lstrip("\ufeff"))

        self._bodies[file.id] = (file.body_version, body)
        self._bodies.move_to_end(file.id)
        while len(self._bodies) > BODY_CACHE_MAX_ENTRIES:
            self._bodies.popitem(last=False)

        return body, source
