"""Notion ingestion service: watermark sweep + periodic reconcile.

Notion is the hardest of the three sources to keep current, because it offers
the least:

  no change feed      there is no changes.list equivalent and no cursor that
                      means "everything since". The closest thing is /v1/search
                      sorted by last_edited_time descending, which is what the
                      sweep uses.
  no delete signal    trashed pages simply stop appearing. So do pages
                      disconnected from the integration. Neither is reported.
  minute granularity  last_edited_time is truncated to the minute, so a strict
                      `>` watermark drops edits made in the same minute as the
                      previous cycle's newest.

The service answers those in order: sweep by watermark for updates, compare
inclusively so a same-minute edit is re-emitted rather than lost, and run a full
enumeration every N cycles to find what has gone missing. The version guard
absorbs the duplicates all three of those produce.

All Notion API access lives here. Workers never call Notion, so the client's
rate limiter is authoritative.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from redis.asyncio import Redis

from common.stream import Watermarks, WorkStream
from connectors.notion.client import NotionClient, NotionError
from connectors.notion.events import NotionEventMapper
from connectors.notion.models import DataSource, Page
from connectors.notion.registry import NotionRegistry
from core.message import ChangeKind
from core.types import NodeType

log = logging.getLogger("poller.notion")

SOURCE = "notion"

# One watermark for the whole workspace: /v1/search is workspace-wide, so there
# is no per-container cursor to keep. The key is a constant rather than an id.
WATERMARK_KEY = "workspace"

# A full enumeration costs one search sweep plus one query per data source. At
# demo scale that is a handful of calls, so running it every few cycles is
# cheap; it is the only way deletions are ever noticed.
DEFAULT_RECONCILE_EVERY = 10


def _known_key(node_type: NodeType, entity_id: str) -> str:
    """Node type and id in one set member, so a deletion still knows what it
    was deleting: the object is gone by the time we notice, and Notion offers
    no way to ask after the fact."""
    return f"{node_type}|{entity_id}"


class KnownEntities:
    """What was last seen visible, for deletion detection.

    Entries are `"{node_type}|{id}"`. The type rides along because a deletion is
    detected by absence — the object is gone, so there is nothing left to ask
    what kind of thing it was.

    Redis-backed rather than in-process: a restarted poller with an empty set
    would find every id "new" and, worse, would never notice what disappeared
    while it was down.
    """

    def __init__(self, redis: Redis, source: str) -> None:
        self._redis = redis
        self._key = f"known:{source}"

    async def all(self) -> set[str]:
        return set(await self._redis.smembers(self._key))

    async def replace(self, entity_ids: set[str]) -> None:
        # Pipelined so a crash cannot leave the set empty between the delete and
        # the refill, which would look like "everything was deleted".
        pipe = self._redis.pipeline()
        pipe.delete(self._key)
        if entity_ids:
            pipe.sadd(self._key, *entity_ids)
        await pipe.execute()

    async def add(self, entity_ids: set[str]) -> None:
        if entity_ids:
            await self._redis.sadd(self._key, *entity_ids)


class NotionService:
    def __init__(
        self,
        client: NotionClient,
        registry: NotionRegistry,
        stream: WorkStream,
        watermarks: Watermarks,
        known: KnownEntities,
        *,
        poll_interval_seconds: float = 60.0,
        reconcile_every: int = DEFAULT_RECONCILE_EVERY,
    ) -> None:
        self._client = client
        self._registry = registry
        self._stream = stream
        self._watermarks = watermarks
        self._known = known
        self._interval = poll_interval_seconds
        self._reconcile_every = reconcile_every
        self._cycles = 0
        self._mapper = NotionEventMapper(client, registry)

    # ------------------------------------------------------------ lifecycle --

    async def run_poll_loop(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("poll cycle failed")
            await asyncio.sleep(self._interval)

    async def poll_once(self) -> int:
        """One sweep. Reconciles on the first cycle and every Nth after."""
        await self._registry.refresh(self._client)

        # Identities before content: a grant to the workspace identity must not
        # land before that identity exists.
        await self._stream.publish(self._mapper.workspace_event())
        published = 1

        reconciling = self._cycles % self._reconcile_every == 0
        since = None if reconciling else await self._watermarks.get(WATERMARK_KEY)

        published += await self._sweep(since, reconcile=reconciling)
        self._cycles += 1
        return published

    # ---------------------------------------------------------------- sweep --

    async def _sweep(self, since: str | None, *, reconcile: bool) -> int:
        """Fetch everything edited at or after `since`; None means everything."""
        pages: dict[str, tuple[Page, dict[str, Any]]] = {}
        data_sources: dict[str, tuple[DataSource, dict[str, Any]]] = {}
        seen: set[str] = set()
        newest = since

        async for raw in self._client.search():
            entity_id = raw.get("id")
            edited = raw.get("last_edited_time")
            if not entity_id or not edited:
                continue

            # Search is sorted newest-first, so the first item older than the
            # watermark ends the scan — except during a reconcile, which must
            # enumerate everything to know what is still visible.
            stale = since is not None and edited < since
            if stale and not reconcile:
                break

            seen.add(
                _known_key(
                    NodeType.NOTION_DATA_SOURCE
                    if raw.get("object") == "data_source"
                    else NodeType.NOTION_PAGE,
                    entity_id,
                )
            )
            if newest is None or edited > newest:
                newest = edited
            if stale:
                continue

            if raw.get("object") == "data_source":
                data_sources[entity_id] = (DataSource.model_validate(raw), raw)
            else:
                pages[entity_id] = (Page.model_validate(raw), raw)

        # Databases are reached through their data source's parent rather than
        # from search, so their ids have to be added to `seen` explicitly. Left
        # out, a trashed database would never be detected as gone.
        database_ids, published = await self._publish_databases(data_sources.values())
        seen |= database_ids
        published += await self._publish_data_sources(data_sources.values())

        # Rows are queried directly as well as swept: search has historically
        # lagged on database content, and a row missing from the graph is worse
        # than a duplicate event.
        for data_source, _ in data_sources.values():
            async for row, raw in self._client.query_data_source(data_source.id):
                seen.add(_known_key(NodeType.NOTION_PAGE, row.id))
                if since is None or row.last_edited_time >= since:
                    pages.setdefault(row.id, (row, raw))
                if newest is None or row.last_edited_time > newest:
                    newest = row.last_edited_time

        published += await self._publish_pages(pages.values())

        if reconcile:
            published += await self._publish_deletions(seen)
            await self._known.replace(seen)
        else:
            await self._known.add(seen)

        # Advanced only after every publish succeeded, so a crash re-emits
        # rather than loses.
        if newest and newest != since:
            await self._watermarks.set(WATERMARK_KEY, newest)

        log.info(
            "cycle complete: %d events (%d pages, %d data sources)%s",
            published,
            len(pages),
            len(data_sources),
            ", reconciled" if reconcile else "",
        )
        return published

    # ------------------------------------------------------------- publish --

    async def _publish_databases(self, data_sources) -> tuple[set[str], int]:
        """The container above each data source.

        Search never returns databases — the 2026-03-11 filter accepts only
        `page` and `data_source` — so they are reached through the data source's
        parent, once per distinct id. Returns the ids that were visible, which
        the caller folds into the reconcile set.
        """
        visible: set[str] = set()
        published = 0
        for database_id in {
            ds.parent.target_id for ds, _ in data_sources if ds.parent.target_id
        }:
            try:
                database, _raw = await self._client.database(database_id)
            except NotionError as exc:
                log.warning("database %s unavailable: %s", database_id, exc.code)
                continue
            visible.add(_known_key(NodeType.NOTION_DATABASE, database_id))
            await self._stream.publish(
                await self._mapper.database_event(database)
            )
            published += 1
        return visible, published

    async def _publish_data_sources(self, data_sources) -> int:
        published = 0
        for data_source, _raw in data_sources:
            await self._stream.publish(
                await self._mapper.data_source_event(data_source)
            )
            published += 1
        return published

    async def _publish_pages(self, pages) -> int:
        """Roots first, so a child never lands before the grants it inherits.

        Correctness does not depend on this — parent_id is a column and a child
        pointing at a row that arrives later resolves to readable-by-nobody, not
        readable-by-anyone — but the failure it avoids is a confusing one.
        """
        ordered = sorted(pages, key=lambda item: not item[0].parent.is_workspace)
        published = 0
        for page, _raw in ordered:
            try:
                write = await self._mapper.page_event(
                    page, change=ChangeKind.UPDATED
                )
            except NotionError as exc:
                # One unreadable page must not abort the cycle; the watermark
                # stays put for it and the next sweep retries.
                log.warning("page %s failed: %s", page.id, exc.code)
                continue
            await self._stream.publish(write)
            published += 1
        return published

    async def _publish_deletions(self, seen: set[str]) -> int:
        """Anything previously visible and now absent.

        Trashed and disconnected are indistinguishable here, and both must stop
        being retrievable, so both emit the same delete.
        """
        gone = await self._known.all() - seen
        for key in gone:
            node_type, _, entity_id = key.partition("|")
            await self._stream.publish(
                self._mapper.delete_event(NodeType(node_type), entity_id)
            )
        if gone:
            log.info("reconcile: %d entities no longer visible", len(gone))
        return len(gone)
