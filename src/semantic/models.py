"""What one extraction pass produces, and the job that triggers it.

`SemanticWrite` is the buffered result of a loop, not a single response. The
extractor resolves entity ids as it goes — `resolve` is a read — and accumulates
intent, so nothing is written until the loop finishes. A run that fails halfway
leaves the graph exactly as it was.

Facts are not addressed by the model. It names a subject entity and a statement;
the fact's own entity id is minted here from the (document, entity) pair, so a
re-extraction of the same document rewrites the same rows rather than
accumulating beside them. That determinism is what lets reconciliation be a
delete-by-parent followed by a rewrite, with nothing recording what the previous
pass concluded.
"""

from __future__ import annotations

import hashlib

from pydantic import BaseModel, ConfigDict, Field

from core.graph import Edge, SemanticNode
from core.message import ChangeKind

MAX_ENTITIES = 24
MAX_FACTS = 48
MAX_LINKS = 48


class SemanticJob(BaseModel):
    """One unit of semantic work, as it travels on `stream:semantic`.

    Deliberately thin. The body is not carried: the worker re-reads the node, so
    a job queued while two more edits landed extracts the text as it is *now*
    rather than as it was when the job was written. That makes out-of-order
    delivery converge instead of race, and keeps a large Drive document out of a
    Redis stream entry.

    `change` is what makes this more than an extraction queue. A created
    document is extracted; an updated one has to have its previous conclusions
    retracted first, and a deleted one has to have them retracted and not
    replaced. All three arrive here, and the worker branches on this field.
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str
    node_type: str
    content_version: str
    change: ChangeKind = ChangeKind.CREATED
    # Structural edges the source generator just wrote. Context for the prompt —
    # a reply knowing it is a reply — never something the extractor reproduces.
    relations: list[str] = Field(default_factory=list)


class ResolvedEntity(BaseModel):
    """An entity the extractor decided this document is about.

    `entity_id` is real: resolution ran against the graph before this was
    created, so an existing person was found rather than duplicated.
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str
    node_type: str
    identity: dict[str, str] = Field(default_factory=dict)
    is_new: bool = True


class DraftFact(BaseModel):
    """One claim, before it is folded into its entity's fact node."""

    model_config = ConfigDict(frozen=True)

    subject_entity_id: str
    statement: str


class DraftLink(BaseModel):
    """One relation between two entities, as the extractor drew it."""

    model_config = ConfigDict(frozen=True)

    from_entity_id: str
    relation: str
    to_entity_id: str


def fact_entity_id(source_entity_id: str, subject_entity_id: str) -> str:
    """`fact:<digest>` for one (document, entity) pair.

    One node per pair, not per claim. Everything a document says about an entity
    is one text dump, which is what an agent actually wants to read and what
    embeds usefully — forty one-line nodes hanging off a person is a worse
    answer to "what do we know about Jane" than four paragraphs.

    Deterministic, so a re-extraction of the same document rewrites the same row
    rather than accumulating beside it, and a duplicate delivery is idempotent.
    Hashed rather than embedded whole: both ids are long and this one ends up in
    citations and edge rows.
    """
    digest = hashlib.sha256(
        f"{source_entity_id}\x00{subject_entity_id}".encode()
    ).hexdigest()[:24]
    return f"fact:{digest}"


class SemanticWrite(BaseModel):
    """One resolved pass, ready to apply. One source document, one write."""

    model_config = ConfigDict(frozen=True)

    source_entity_id: str
    content_version: str
    config_version: int

    # Identity-only, and with no permission parent at all: an entity is visible
    # exactly when a fact about it is, which `query.visibility` derives.
    entities: list[SemanticNode] = Field(default_factory=list)
    # Content, permission_parent = the source document.
    facts: list[SemanticNode] = Field(default_factory=list)
    # `fact -about-> entity`, plus whatever entity-to-entity relations the
    # extractor drew.
    edges: list[Edge] = Field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.entities and not self.facts and not self.edges
