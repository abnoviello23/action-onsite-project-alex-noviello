"""Persisting the semantic layer: identity resolution, facts, retraction.

Two node shapes, two access rules, and the split is the whole design.

**Entities** carry identity and nothing else, and carry **no access of their
own**: no permission parent, and no grants. An entity is visible exactly when a
fact about it is visible, which `query.visibility` derives at read time. Nothing
is copied down, so nothing has to be withdrawn when a channel grant is revoked
upstream.

**Facts** carry the content — one node per (document, entity), holding
everything that document says about that entity — and their permission parent is
the document itself. They inherit access along exactly the path a Slack message
does, decided by the same kernel walk, with nothing materialised and no second
rule. A claim from a private channel is visible to that channel; one from a
public channel to the workspace; both hang off the same entity, and neither
reader can tell the other's exists.

That is why content must never be written onto an entity. The entity is a name
the workspace may know. The fact is a claim only some people may read.

**Which entity is this?** A connector is handed an id by Slack; an extractor is
handed a name in a sentence. `resolve` closes that gap with a fixed cascade:
look for an existing entity matching any populated identity key, strongest key
first, and mint a new id only when nothing matches.

**Retraction.** Facts are owned by their source. When a document changes or is
deleted, every fact derived from it is deleted outright — they described text
that no longer exists — and re-derived from the new content if there is any.
That is `retract_from`, and because entity access is derived from exactly
those rows, it is also the whole of the access change: the same delete that
retracts a claim withdraws the audience that claim conferred.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

import asyncpg

from core.registry import NodeTypeSpec, safe_ident, spec_for
from semantic.config import FACT_TYPE, SemanticEntityType
from semantic.models import SemanticWrite
from store import Store

log = logging.getLogger("semantic.store")

# Longest identity slug kept verbatim in an entity id. Past it the value is
# hashed: a task title can be a sentence, and an unbounded entity id would be
# unreadable in a citation and awkward as a btree key.
MAX_SLUG_CHARS = 96

_UNSAFE = re.compile(r"[^a-z0-9._@+-]+")


def slugify(value: str) -> str:
    """A stable, readable component of an entity id.

    Case- and whitespace-normalised, because "Alex Brooks" and "alex  brooks"
    are the same person and an id that distinguished them would split the node.
    """
    lowered = _UNSAFE.sub("-", value.strip().lower()).strip("-")
    if not lowered:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
    if len(lowered) > MAX_SLUG_CHARS:
        return hashlib.sha256(lowered.encode("utf-8")).hexdigest()[:32]
    return lowered


def mint_entity_id(type_name: str, key: str, value: str) -> str:
    """`person:slack_user_id:u123`. The key is in the id deliberately.

    Two entities resolved by different keys are different rows until something
    links them, and burying that in an opaque hash would make the split
    invisible. With the key present, `person:name:alex-brooks` sitting next to
    `person:slack_user_id:u123` reads as exactly what it is.
    """
    return f"{type_name}:{key}:{slugify(value)}"


def identity_values(
    spec: SemanticEntityType, identity: dict[str, Any]
) -> list[tuple[str, str]]:
    """Populated identity keys in cascade order, as `(key, value)` pairs."""
    out: list[tuple[str, str]] = []
    for key in spec.identity_keys:
        value = identity.get(key)
        if isinstance(value, str) and value.strip():
            out.append((key, value.strip()))
    return out


_SOURCE_NODE = """
SELECT id, entity_id, node_type, body, payload, created_at, updated_at,
       content_version, semantic_version
FROM node
WHERE entity_id = $1 AND node_type IS NOT NULL AND deleted_at IS NULL
"""

# Guarded like the ingest watermark it mirrors: a redelivery already handled at
# this version writes nothing and reports zero rows.
_MARK = """
UPDATE node SET semantic_version = $2
WHERE entity_id = $1 AND semantic_version < $2
"""

# Facts about one entity. Not permission-filtered: this is the extractor's view,
# and it runs as a system principal. Readers go through `query.session`.
_FACTS_ABOUT = """
SELECT entity_id, body, payload->>'source' AS source
FROM node
WHERE node_type = $1 AND deleted_at IS NULL AND payload->>'subject' = $2
ORDER BY updated_at DESC
LIMIT $3
"""

# Candidate entities for a name the extractor read in a document.
#
# Substring over the label and over the identity payload, so "Jane" reaches
# "Jane Doe" and a raw Slack id or email reaches its person. Deliberately loose:
# this is the recall half, and the model does the deciding with the full
# identity in front of it. `node_body_trgm` serves the label arm; the payload
# arm is a scan over a small table and is the thing to revisit at scale.
#
# One recent fact travels with each hit. Two people can share a first name, and
# "which Jane" is answerable from what is known about them — but not from the
# name alone, which is all a bare identity gives you.
_SEARCH_ENTITIES = """
SELECT n.entity_id, n.node_type, n.body, n.payload,
       (SELECT f.body FROM node f
         WHERE f.node_type = $4 AND f.deleted_at IS NULL
           AND f.payload->>'subject' = n.entity_id
         ORDER BY f.updated_at DESC LIMIT 1) AS known
FROM node n
WHERE n.node_type = ANY($1::text[])
  AND n.deleted_at IS NULL
  AND (n.body ILIKE '%' || $2 || '%' OR n.payload::text ILIKE '%' || $2 || '%')
ORDER BY length(n.body), n.updated_at DESC
LIMIT $3
"""

# One entity by id, for binding a search hit into a run.
_GET_ENTITY = """
SELECT entity_id, node_type, payload
FROM node
WHERE entity_id = $1 AND deleted_at IS NULL AND node_type = ANY($2::text[])
"""

# Every fact derived from one document. The parent pointer is the ownership
# link, so this is one index scan on `node_fact_parent_idx`.
_FACTS_FROM_SOURCE = """
SELECT id, entity_id, payload->>'subject' AS subject
FROM node
WHERE node_type = $1 AND permission_parent_id = $2
"""

# Hard delete, not a tombstone. A fact is derived data with no independent
# existence: the document it described is gone or changed, so the claim is not
# "deleted" in the sense a message is — it was never anything but a reading of
# text that no longer says that. Cascades take its edges with it.
_DELETE_FACTS = """
DELETE FROM node
WHERE node_type = $1 AND permission_parent_id = $2
"""

class SemanticStore:
    """Semantic persistence for one connection. The caller owns the transaction."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._store = Store(conn)
        # Fact entity ids written by this pass, for the embed stream.
        #
        # Facts go in through `Store.upsert_node`, which is the guarded
        # primitive and deliberately does no bookkeeping — the re-embed list
        # lives on `Store.apply`, the ingest path, which the semantic layer does
        # not use. Without collecting them here the semantic layer would be
        # full-text searchable and invisible to `semantic_search`, which is most
        # of what it exists for.
        #
        # Drained by the caller *after* commit, for the same reason ingest
        # drains its own there: a job visible before its row makes the writer
        # embed the text it replaced. Retraction needs no counterpart — facts
        # are hard-deleted, so `node_chunk` cascades with them.
        self.embed: list[str] = []

    # --------------------------------------------------------------- reads --

    async def source_node(self, entity_id: str) -> asyncpg.Record | None:
        """The document to extract from, or None if it is gone or tombstoned."""
        return await self._conn.fetchrow(_SOURCE_NODE, entity_id)

    async def resolve(
        self, spec: SemanticEntityType, identity: dict[str, Any]
    ) -> tuple[str, bool] | None:
        """`(entity_id, is_new)`, or None if no identity key was populated.

        Looks for an existing row matching *any* populated identity key before
        minting, which is what merges a later sighting into an earlier node
        rather than creating a near-duplicate beside it. Matches are ranked by
        cascade position, so a Slack id match beats a name match when both hit.
        """
        populated = identity_values(spec, identity)
        if not populated:
            return None

        existing = await self._match(spec, populated)
        if existing is not None:
            return existing, False

        key, value = populated[0]
        return mint_entity_id(spec.name, key, value), True

    async def _match(
        self, spec: SemanticEntityType, populated: list[tuple[str, str]]
    ) -> str | None:
        """Strongest existing match across the populated identity keys.

        One statement rather than a lookup per key, ordered by cascade position.
        Served by the type's `semantic_<name>_identity` partial index.

        Column and view names are interpolated, which is only sound because they
        originate in `NodeTypeSpec` — never in the extraction — and the registry
        checked them with `safe_ident` before this ran. Re-checking here keeps
        that guarantee next to the interpolation. Values are always bound.
        """
        node_spec: NodeTypeSpec | None = spec_for(spec.name)
        if node_spec is None:
            return None

        columns = set(node_spec.payload_model.model_fields)
        terms: list[str] = []
        params: list[Any] = []
        ranks: list[str] = []
        for key, value in populated:
            if key not in columns:
                continue
            if not safe_ident(key):
                raise ValueError(f"unsafe identity key {key!r} on {spec.name}")
            params.append(value)
            terms.append(f"{key} = ${len(params)}")
            ranks.append(f"WHEN {key} = ${len(params)} THEN {spec.identity_keys.index(key)}")
        if not terms:
            return None

        sql = (
            f"SELECT entity_id FROM {node_spec.view_name} "
            f"WHERE deleted_at IS NULL AND ({' OR '.join(terms)}) "
            f"ORDER BY CASE {' '.join(ranks)} ELSE 99 END, updated_at DESC "
            f"LIMIT 1"
        )
        return await self._conn.fetchval(sql, *params)

    async def facts_about(self, entity_id: str, *, limit: int = 10) -> list[dict[str, Any]]:
        """Facts already recorded against an entity, newest first."""
        rows = await self._conn.fetch(_FACTS_ABOUT, FACT_TYPE, entity_id, limit)
        return [{"known": r["body"], "source": r["source"]} for r in rows]

    async def search_entities(
        self, query: str, *, type_names: list[str], limit: int = 15
    ) -> list[dict[str, Any]]:
        """Candidate entities for a name, shortest label first.

        Shortest-first because an exact name is a better answer than a longer
        one that merely contains it: searching "Atlas" should surface the
        project called Atlas above "Atlas migration retrospective".
        """
        rows = await self._conn.fetch(
            _SEARCH_ENTITIES, type_names, query, limit, FACT_TYPE
        )
        return [
            {
                "entity_id": r["entity_id"],
                "type": r["node_type"],
                "identity": r["payload"] or {},
                "known": r["known"],
            }
            for r in rows
        ]

    async def get_entity(
        self, entity_id: str, *, type_names: list[str]
    ) -> dict[str, Any] | None:
        """One entity by id, or None if it is gone or not a declared type."""
        row = await self._conn.fetchrow(_GET_ENTITY, entity_id, type_names)
        if row is None:
            return None
        return {
            "entity_id": row["entity_id"],
            "type": row["node_type"],
            "identity": row["payload"] or {},
        }

    async def sources_of(self, entity_id: str, *, limit: int = 25) -> list[str]:
        """Live documents this entity was extracted from.

        Derived rather than stored: an entity's sources are exactly the
        permission parents of the facts about it. There was a `semantic_source`
        table holding this and nothing else, which was one more thing to keep in
        step for an answer already present in the graph.
        """
        rows = await self._conn.fetch(
            """
            SELECT DISTINCT p.entity_id
            FROM node f
            JOIN node p ON p.id = f.permission_parent_id
            WHERE f.node_type = $1
              AND f.deleted_at IS NULL
              AND f.payload->>'subject' = $2
              AND p.deleted_at IS NULL
            ORDER BY p.entity_id
            LIMIT $3
            """,
            FACT_TYPE,
            entity_id,
            limit,
        )
        return [r["entity_id"] for r in rows]

    async def entities_from(self, source_entity_id: str) -> list[str]:
        """Entities the facts of this document are about.

        The audit that follows a change needs to know which entities were
        affected, and the facts themselves are the record of that — read before
        they are retracted.
        """
        source_id = await self._store.ensure_node_id(source_entity_id)
        rows = await self._conn.fetch(_FACTS_FROM_SOURCE, FACT_TYPE, source_id)
        return sorted({r["subject"] for r in rows if r["subject"]})

    # -------------------------------------------------------------- writes --

    async def retract_from(self, source_entity_id: str) -> int:
        """Delete everything one document implied. Returns the fact count.

        Called before re-extracting a changed document and on deletion of one.
        A pass owns what it wrote: the previous run read text that has since
        changed or vanished, so its conclusions go rather than accumulate beside
        the new ones.

        Two deletes, because the layer writes two kinds of row and they are
        owned through different columns. Facts hang off the document by
        `permission_parent_id`; the edges a pass drew — `mentions` from the
        document, and any entity-to-entity relation it asserted — carry
        `source_node_id` instead. Deleting only the first is what let a stale
        `works_on` outlive the sentence that produced it.

        Entities are left alone, and need no attention: their visibility is
        derived from exactly these fact rows, so this delete *is* the access
        change. An entity whose last readable fact just went is now invisible;
        one with facts from other documents is exactly as visible as before.
        """
        source_id = await self._store.ensure_node_id(source_entity_id)
        status = await self._conn.execute(_DELETE_FACTS, FACT_TYPE, source_id)
        await self._conn.execute(
            "DELETE FROM edge WHERE source_node_id = $1", source_id
        )
        return int(str(status).rsplit(" ", 1)[-1] or 0)

    async def apply(self, write: SemanticWrite) -> tuple[int, int]:
        """Persist one pass. Returns `(entities_written, facts_written)`.

        Entities first, then facts, then edges — so an edge never lands before
        the nodes it points at, and a fact never lands before the entity it is
        about.

        No grants are written and no provenance is recorded, because there is
        nothing to record: a fact's access is the permission parent `upsert_node`
        just set, and an entity's is derived from its facts. The entire access
        consequence of this method is the parent pointer on each fact row.
        """
        entities = 0
        for node in write.entities:
            if await self._store.upsert_node(node):
                entities += 1

        facts = 0
        for node in write.facts:
            if await self._store.upsert_node(node):
                facts += 1
                self.embed.append(node.entity_id)

        for edge in write.edges:
            await self._store.upsert_edge(edge)

        return entities, facts

    async def mark_extracted(self, entity_id: str, content_version: str) -> bool:
        """Advance the extraction watermark. False if it was already at or past."""
        status = await self._conn.execute(_MARK, entity_id, content_version)
        return not str(status).rstrip().endswith(" 0")
