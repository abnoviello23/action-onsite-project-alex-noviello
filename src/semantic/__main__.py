"""Semantic worker: derives entities and facts, and re-derives them on change.

Consumes `stream:semantic`, one source document per message, and branches on
what happened to that document.

  **created** — extract. Entities are resolved or minted, facts are recorded
  against them, links are drawn between them.

  **updated** — retract, then extract. Every fact derived from the previous
  version is deleted first: it was a reading of text that has since changed, and
  leaving it beside the new conclusions would let a stale claim outlive the
  sentence that produced it. The entities stay; only what was said about them is
  re-derived.

  **deleted** — retract, and do not replace. The document is gone, so the claims
  it supported are gone with it. Entities stay: a person does not stop existing
  because one message about them was deleted, and an entity whose last readable
  note has gone is already invisible without anything being withdrawn.

Reconciliation is deterministic rather than a judgement call, and that is the
point. Facts are *owned* by their source — one document, one set of claims — so
"which of these are stale" has an exact answer that needs no model to decide.
The model is spent on reading the new text, not on adjudicating the old.

One loop runs here: the consumer. There is no background republisher, and
that is deliberate — the ingest worker's enqueue asks the row whether an
extraction is owed rather than whether this delivery wrote it, so a publish lost
to a crash is repaired by the redelivery that follows it. Re-offering the corpus
is a deliberate act instead: `python -m semantic --backfill`, which is what an
ontology change calls for.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from enum import StrEnum

from agent.client import MessagesClient
from agent.openai_client import OpenAIMessagesClient
from common import config, db, redis_client
from common.consumer import consume
from common.logging import setup
from common.stream import STREAM_MAXLEN
from core.message import ChangeKind
from semantic.config import load_active
from semantic.extract import Extractor
from semantic.models import SemanticJob, SemanticWrite
from semantic.registry import ActiveConfig, load
from semantic.store import SemanticStore

log = setup("semantic")

# Rows whose extraction watermark is behind — the same predicate the ingest
# worker checks per row, so "pending" means one thing in both places. Oldest
# first so a backfill proceeds in a predictable direction.
#
# Restricted to declared source types. Every derived node also has a lagging
# watermark, because nothing ever advances one for a fact — so without this the
# backfill offers the whole semantic layer back to the extractor, which reads
# each job only to find it has no types declared for a `fact` and drop it.
_PENDING_ALL = """
SELECT entity_id, node_type, content_version
FROM node
WHERE semantic_version < content_version
  AND node_type = ANY($1::text[])
  AND deleted_at IS NULL
ORDER BY updated_at
"""


async def _retract_only(store: SemanticStore, conn, job: SemanticJob) -> int:
    """The document is gone. Withdraw what it implied and replace nothing.

    Deleting the facts is the whole of it. Entity visibility is derived from
    exactly these rows, so an entity whose last readable note just went is now
    invisible, and one with notes from other documents is untouched — no grant
    to withdraw, no provenance to prune.
    """
    async with conn.transaction():
        affected = await store.entities_from(job.entity_id)
        retracted = await store.retract_from(job.entity_id)
    if retracted:
        log.info(
            "%s is gone; retracted %d note(s) across %d entity(ies)",
            job.entity_id,
            retracted,
            len(affected),
        )
    return retracted


class Outcome(StrEnum):
    """Which of the four paths a job took.

    Named rather than inferred, because the distinction between them is the
    invariant this module exists to hold: `RETRACTED` and `APPLIED` are
    conclusions about the document, `NO_RULES` is a statement about the config,
    and `UNEVALUATED` is not a statement at all. Collapsing the last two into
    "we looked and found nothing" is what once deleted a document's notes and
    stamped it complete.
    """

    SKIPPED = "skipped"          # watermark already at or past this version
    RETRACTED = "retracted"      # document gone; conclusions withdrawn
    NO_RULES = "no_rules"        # ontology declares nothing for this type
    UNEVALUATED = "unevaluated"  # refused or errored; nothing was read
    APPLIED = "applied"          # read it, wrote whatever it found


@dataclass(frozen=True)
class JobResult:
    """What one job did. `embed` is drained by the caller after commit."""

    outcome: Outcome
    write: SemanticWrite | None = None
    entities: int = 0
    facts: int = 0
    retracted: int = 0
    embed: tuple[str, ...] = ()

    @property
    def touched_notes(self) -> bool:
        """Whether this job was allowed to change what is on disk."""
        return self.outcome in (Outcome.APPLIED, Outcome.RETRACTED)


async def _handle_job(
    conn, client: MessagesClient, active: ActiveConfig, job: SemanticJob
) -> JobResult:
    """One document, end to end. Returns fact ids to enqueue for embedding.

    Takes a connection rather than a pool so the caller owns its lifetime, and so
    this is directly callable from a test. The previous shape could only be
    exercised by reimplementing it, and the reimplementation is what let the bug
    below survive: the test mirrored the defect instead of catching it.

    Four outcomes, and keeping them apart is the whole point of this function.
    Retraction deletes what an earlier pass concluded, so it may run only when
    this pass actually read the document:

      gone         row absent or tombstoned  -> retract, replace nothing
      no rules     ontology declares nothing -> mark, keep existing notes
      unevaluated  model refused or errored  -> touch nothing, retry later
      evaluated    read it                   -> retract, apply, mark

    Only the last two say anything about the *document*. "No rules" is a
    statement about the config and must not delete a previous ontology's work;
    "unevaluated" is not a statement at all. Both once arrived here as an empty
    write and were handled as "we looked, there is nothing there" — which
    stripped the document's notes and stamped it complete, so nothing ever
    revisited it. The absence of a result is not a result of absence.
    """
    store = SemanticStore(conn)
    row = await store.source_node(job.entity_id)

    # Gone or tombstoned. Whatever the job said, the document is not there now,
    # so everything it implied goes and nothing replaces it.
    if row is None:
        retracted = await _retract_only(store, conn, job)
        return JobResult(outcome=Outcome.RETRACTED, retracted=retracted)

    # The watermark, not the job, decides. A redelivery of an already-handled
    # version does nothing, and a job queued while newer edits landed works from
    # the body as it is now — which is why the current version is read from the
    # row rather than trusted from the message.
    current = row["content_version"]
    if row["semantic_version"] >= current:
        log.debug("%s already handled at %s", job.entity_id, current)
        return JobResult(outcome=Outcome.SKIPPED)

    # The row's type, never the job's. A message naming the wrong type would
    # otherwise find nothing declared for it, and a perfectly extractable
    # document would be treated as having nothing to extract. The row is what the
    # graph actually holds, so a malformed job self-heals instead of doing damage.
    node_type = row["node_type"]
    if node_type != job.node_type:
        log.warning(
            "job for %s claims node_type %r but the row is %r; using the row",
            job.entity_id,
            job.node_type,
            node_type,
        )

    cfg = await active.get(conn)
    extractor = Extractor(client, cfg, max_turns=config.SEMANTIC_MAX_TURNS)

    if not extractor.applies_to(node_type):
        # Nothing is declared extractable from this kind of document, so there is
        # nothing to find. Mark it, or every sweep re-offers it forever — but do
        # not retract. Notes an earlier ontology drew are still true readings of
        # this document, and dropping a type from the config should not silently
        # delete them. That is a deliberate backfill, never a side effect.
        async with conn.transaction():
            await store.mark_extracted(job.entity_id, current)
        log.info(
            "%s: ontology declares nothing for %s; marked, existing notes intact",
            job.entity_id,
            node_type,
        )
        return JobResult(outcome=Outcome.NO_RULES)

    current_job = job.model_copy(
        update={"content_version": current, "node_type": node_type}
    )
    write = await extractor.run(
        conn, store, current_job, row, effort=config.SEMANTIC_EFFORT or None
    )

    if write is None:
        # Not evaluated. Leave the notes and the watermark exactly as they were,
        # so the document is offered again rather than recorded as done.
        log.warning(
            "%s was not evaluated; leaving notes and watermark untouched",
            job.entity_id,
        )
        return JobResult(outcome=Outcome.UNEVALUATED)

    async with conn.transaction():
        # Retract inside the same transaction as the write. A crash between them
        # would otherwise leave a document with its old conclusions deleted and
        # its new ones never applied.
        retracted = await store.retract_from(job.entity_id)
        entities = facts = 0
        if not write.is_empty:
            entities, facts = await store.apply(write)
        # Marked even when nothing was found. "This document implies nothing" is
        # a real conclusion and a common one, and not recording it would make
        # every sweep re-offer the document forever.
        await store.mark_extracted(job.entity_id, current)
        stale = list(store.embed)

    log.info(
        "%s -> %d entity(ies), %d note(s), %d edge(s); retracted %d",
        job.entity_id,
        entities,
        facts,
        len(write.edges),
        retracted,
    )
    return JobResult(
        outcome=Outcome.APPLIED,
        write=write,
        entities=entities,
        facts=facts,
        retracted=retracted,
        embed=tuple(stale),
    )


def _client():
    """The extraction client for the configured provider.

    Both present the same `create(...) -> Turn`, so nothing downstream branches
    on which one is in play. See `agent.openai_client` for why the second exists.
    """
    if config.SEMANTIC_PROVIDER == "openai":
        return OpenAIMessagesClient(
            base_url=config.OPENAI_BASE_URL,
            api_key=config.OPENAI_API_KEY,
            model=config.OPENAI_MODEL,
        )
    return MessagesClient(
        base_url=config.ANTHROPIC_BASE_URL,
        api_key=config.ANTHROPIC_API_KEY,
        version=config.ANTHROPIC_VERSION,
        model=config.SEMANTIC_MODEL,
    )


async def backfill(pool, redis) -> int:
    """Publish an extraction job for every document whose watermark is behind.

    Runs once and returns. It is not a background loop, and the difference is
    the whole point.

    A loop cannot tell three states apart — never published, sitting in the
    queue, being extracted right now — because `semantic_version` only advances
    when extraction *finishes* and Postgres cannot see Redis. So it re-offers
    work that is already in flight, on a fixed interval, regardless of how much
    is already queued. Run alongside a consumer that is slower than the
    interval, it fills the stream with copies of its own backlog and crowds out
    the work it is trying to help.

    Nothing needs it to run continuously any more. The ingest worker's enqueue
    is idempotent — it asks the row whether extraction is owed rather than
    whether this delivery wrote it — so a publish lost to a crash is repaired
    by the redelivery that follows. This is now what it should always have
    been: a deliberate re-offer, for when the ontology changes and the corpus
    has to be read again.

    Streamed through a server-side cursor rather than fetched whole: a backfill
    over a large corpus should not be bounded by how much of it fits in memory.
    """
    total = 0
    async with pool.acquire() as conn:
        cfg = await load_active(conn)
        if cfg is None:
            log.warning("no active ontology; nothing to offer")
            return 0
        sources = cfg.source_types()
        async with conn.transaction():
            async for row in conn.cursor(_PENDING_ALL, sources):
                job = SemanticJob(
                    entity_id=row["entity_id"],
                    node_type=row["node_type"],
                    content_version=row["content_version"],
                    # Indistinguishable from a first extraction here, and it
                    # does not matter: retraction is a no-op when there is
                    # nothing to retract.
                    change=ChangeKind.UPDATED,
                )
                await redis.xadd(
                    config.SEMANTIC_STREAM,
                    {"job": job.model_dump_json()},
                    maxlen=STREAM_MAXLEN,
                    approximate=True,
                )
                total += 1
    log.info("backfill published %d pending document(s)", total)
    return total


async def main() -> None:
    if not config.SEMANTIC_ENABLED:
        raise SystemExit("SEMANTIC_ENABLED is false; not starting the semantic worker")
    if config.SEMANTIC_PROVIDER == "openai":
        if not config.OPENAI_API_KEY:
            raise SystemExit("OPENAI_API_KEY is required when SEMANTIC_PROVIDER=openai")
    elif not config.ANTHROPIC_API_KEY:
        raise SystemExit("ANTHROPIC_API_KEY is required for extraction")

    log.info(
        "starting; stream=%s group=%s consumer=%s provider=%s turns=%d concurrency=%d",
        config.SEMANTIC_STREAM,
        config.SEMANTIC_GROUP,
        config.SEMANTIC_CONSUMER,
        config.SEMANTIC_PROVIDER,
        config.SEMANTIC_MAX_TURNS,
        config.SEMANTIC_CONCURRENCY,
    )

    redis = redis_client.client()
    pool = await db.pool()
    # Loaded once here so a bad ontology fails the process at boot rather than
    # one document at a time; `ActiveConfig` refreshes it from then on.
    async with pool.acquire() as conn:
        await load(conn)
    active = ActiveConfig()

    # Bounded because every extraction is several model calls against one shared
    # rate limit; Postgres is nowhere near the constraint.
    gate = asyncio.Semaphore(max(1, config.SEMANTIC_CONCURRENCY))

    async with _client() as client:

        async def handle(fields: dict[str, str]) -> None:
            raw = fields.get("job") or ""
            if not raw:
                raise ValueError("semantic job has no payload")
            job = SemanticJob.model_validate_json(raw)
            async with gate:
                async with pool.acquire() as conn:
                    result = await _handle_job(conn, client, active, job)
            # After the commit, for the same reason the ingest worker drains its
            # own queue there: a fact the embed writer picks up before the row is
            # visible would produce chunks for text that is not there yet.
            for entity_id in result.embed:
                await redis.xadd(config.EMBED_STREAM, {"entity_id": entity_id})

        try:
            await consume(
                redis,
                stream=config.SEMANTIC_STREAM,
                group=config.SEMANTIC_GROUP,
                consumer=config.SEMANTIC_CONSUMER,
                handle=handle,
                log=log,
                # Extraction jobs are order-independent — they carry an id, read
                # current state, and are version-guarded — so the batch really
                # can run in parallel. Without this the semaphore below has
                # nothing to gate and the setting is inert.
                concurrency=config.SEMANTIC_CONCURRENCY,
            )
        finally:
            await pool.close()
            await redis.aclose()


async def _backfill_once() -> None:
    """`python -m semantic --backfill`: re-offer every pending document, then exit."""
    redis = redis_client.client()
    pool = await db.pool()
    try:
        async with pool.acquire() as conn:
            await load(conn)
        await backfill(pool, redis)
    finally:
        await pool.close()
        await redis.aclose()


if __name__ == "__main__":
    if "--backfill" in sys.argv[1:]:
        asyncio.run(_backfill_once())
    else:
        asyncio.run(main())
