"""Graph persistence: id minting, guarded upsert, grants, edges, tombstones.

Write-only, and the ingest path's sole route into Postgres. Nothing here
consults access — `query.visibility` owns that, and keeping the two apart is why
a generator cannot accidentally read around a permission.

`upsert_node` and `upsert_edge` are public because the semantic layer writes
through them too (see `semantic.store`). They are the guarded primitives: the
version comparison that makes redelivery safe lives in the first, and the
provenance column that makes an inferred edge retractable lives in the second.
Reimplementing either alongside would mean two definitions of "safe to apply".
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from core.access import Identity
from core.graph import Edge, Node, SemanticNode
from core.message import ChangeKind, GraphWrite, RosterMode
from core.registry import NODE_TYPES
from core.types import NodeType

# The built-in fact type, spelled here rather than imported: `store` is the
# ingest path's write surface and must not depend on the semantic package, which
# depends on it. One string is a cheaper coupling than a cycle.
FACT_TYPE = "fact"


def _payload(value: object) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str | bytes | bytearray):
        return json.loads(value)
    return dict(value)


class Store:
    """One connection at a time. The worker holds the transaction."""

    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        # Entity ids whose chunks are now stale: a content version actually
        # advanced, or the node was tombstoned. Collected rather than published
        # because this object only holds a connection, and because a job
        # enqueued inside the transaction would be visible to the embed writer
        # before the row it describes was committed — the writer would read the
        # old body and write chunks that look current. The worker drains this
        # after commit. See `worker.__main__` and `embed.writer`.
        self.reembed: list[str] = []

    # -------------------------------------------------------------- reads --

    async def node_payload(self, entity_id: str) -> dict[str, Any] | None:
        row = await self._conn.fetchrow(
            "SELECT payload FROM node WHERE entity_id = $1", entity_id
        )
        if row is None:
            return None
        return _payload(row["payload"])

    async def slack_neighbors(
        self,
        *,
        channel_id: str,
        ts: str,
        thread_ts: str | None,
        exclude: str,
    ) -> tuple[str | None, str | None]:
        view = NODE_TYPES[NodeType.SLACK_MESSAGE].view_name
        if thread_ts is None:
            extra = "(thread_ts IS NULL OR thread_ts = ts)"
            ts_slot = "$3"
            args: tuple[object, ...] = (exclude, channel_id, ts)
        else:
            extra = "thread_ts = $3 AND ts <> thread_ts"
            ts_slot = "$4"
            args = (exclude, channel_id, thread_ts, ts)
        pred = await self._conn.fetchval(
            f"""
            SELECT entity_id FROM {view}
            WHERE deleted_at IS NULL AND entity_id <> $1
              AND channel_id = $2 AND {extra} AND ts < {ts_slot}
            ORDER BY ts DESC LIMIT 1
            """,
            *args,
        )
        succ = await self._conn.fetchval(
            f"""
            SELECT entity_id FROM {view}
            WHERE deleted_at IS NULL AND entity_id <> $1
              AND channel_id = $2 AND {extra} AND ts > {ts_slot}
            ORDER BY ts ASC LIMIT 1
            """,
            *args,
        )
        return pred, succ

    async def outgoing(self, entity_id: str, relation: str) -> list[str]:
        rows = await self._conn.fetch(
            """
            SELECT t.entity_id
            FROM edge e
            JOIN node f ON f.id = e.from_node_id
            JOIN node t ON t.id = e.to_node_id
            WHERE f.entity_id = $1 AND e.relation = $2
            """,
            entity_id,
            relation,
        )
        return [r["entity_id"] for r in rows]

    async def edges_from(self, entity_id: str) -> list[Edge]:
        rows = await self._conn.fetch(
            """
            SELECT t.entity_id AS to_entity_id, e.relation
            FROM edge e
            JOIN node f ON f.id = e.from_node_id
            JOIN node t ON t.id = e.to_node_id
            WHERE f.entity_id = $1
            """,
            entity_id,
        )
        return [
            Edge(
                from_entity_id=entity_id,
                to_entity_id=r["to_entity_id"],
                relation=r["relation"],
            )
            for r in rows
        ]

    async def content_version(self, entity_id: str) -> str | None:
        return await self._conn.fetchval(
            """
            SELECT content_version FROM node
            WHERE entity_id = $1 AND node_type IS NOT NULL
            """,
            entity_id,
        )

    async def existing(self, entity_ids: list[str]) -> set[str]:
        """Live mirrored rows among these ids. Unmaterialized and tombstones
        are absent: a generator must not mint a `mentions` edge to a node the
        connector has not actually written."""
        if not entity_ids:
            return set()
        rows = await self._conn.fetch(
            """
            SELECT entity_id FROM node
            WHERE entity_id = ANY($1::text[])
              AND node_type IS NOT NULL
              AND deleted_at IS NULL
            """,
            entity_ids,
        )
        return {r["entity_id"] for r in rows}

    async def mentioning(self, needles: list[str]) -> list[str]:
        """Nodes whose `payload.link_urls` contain any of these substrings.

        Used when a target arrives after the documents that already named it:
        those documents stored the URL on the payload, and this finds them so
        the generator can mint the `mentions` edge from that side.
        """
        found = [n for n in needles if n]
        if not found:
            return []
        rows = await self._conn.fetch(
            """
            SELECT DISTINCT n.entity_id
            FROM node n,
                 jsonb_array_elements_text(
                     COALESCE(n.payload->'link_urls', '[]'::jsonb)
                 ) AS url
            WHERE n.deleted_at IS NULL
              AND n.node_type IS NOT NULL
              AND EXISTS (
                  SELECT 1 FROM unnest($1::text[]) AS needle
                  WHERE needle <> ''
                    AND position(lower(needle) in lower(url)) > 0
              )
            """,
            found,
        )
        return [r["entity_id"] for r in rows]

    # -------------------------------------------------------------- apply --

    async def apply(self, write: GraphWrite) -> None:
        for identity in write.identities:
            await self._upsert_identity(identity)
        for grant in write.grants:
            await self._ensure_identity(grant.identity_id)
        for membership in write.memberships:
            await self._ensure_identity(membership.child_identity_id)
            await self._ensure_identity(membership.parent_identity_id)

        named = {i.id for i in write.identities}
        parents = {m.parent_identity_id for m in write.memberships}
        children = {m.child_identity_id for m in write.memberships}
        if write.roster is RosterMode.PARENT:
            for parent in parents:
                await self._conn.execute(
                    "DELETE FROM membership WHERE parent_identity_id = $1", parent
                )
        elif write.roster is RosterMode.CHILDREN:
            for child_id in children | (named - parents):
                await self._conn.execute(
                    "DELETE FROM membership WHERE child_identity_id = $1", child_id
                )
        for membership in write.memberships:
            await self._conn.execute(
                """
                INSERT INTO membership (child_identity_id, parent_identity_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                membership.child_identity_id,
                membership.parent_identity_id,
            )

        apply_edges = False
        if write.change is ChangeKind.DELETED:
            await self._tombstone(write.entity_id)
            # A tombstone is an UPDATE, not a DELETE, so `node_chunk`'s
            # ON DELETE CASCADE never fires on this path. Without this enqueue
            # the vectors of a deleted document would outlive it.
            self.reembed.append(write.entity_id)
            apply_edges = True
        elif write.node is not None:
            applied = await self.upsert_node(write.node)
            if applied:
                node_id = await self.ensure_node_id(write.entity_id)
                await self._rewrite_grants(node_id, write)
                apply_edges = True
                # Only when the guarded upsert actually took the row. A
                # duplicate or out-of-order delivery that the version guard
                # rejected has not changed the body and must not re-embed.
                self.reembed.append(write.entity_id)

        if apply_edges:
            for edge in write.retract_edges:
                await self._retract_edge(edge)
            for edge in write.edges:
                await self.upsert_edge(edge)

    async def ensure_node_id(self, entity_id: str) -> UUID:
        row = await self._conn.fetchrow(
            """
            INSERT INTO node (entity_id) VALUES ($1)
            ON CONFLICT (entity_id) DO UPDATE SET entity_id = node.entity_id
            RETURNING id
            """,
            entity_id,
        )
        return row["id"]

    async def upsert_node(self, node: Node | SemanticNode) -> bool:
        """Guarded upsert. True when the row was actually written.

        False means a duplicate or out-of-order delivery lost the version
        comparison, and callers use that to skip everything that only follows
        from a real change: rewriting grants, re-embedding, re-extracting.

        A `SemanticNode` has no permission parent by construction, so the parent
        lookup is skipped rather than defaulted. The `getattr` reads a field that
        exists on one of the two models — it is not tolerating a missing one.
        """
        parent_id = None
        parent_entity_id = getattr(node, "permission_parent_entity_id", None)
        if parent_entity_id:
            parent_id = await self.ensure_node_id(parent_entity_id)
        await self.ensure_node_id(node.entity_id)
        status = await self._conn.execute(
            """
            INSERT INTO node (
                entity_id, node_type, permission_parent_id, body,
                created_at, updated_at, content_version, payload, deleted_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8::jsonb, NULL)
            ON CONFLICT (entity_id) DO UPDATE SET
                node_type = EXCLUDED.node_type,
                permission_parent_id = EXCLUDED.permission_parent_id,
                body = EXCLUDED.body,
                created_at = EXCLUDED.created_at,
                updated_at = EXCLUDED.updated_at,
                content_version = EXCLUDED.content_version,
                payload = EXCLUDED.payload,
                deleted_at = NULL
            WHERE EXCLUDED.content_version > node.content_version
               OR node.node_type IS NULL
            """,
            node.entity_id,
            str(node.node_type),
            parent_id,
            node.body,
            node.created_at,
            node.updated_at,
            node.content_version,
            # The connection registers a jsonb codec whose encoder is
            # json.dumps, so the dict goes over as-is. Serializing here too
            # dumped it twice and stored a jsonb *string* holding JSON text:
            # jsonb_typeof(payload) came back 'string', payload->>'name' was
            # null for every node, and readers that did not defensively
            # json.loads (the API's label_of) blew up on 'str' has no 'get'.
            node.payload,
        )
        return not str(status).rstrip().endswith(" 0")

    async def _tombstone(self, entity_id: str) -> None:
        """Keep inbound edges (mentions, remaining thread replies); drop ours.

        Drive/Notion retract `in` this way so a folder does not keep listing a
        trashed child. Slack must too: `in_channel` / `in_thread` are the same
        membership, and `next` from this node is repaired by the generator's
        splice onto the predecessor.

        Inferred edges are the exception to "keep inbound". An edge justified by
        this document is a claim this document made, and a deleted document
        makes no claims — so those go regardless of which end they point from.
        The foreign key would do it on a hard delete, but a tombstone is an
        UPDATE and never fires it.

        Facts go with them, and go *hard*. A fact is this system's reading of
        text that no longer exists; it has no independent life to tombstone, and
        leaving it soft-deleted under a soft-deleted parent would leave it
        visible — `query.visibility` checks liveness on the candidate, not on
        its ancestors, deliberately, so that a stale container tombstone cannot
        hide live documents.

        The *entities* stay, and need no attention at all. Entity visibility is
        derived from the facts about them (`query.visibility`), so deleting this
        document's facts is the whole of the access change: an entity whose last
        readable fact just went is now invisible, and one with facts left over
        from other documents is exactly as visible as it was. There is nothing
        copied down to withdraw.
        """
        node_id = await self.ensure_node_id(entity_id)
        await self._conn.execute(
            """
            UPDATE node SET deleted_at = COALESCE(deleted_at, now())
            WHERE entity_id = $1
            """,
            entity_id,
        )
        await self._conn.execute(
            "DELETE FROM edge WHERE from_node_id = $1 OR source_node_id = $1",
            node_id,
        )
        # Cascades take each fact's edges with it.
        await self._conn.execute(
            "DELETE FROM node WHERE node_type = $1 AND permission_parent_id = $2",
            FACT_TYPE,
            node_id,
        )

    async def _rewrite_grants(self, node_id: UUID, write: GraphWrite) -> None:
        await self._conn.execute("DELETE FROM access WHERE node_id = $1", node_id)
        for grant in write.grants:
            await self._ensure_identity(grant.identity_id)
            resource_id = await self.ensure_node_id(grant.resource_entity_id)
            await self._conn.execute(
                """
                INSERT INTO access (identity_id, node_id, level)
                VALUES ($1, $2, $3)
                ON CONFLICT (identity_id, node_id) DO UPDATE SET level = EXCLUDED.level
                """,
                grant.identity_id,
                resource_id,
                grant.level,
            )

    async def upsert_edge(self, edge: Edge) -> None:
        """Insert if absent. Provenance is written only when the edge carries it.

        `DO NOTHING` rather than `DO UPDATE`: the first justification recorded
        for a relation is the one kept. A later extraction reaching the same
        conclusion from a different document does not overwrite the earlier
        source, so what is stored stays a document that genuinely implied the
        edge rather than the most recent one to mention it.
        """
        frm = await self.ensure_node_id(edge.from_entity_id)
        to = await self.ensure_node_id(edge.to_entity_id)
        source_id = None
        if edge.source_entity_id:
            source_id = await self.ensure_node_id(edge.source_entity_id)
        await self._conn.execute(
            """
            INSERT INTO edge (from_node_id, to_node_id, relation, source_node_id)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT DO NOTHING
            """,
            frm,
            to,
            edge.relation,
            source_id,
        )

    async def _retract_edge(self, edge: Edge) -> None:
        await self._conn.execute(
            """
            DELETE FROM edge e
            USING node f, node t
            WHERE e.from_node_id = f.id AND e.to_node_id = t.id
              AND f.entity_id = $1 AND t.entity_id = $2 AND e.relation = $3
            """,
            edge.from_entity_id,
            edge.to_entity_id,
            edge.relation,
        )

    async def _upsert_identity(self, identity: Identity) -> None:
        await self._conn.execute(
            """
            INSERT INTO identity (id, display_name, email, can_authenticate, is_active)
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT (id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                email = EXCLUDED.email,
                can_authenticate = EXCLUDED.can_authenticate,
                is_active = EXCLUDED.is_active
            """,
            identity.id,
            identity.display_name,
            identity.email,
            identity.can_authenticate,
            identity.is_active,
        )

    async def _ensure_identity(self, identity_id: str) -> None:
        await self._conn.execute(
            "INSERT INTO identity (id) VALUES ($1) ON CONFLICT DO NOTHING",
            identity_id,
        )
