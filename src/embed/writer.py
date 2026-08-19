"""Keeps `node_chunk` in step with `node`.

Consumes entity ids from `stream:embed` and rewrites that node's passages. The
message carries an id and nothing else: the writer re-reads Postgres, so a job
that has been sitting in the stream while three more edits landed embeds the
body as it is *now*, not as it was when the job was written. That is what makes
out-of-order delivery safe — job A running after job B still writes B's text.

**Rewrite is delete-all-then-insert, never a patch by ordinal.** Chunk
boundaries move when a body is edited: insert a paragraph near the top and every
later `ord` shifts. Patching by ordinal would leave orphaned vectors from the
old, longer split and misalign the text of everything after the edit.

The delete runs unconditionally, before the decision about whether to re-embed.
A node that has been tombstoned, emptied, or reduced below the embedding policy
must end with no chunks — and that is the case that reaches this worker as
"nothing to insert", which a delete guarded by "did we produce chunks" would
skip. Tombstones matter especially: `Store._tombstone` is an UPDATE, so the
`ON DELETE CASCADE` on `node_chunk` never fires on the normal delete path.
"""

from __future__ import annotations

import asyncio

import asyncpg

from common import config, db, redis_client
from common.consumer import consume
from common.logging import setup
from common.vector import to_sql
from core.types import NodeType
from embed import ChunkerClient, should_embed
from embed.chunk import MAX_CHUNKS

log = setup("embed.writer")

_SELECT_NODE = """
SELECT id, node_type, body, payload, deleted_at
FROM node
WHERE entity_id = $1
"""

_DELETE_CHUNKS = "DELETE FROM node_chunk WHERE node_id = $1"

_INSERT_CHUNK = """
INSERT INTO node_chunk (node_id, ord, text, embedding)
VALUES ($1, $2, $3, $4::vector)
"""


def embed_text(node_type: str, body: str, payload: dict) -> str:
    """The text that represents this node to semantic search.

    `body` for everything, plus Notion page properties. A database row's
    properties *are* its content — "Status: Shipped", "Owner: Ana" live nowhere
    in the block tree — so a row embedded from `body` alone is embedded from
    almost nothing.

    The title is not concatenated here. `chunk_document` reserves budget for it
    and prefixes every passage, which keeps the subject attached to passage
    twelve instead of only to passage one.
    """
    parts = [body.strip()]
    if node_type == NodeType.NOTION_PAGE:
        props = payload.get("properties")
        if isinstance(props, dict):
            parts.extend(
                f"{k}: {v}"
                for k, v in sorted(props.items())
                if isinstance(v, str) and v.strip()
            )
    return "\n".join(p for p in parts if p)


def _title_of(payload: dict) -> str | None:
    for key in ("title", "name"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


async def rewrite(
    conn: asyncpg.Connection, client: ChunkerClient, entity_id: str
) -> int:
    """Replace this node's chunks. Returns how many now exist."""
    row = await conn.fetchrow(_SELECT_NODE, entity_id)
    if row is None:
        # The id was enqueued, so a row existed at commit time; a reset between
        # then and now is the only way here. Nothing to keep in step.
        return 0

    node_id = row["id"]
    payload = row["payload"] or {}
    body = row["body"] or ""

    # Unmaterialized (no type yet) and tombstoned rows both end with zero
    # chunks. The delete above already achieved that.
    if row["node_type"] is None or row["deleted_at"] is not None:
        async with conn.transaction():
            await conn.execute(_DELETE_CHUNKS, node_id)
        return 0

    text = embed_text(row["node_type"], body, payload)
    chunks = []
    if should_embed(row["node_type"], text):
        chunks = await client.chunk(
            text, title=_title_of(payload), kind=row["node_type"]
        )
        if len(chunks) > MAX_CHUNKS:  # defensive; the service already caps
            chunks = chunks[:MAX_CHUNKS]

    # Delete and insert in one transaction so a reader never observes a node
    # with half its passages replaced.
    async with conn.transaction():
        await conn.execute(_DELETE_CHUNKS, node_id)
        if chunks:
            await conn.executemany(
                _INSERT_CHUNK,
                [(node_id, c.ord, c.text, to_sql(c.embedding)) for c in chunks],
            )
    return len(chunks)


async def main() -> None:
    log.info(
        "starting; stream=%s group=%s consumer=%s embed=%s",
        config.EMBED_STREAM,
        config.EMBED_GROUP,
        config.EMBED_CONSUMER,
        config.EMBED_URL,
    )
    redis = redis_client.client()
    pool = await db.pool()

    async with ChunkerClient(config.EMBED_URL) as client:

        async def handle(fields: dict[str, str]) -> None:
            entity_id = fields.get("entity_id") or ""
            if not entity_id:
                raise ValueError("embed job has no entity_id")
            async with pool.acquire() as conn:
                n = await rewrite(conn, client, entity_id)
            log.debug("%s -> %d chunk(s)", entity_id, n)

        try:
            await consume(
                redis,
                stream=config.EMBED_STREAM,
                group=config.EMBED_GROUP,
                consumer=config.EMBED_CONSUMER,
                handle=handle,
                log=log,
            )
        finally:
            await pool.close()
            await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
