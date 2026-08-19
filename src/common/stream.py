"""Work stream publishing and poll watermarks, both on Redis."""

from __future__ import annotations

import hashlib

from redis.asyncio import Redis

from common import config
from core.message import Envelope

# Cap on stream length. Redis Streams are memory-resident, so this is a dispatch
# buffer. The sources remain the truth and the pollers re-emit, so losing the
# tail on a hard crash costs a poll cycle rather than data.
STREAM_MAXLEN = 100_000


def partition_for(partition_key: str, num_partitions: int) -> int:
    """Stable across processes.

    Python's builtin hash() is salted per interpreter, so a poller and a worker
    would disagree about which partition a key belongs to and per-entity
    (or per-channel) ordering would silently break.
    """
    digest = hashlib.blake2b(partition_key.encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % num_partitions


class WorkStream:
    """Publishes envelopes, partitioned by `partition_key`."""

    def __init__(self, redis: Redis, num_partitions: int | None = None) -> None:
        self._redis = redis
        self._partitions = num_partitions or config.NUM_PARTITIONS

    async def publish(self, envelope: Envelope) -> str:
        partition = partition_for(envelope.partition_key, self._partitions)
        return await self._redis.xadd(
            config.work_stream(partition),
            {"envelope": envelope.model_dump_json()},
            maxlen=STREAM_MAXLEN,
            approximate=True,
        )


class Watermarks:
    """Last-seen cursor per source key (for Slack, per channel).

    Advanced only after a successful publish: a crash in between re-emits
    events rather than losing them, and the version guard absorbs the
    duplicates.
    """

    def __init__(self, redis: Redis, source: str) -> None:
        self._redis = redis
        self._key = f"watermark:{source}"

    async def get(self, key: str) -> str | None:
        return await self._redis.hget(self._key, key)

    async def set(self, key: str, value: str) -> None:
        await self._redis.hset(self._key, key, value)
