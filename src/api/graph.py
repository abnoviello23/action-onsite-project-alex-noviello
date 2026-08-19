"""Read-only graph topology endpoints, backing the canvas visualizer.

Two different things are drawn as edges here, and keeping them distinct is the
whole point of the view:

  * `permission` edges are `node.permission_parent_id` — the ACL tree access
    inherits along. Synthesized here because that relationship is a column.
  * `relation` edges are `edge` rows — `in` (folder/page containment),
    `in_channel`, a Notion mention. They confer no access.

An `in` row that duplicates the permission parent is not drawn a second time;
the column already produced that line as kind=permission. The row still exists
so neighbors and `outgoing` can walk containment without reading the column.

Every query in this module is unfiltered by principal: this is an operator's
view of what was ingested, not the permissioned retrieval path an agent uses.
That path resolves visibility per session and belongs in its own module; if this
one is ever exposed beyond localhost it needs the same treatment first.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

import asyncpg
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.graph import IN
from core.labels import (
    PREVIEW_MAX_CHARS,
    UNMATERIALIZED,
    clip,
    label_of,
    source_of,
)

router = APIRouter(prefix="/graph", tags=["graph"])

# A canvas stops being readable long before a force layout stops being able to
# settle, so the ceiling is a rendering limit rather than a database one.
DEFAULT_LIMIT = 600
MAX_LIMIT = 5000

# `limit=0` means every matching node. Spelled as a sentinel rather than as an
# absent parameter so that "all of it" is something a caller states outright,
# and so the default stays bounded for anything that is not the canvas.
NO_LIMIT = 0

# Guards the ancestor walk against a permission cycle. Real trees are shallow
# (drive -> folder -> file is 3), so this only ever fires on corrupt data.
MAX_PARENT_DEPTH = 32

# ------------------------------------------------------------------ models --


class GraphNode(BaseModel):
    id: str
    entity_id: str
    # None on an unmaterialized row: one minted so a uuid could be referenced
    # before the event that defines it arrived.
    node_type: str | None
    # split_part of node_type. Sent precomputed because the client colours by it
    # on every frame and should not be re-parsing strings in the render loop.
    source: str
    label: str
    parent_id: str | None = None
    body_preview: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    deleted: bool = False
    materialized: bool = True
    # Access rows on this node itself. Not effective access — that is inherited
    # along the parent chain, and this view does not claim to resolve it.
    grant_count: int = 0
    child_count: int = 0


class GraphEdge(BaseModel):
    from_id: str
    to_id: str
    relation: str
    kind: Literal["permission", "relation"]


class GraphStats(BaseModel):
    node_count: int
    edge_count: int
    # Nodes matching the filter before the limit was applied. When this exceeds
    # what was drawn the client says so, rather than implying it drew the lot.
    matched: int
    truncated: bool
    # Ancestors pulled in beyond the matched set to keep the tree connected.
    ancestors_added: int
    by_source: dict[str, int] = Field(default_factory=dict)
    by_node_type: dict[str, int] = Field(default_factory=dict)


class GraphResponse(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    stats: GraphStats


class MetaCount(BaseModel):
    key: str
    count: int


class GraphMeta(BaseModel):
    total_nodes: int
    total_edges: int
    total_identities: int
    total_grants: int
    sources: list[MetaCount]
    node_types: list[MetaCount]
    relations: list[MetaCount]


class Grant(BaseModel):
    identity_id: str
    level: str
    display_name: str | None = None
    email: str | None = None


class Neighbor(BaseModel):
    entity_id: str
    label: str
    node_type: str | None
    relation: str
    direction: Literal["out", "in"]


class NodeDetail(BaseModel):
    node: GraphNode
    parent_entity_id: str | None = None
    grants: list[Grant]
    neighbors: list[Neighbor]
    payload: dict[str, Any]
    body: str


# ------------------------------------------------------------- projections --


def _to_node(
    row: asyncpg.Record,
    grant_counts: dict[UUID, int],
    child_counts: dict[UUID, int],
) -> GraphNode:
    payload = row["payload"] or {}
    body = row["body"] or ""
    parent = row["permission_parent_id"]
    return GraphNode(
        id=str(row["id"]),
        entity_id=row["entity_id"],
        node_type=row["node_type"],
        source=source_of(row["node_type"]),
        label=label_of(payload, body, row["entity_id"]),
        parent_id=str(parent) if parent else None,
        body_preview=clip(body, PREVIEW_MAX_CHARS),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        deleted=row["deleted_at"] is not None,
        materialized=row["node_type"] is not None,
        grant_count=grant_counts.get(row["id"], 0),
        child_count=child_counts.get(row["id"], 0),
    )


# ---------------------------------------------------------------- queries --

# The SQL form of `core.labels.source_of`, which the client colours and filters
# by. Mirrored types are `{source}:{thing}`; a declared semantic type is a bare
# identifier, because `safe_ident` rejects a colon. Kept as one constant because
# a facet list and the filter it drives that disagree is a filter that silently
# matches nothing.
_SOURCE_SQL = (
    "CASE WHEN node_type IS NULL THEN 'unknown' "
    "     WHEN node_type LIKE '%:%' THEN split_part(node_type, ':', 1) "
    "     ELSE 'semantic' END"
)

# Selected nodes, then the transitive closure of their permission parents.
#
# The ancestor walk is not optional. Filtering to "the 600 most recently updated
# nodes" reliably excludes the drives and channels everything hangs off, and the
# result draws as a field of disconnected dots — technically the right rows,
# useless as topology. Climbing to the roots costs a handful of extra rows and
# is what makes the picture a tree.
_SELECT_NODES = f"""
WITH RECURSIVE seed AS (
    SELECT id
    FROM node
    WHERE ($1::text[] IS NULL OR {_SOURCE_SQL} = ANY($1))
      AND ($2::text[] IS NULL OR node_type = ANY($2))
      AND ($3::bool OR deleted_at IS NULL)
      AND ($4::text IS NULL
           OR body ILIKE '%' || $4 || '%'
           OR entity_id ILIKE '%' || $4 || '%'
           OR payload::text ILIKE '%' || $4 || '%')
    ORDER BY updated_at DESC NULLS LAST
    -- NULL is Postgres for "no limit", which is what NO_LIMIT binds to.
    LIMIT $5::bigint
),
closure AS (
    SELECT n.id, n.permission_parent_id, 0 AS depth, true AS seeded
    FROM node n
    JOIN seed ON seed.id = n.id
  UNION
    SELECT p.id, p.permission_parent_id, c.depth + 1, false
    FROM node p
    JOIN closure c ON p.id = c.permission_parent_id
    WHERE c.depth < $6
)
SELECT n.id, n.entity_id, n.node_type, n.permission_parent_id, n.body,
       n.created_at, n.updated_at, n.deleted_at, n.payload,
       bool_or(c.seeded) AS seeded
FROM node n
JOIN closure c ON c.id = n.id
GROUP BY n.id
"""

_COUNT_MATCHED = f"""
SELECT count(*)::int
FROM node
WHERE ($1::text[] IS NULL OR {_SOURCE_SQL} = ANY($1))
  AND ($2::text[] IS NULL OR node_type = ANY($2))
  AND ($3::bool OR deleted_at IS NULL)
  AND ($4::text IS NULL
       OR body ILIKE '%' || $4 || '%'
       OR entity_id ILIKE '%' || $4 || '%'
       OR payload::text ILIKE '%' || $4 || '%')
"""

# Both endpoints constrained to the drawn set: a relation edge to a node that is
# not on screen has nowhere to land, and dropping it client-side would mean the
# client had to know the node set twice.
_SELECT_EDGES = """
SELECT from_node_id, to_node_id, relation
FROM edge
WHERE from_node_id = ANY($1::uuid[]) AND to_node_id = ANY($1::uuid[])
"""

_GRANT_COUNTS = """
SELECT node_id, count(*)::int AS n
FROM access
WHERE node_id = ANY($1::uuid[])
GROUP BY node_id
"""

# Counted over the whole table rather than the drawn set, so a folder reads
# "40 children" even when the limit only let six of them onto the canvas.
_CHILD_COUNTS = """
SELECT permission_parent_id AS id, count(*)::int AS n
FROM node
WHERE permission_parent_id = ANY($1::uuid[])
GROUP BY permission_parent_id
"""


def _csv(value: str | None) -> list[str] | None:
    """Query params take a comma-separated list; empty means no filter."""
    if not value:
        return None
    items = [v.strip() for v in value.split(",") if v.strip()]
    return items or None


def _pool(request: Request) -> asyncpg.Pool:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="database pool not ready")
    return pool


@router.get("", response_model=GraphResponse)
async def get_graph(
    request: Request,
    sources: str | None = Query(None, description="csv of sources, e.g. slack,drive"),
    node_types: str | None = Query(None, description="csv of full node types"),
    q: str | None = Query(None, description="substring over body, entity id, payload"),
    limit: int = Query(
        DEFAULT_LIMIT,
        ge=0,
        le=MAX_LIMIT,
        description="0 for every matching node",
    ),
    include_deleted: bool = Query(False),
) -> GraphResponse:
    pool = _pool(request)

    async with pool.acquire() as conn:
        source_list = _csv(sources)
        type_list = _csv(node_types)

        rows = await conn.fetch(
            _SELECT_NODES,
            source_list,
            type_list,
            include_deleted,
            q,
            limit or None,
            MAX_PARENT_DEPTH,
        )
        matched = await conn.fetchval(
            _COUNT_MATCHED, source_list, type_list, include_deleted, q
        )

        ids = [r["id"] for r in rows]
        grant_counts = {
            r["node_id"]: r["n"] for r in await conn.fetch(_GRANT_COUNTS, ids)
        }
        child_counts = {r["id"]: r["n"] for r in await conn.fetch(_CHILD_COUNTS, ids)}
        edge_rows = await conn.fetch(_SELECT_EDGES, ids)

    nodes = [_to_node(r, grant_counts, child_counts) for r in rows]
    present = {n.id for n in nodes}
    parent_pairs = {(n.id, n.parent_id) for n in nodes if n.parent_id}

    edges: list[GraphEdge] = [
        GraphEdge(
            from_id=str(r["from_node_id"]),
            to_id=str(r["to_node_id"]),
            relation=r["relation"],
            kind="relation",
        )
        for r in edge_rows
        if not (
            r["relation"] == IN
            and (str(r["from_node_id"]), str(r["to_node_id"])) in parent_pairs
        )
    ]
    # Drawn child -> parent, the direction access is resolved in: the visibility
    # walk climbs from a candidate node to the grants that govern it.
    edges += [
        GraphEdge(from_id=n.id, to_id=n.parent_id, relation=IN, kind="permission")
        for n in nodes
        if n.parent_id and n.parent_id in present
    ]

    by_source: dict[str, int] = {}
    by_node_type: dict[str, int] = {}
    for n in nodes:
        by_source[n.source] = by_source.get(n.source, 0) + 1
        key = n.node_type or UNMATERIALIZED
        by_node_type[key] = by_node_type.get(key, 0) + 1

    seeded = sum(1 for r in rows if r["seeded"])

    return GraphResponse(
        nodes=nodes,
        edges=edges,
        stats=GraphStats(
            node_count=len(nodes),
            edge_count=len(edges),
            matched=matched or 0,
            truncated=(matched or 0) > seeded,
            ancestors_added=len(nodes) - seeded,
            by_source=by_source,
            by_node_type=by_node_type,
        ),
    )


@router.get("/meta", response_model=GraphMeta)
async def get_meta(request: Request) -> GraphMeta:
    """What exists, so the client builds its filters from the data rather than
    from a hardcoded copy of the registry that drifts the moment a type lands."""
    pool = _pool(request)

    async with pool.acquire() as conn:
        node_types = await conn.fetch(
            """
            SELECT coalesce(node_type, $1) AS key, count(*)::int AS count
            FROM node GROUP BY 1 ORDER BY 2 DESC
            """,
            UNMATERIALIZED,
        )
        sources = await conn.fetch(
            f"SELECT {_SOURCE_SQL} AS key, count(*)::int AS count "
            f"FROM node GROUP BY 1 ORDER BY 2 DESC"
        )
        relations = await conn.fetch(
            "SELECT relation AS key, count(*)::int AS count "
            "FROM edge GROUP BY 1 ORDER BY 2 DESC"
        )
        total_nodes = await conn.fetchval("SELECT count(*)::int FROM node")
        total_edges = await conn.fetchval("SELECT count(*)::int FROM edge")
        total_identities = await conn.fetchval("SELECT count(*)::int FROM identity")
        total_grants = await conn.fetchval("SELECT count(*)::int FROM access")

    def counts(rows: list[asyncpg.Record]) -> list[MetaCount]:
        return [MetaCount(key=r["key"], count=r["count"]) for r in rows]

    return GraphMeta(
        total_nodes=total_nodes,
        total_edges=total_edges,
        total_identities=total_identities,
        total_grants=total_grants,
        sources=counts(sources),
        node_types=counts(node_types),
        relations=counts(relations),
    )


@router.get("/nodes/{entity_id:path}", response_model=NodeDetail)
async def get_node(request: Request, entity_id: str) -> NodeDetail:
    """Detail for the click-through panel.

    Keyed by entity id rather than uuid: entity ids are what every other layer
    speaks, and the uuid is an internal join key the client should not have to
    round-trip. `:path` because entity ids contain colons and, for Slack, dots.
    """
    pool = _pool(request)

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT id, entity_id, node_type, permission_parent_id, body,
                   created_at, updated_at, deleted_at, payload
            FROM node WHERE entity_id = $1
            """,
            entity_id,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=f"no node {entity_id!r}")

        node_id = row["id"]
        grant_rows = await conn.fetch(
            """
            SELECT a.identity_id, a.level, i.display_name, i.email
            FROM access a
            LEFT JOIN identity i ON i.id = a.identity_id
            WHERE a.node_id = $1
            ORDER BY a.level, a.identity_id
            """,
            node_id,
        )
        neighbor_rows = await conn.fetch(
            """
            SELECT n.entity_id, n.node_type, n.payload, n.body,
                   e.relation, 'out' AS direction
            FROM edge e JOIN node n ON n.id = e.to_node_id
            WHERE e.from_node_id = $1
            UNION ALL
            SELECT n.entity_id, n.node_type, n.payload, n.body,
                   e.relation, 'in' AS direction
            FROM edge e JOIN node n ON n.id = e.from_node_id
            WHERE e.to_node_id = $1
            """,
            node_id,
        )
        parent_entity_id = None
        if row["permission_parent_id"]:
            parent_entity_id = await conn.fetchval(
                "SELECT entity_id FROM node WHERE id = $1", row["permission_parent_id"]
            )
        child_count = await conn.fetchval(
            "SELECT count(*)::int FROM node WHERE permission_parent_id = $1", node_id
        )

    return NodeDetail(
        node=_to_node(row, {node_id: len(grant_rows)}, {node_id: child_count}),
        parent_entity_id=parent_entity_id,
        grants=[
            Grant(
                identity_id=g["identity_id"],
                level=g["level"],
                display_name=g["display_name"],
                email=g["email"],
            )
            for g in grant_rows
        ],
        neighbors=[
            Neighbor(
                entity_id=n["entity_id"],
                label=label_of(
                    n["payload"] or {}, n["body"] or "", n["entity_id"]
                ),
                node_type=n["node_type"],
                relation=n["relation"],
                direction=n["direction"],
            )
            for n in neighbor_rows
        ],
        payload=row["payload"] or {},
        body=row["body"] or "",
    )
