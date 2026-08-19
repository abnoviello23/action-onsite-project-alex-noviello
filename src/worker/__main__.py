"""Data worker: consumes one partition stream.

Per envelope: generate a GraphWrite, apply it in one transaction, then enqueue
the follow-on work it implies — re-embedding, and semantic extraction. Workers
never call source APIs and never call a model.

Both enqueues are deliberately outside the transaction. `Store` collects the
entity ids whose derived state is now stale and this drains them after the
commit returns; publishing inside would let a consumer read the row before it
was visible and derive from the body it replaced, producing chunks and entities
that look current and are not.

The reverse ordering — commit, then crash before publishing — loses a job rather
than corrupting one. The next real edit repairs the embed side, and the semantic
sweeper repairs the extraction side by finding rows whose `semantic_version` is
behind. That asymmetry is why both enqueues go here and not in `Store.apply`.

Poison handling, claiming, and ACK ordering live in `common.consumer`.
"""

from __future__ import annotations

import asyncio

from common import config, db, redis_client
from common.consumer import consume
from common.logging import setup
from core.message import ChangeKind, Envelope
from graph.registry import generator_for
from semantic.models import SemanticJob
from store import Store

log = setup("worker")

# Whether this document still owes an extraction. The same predicate the
# backfill uses, so the two can never disagree about what "pending" means.
_NEEDS_EXTRACTION = """
SELECT semantic_version < content_version
FROM node
WHERE entity_id = $1 AND node_type IS NOT NULL AND deleted_at IS NULL
"""


async def _handle(pool, redis, fields: dict[str, str]) -> None:
    raw = fields.get("envelope") or ""
    env = Envelope.model_validate_json(raw)
    gen = generator_for(env.node_type)

    async with pool.acquire() as conn:
        async with conn.transaction():
            store = Store(conn)
            write = await gen.generate(env, store)
            if write is not None:
                await store.apply(write)
        stale = list(store.reembed)
        # Asked of the row, not of the write. `apply` reports whether the
        # guarded upsert *took* this delivery, which is a different question
        # and the wrong one: if a previous delivery committed and then died
        # before publishing, the redelivery that exists to repair it upserts
        # nothing, and a signal derived from the write would skip the enqueue
        # precisely when it is needed. The watermark is a durable fact about
        # the row, so re-asking it is idempotent and self-healing.
        needs_extraction = bool(
            await conn.fetchval(_NEEDS_EXTRACTION, env.entity_id)
        )

    for entity_id in stale:
        await redis.xadd(config.EMBED_STREAM, {"entity_id": entity_id})

    if not (config.SEMANTIC_ENABLED and write is not None):
        return

    # Deletes are published too, and that is the point of raising a job on this
    # path at all rather than only on new content. A tombstoned document has
    # already had its facts dropped in the transaction above; the job is what
    # makes the semantic worker notice which entities were affected and record
    # the retraction. A tombstone leaves no content to extract, so the watermark
    # check below would never raise this job.
    if write.change is ChangeKind.DELETED:
        job = SemanticJob(
            entity_id=write.entity_id,
            node_type=str(write.node_type),
            content_version="",
            change=ChangeKind.DELETED,
        )
        await redis.xadd(config.SEMANTIC_STREAM, {"job": job.model_dump_json()})
        return

    # A duplicate or out-of-order delivery whose content the graph already
    # holds leaves the watermark level, and re-extracting it would pay for the
    # same conclusions twice.
    if not (needs_extraction and write.node):
        return

    # `relations` carries the structural edges this write just minted. It is
    # context for the extraction prompt — a reply knowing it is a reply — and
    # never something the extractor is asked to reproduce.
    job = SemanticJob(
        entity_id=write.entity_id,
        node_type=str(write.node.node_type),
        content_version=write.node.content_version,
        change=write.change,
        relations=[e.relation for e in write.edges],
    )
    await redis.xadd(config.SEMANTIC_STREAM, {"job": job.model_dump_json()})


async def main() -> None:
    partition = config.PARTITION
    consumer = config.CONSUMER_NAME
    if partition == "" or consumer == "":
        raise SystemExit("PARTITION and CONSUMER_NAME are required")

    stream = config.work_stream(int(partition))
    log.info(
        "starting; stream=%s group=%s consumer=%s (of %d partitions)",
        stream,
        config.CONSUMER_GROUP,
        consumer,
        config.NUM_PARTITIONS,
    )

    redis = redis_client.client()
    pool = await db.pool()
    try:
        await consume(
            redis,
            stream=stream,
            group=config.CONSUMER_GROUP,
            consumer=consumer,
            handle=lambda fields: _handle(pool, redis, fields),
            log=log,
        )
    finally:
        await pool.close()
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
