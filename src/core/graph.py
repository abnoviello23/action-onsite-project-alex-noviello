"""Graph vocabulary.

Core imports nothing from connectors or services; the dependency only ever runs
the other way. Node types live in `core.types`; payload models in `core.payloads`.
Identifiers at this layer are always `entity_id` strings, never uuids.

Two node models share one table, and the split is the whole shape of the system.
A `Node` is **mirrored**: a connector saw it in Slack, Drive, or Notion, its type
is in the closed `NodeType` enum, and its access is inherited along
`permission_parent_entity_id`. A `SemanticNode` is **inferred**: an extractor
concluded it from one or more mirrored nodes, and its type is whatever the
active `semantic_config` declares. Keeping them as separate models is what stops
an extraction from ever writing down a source path, or a connector from minting
a type nobody declared.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from core.types import IDENTITY_ONLY, NodeType

# Guards every walk up `permission_parent_id` against a cycle. Real chains are
# shallow — a Slack message's parent is its channel, a thread reply's is the
# thread parent — so this only ever fires on corrupt data. It lives here rather
# than with either walker because the read path and the write path both climb
# the same tree and must agree on where it ends.
MAX_PARENT_DEPTH = 32

# Topology relations. ACL still lives on permission_parent; these are
# traversable and confer nothing.
IN = "in"
IN_CHANNEL = "in_channel"
IN_THREAD = "in_thread"
NEXT = "next"
# A document names another ingested thing — a Slack permalink, a Drive
# web-view link, a Notion URL, or a Notion @-mention. Same name the semantic
# layer uses for document -> entity, on purpose: both mean "this refers to
# that", and `follow(x, 'mentions', 'in')` is the backlink set. Confers
# nothing; ACL still lives on permission_parent.
MENTIONS = "mentions"


class Edge(BaseModel):
    """A directed relationship between two nodes.

    Edges carry no permission semantics whatsoever. Access is decided by
    `permission_parent_entity_id`, by direct grants, and by the grant tables; an
    edge is traversable exactly when the node at its far end is visible, which
    the visibility set already answers without this table being consulted.
    """

    model_config = ConfigDict(frozen=True)

    from_entity_id: str
    to_entity_id: str
    relation: str

    # The source node an extractor read to justify this edge. NULL for every
    # structural edge, which is every edge a connector mints.
    #
    # It is provenance, not access: the audit trail behind an inferred claim,
    # and the handle that retracts the claim when the document behind it is
    # deleted. Visibility is still decided by the endpoints.
    source_entity_id: str | None = None


class _NodeBase(BaseModel):
    """Fields every node has, whether mirrored or inferred."""

    model_config = ConfigDict(frozen=True)

    # '{source}:{native_id}' for mirrored nodes, '{type}:{identity}' for
    # inferred ones. Stable across content edits: an edit advances
    # content_version, never identity.
    entity_id: str

    body: str = ""

    # Source semantics, both: when the thing was created and last edited in the
    # source. Never ingestion time.
    created_at: datetime
    updated_at: datetime

    # Monotonic per entity. The guarded upsert applies a row only when this
    # exceeds the stored value, making duplicate and out-of-order delivery safe.
    # Compared lexicographically, so sources must zero-pad or use ISO-8601.
    content_version: str

    # Typed payload, dumped to jsonb. The model is the type's registry spec.
    payload: dict[str, Any] = Field(default_factory=dict)


class Node(_NodeBase):
    """One mirrored thing in the graph: a Slack message, a Drive file."""

    node_type: NodeType

    # The node whose grants govern this one. Access inherits along it and
    # nothing else, so the field is named for what it controls rather than for a
    # generic tree relationship. Holds an entity id here; the store maps it to a
    # uuid, minting an unmaterialized row if the parent has not arrived yet.
    permission_parent_entity_id: str | None = None

    @model_validator(mode="after")
    def _payload_matches_type(self) -> Node:
        from core.registry import NODE_TYPES

        if self.node_type in IDENTITY_ONLY:
            raise ValueError(f"{self.node_type} is identity-only and has no node")
        spec = NODE_TYPES.get(self.node_type)
        if spec is None:
            raise ValueError(f"unknown node type {self.node_type}")
        spec.payload_model.model_validate(self.payload)
        return self


class SemanticNode(_NodeBase):
    """One inferred thing in the graph, in one of exactly two shapes.

    **An entity** — a person, a project, a task — carries identity and nothing
    else, and has **no permission parent and no grants**. It is reachable from
    every document it was extracted from, and a single parent pointer can only
    express one of them, so its visibility is *derived*: an entity is visible
    exactly when a fact about it is (`query.visibility`). Pinning a person to
    the first channel that mentioned them would make every later public mention
    inherit that channel's ACL, which is the failure this shape exists to avoid;
    copying each channel's audience onto her instead would leave those copies
    behind when the grant was revoked.

    **A fact** — one claim read out of one document — carries the text and
    **does** have a permission parent: the document itself. It therefore
    inherits access along exactly the path a mirrored node does, with no
    materialisation and no second rule for the kernel to know about.

    That split is what makes the layer permission-correct. Identity is a name
    the workspace may know; content is a claim only that document's readers may
    read. Both live in `node`, and the presence of a permission parent is what
    distinguishes them.
    """

    node_type: str

    # Set on facts, never on entities. See above: the two shapes are the whole
    # design, and this field is the thing that tells them apart.
    permission_parent_entity_id: str | None = None

    @model_validator(mode="after")
    def _payload_matches_type(self) -> SemanticNode:
        from core.registry import SEMANTIC_TYPES

        spec = SEMANTIC_TYPES.get(self.node_type)
        if spec is None:
            raise ValueError(
                f"{self.node_type!r} is not a declared semantic type; "
                f"the active config declares {sorted(SEMANTIC_TYPES)}"
            )
        spec.payload_model.model_validate(self.payload)
        return self
