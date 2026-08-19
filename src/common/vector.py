"""pgvector literals, without the pgvector Python package.

asyncpg has no codec for the `vector` type, so an embedding has to cross as
something Postgres can cast. pgvector's text input format is `[0.1,0.2,…]`, so
that is what we send, always through a bound parameter with an explicit
`::vector` cast at the call site.

Registering a real codec would mean adding `pgvector` to requirements for one
type conversion, in a repo that already declines vendor SDKs for a handful of
endpoints. This is that same trade.
"""

from __future__ import annotations

from collections.abc import Sequence

from embed.models import EMBED_DIM


def to_sql(embedding: Sequence[float]) -> str:
    """`[0.1,0.2,…]` for a `$n::vector` parameter.

    Width is checked here rather than left to Postgres: the column is
    `vector(384)` and a mismatch surfaces as a cast error on insert, far from
    the model that produced the wrong width.
    """
    if len(embedding) != EMBED_DIM:
        raise ValueError(
            f"embedding has {len(embedding)} dims, expected {EMBED_DIM}"
        )
    return "[" + ",".join(repr(float(v)) for v in embedding) + "]"
