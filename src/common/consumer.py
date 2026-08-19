"""Redis Streams consumer-group plumbing, shared by the ingest and embed workers.

Both consume a stream, both must claim work abandoned by a dead peer, both must
stop redelivering a message that will never succeed, and both must ACK only
after their side effect has committed. That is the whole of this module; what a
message *means* stays with the caller's `handle`.

ACK ordering is the load-bearing part. A message is acknowledged only after
`handle` returns, so a crash mid-work leaves it pending and `xautoclaim` brings
it back. The cost is duplicate delivery, which every handler here is expected to
absorb — the ingest worker through its version guard, the embed writer by
re-reading current state.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import ResponseError

from common import config

Handler = Callable[[dict[str, str]], Awaitable[None]]

# Redeliveries before a message is treated as poison and sidelined. Set against
# the ingest worker's failure modes: a transient Postgres blip clears well
# inside this, a malformed envelope never will.
POISON_AFTER = 5
CLAIM_IDLE_MS = 60_000
READ_COUNT = 10
READ_BLOCK_MS = 5_000


async def ensure_group(redis: Redis, stream: str, group: str) -> None:
    try:
        await redis.xgroup_create(stream, group, id="0", mkstream=True)
    except ResponseError as exc:
        if "BUSYGROUP" not in str(exc):
            raise


async def delivery_count(redis: Redis, stream: str, group: str, msg_id: str) -> int:
    pending = await redis.xpending_range(
        stream, group, min=msg_id, max=msg_id, count=1
    )
    if not pending:
        return 1
    entry = pending[0]
    if isinstance(entry, dict):
        return int(entry.get("times_delivered") or 1)
    return int(getattr(entry, "times_delivered", 1) or 1)


async def to_dlq(
    redis: Redis,
    *,
    stream: str,
    group: str,
    msg_id: str,
    body: dict[str, str],
    reason: str,
    log: logging.Logger,
) -> None:
    """Sideline and ACK. The original fields are carried through verbatim so the
    DLQ entry can be replayed without reconstructing it."""
    await redis.xadd(
        config.DLQ_STREAM,
        {**body, "reason": reason, "from": stream, "id": msg_id},
    )
    await redis.xack(stream, group, msg_id)
    log.error("poison %s -> dlq (%s)", msg_id, reason)


async def _dispatch(
    redis: Redis,
    *,
    stream: str,
    group: str,
    msg_id: str,
    fields: dict[str, str],
    handle: Handler,
    log: logging.Logger,
) -> None:
    if await delivery_count(redis, stream, group, msg_id) > POISON_AFTER:
        await to_dlq(
            redis,
            stream=stream,
            group=group,
            msg_id=msg_id,
            body=fields,
            reason="delivery_count",
            log=log,
        )
        return
    try:
        await handle(fields)
    except Exception:
        log.exception("failed %s %s", stream, msg_id)
        # Left unacknowledged so xautoclaim retries it; sidelined once the
        # redelivery budget is spent.
        if await delivery_count(redis, stream, group, msg_id) >= POISON_AFTER:
            await to_dlq(
                redis,
                stream=stream,
                group=group,
                msg_id=msg_id,
                body=fields,
                reason="exception",
                log=log,
            )
        return
    await redis.xack(stream, group, msg_id)


async def _claim_stale(
    redis: Redis, stream: str, group: str, consumer: str
) -> list[tuple[str, dict[str, str]]]:
    claimed = await redis.xautoclaim(
        stream,
        group,
        consumer,
        min_idle_time=CLAIM_IDLE_MS,
        start_id="0-0",
        count=READ_COUNT,
    )
    # redis-py: (next_id, messages[, deleted])
    messages = claimed[1] if claimed else []
    return [(mid, fields) for mid, fields in messages]


async def consume(
    redis: Redis,
    *,
    stream: str,
    group: str,
    consumer: str,
    handle: Handler,
    log: logging.Logger,
    concurrency: int = 1,
) -> None:
    """Claim, read, dispatch, forever. Returns only on cancellation.

    `concurrency` defaults to 1 — serial — and that default is load-bearing
    rather than conservative. The ingest worker's correctness rests on per-entity
    ordering: the poller partitions by entity so that two edits to one document
    reach the same consumer in order, and dispatching a batch concurrently would
    throw that away for the sake of throughput it does not need.

    Semantic extraction is the opposite case and may raise it. Those jobs carry
    an id and nothing else, re-read current state from Postgres, and are guarded
    by a version watermark — so they are order-independent by construction, and
    a batch of ten can run at once. Each message acknowledges on its own
    completion, so a failure inside a batch still leaves exactly its own message
    pending for redelivery.
    """
    await ensure_group(redis, stream, group)
    gate = asyncio.Semaphore(max(1, concurrency))

    async def run(msg_id: str, fields: dict[str, str]) -> None:
        async with gate:
            await _dispatch(
                redis,
                stream=stream,
                group=group,
                msg_id=msg_id,
                fields=fields,
                handle=handle,
                log=log,
            )

    async def drain(batch: list[tuple[str, dict[str, str]]]) -> None:
        if not batch:
            return
        if concurrency <= 1:
            for msg_id, fields in batch:
                await run(msg_id, fields)
            return
        # `_dispatch` never raises — it sidelines or leaves pending — so a
        # gather here cannot lose siblings to one bad message.
        await asyncio.gather(*(run(m, f) for m, f in batch))

    while True:
        await drain(await _claim_stale(redis, stream, group, consumer))

        results = await redis.xreadgroup(
            group,
            consumer,
            {stream: ">"},
            # Enough to keep every worker slot busy. Reading one batch of ten
            # for a pool of twelve leaves two idle for the whole batch.
            count=max(READ_COUNT, concurrency),
            block=READ_BLOCK_MS,
        )
        if not results:
            continue
        for _name, messages in results:
            await drain(list(messages))
