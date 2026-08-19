"""The one place access is decided.

Three steps, resolved once per request and then reused by every tool:

  1. **Principals** — expand the requester upward through `membership` to every
     group, workspace, and domain identity they inherit from, plus `public`.
  2. **Granted roots** — the node ids carrying an `access` row for any of those
     principals. A small set: channels, folders, drives, Notion roots.
  3. **Visible?** — climb `permission_parent_id` from a candidate and keep it if
     any ancestor, itself included, is a granted root.

Direction matters. Retrieval asks "of these N candidates, which can Alice see",
so the walk goes **up** from candidates — bounded by tree depth — rather than
down from grants, whose subtree is unbounded (one Slack public-channel grant is
the entire workspace). Nothing is materialized onto descendants, so revoking a
folder grant stays one row delete in `access`.

**Semantic entities are the one exception, and they are derived rather than
granted.** A `person` has no permission parent — it was inferred from several
documents with different audiences, and one pointer cannot express that — so the
walk above finds nothing to climb. Instead:

    an entity is visible exactly when at least one fact about it is visible

which is the same statement as "you may know Jane exists if you may read
something about Jane". Facts *do* have a permission parent (the document they
were read out of), so this reduces to the same ancestor walk, run one level
further out and projected back onto the entity.

Deriving it rather than storing it is what keeps revocation honest. Copying an
audience onto the entity would leave that copy behind when the channel grant
that justified it was removed; here there is no copy, and the entity goes dark
the moment its last readable fact does.

This module authenticates nothing. It takes a seed identity id and expands it.
Proving the caller *is* that identity belongs to the API boundary; see
`api.agent`. Keeping that seam here is what lets a demo header today and an
OAuth-proven membership later share one kernel.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

import asyncpg

from core.graph import MAX_PARENT_DEPTH
from core.identity import PUBLIC_ID
from core.registry import SEMANTIC_TYPES

log = logging.getLogger("query.visibility")

__all__ = [
    "MAX_PARENT_DEPTH",
    "UnknownIdentity",
    "UnknownLevel",
    "Visibility",
    "level_priority",
]


class UnknownIdentity(LookupError):
    """No active `identity` row for the requested seed.

    Distinct from "this principal can see nothing": an identity that exists and
    holds no grants is a valid session that returns empty results, and the two
    must not collapse into the same response or the caller cannot tell a typo
    from a correct denial.
    """


# UNION, not UNION ALL. `membership` has no acyclicity constraint and no foreign
# key to `identity` — nested Drive groups can legitimately be circular and a
# connector bug can write a cycle directly — so the dedup is what terminates
# this recursion. The ancestor walk below can afford UNION ALL because it has a
# depth guard; this one has no natural bound.
#
# `is_active` is enforced on every hop: a deactivated group must not keep
# conferring the grants held in its name.
_PRINCIPALS = """
WITH RECURSIVE principals AS (
    SELECT seed.id
    FROM identity seed
    WHERE seed.id = $1 AND seed.is_active
  UNION
    SELECT parent.id
    FROM membership m
    JOIN principals child ON child.id = m.child_identity_id
    JOIN identity parent
      ON parent.id = m.parent_identity_id AND parent.is_active
)
SELECT id FROM principals
"""

# Every node any principal holds a grant on. Served by access_identity_idx.
_GRANTED_ROOTS = """
SELECT DISTINCT node_id
FROM access
WHERE identity_id = ANY($1::text[])
"""

# Candidates whose ancestor chain reaches a granted root.
#
# The filter is on `a.id` — *any* ancestor being granted — while the projection
# is `a.root`, the candidate that ancestor was reached from. Filtering on
# `a.root` instead would ask whether the candidate itself carries a grant and
# would silently discard everything that inherits, which is nearly everything.
#
# Liveness is checked on the candidate only. An ancestor's tombstone does not
# hide its children: sources cascade their own deletes as per-item events, and
# treating a stale container tombstone as a revocation would hide live
# documents the user can still open in Drive.
_VISIBLE = """
WITH RECURSIVE ancestors AS (
    SELECT n.id AS root, n.id, n.permission_parent_id, 0 AS depth
    FROM node n
    WHERE n.id = ANY($1::uuid[])
      AND n.deleted_at IS NULL
      AND n.node_type IS NOT NULL
  UNION ALL
    SELECT a.root, p.id, p.permission_parent_id, a.depth + 1
    FROM node p
    JOIN ancestors a ON p.id = a.permission_parent_id
    WHERE a.depth < $3
)
SELECT DISTINCT root
FROM ancestors
WHERE id = ANY($2::uuid[])
"""


# Entities admitted by their facts.
#
# `cand` narrows to the entity-typed candidates the ancestor walk could not
# place; `reachable` is the ordinary permission climb, seeded from every live
# fact about one of them and carrying the entity it belongs to along the way.
# An entity is kept when any of those climbs reaches a granted root.
#
# The seed join is `payload->>'subject'`, served by `node_fact_subject_live`.
# Without that index this is a sequential scan of `node` on every page of every
# query, which is why the index is created in its own migration with a comment
# saying so.
_VISIBLE_ENTITIES = """
WITH RECURSIVE cand AS (
    SELECT n.id, n.entity_id
    FROM node n
    WHERE n.id = ANY($1::uuid[])
      AND n.deleted_at IS NULL
      AND n.node_type = ANY($4::text[])
),
reachable AS (
    SELECT f.id, f.permission_parent_id, c.id AS entity_uuid, 0 AS depth
    FROM node f
    JOIN cand c ON c.entity_id = f.payload->>'subject'
    WHERE f.node_type = $5 AND f.deleted_at IS NULL
  UNION ALL
    SELECT p.id, p.permission_parent_id, r.entity_uuid, r.depth + 1
    FROM node p
    JOIN reachable r ON p.id = r.permission_parent_id
    WHERE r.depth < $3
)
SELECT DISTINCT entity_uuid
FROM reachable
WHERE id = ANY($2::uuid[])
"""

# The strongest grant this principal holds anywhere on a node's ancestor chain.
#
# The same climb as `_VISIBLE`, asked for a different answer. Visibility is a
# question about *reaching* a granted root at all; this is a question about how
# much the grants found there say — which is the difference between being able
# to read a document and being allowed to overwrite it.
#
# Strongest rather than nearest. Grants accumulate down a tree: an `organizer`
# on the drive and a `reader` on one folder inside it is a person who may still
# write in that folder, because Drive resolves the pair the same way. Taking the
# closest grant instead would let a redundant narrow one revoke a broad one.
#
# Priorities are only ever compared within a source, which needs no enforcement
# here: a node belongs to exactly one source, so every grant on its chain is
# from that source's vocabulary.
_STRONGEST_LEVEL = """
WITH RECURSIVE ancestors AS (
    SELECT n.id, n.permission_parent_id, 0 AS depth
    FROM node n
    WHERE n.id = $1
      AND n.deleted_at IS NULL
      AND n.node_type IS NOT NULL
  UNION ALL
    SELECT p.id, p.permission_parent_id, a.depth + 1
    FROM node p
    JOIN ancestors a ON p.id = a.permission_parent_id
    WHERE a.depth < $3
)
SELECT lvl.name, lvl.priority
FROM ancestors a
JOIN access acc ON acc.node_id = a.id AND acc.identity_id = ANY($2::text[])
JOIN access_level lvl ON lvl.name = acc.level
ORDER BY lvl.priority DESC
LIMIT 1
"""

_LEVEL_PRIORITY = "SELECT priority FROM access_level WHERE name = $1"

# The built-in fact type, spelled rather than imported: `core.registry` holds the
# type table and importing the semantic package from the visibility kernel would
# invert the dependency. One string is cheaper than that.
_FACT_TYPE = "fact"


class UnknownLevel(LookupError):
    """No `access_level` row by that name.

    A spec asking for a level the catalog does not define is a bug in the spec,
    not a denial: answering "you lack it" would silently turn a typo into a
    permanent, invisible refusal.
    """


async def level_priority(conn: asyncpg.Connection, name: str) -> int:
    """Where this level sits on its source's scale."""
    priority = await conn.fetchval(_LEVEL_PRIORITY, name)
    if priority is None:
        raise UnknownLevel(name)
    return int(priority)


@dataclass(frozen=True)
class Visibility:
    """One request's resolved access. Immutable; safe to share across tools."""

    identity_id: str
    principal_ids: tuple[str, ...]
    granted_root_ids: tuple[UUID, ...]

    @classmethod
    async def resolve(cls, conn: asyncpg.Connection, identity_id: str) -> Visibility:
        if not identity_id:
            raise UnknownIdentity("identity id is required")

        rows = await conn.fetch(_PRINCIPALS, identity_id)
        if not rows:
            raise UnknownIdentity(identity_id)

        # `public` is a system principal, not a membership fact: Notion public
        # pages and Drive "anyone with the link" both grant to it, and no
        # identity row needs to exist for that to be true.
        principals = {r["id"] for r in rows}
        principals.add(PUBLIC_ID)

        granted = await conn.fetch(_GRANTED_ROOTS, list(principals))
        vis = cls(
            identity_id=identity_id,
            principal_ids=tuple(sorted(principals)),
            granted_root_ids=tuple(r["node_id"] for r in granted),
        )
        log.debug(
            "resolved %s: %d principal(s), %d granted root(s)",
            identity_id,
            len(vis.principal_ids),
            len(vis.granted_root_ids),
        )
        return vis

    @property
    def sees_nothing(self) -> bool:
        """No grants reach this principal, so no candidate can ever pass.

        Callers short-circuit on this rather than issuing a walk that is
        guaranteed to return the empty set.
        """
        return not self.granted_root_ids

    async def visible(
        self, conn: asyncpg.Connection, candidate_ids: Sequence[UUID]
    ) -> set[UUID]:
        """The subset of `candidate_ids` this principal may see.

        Order is not preserved — callers hold their own ranking and use this as
        a membership test.

        Two passes, and the second usually does not run. The ancestor walk
        places everything with a permission parent, which is every mirrored node
        and every fact. Only semantic entities can come back unplaced, and only
        then is the derived rule evaluated — so a request that touches no
        entities pays exactly what it paid before.
        """
        if not candidate_ids or self.sees_nothing:
            return set()
        candidates = list(candidate_ids)
        rows = await conn.fetch(
            _VISIBLE,
            candidates,
            list(self.granted_root_ids),
            MAX_PARENT_DEPTH,
        )
        visible = {r["root"] for r in rows}

        entity_types = [t for t in SEMANTIC_TYPES if t != _FACT_TYPE]
        remaining = [c for c in candidates if c not in visible]
        if not entity_types or not remaining:
            return visible

        rows = await conn.fetch(
            _VISIBLE_ENTITIES,
            remaining,
            list(self.granted_root_ids),
            MAX_PARENT_DEPTH,
            entity_types,
            _FACT_TYPE,
        )
        visible.update(r["entity_uuid"] for r in rows)
        return visible

    async def is_visible(self, conn: asyncpg.Connection, node_id: UUID) -> bool:
        return node_id in await self.visible(conn, [node_id])

    async def strongest_level(
        self, conn: asyncpg.Connection, node_id: UUID
    ) -> tuple[str, int] | None:
        """The best `(level, priority)` this principal holds here, or None.

        None is not a denial on its own. A semantic entity has no permission
        parent and no grants — it is visible because a fact about it is — so a
        principal who can legitimately see one holds no level on it whatsoever.
        Whether that is enough is the caller's question to answer, and only an
        action declaring a `requires_level` is entitled to say no.

        Read access is *not* implied by this returning something, nor denied by
        it returning nothing. `visible` remains the only answer to that; this
        exists solely to separate "may read" from "may write", which the boolean
        cannot express.
        """
        if self.sees_nothing:
            return None
        row = await conn.fetchrow(
            _STRONGEST_LEVEL, node_id, list(self.principal_ids), MAX_PARENT_DEPTH
        )
        return (row["name"], int(row["priority"])) if row else None
