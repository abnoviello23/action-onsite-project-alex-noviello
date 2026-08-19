"""The only graph surface the agent touches. Every read goes through the kernel.

Bound to one request's `Visibility`. There is no unfiltered method here and no
way to reach one: `Store` stays write-only, ingest generators keep their
unpermissioned `GraphView`, and `/graph` remains the operator canvas.

Two rules run through all of it.

**Invisible means absent, not forbidden.** A node the principal cannot see
returns `None` from `get` and never appears in a neighbor list. It is never a
403, because a 403 is an answer: it confirms the entity exists. The private
channel in the demo has to be indistinguishable from a channel that was never
created.

**Visibility is checked on the peer.** Traversal is bidirectional — `mentions`
inward is the backlink set, `next` inward is the previous message, `in` inward
is the child list — so for an inbound edge the node under test is the `from`
side, not the `to` side. Checking "the target" would, on an inbound row, test
the node the caller is already standing on: a tautology, and a leak of every
source that points at it. `_peer_visible` is the single place that decides.

**And on the provenance, when an edge has any.** An inferred edge between two
entities is the one case the peer rule cannot cover: both ends are entities, and
an entity is visible as soon as *any* document mentions it — so a relation drawn
from a private message would show to anyone who could see both endpoints from
public ones. `edge.source_node_id` names the document that asserted it, and an
edge carrying one is admitted only when that document is visible too. Structural
edges have no source and are unaffected.

Traversal starts are checked too. `neighbors('slack:message:…')` on a node the
principal cannot see returns nothing rather than enumerating its edges, or an
invisible node would be a usable index into the graph around it.
"""

from __future__ import annotations

import logging
from typing import Literal
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from common import config
from embed import ChunkerClient
from query import search
from query.compile import Compiled, TypeQuery, compile_query
from query.entities import (
    DEFAULT_ENTITY_LIMIT,
    EntityMatchResult,
    find_entities,
)
from query.models import (
    Neighbor,
    NeighborPage,
    NodeDetail,
    NodeSummary,
    to_detail,
    to_neighbor,
    to_summary,
)
from query.paging import fill_visible
from query.visibility import Visibility

log = logging.getLogger("query.session")

Traversal = Literal["out", "in", "both"]

# Raised well above the default because the common narrowed call — "every fact
# about this person" — is exactly the one that must not come back clipped. The
# default stays small so an unnarrowed sweep does not dump a channel into the
# walker's context; `complete: false` is the signal to ask for more.
MAX_NEIGHBORS = 200
DEFAULT_NEIGHBORS = 25
# Edge rows read before giving up on filling a neighbor page. Generous: a busy
# channel node has thousands of inbound `in_channel` rows and the visible ones
# are not necessarily first.
MAX_EDGE_SCAN = 1_000

_RESOLVE = """
SELECT id, entity_id, node_type, body, payload, created_at, updated_at
FROM node
WHERE entity_id = $1 AND deleted_at IS NULL AND node_type IS NOT NULL
"""

# Both directions in one read. The peer is projected as `n` in each arm — the
# `to` side going out, the `from` side coming in — so the visibility check
# downstream is uniform and cannot accidentally test the origin.
#
# `$3` and `$4` gate the arms. Passing booleans rather than assembling the SQL
# keeps one statement in the plan cache for all three traversal modes.
#
# `$6` narrows by the peer's own type. Relation and type are different questions
# and neither substitutes for the other: the relation vocabulary between entities
# is declared by the published ontology and changes under the agent's feet, so
# "the person neighbours of this project" is stable where "the `works_on`
# neighbours" is only stable until someone publishes a revision.
_NEIGHBORS = """
SELECT n.id, n.entity_id, n.node_type, n.body, n.payload,
       e.relation, 'out' AS direction, n.updated_at, e.source_node_id
FROM edge e
JOIN node n ON n.id = e.to_node_id
WHERE $3 AND e.from_node_id = $1
  AND ($2::text[] IS NULL OR e.relation = ANY($2))
  AND ($6::text[] IS NULL OR n.node_type = ANY($6))
UNION ALL
SELECT n.id, n.entity_id, n.node_type, n.body, n.payload,
       e.relation, 'in' AS direction, n.updated_at, e.source_node_id
FROM edge e
JOIN node n ON n.id = e.from_node_id
WHERE $4 AND e.to_node_id = $1
  AND ($2::text[] IS NULL OR e.relation = ANY($2))
  AND ($6::text[] IS NULL OR n.node_type = ANY($6))
ORDER BY relation, direction, updated_at DESC NULLS LAST
LIMIT $5
"""


class QueryResult(BaseModel):
    """A candidate page, and an honest statement about its completeness."""

    model_config = ConfigDict(frozen=True)

    results: list[NodeSummary] = Field(default_factory=list)
    # The scan cap stopped the fill before the page was full. Surfaced to the
    # model so a capped page is never reported as "all there is" — the counts
    # here are post-visibility, so this leaks nothing about hidden rows.
    truncated: bool = False


class NodeBudgetExceeded(RuntimeError):
    """The request opened more nodes in full than it is allowed to."""


class SessionGraph:
    """Permissioned reads for one request. Not safe to share across requests."""

    def __init__(
        self,
        pool: asyncpg.Pool,
        vis: Visibility,
        client: ChunkerClient,
        *,
        max_nodes: int = config.AGENT_MAX_NODES,
    ) -> None:
        self._pool = pool
        self._vis = vis
        self._client = client
        self._max_nodes = max_nodes
        # Full opens, deduplicated. Re-reading a node the walker already has is
        # free against the budget; the budget bounds breadth, not repetition.
        self._opened: set[str] = set()

    @property
    def identity_id(self) -> str:
        return self._vis.identity_id

    @property
    def nodes_opened(self) -> int:
        return len(self._opened)

    # ------------------------------------------------------ candidate sets --

    async def query_type(self, query: TypeQuery) -> QueryResult:
        """Filtered view query. `limit` counts visible rows, not scanned ones."""
        compiled: Compiled = compile_query(query)
        async with self._pool.acquire() as conn:
            page = await fill_visible(
                conn, self._vis, compiled.sql, compiled.params, compiled.limit
            )
        return QueryResult(
            results=[to_summary(r) for r in page.rows],
            truncated=page.truncated,
        )

    async def find_entities(
        self,
        node_type: str,
        constraints: list[str],
        *,
        limit: int = DEFAULT_ENTITY_LIMIT,
    ) -> EntityMatchResult:
        """Entities ranked by how many constraints they satisfy.

        Each constraint is retrieved separately over facts and intersected on
        the subject, so a question whose halves live in different facts about
        one person is answerable — and answerable differently depending on which
        of those facts the principal may read.
        """
        return await find_entities(
            self._pool,
            self._vis,
            self._client,
            node_type=node_type,
            constraints=constraints,
            limit=limit,
        )

    async def semantic_search(
        self,
        text: str,
        *,
        k: int = search.DEFAULT_K,
        node_types: list[str] | None = None,
    ) -> QueryResult:
        async with self._pool.acquire() as conn:
            rows, distances, truncated = await search.semantic_search(
                conn, self._vis, self._client, text, k=k, node_types=node_types
            )
        return QueryResult(
            results=[to_summary(r, distance=distances.get(r["id"])) for r in rows],
            truncated=truncated,
        )

    # ------------------------------------------------------- local traversal --

    async def get(self, entity_id: str) -> NodeDetail | None:
        """Full node, or None if it does not exist *or* is not visible.

        The two collapse deliberately. Distinguishing them would answer "does
        this id exist" for anyone willing to ask.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(_RESOLVE, entity_id)
            if row is None:
                return None
            if not await self._vis.is_visible(conn, row["id"]):
                return None

        if entity_id not in self._opened:
            if len(self._opened) >= self._max_nodes:
                raise NodeBudgetExceeded(
                    f"request opened {self._max_nodes} nodes; stop exploring and "
                    f"answer from what you have"
                )
            self._opened.add(entity_id)
        return to_detail(row)

    async def neighbors(
        self,
        entity_id: str,
        *,
        relations: list[str] | None = None,
        node_types: list[str] | None = None,
        direction: Traversal = "both",
        query: str | None = None,
        limit: int = DEFAULT_NEIGHBORS,
    ) -> NeighborPage:
        """Incident edges whose **peer** is visible, each labelled with the
        direction it was traversed in, and whether that is all of them.

        Returns empty when the origin itself is not visible — an invisible node
        must not work as a handle on the graph around it. That case reports
        `complete=True`: there is genuinely nothing more to show this principal,
        and claiming truncation would hint that there was.

        `query` changes what `limit` means. Without it the page is the *most
        recently updated* peers, which is a silent bias — a container's answer
        is often not its newest child, and a walker that asked for 25 of 40 has
        no way to know it got the wrong 25. With it, peers are ranked by
        relevance and the page is the ones that bear on the question. Peers
        matching nothing are dropped rather than ranked last, so an empty result
        means "nothing here is about that" instead of "here is everything,
        reordered".

        Ranking runs strictly after the kernel, on peers already established as
        visible, so relevance can never promote something access did not admit.
        """
        limit = max(1, min(limit, MAX_NEIGHBORS))
        async with self._pool.acquire() as conn:
            origin = await conn.fetchrow(_RESOLVE, entity_id)
            if origin is None or not await self._vis.is_visible(conn, origin["id"]):
                return NeighborPage()

            rows = await conn.fetch(
                _NEIGHBORS,
                origin["id"],
                relations,
                direction in ("out", "both"),
                direction in ("in", "both"),
                MAX_EDGE_SCAN,
                node_types,
            )
            if not rows:
                return NeighborPage()
            # Peers and provenance resolved in one pass: both have to clear the
            # kernel, and asking twice would double the walk.
            peers = {r["id"] for r in rows}
            sources = {r["source_node_id"] for r in rows if r["source_node_id"]}
            visible: set[UUID] = await self._vis.visible(
                conn, list(peers | sources)
            )

        # One edge per (peer, relation, direction). A duplicate cannot arise
        # from the edge PK, but the union can surface the same peer twice when
        # a pair is linked in both directions, and those are genuinely two
        # different facts.
        out = [
            to_neighbor(r)
            for r in rows
            if r["id"] in visible
            and (r["source_node_id"] is None or r["source_node_id"] in visible)
        ]
        # Two distinct ways to be incomplete, and both have to be reported: the
        # edge scan stopped early, or more peers survived the kernel than the
        # caller asked for.
        scanned_all = len(rows) < MAX_EDGE_SCAN
        if not scanned_all:
            log.info("edge scan cap on %s for %s", entity_id, self._vis.identity_id)

        # Ranked pages count completeness the same way, over a different set:
        # `out` becomes the peers that matched, and peers matching nothing were
        # never candidates — so their absence is not truncation.
        if query:
            out = await self._rank(out, query)

        return NeighborPage(
            neighbors=out[:limit],
            complete=scanned_all and len(out) <= limit,
        )

    async def _rank(self, rows: list[Neighbor], query: str) -> list[Neighbor]:
        """Reorder visible neighbours by relevance, dropping non-matches.

        One peer can appear on several rows — reachable by two relations, or in
        both directions — and those are genuinely different facts about the
        graph. They are ranked together by their peer and kept together in the
        output, so a relation is never split across the page boundary.
        """
        by_entity: dict[str, list[Neighbor]] = {}
        for row in rows:
            by_entity.setdefault(row.entity_id, []).append(row)

        async with self._pool.acquire() as conn:
            ids = await conn.fetch(
                "SELECT id, entity_id FROM node WHERE entity_id = ANY($1::text[])",
                list(by_entity),
            )
            entity_of = {r["id"]: r["entity_id"] for r in ids}
            ranked = await search.search_within(
                conn, self._vis, self._client, query, list(entity_of)
            )

        out: list[Neighbor] = []
        for node_id in ranked:
            out.extend(by_entity.get(entity_of.get(node_id, ""), ()))
        return out

    async def follow(
        self,
        entity_id: str,
        relation: str,
        *,
        direction: Traversal = "both",
        limit: int = DEFAULT_NEIGHBORS,
    ) -> NeighborPage:
        """`neighbors` narrowed to one relation.

        `direction` is not optional decoration: `follow(msg, "in", "out")` is
        the containing channel and `follow(chan, "in", "in")` is its messages.
        Same relation, opposite questions.
        """
        return await self.neighbors(
            entity_id,
            relations=[relation],
            direction=direction,
            limit=limit,
        )
