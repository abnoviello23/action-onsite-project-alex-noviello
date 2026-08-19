"""Redis connection helper. Streams, cache, and rate-limit buckets share it."""

import redis.asyncio as aioredis

from common import config


def client() -> aioredis.Redis:
    return aioredis.from_url(config.REDIS_URL, decode_responses=True)
