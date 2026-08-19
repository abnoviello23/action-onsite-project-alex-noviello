"""Postgres connection helpers.

Both entry points install the same jsonb codec. Without it asyncpg hands back
`payload` as a JSON *string*, so every caller ends up sprinkling json.loads at
the point of use and one of them eventually forgets. Registering it on the
connection means `payload` is a dict everywhere, in the same way it is a dict
on the way in.
"""

import json

import asyncpg

from common import config


async def _init(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def connect() -> asyncpg.Connection:
    conn = await asyncpg.connect(config.DSN)
    await _init(conn)
    return conn


async def pool() -> asyncpg.Pool:
    return await asyncpg.create_pool(
        config.DSN,
        min_size=1,
        # Sized for the agent, which is the only fan-out in the system: one
        # request can hold a connection per parallel walker, per gathered
        # orchestrator tool call, and per `find_entities` constraint at once.
        # At five they queued behind each other and the concurrency was
        # notional — the work was parallel, the database access was not.
        max_size=config.DB_POOL_SIZE,
        init=_init,
    )
