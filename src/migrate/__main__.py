"""Schema lifecycle: migrate (default), --reset.

  migrate   idempotent DDL; safe on every boot, everything depends on it
  --reset   DESTRUCTIVE: drop schema + FLUSHDB, then migrate

There is no replay. The sources are the source of truth and the pollers
re-emit — a reset plus one poll cycle rebuilds the graph, so nothing here needs
to keep a log of its own.
"""

import argparse
import asyncio

from common import db, redis_client
from common.logging import setup
from migrate import runner

log = setup("migrate")


async def migrate() -> None:
    conn = await db.connect()
    try:
        await runner.migrate(conn)
    finally:
        await conn.close()


async def reset() -> None:
    """Drops the schema and flushes Redis. Profile-gated in Compose so it can
    never run as part of a plain `compose up`."""
    log.warning("reset: DESTRUCTIVE — dropping schema and flushing Redis")

    conn = await db.connect()
    try:
        await runner.drop_schema(conn)
    finally:
        await conn.close()

    redis = redis_client.client()
    try:
        # Work streams, consumer groups, watermarks and rate-limit buckets all
        # live here. Keeping them across a schema drop would leave pollers
        # resuming from a cursor whose data no longer exists, so the graph would
        # come back missing everything before that cursor.
        await redis.flushdb()
        log.warning("flushed redis")
    finally:
        await redis.aclose()


async def main() -> None:
    parser = argparse.ArgumentParser(prog="migrate")
    parser.add_argument("--reset", action="store_true")
    args = parser.parse_args()

    if args.reset:
        await reset()
    await migrate()

    log.info("done")


if __name__ == "__main__":
    asyncio.run(main())
