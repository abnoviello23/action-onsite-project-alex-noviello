"""What the session graph hands back, for the model and for the HTTP response.

Two shapes, and the split is deliberate. A `NodeSummary` is what a candidate
looks like in a list: enough to decide whether to open it, and nothing more.
`NodeDetail` is what opening it returns. Candidate lists routinely run to
dozens of rows, and a full body each would spend the orchestrator's context on
material it is about to discard.

Every projection here assumes the caller has already filtered for visibility.
Nothing in this module consults access — see `query.visibility`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from core.labels import PREVIEW_MAX_CHARS, clip, label_of, native_keys

Direction = Literal["out", "in"]


class NodeSummary(BaseModel):
    """One candidate. Cheap to produce, cheap to read."""

    model_config = ConfigDict(frozen=True)

    entity_id: str
    node_type: str | None
    label: str
    preview: str = ""
    updated_at: datetime | None = None
    # Source-side ids for handoff to a write tool. Carried on every summary so a
    # citation can be acted on without a second round trip to `get`.
    native: dict[str, str] = Field(default_factory=dict)
    # Cosine distance for semantic hits, absent for type-query hits. Lower is
    # closer; it is a ranking aid and not a confidence.
    distance: float | None = None


class Neighbor(BaseModel):
    """An incident edge, with the direction it was traversed in.

    Direction is part of the meaning, not metadata: `in` outward is "my
    container" and `in` inward is "my children"; `mentions` inward is the
    backlink set. Collapsing the two would make the relation name ambiguous.
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str
    node_type: str | None
    label: str
    preview: str = ""
    relation: str
    direction: Direction


class NeighborPage(BaseModel):
    """Incident edges, and whether they are all of them.

    `complete` exists because the alternative is a silent wrong answer. A walker
    that asks for a node's neighbours, receives the first 25 of 40, and cannot
    tell the difference will summarise from 60% of the evidence and cite it with
    full confidence — and "there is nothing about that" becomes indistinguishable
    from "I stopped looking". `query_type` and `semantic_search` already say when
    they were capped; this is the same promise on the third retrieval path.

    The count is post-visibility, so it discloses nothing about hidden peers: it
    reports on what this principal could have been shown, not on what exists.
    """

    model_config = ConfigDict(frozen=True)

    neighbors: list[Neighbor] = Field(default_factory=list)
    complete: bool = True

    def __len__(self) -> int:
        return len(self.neighbors)

    def __iter__(self):  # type: ignore[override]
        return iter(self.neighbors)


class NodeDetail(BaseModel):
    """A node opened in full."""

    model_config = ConfigDict(frozen=True)

    entity_id: str
    node_type: str | None
    label: str
    body: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    native: dict[str, str] = Field(default_factory=dict)


def to_summary(row: asyncpg.Record, *, distance: float | None = None) -> NodeSummary:
    payload = row["payload"] or {}
    body = row["body"] or ""
    return NodeSummary(
        entity_id=row["entity_id"],
        node_type=row["node_type"],
        label=label_of(payload, body, row["entity_id"]),
        preview=clip(" ".join(body.split()), PREVIEW_MAX_CHARS),
        updated_at=row["updated_at"],
        native=native_keys(row["node_type"], payload),
        distance=distance,
    )


def to_neighbor(row: asyncpg.Record) -> Neighbor:
    payload = row["payload"] or {}
    body = row["body"] or ""
    return Neighbor(
        entity_id=row["entity_id"],
        node_type=row["node_type"],
        label=label_of(payload, body, row["entity_id"]),
        preview=clip(" ".join(body.split()), PREVIEW_MAX_CHARS),
        relation=row["relation"],
        direction=row["direction"],
    )


def to_detail(row: asyncpg.Record) -> NodeDetail:
    payload = row["payload"] or {}
    body = row["body"] or ""
    return NodeDetail(
        entity_id=row["entity_id"],
        node_type=row["node_type"],
        label=label_of(payload, body, row["entity_id"]),
        body=body,
        payload=payload,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        native=native_keys(row["node_type"], payload),
    )
