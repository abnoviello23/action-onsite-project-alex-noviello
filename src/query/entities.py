"""Find entities by several constraints at once, each satisfied by its own fact.

The question this exists for is *"which people work on Atlas and were let go
recently"*. One similarity score cannot answer it. The two halves are recorded
in **different facts about the same person** — one from a project channel, one
from an HR thread — so a single search over the whole sentence matches neither
half well and ranks a person who merely talks about layoffs above the person the
question is about.

So each constraint is retrieved independently and the results are intersected on
the subject entity. Per constraint: lexical and vector retrieval over facts, both
through the visibility kernel, fused; then `fact.subject` gives the entity.

**Ranked by constraints satisfied, not filtered to all of them.** A hard AND
returns nothing the moment the extractor phrased something unexpectedly — and
extraction phrasing is the least predictable part of this system. Returning
`jane 2/2, bob 1/2` lets the caller see the near-misses and decide, and degrades
into a useful answer instead of an empty one.

**Why this is the permission demo.** Each constraint's facts are filtered
independently, so a principal who cannot read the HR thread does not merely lose
the firing fact — they lose the *conjunction*, and Jane drops from 2/2 to 1/2.
Two people ask the same question and get "Jane Doe" and "no one", from the same
code path, with neither able to detect the other's answer.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from core.labels import PREVIEW_MAX_CHARS, clip, label_of, native_keys
from core.registry import SEMANTIC_TYPES
from embed import ChunkerClient
from query import search
from query.paging import fill_visible
from query.visibility import Visibility

log = logging.getLogger("query.entities")

FACT_TYPE = "fact"

MAX_CONSTRAINTS = 6
DEFAULT_ENTITY_LIMIT = 10
MAX_ENTITY_LIMIT = 25
# Facts pulled per constraint per retrieval leg. Generous relative to the entity
# limit because many facts collapse onto few entities, and because the fusion
# below only benefits from seeing more of each ranking.
FACTS_PER_LEG = 40

# Lexical leg.
#
# The query is ORed, not ANDed, and that is the difference between this
# returning something and returning nothing. `plainto_tsquery` conjoins every
# term — "works on the Atlas project" compiles to `work & atlas & project`, so a
# fact reading "leading the Atlas migration project" fails on the single missing
# word "work". Constraints here are natural-language descriptions of a state of
# affairs, not search terms the writer agreed to use, and demanding every lexeme
# is a precision setting for a recall problem.
#
# Swapping the operators on the already-parsed query is deliberate: it inherits
# `plainto_tsquery`'s sanitisation rather than re-implementing it against
# caller-supplied text, so nothing here has to reason about quoting.
#
# `ts_rank` then does the ordering it was built for — a fact matching all three
# lexemes outranks one matching a single common word — and the fusion below
# reorders again against the vector leg.
#
# `subject IS NOT NULL` because a fact with no subject cannot be attributed to
# an entity and would only waste a slot in the fused ranking.
_FACT_FTS = """
SELECT f.id, f.entity_id, f.node_type, f.body, f.payload,
       f.created_at, f.updated_at
FROM fact f,
     (SELECT replace(plainto_tsquery('english', $1)::text, '&', '|')::tsquery) AS t(q)
WHERE f.deleted_at IS NULL
  AND f.subject IS NOT NULL
  AND f.fts @@ q
ORDER BY ts_rank(f.fts, q) DESC, f.id DESC
"""

_LOAD_ENTITIES = """
SELECT id, entity_id, node_type, body, payload
FROM node
WHERE entity_id = ANY($1::text[])
  AND deleted_at IS NULL
  AND node_type = ANY($2::text[])
"""


class Evidence(BaseModel):
    """The fact that satisfied one constraint, and where it was read from."""

    model_config = ConfigDict(frozen=True)

    constraint: str
    fact_entity_id: str
    statement: str
    source_entity_id: str | None = None


class EntityMatch(BaseModel):
    model_config = ConfigDict(frozen=True)

    entity_id: str
    node_type: str | None
    label: str
    # How many of the requested constraints this entity satisfied, and which.
    # Both are reported: the count is the ranking key, the list is the reason.
    matched: int = 0
    total: int = 0
    evidence: list[Evidence] = Field(default_factory=list)
    native: dict[str, str] = Field(default_factory=dict)

    @property
    def complete(self) -> bool:
        return self.total > 0 and self.matched == self.total


class EntityMatchResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    constraints: list[str] = Field(default_factory=list)
    matches: list[EntityMatch] = Field(default_factory=list)
    # A constraint that matched no visible fact at all. Named rather than
    # silently contributing zero, because "nobody satisfies this" and "you
    # cannot see anything that would" lead to different follow-up questions.
    unmatched_constraints: list[str] = Field(default_factory=list)
    truncated: bool = False


class EntitySearchError(ValueError):
    """The search is not expressible. The message is returned to the model."""


@dataclass
class _Candidate:
    """One entity's accumulating case, across constraints."""

    entity_id: str
    score: float = 0.0
    # constraint index -> the best fact seen for it
    hits: dict[int, tuple[str, str, str | None]] = field(default_factory=dict)


async def find_entities(
    pool: asyncpg.Pool,
    vis: Visibility,
    client: ChunkerClient | None,
    *,
    node_type: str,
    constraints: list[str],
    limit: int = DEFAULT_ENTITY_LIMIT,
) -> EntityMatchResult:
    """Entities of `node_type` ranked by how many constraints they satisfy."""
    if node_type not in SEMANTIC_TYPES:
        raise EntitySearchError(
            f"{node_type!r} is not an entity type; choose one of "
            f"{sorted(SEMANTIC_TYPES)}"
        )
    cleaned = [c.strip() for c in constraints if c and c.strip()]
    if not cleaned:
        raise EntitySearchError("find_entities needs at least one constraint")
    if len(cleaned) > MAX_CONSTRAINTS:
        raise EntitySearchError(f"at most {MAX_CONSTRAINTS} constraints per call")
    limit = max(1, min(limit, MAX_ENTITY_LIMIT))

    if vis.sees_nothing:
        return EntityMatchResult(constraints=cleaned, unmatched_constraints=cleaned)

    candidates: dict[str, _Candidate] = {}
    unmatched: list[str] = []
    truncated = False

    # Constraints are retrieved concurrently. They are independent by
    # construction — each is its own search, and the intersection happens after
    # all of them return — and each carries an HTTP round trip to the embedder
    # before its vector leg can run, so in sequence a six-constraint question
    # paid six of those end to end before ranking anything.
    #
    # A pool rather than a connection is what makes that possible: asyncpg
    # connections are not safe for concurrent use, so sharing one across
    # gathered constraints raises "another operation is in progress" instead of
    # running them at once.
    per_constraint = await asyncio.gather(
        *(_facts_for(pool, vis, client, c) for c in cleaned)
    )

    for index, (constraint, (facts, hit_cap)) in enumerate(
        zip(cleaned, per_constraint)
    ):
        truncated = truncated or hit_cap
        if not facts:
            unmatched.append(constraint)
            continue

        for rank, row in enumerate(facts):
            payload = row["payload"] or {}
            subject = payload.get("subject")
            if not subject:
                continue
            cand = candidates.setdefault(subject, _Candidate(entity_id=subject))
            cand.score += 1.0 / (search.RRF_K + rank + 1)
            # First sighting wins: `facts` is already in fused rank order, so
            # the best evidence for this constraint is the one we keep.
            if index not in cand.hits:
                cand.hits[index] = (
                    row["entity_id"],
                    clip(" ".join((row["body"] or "").split()), PREVIEW_MAX_CHARS),
                    payload.get("source"),
                )

    if not candidates:
        return EntityMatchResult(
            constraints=cleaned, unmatched_constraints=unmatched, truncated=truncated
        )

    # Entities are visible exactly when a fact about them is, and every fact
    # above already cleared the kernel — so the subject is visible by
    # construction and needs no second check. This read is for the label and to
    # drop subjects of the wrong type.
    async with pool.acquire() as conn:
        rows = await conn.fetch(_LOAD_ENTITIES, list(candidates), [node_type])
    by_id = {r["entity_id"]: r for r in rows}

    matches: list[EntityMatch] = []
    for entity_id, cand in candidates.items():
        row = by_id.get(entity_id)
        if row is None:
            continue
        payload = row["payload"] or {}
        body = row["body"] or ""
        matches.append(
            EntityMatch(
                entity_id=entity_id,
                node_type=row["node_type"],
                label=label_of(payload, body, entity_id),
                matched=len(cand.hits),
                total=len(cleaned),
                evidence=[
                    Evidence(
                        constraint=cleaned[i],
                        fact_entity_id=fact_id,
                        statement=statement,
                        source_entity_id=source,
                    )
                    for i, (fact_id, statement, source) in sorted(cand.hits.items())
                ],
                native=native_keys(row["node_type"], payload),
            )
        )

    # Constraints satisfied first, fused relevance as the tiebreak. An entity
    # that answers the whole question outranks a strong partial match, which is
    # the ordering the question asked for.
    matches.sort(key=lambda m: (m.matched, candidates[m.entity_id].score), reverse=True)
    return EntityMatchResult(
        constraints=cleaned,
        matches=matches[:limit],
        unmatched_constraints=unmatched,
        truncated=truncated,
    )


async def _facts_for(
    pool: asyncpg.Pool,
    vis: Visibility,
    client: ChunkerClient | None,
    constraint: str,
) -> tuple[list[asyncpg.Record], bool]:
    """Visible facts matching one constraint, lexical and vector legs fused.

    Takes its own connection from the pool so several of these can run at once.
    """
    async with pool.acquire() as conn:
        page = await fill_visible(conn, vis, _FACT_FTS, [constraint], FACTS_PER_LEG)
        lexical = list(page.rows)
        truncated = page.truncated

        vector: list[asyncpg.Record] = []
        if client is not None:
            try:
                vector, _, vec_truncated = await search.semantic_search(
                    conn, vis, client, constraint,
                    k=FACTS_PER_LEG, node_types=[FACT_TYPE],
                )
                truncated = truncated or vec_truncated
            except Exception:
                # A chunker outage degrades this to lexical-only rather than
                # failing the request. Half a ranking still answers many
                # constraints, and the alternative is that one flaky dependency
                # takes out entity search entirely.
                log.warning("vector leg unavailable for %r; lexical only", constraint)

    return _fuse(lexical, vector), truncated


def _fuse(*rankings: list[asyncpg.Record]) -> list[asyncpg.Record]:
    """Reciprocal rank fusion over rankings of the same rows.

    Shares `search.rrf` rather than reimplementing it — two fusion functions
    would eventually disagree about the damping constant, and the one that
    drifted would be the one nobody was looking at.
    """
    rows: dict[str, asyncpg.Record] = {}
    keyed: list[list[str]] = []
    for ranking in rankings:
        keys = []
        for row in ranking:
            rows[row["entity_id"]] = row
            keys.append(row["entity_id"])
        keyed.append(keys)
    return [rows[k] for k in search.rrf(*keyed)]
