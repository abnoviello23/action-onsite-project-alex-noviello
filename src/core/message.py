"""Work stream envelopes, and the graph write a generator produces.

Pollers publish an `Envelope` keyed by `NodeType`. Workers dispatch a generator
for that type and apply the `GraphWrite` in one transaction.

The JSON field is `node_type`. `payload_type` is still accepted on read so
in-flight stream messages from before the rename parse.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from core.access import AccessGrant, Identity, Membership
from core.graph import Edge, Node
from core.types import IDENTITY_ONLY, NodeType


class ChangeKind(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    DELETED = "deleted"


class RosterMode(StrEnum):
    """How `apply` treats memberships on this write."""

    NONE = "none"
    CHILDREN = "children"
    PARENT = "parent"


class Envelope(BaseModel):
    """What a poller puts on the work stream: source facts, not graph rows."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    node_type: NodeType = Field(
        validation_alias=AliasChoices("node_type", "payload_type")
    )
    entity_id: str
    partition_key: str
    change: ChangeKind = ChangeKind.UPDATED
    payload: dict = Field(default_factory=dict)


class GraphWrite(BaseModel):
    """One generator's output: everything one change implies about the graph."""

    model_config = ConfigDict(frozen=True, populate_by_name=True)

    node_type: NodeType = Field(
        validation_alias=AliasChoices("node_type", "payload_type")
    )
    entity_id: str
    change: ChangeKind = ChangeKind.UPDATED
    roster: RosterMode = RosterMode.NONE

    node: Node | None = None
    edges: list[Edge] = Field(default_factory=list)
    retract_edges: list[Edge] = Field(default_factory=list)
    grants: list[AccessGrant] = Field(default_factory=list)
    identities: list[Identity] = Field(default_factory=list)
    memberships: list[Membership] = Field(default_factory=list)

    @model_validator(mode="after")
    def _contracts(self) -> GraphWrite:
        if self.node is not None and self.node.node_type != self.node_type:
            raise ValueError(
                f"node.node_type {self.node.node_type} != write {self.node_type}"
            )
        if self.node_type in IDENTITY_ONLY:
            if self.node is not None:
                raise ValueError(f"{self.node_type} is identity-only and has no node")
            if self.edges or self.retract_edges or self.grants:
                raise ValueError(f"{self.node_type} writes are roster only")
        elif self.change is not ChangeKind.DELETED and self.node is None:
            raise ValueError(f"{self.node_type} writes need a node unless deleted")
        return self
