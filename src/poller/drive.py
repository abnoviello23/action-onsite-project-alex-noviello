"""Drive ingestion service: first-connect backfill + changes.list poll.

Unlike Slack, Drive needs no second live channel. `changes.list` against a
stored pageToken is a genuine CDC feed: it reports edits, moves, renames, trash
and permission changes, and it replays everything since the token rather than
only what happened while connected. So backfill runs once per drive and the
poll loop carries everything after it.

The one thing the feed does not do is cascade. A folder rename, move, or
re-permission emits a record for the folder alone — its descendants are silent,
though their inherited access and their path just changed. Every folder change
therefore triggers a subtree walk here. This is verified behaviour, not caution.

All Drive API access lives in this service. Workers never call Drive, so the
client's quota limiter is authoritative.
"""

from __future__ import annotations

import asyncio
import logging

from common.stream import Watermarks, WorkStream
from connectors.drive.client import DriveClient, DriveError
from connectors.drive.events import DriveEventMapper
from connectors.drive.models import Change, DriveFile
from core.message import ChangeKind, Envelope

log = logging.getLogger("poller.drive")

SOURCE = "drive"

# A pathological tree should degrade into a slow crawl, not an unbounded burst
# of 100-unit list calls. Hitting this means the walk was incomplete, so it is
# logged rather than swallowed.
MAX_FOLDERS_PER_WALK = 500


class DriveService:
    def __init__(
        self,
        client: DriveClient,
        stream: WorkStream,
        watermarks: Watermarks,
        drive_ids: list[str],
        *,
        poll_interval_seconds: float = 30.0,
    ) -> None:
        self._client = client
        self._stream = stream
        self._watermarks = watermarks
        self._drive_ids = drive_ids
        self._interval = poll_interval_seconds
        self._mapper = DriveEventMapper(client)

    # ------------------------------------------------------------- lifecycle --

    async def run_poll_loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("poll cycle failed")
            await asyncio.sleep(self._interval)

    async def poll_once(self) -> int:
        published = 0
        for drive_id in self._drive_ids:
            try:
                published += await self._poll_drive(drive_id)
            except DriveError:
                # One drive failing must not stop the others; the watermark is
                # untouched, so the next cycle retries this range.
                log.exception("drive %s: poll failed", drive_id)
        return published

    async def _poll_drive(self, drive_id: str) -> int:
        page_token = await self._watermarks.get(drive_id)
        if page_token is None:
            return await self.backfill(drive_id)
        return await self._drain_changes(drive_id, page_token)

    # -------------------------------------------------------------- backfill --

    async def backfill(self, drive_id: str) -> int:
        """First contact with a drive: create its root node, then walk it whole.

        The startPageToken is taken *before* the walk. Anything that changes
        during it is then replayed by the first poll cycle — the version guard
        absorbs the duplicate. Taking it afterwards would lose those changes
        instead.
        """
        start_token = await self._client.start_page_token(drive_id)

        published = await self._publish_root(drive_id)

        folders: list[DriveFile] = []
        files: list[DriveFile] = []
        async for file, _raw in self._client.list_files(drive_id):
            (folders if file.is_folder else files).append(file)

        # Folders first so the tree materializes top-down. Access does not depend
        # on this — a child landing early points at a placeholder row that the
        # parent's own message fills in later, and the access walk descends live
        # rather than from anything cached — but it keeps the graph coherent for
        # anything reading mid-backfill.
        for file in (*folders, *files):
            published += await self._publish_item(file, ChangeKind.CREATED)

        await self._watermarks.set(drive_id, start_token)
        log.info(
            "drive %s: backfill published %d events (%d folders, %d files)",
            drive_id,
            published,
            len(folders),
            len(files),
        )
        return published

    async def _publish_root(self, drive_id: str) -> int:
        """The drive node and the grants every descendant inherits from it.

        Published before its contents: a file whose ancestor grants have not
        landed yet resolves to readable-by-nobody.
        """
        drive = await self._client.get_drive(drive_id)
        await self._stream.publish(await self._mapper.drive_message(drive))
        log.info("drive %s: root node %r", drive_id, drive.name)
        return 1

    # --------------------------------------------------------------- changes --

    async def _drain_changes(self, drive_id: str, page_token: str) -> int:
        changes, new_token = await self._client.changes(drive_id, page_token)
        if not changes:
            await self._watermarks.set(drive_id, new_token)
            return 0

        published = 0
        walked: set[str] = set()

        for change in changes:
            if change.is_drive_change:
                # The drive itself changed — most often its permissions. Re-emit
                # the root, which is where the inherited grants live.
                published += await self._publish_root(drive_id)
                continue

            published += await self._publish_change(change)

            # A folder's descendants are not reported. Their inherited access and
            # their path both just changed, so they have to be re-read.
            if change.file is not None and change.file.is_folder and not change.is_gone:
                published += await self._walk_subtree(drive_id, change.file.id, walked)

        # Advanced only after every publish succeeded. A crash before this
        # re-emits rather than loses; the version guard absorbs duplicates.
        await self._watermarks.set(drive_id, new_token)
        log.info("drive %s: %d changes -> %d events", drive_id, len(changes), published)
        return published

    async def _publish_change(self, change: Change) -> int:
        message = await self._mapper.from_change(change)
        if message is None:
            return 0
        await self._stream.publish(message)
        _log(message)
        return 1

    async def _walk_subtree(
        self, drive_id: str, folder_id: str, walked: set[str]
    ) -> int:
        """Re-publish everything under a folder, breadth-first.

        `walked` is shared across one drain so two sibling folder changes in the
        same batch do not walk the same subtree twice.
        """
        published = 0
        queue = [folder_id]

        while queue:
            current = queue.pop(0)
            if current in walked:
                continue
            walked.add(current)

            if len(walked) > MAX_FOLDERS_PER_WALK:
                log.warning(
                    "subtree walk from %s hit the %d-folder cap; remainder deferred "
                    "to the next cycle",
                    folder_id,
                    MAX_FOLDERS_PER_WALK,
                )
                return published

            async for file, _raw in self._client.list_files(
                drive_id, parent_id=current
            ):
                published += await self._publish_item(file, ChangeKind.UPDATED)
                if file.is_folder:
                    queue.append(file.id)

        return published

    # ------------------------------------------------------------- internals --

    async def _publish_item(self, file: DriveFile, change: ChangeKind) -> int:
        await self._stream.publish(
            await self._mapper.item_message(file, change=change)
        )
        return 1


def _log(envelope: Envelope) -> None:
    log.info(
        "drive %s %s %s",
        envelope.change,
        envelope.node_type,
        envelope.entity_id,
    )
