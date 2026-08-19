"""Notion writes, for the seeder.

Separate from the read client on purpose: the ingestor should hold read-only
capabilities, and keeping the write surface in its own module makes it obvious
which credentials each service actually needs.

One constraint shapes everything here. **An internal integration cannot create
a top-level page.** Notion rejects `parent: {workspace: true}` outright:

    Internal integrations aren't owned by a single user, so creating
    workspace-level private pages is not supported.

So every fixture hangs under a root page a human created and connected to the
integration. Unlike the Slack and Drive seeders, this one cannot bootstrap from
nothing, and that is a property of Notion rather than of this code.
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.notion.client import NotionClient
from connectors.notion.models import Page

log = logging.getLogger("connectors.notion.writer")

# Notion rejects a rich_text span longer than this. Paragraphs are split rather
# than truncated so seeded prose stays intact.
MAX_TEXT_CHARS = 2000

# How far `if_exists='version'` will count before giving up. See `_free_title`.
MAX_VERSIONED_TITLES = 50


class NotionWriteError(RuntimeError):
    """A write cannot proceed. The message is caller-facing.

    Distinct from `NotionError`, which reports what an HTTP call did. This one
    reports a decision made before any call went out — a name already taken, and
    a caller who asked not to reuse it.
    """


def rich_text(content: str) -> list[dict[str, Any]]:
    return [
        {"type": "text", "text": {"content": chunk}}
        for chunk in _chunks(content, MAX_TEXT_CHARS)
    ]


def paragraph(text: str) -> dict[str, Any]:
    return {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich_text(text)}}


def heading(text: str, level: int = 2) -> dict[str, Any]:
    kind = f"heading_{min(max(level, 1), 3)}"
    return {"object": "block", "type": kind, kind: {"rich_text": rich_text(text)}}


def bullet(text: str) -> dict[str, Any]:
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {"rich_text": rich_text(text)},
    }


def code(text: str, language: str = "plain text") -> dict[str, Any]:
    return {
        "object": "block",
        "type": "code",
        "code": {"rich_text": rich_text(text), "language": language},
    }


def bookmark(url: str) -> dict[str, Any]:
    """A link block, which becomes a "mentions" edge on ingest."""
    return {"object": "block", "type": "bookmark", "bookmark": {"url": url}}


def blocks_from_markdown(text: str) -> list[dict[str, Any]]:
    """Enough markdown for fixtures: headings, bullets, fenced code, prose."""
    out: list[dict[str, Any]] = []
    in_code: list[str] = []
    fenced = False

    for line in text.splitlines():
        if line.startswith("```"):
            if fenced:
                out.append(code("\n".join(in_code)))
                in_code, fenced = [], False
            else:
                fenced = True
            continue
        if fenced:
            in_code.append(line)
        elif line.startswith("### "):
            out.append(heading(line[4:], 3))
        elif line.startswith("## "):
            out.append(heading(line[3:], 2))
        elif line.startswith("# "):
            out.append(heading(line[2:], 1))
        elif line.startswith(("- ", "* ")):
            out.append(bullet(line[2:]))
        elif line.strip():
            out.append(paragraph(line))

    if in_code:
        out.append(code("\n".join(in_code)))
    return out


class NotionWriter:
    """Fixture writes. Every create is idempotent by title."""

    def __init__(self, client: NotionClient) -> None:
        self._client = client

    # -------------------------------------------------------------- lookup --

    async def find_child_page(self, parent_id: str, title: str) -> str | None:
        """A child page of `parent_id` with this exact title, if one exists.

        Reads the block children rather than searching: search is eventually
        consistent, and a fixture created seconds ago may not be indexed yet,
        which would make re-running the seeder duplicate everything.
        """
        for raw in await self._client.block_children(parent_id):
            if raw.get("in_trash") or raw.get("archived"):
                continue
            if raw.get("type") == "child_page":
                if (raw.get("child_page") or {}).get("title") == title:
                    return raw["id"]
        return None

    async def find_child_database(self, parent_id: str, title: str) -> str | None:
        for raw in await self._client.block_children(parent_id):
            if raw.get("in_trash") or raw.get("archived"):
                continue
            if raw.get("type") == "child_database":
                if (raw.get("child_database") or {}).get("title") == title:
                    return raw["id"]
        return None

    # -------------------------------------------------------------- create --

    async def page(
        self,
        parent_id: str,
        title: str,
        *,
        body: str | None = None,
        blocks: list[dict[str, Any]] | None = None,
        if_exists: str = "reuse",
    ) -> dict[str, str]:
        """Create a page under a page, or return the existing one.

        `if_exists` decides what a title collision means, because the seeder and
        an action want opposite things from one. Seeding twice must not seed
        double, so the default is `reuse`. An action asked to create a page must
        never resolve to "your content was silently discarded", so it passes
        `fail` or `version` instead.

        Returns the page's `id` and `url`. A dict rather than a bare id because
        the url is what a later step posts into Slack, and only the create
        response carries it.
        """
        existing = await self.find_child_page(parent_id, title)
        if existing:
            if if_exists == "reuse":
                log.info("page %r exists", title)
                return {"id": existing, "url": ""}
            if if_exists == "fail":
                raise NotionWriteError(
                    f"a child page titled {title!r} already exists here"
                )
            title = await self._free_title(parent_id, title)

        children = blocks if blocks is not None else blocks_from_markdown(body or "")
        payload = {
            "parent": {"type": "page_id", "page_id": parent_id},
            "properties": {"title": {"title": rich_text(title)}},
            # Notion caps children per request; the seeder's pages stay well
            # under it, and append_blocks covers anything longer.
            "children": children[:100],
        }
        created = await self._client.call("POST", "/v1/pages", json=payload)
        page_id = created["id"]
        if len(children) > 100:
            await self.append(page_id, children[100:])
        log.info("page %r created", title)
        return {"id": page_id, "url": created.get("url") or ""}

    async def _free_title(self, parent_id: str, title: str) -> str:
        """`title` with the lowest numeric suffix not already a child page.

        Bounded rather than looped to exhaustion: a parent holding this many
        pages of one title is a runaway caller, and answering that with an error
        beats adding the two-thousandth copy.
        """
        for n in range(2, MAX_VERSIONED_TITLES + 2):
            candidate = f"{title} ({n})"
            if not await self.find_child_page(parent_id, candidate):
                return candidate
        raise NotionWriteError(
            f"{MAX_VERSIONED_TITLES} pages titled like {title!r} already exist here"
        )

    async def database(
        self,
        parent_id: str,
        title: str,
        properties: dict[str, Any],
    ) -> tuple[str, str]:
        """Create a database and return (database_id, data_source_id).

        Since 2025-09-03 the schema belongs to a data source, not the database,
        so it is passed as `initial_data_source` and the id you query afterwards
        is the data source's — not the one in the database URL.
        """
        existing = await self.find_child_database(parent_id, title)
        if existing:
            database, _ = await self._client.database(existing)
            log.info("database %r exists", title)
            return database.id, database.data_sources[0].id

        payload = {
            "parent": {"type": "page_id", "page_id": parent_id},
            "title": rich_text(title),
            "initial_data_source": {"properties": properties},
        }
        created = await self._client.call("POST", "/v1/databases", json=payload)
        database_id = created["id"]
        data_sources = created.get("data_sources") or []
        data_source_id = data_sources[0]["id"] if data_sources else database_id
        log.info("database %r created", title)
        return database_id, data_source_id

    async def row(
        self, data_source_id: str, properties: dict[str, Any]
    ) -> str:
        created = await self._client.call(
            "POST",
            "/v1/pages",
            json={
                "parent": {"type": "data_source_id", "data_source_id": data_source_id},
                "properties": properties,
            },
        )
        return created["id"]

    async def rows(self, data_source_id: str) -> list[Page]:
        """Existing rows of a data source.

        Rows have no stable title-based lookup the way child pages do, so the
        seeder checks emptiness instead to stay idempotent.
        """
        return [row async for row, _ in self._client.query_data_source(data_source_id)]

    async def bookmark_urls(self, page_id: str) -> set[str]:
        """Bookmark hrefs already on this page, so re-seeding does not duplicate."""
        found: set[str] = set()
        for raw in await self._client.block_children(page_id):
            if raw.get("type") == "bookmark":
                url = (raw.get("bookmark") or {}).get("url")
                if isinstance(url, str) and url:
                    found.add(url)
        return found

    async def append(self, page_id: str, blocks: list[dict[str, Any]]) -> None:
        for start in range(0, len(blocks), 100):
            await self._client.call(
                "PATCH",
                f"/v1/blocks/{page_id}/children",
                json={"children": blocks[start : start + 100]},
            )

    # -------------------------------------------------------------- mutate --

    async def set_title(self, page_id: str, title: str) -> Page:
        raw = await self._client.call(
            "PATCH",
            f"/v1/pages/{page_id}",
            json={"properties": {"title": {"title": rich_text(title)}}},
        )
        return Page.model_validate(raw)

    async def edit_block(self, block_id: str, text: str) -> None:
        """Rewrite a paragraph block, which is how an edit is simulated."""
        await self._client.call(
            "PATCH",
            f"/v1/blocks/{block_id}",
            json={"paragraph": {"rich_text": rich_text(text)}},
        )

    async def trash(self, page_id: str) -> None:
        """Move to trash. `in_trash` replaced `archived` in 2026-03-11; the old
        name is still accepted, but writing the current one keeps the pinned
        version honest."""
        await self._client.call(
            "PATCH", f"/v1/pages/{page_id}", json={"in_trash": True}
        )


def _chunks(text: str, size: int) -> list[str]:
    if not text:
        return [""]
    return [text[i : i + size] for i in range(0, len(text), size)]
