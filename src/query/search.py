"""Semantic candidates: HNSW over `node_chunk`, filtered by the kernel.

The recall problem here is the same one `query.paging` solves for type queries,
in a harder place. An ANN index returns its `ef_search` window and stops; drop
the rows the requester cannot see and a low-visibility principal is left with
nothing, from a query with plenty of matching documents they *can* see.

Two mitigations, in order of preference:

  * **Iterative scan** (pgvector 0.8+) tells HNSW to keep pulling until the
    filter is satisfied rather than returning a fixed window. This is the real
    fix, and it is why `hnsw.iterative_scan` is set per transaction below.
  * **Escalating over-fetch** is the fallback when the GUC is unavailable: ask
    for progressively more chunk hits until enough survive. It costs repeated
    index descents, so it is a floor on correctness rather than a plan.

Chunks are scored, nodes are returned. A long document has many passages and
would otherwise occupy the whole result page on the strength of one paragraph
repeated; keeping each node's best chunk collapses that to one row.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from uuid import UUID

import asyncpg

from common import config
from common.vector import to_sql
from core.registry import all_specs
from embed import ChunkerClient
from query.visibility import Visibility

log = logging.getLogger("query.search")

DEFAULT_K = 10
MAX_K = 50
# First over-fetch multiple, then doubling. Generous because a chunk hit and a
# visible node are not the same thing, and one document contributes many chunks.
FIRST_OVERFETCH = 8
MAX_CHUNK_CANDIDATES = 2_000

# Set once per process. pgvector below 0.8 has no `hnsw.iterative_scan`, and
# `SET LOCAL` on an unknown parameter under a loaded extension's prefix is an
# error rather than a no-op — so it is tried once and remembered.
_ITERATIVE_SCAN_OK: bool | None = None

_ANN = """
SELECT c.node_id, c.embedding <=> $1::vector AS distance
FROM node_chunk c
JOIN node n ON n.id = c.node_id
WHERE n.deleted_at IS NULL
  AND n.node_type IS NOT NULL
  AND ($2::text[] IS NULL OR n.node_type = ANY($2))
ORDER BY c.embedding <=> $1::vector
LIMIT $3
"""

_LOAD_NODES = """
SELECT id, entity_id, node_type, body, payload, created_at, updated_at
FROM node
WHERE id = ANY($1::uuid[])
"""


class SearchError(ValueError):
    """The search is not expressible. The message is returned to the model."""


async def _tune(conn: asyncpg.Connection) -> None:
    """Per-transaction HNSW settings. Silently skipped on older pgvector."""
    global _ITERATIVE_SCAN_OK

    await conn.execute(f"SET LOCAL hnsw.ef_search = {int(config.HNSW_EF_SEARCH)}")

    mode = config.HNSW_ITERATIVE_SCAN
    if not mode or _ITERATIVE_SCAN_OK is False:
        return
    try:
        await conn.execute(f"SET LOCAL hnsw.iterative_scan = {mode}")
        _ITERATIVE_SCAN_OK = True
    except asyncpg.PostgresError as exc:
        _ITERATIVE_SCAN_OK = False
        log.warning(
            "hnsw.iterative_scan unavailable (%s); falling back to over-fetch. "
            "pgvector 0.8+ removes the recall cliff on filtered search.",
            exc,
        )


async def _chunk_hits(
    conn: asyncpg.Connection,
    embedding: str,
    node_types: list[str] | None,
    limit: int,
) -> list[tuple]:
    async with conn.transaction():
        await _tune(conn)
        rows = await conn.fetch(_ANN, embedding, node_types, limit)
    return [(r["node_id"], r["distance"]) for r in rows]


async def semantic_search(
    conn: asyncpg.Connection,
    vis: Visibility,
    client: ChunkerClient,
    text: str,
    *,
    k: int = DEFAULT_K,
    node_types: list[str] | None = None,
) -> tuple[list[asyncpg.Record], dict, bool]:
    """Top-k visible nodes for `text`.

    Returns `(rows, distance_by_node_id, truncated)`. `truncated` is true when
    the candidate cap was reached before k visible nodes were found — the caller
    must not present that page as exhaustive.
    """
    if not text.strip():
        raise SearchError("semantic_search needs non-empty text")
    if k < 1:
        raise SearchError("k must be at least 1")
    k = min(k, MAX_K)

    if node_types:
        known = set(all_specs())
        unknown = sorted(set(node_types) - known)
        if unknown:
            raise SearchError(
                f"unknown node_type(s) {unknown}; choose from {sorted(known)}"
            )

    if vis.sees_nothing:
        return [], {}, False

    embedding = to_sql(await client.embed_query(text))

    best: dict = {}
    ordered: list = []
    visible_ids: list = []
    want = min(MAX_CHUNK_CANDIDATES, k * FIRST_OVERFETCH)
    truncated = False

    while True:
        best.clear()
        ordered.clear()
        for node_id, distance in await _chunk_hits(
            conn, embedding, node_types, want
        ):
            # Chunks arrive nearest-first, so the first sighting of a node is
            # already its best passage.
            if node_id not in best:
                best[node_id] = distance
                ordered.append(node_id)

        visible = await vis.visible(conn, ordered)
        visible_ids = [n for n in ordered if n in visible]

        if len(visible_ids) >= k or want >= MAX_CHUNK_CANDIDATES:
            truncated = len(visible_ids) < k and want >= MAX_CHUNK_CANDIDATES
            break
        want = min(MAX_CHUNK_CANDIDATES, want * 2)

    top = visible_ids[:k]
    if not top:
        return [], {}, truncated

    rows = await conn.fetch(_LOAD_NODES, top)
    by_id = {r["id"]: r for r in rows}
    # `_LOAD_NODES` returns rows in whatever order the index hands them back;
    # the ranking is the ANN's, so it is reimposed here.
    return [by_id[n] for n in top if n in by_id], best, truncated


# --------------------------------------------------------------- local search --

# Reciprocal rank fusion. Combines rankings whose scores are not commensurable —
# `ts_rank` and cosine distance live on unrelated scales, and normalising one
# against the other means choosing a constant that is wrong for some corpus.
# Rank position is comparable by construction. 60 is the conventional damping
# term: large enough that the top few positions do not dominate outright.
RRF_K = 60


def rrf(*rankings: list) -> list:
    """Fuse ordered key lists into one. Keys may appear in any subset."""
    scores: dict = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank + 1)
    return sorted(scores, key=lambda k: scores[k], reverse=True)


# Lexical leg, scoped to a candidate set.
#
# ORed rather than ANDed for the same reason `query.entities` ORs: the caller is
# describing what it is looking for, not reciting terms the author agreed to
# use, so requiring every lexeme is a precision setting on a recall problem.
_WITHIN_FTS = """
SELECT n.id
FROM node n,
     (SELECT replace(plainto_tsquery('english', $2)::text, '&', '|')::tsquery) AS t(q)
WHERE n.id = ANY($1::uuid[])
  AND n.deleted_at IS NULL
  AND n.fts @@ q
ORDER BY ts_rank(n.fts, q) DESC, n.id DESC
"""

# Vector leg, scoped to a candidate set — and deliberately **exact**.
#
# No HNSW, no `ef_search`, no over-fetch loop, and none of the recall cliff the
# global path spends `_tune` and `MAX_CHUNK_CANDIDATES` defending against. A
# neighbourhood is tens of nodes, so this reads a few hundred chunk rows through
# the `node_chunk` primary key and computes distance directly. Perfect recall,
# no tuning constant, and faster than an index probe at this size.
#
# Which inverts the usual advice on purpose: globally the index is what makes
# search possible; locally the filter is so selective that the index would be a
# liability, and its absence is the feature.
#
# `GROUP BY` is safe here for the same reason — the global path avoids it
# because aggregation would defeat the index's ordering, and there is no index
# ordering to defeat.
_WITHIN_ANN = """
SELECT c.node_id AS id, min(c.embedding <=> $2::vector) AS distance
FROM node_chunk c
JOIN node n ON n.id = c.node_id
WHERE c.node_id = ANY($1::uuid[])
  AND n.deleted_at IS NULL
GROUP BY c.node_id
ORDER BY distance
"""


async def search_within(
    conn: asyncpg.Connection,
    vis: Visibility,
    client: ChunkerClient | None,
    text: str,
    candidate_ids: Sequence[UUID],
) -> list[UUID]:
    """Rank a bounded candidate set by relevance to `text`. Most relevant first.

    Returns only ids that both match and clear the kernel; a candidate matching
    nothing is dropped rather than ranked last, so the caller can tell "these
    are about your question" from "here is everything, reordered".

    The kernel runs here even when the caller has already applied it — which is
    the case for `neighbors`. The redundancy is deliberate and cheap at this set
    size, and it keeps the invariant free of exceptions: every retrieval path in
    this package filters, so a future caller cannot introduce a leak by
    forgetting that this one trusted its input.
    """
    if not text.strip() or not candidate_ids:
        return []

    ids = list(candidate_ids)
    visible = await vis.visible(conn, ids)
    if not visible:
        return []
    ids = [i for i in ids if i in visible]

    lexical = [r["id"] for r in await conn.fetch(_WITHIN_FTS, ids, text)]

    vector: list[UUID] = []
    if client is not None:
        try:
            embedding = to_sql(await client.embed_query(text))
            vector = [r["id"] for r in await conn.fetch(_WITHIN_ANN, ids, embedding)]
        except Exception:
            # A chunker outage degrades this to lexical-only rather than failing
            # the traversal. Half a ranking still orders most neighbourhoods
            # usefully, and the alternative is that one flaky dependency takes
            # local search out entirely.
            log.warning("vector leg unavailable for local search; lexical only")

    return rrf(lexical, vector)
