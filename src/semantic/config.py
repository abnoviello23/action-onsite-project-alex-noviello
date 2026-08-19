"""The declared ontology: which entity types exist and what they represent.

Config, not code. The types an organisation cares about — `person`, `project`,
`task` — are a product decision that changes on a different clock from the
ingest pipeline, so they live in `semantic_config` as a versioned jsonb document
an API or a future ontology agent can append to. Connectors keep minting source
nodes in Python; nothing here touches that path.

**A declared type carries identity, never content.** `person` has a name, an
email, a Slack id — the things that decide *which* person this is — and nothing
else. What is known *about* Jane lives in `fact` nodes hanging off her, each one
inheriting access from the document it came from. That split is the whole reason
the layer is permission-correct: an entity is a name everyone may know, and a
fact is a claim only some people may read. Putting `role` or `status` on the
entity would put one audience's content on a node the whole workspace can see.

So `identity` is deliberately small and deliberately hard to widen: every field
declared here becomes a column the entity carries in the clear.

Relations between entities are **not** declared. The extractor draws them as it
finds them, because which relations matter is exactly what nobody knows in
advance — and unlike a payload field, an edge between two identity-only nodes
leaks nothing on its own.

The payoff for validating this into real specs is that an entity type costs no
new query code. `to_node_spec` compiles each declared type into the same
`NodeTypeSpec` a Slack message uses, so migrate builds it a view and its partial
indexes, `query.compile` type-checks predicates against its fields, and the
agent's schema digest lists it — all on the existing path.

The boundary that creates: **views are compiled at boot**. Editing a type's
description or prompt takes effect within one `ActiveConfig` TTL; adding or
removing an identity *field* needs a restart, because a view is DDL.
"""

from __future__ import annotations

import json
from typing import Any, Literal

import asyncpg
from pydantic import BaseModel, ConfigDict, Field, create_model, model_validator

from core.graph import MENTIONS
from core.registry import (
    NODE_COLUMNS,
    NodeTypeSpec,
    PayloadIndex,
    safe_ident,
)
from core.types import NodeType

# Identity fields are scalar by construction. A list cannot be an identity key —
# "which of these values is this entity" has no answer — and a nested object on
# an identity-only node is content wearing a disguise.
_ANNOTATIONS: dict[str, Any] = {
    "string": (str | None, None),
    "int": (int | None, None),
}

FieldType = Literal["string", "int"]

MAX_TYPES = 24
# Small on purpose. Every field here is public to everyone who can see the
# entity, so the pressure should always be to move one into a fact instead.
MAX_IDENTITY_FIELDS = 8


class ConfigError(ValueError):
    """The ontology is not usable. Raised at load, so a bad config fails the
    process at boot rather than one extraction at a time."""


class SemanticField(BaseModel):
    """One identity column on an entity type."""

    model_config = ConfigDict(frozen=True)

    name: str
    type: FieldType = "string"
    # Shown to the extraction model. This is the whole specification of what
    # belongs in the field, so a vague one produces vague identities.
    description: str = ""


class SemanticEntityType(BaseModel):
    """A declared entity type: what it represents, and what identifies one."""

    model_config = ConfigDict(frozen=True)

    name: str

    # What this type *is*, in the organisation's terms. Read by the extractor
    # when it decides whether something in a document is one of these, and by
    # the retrieval agent when it decides whether to query this type at all.
    # It is the field the user edits to change what the system pays attention
    # to, so it earns its place as prose rather than as a label.
    description: str

    # How to recognise one in a document, and how to identify it once found.
    extract_prompt: str

    # The entity's whole payload. Identity, never content.
    identity: list[SemanticField] = Field(min_length=1)

    # The upsert key cascade, in order, over `identity` field names. The first
    # key an extraction populated decides `entity_id`, so the order is a
    # statement about which identifiers are trustworthy: a Slack user id is
    # issued by Slack and collides with nothing; a display name is neither.
    identity_keys: list[str] = Field(min_length=1)

    # Source node types this may be extracted from. A job whose node type is not
    # listed skips this type entirely, which is what keeps a Drive folder from
    # being run through the project prompt.
    source_types: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def _check(self) -> SemanticEntityType:
        if not safe_ident(self.name):
            raise ConfigError(
                f"entity type {self.name!r} is not a valid identifier; it "
                f"becomes a view name"
            )
        if self.name == FACT_TYPE:
            raise ConfigError(
                f"{FACT_TYPE!r} is the built-in fact type and cannot be declared"
            )

        names = [f.name for f in self.identity]
        if len(set(names)) != len(names):
            raise ConfigError(f"{self.name}: duplicate identity field names")
        if len(names) > MAX_IDENTITY_FIELDS:
            raise ConfigError(
                f"{self.name}: over {MAX_IDENTITY_FIELDS} identity fields. "
                f"Identity is what names the entity; anything else is a fact."
            )
        for name in names:
            if not safe_ident(name):
                raise ConfigError(f"{self.name}: field {name!r} is not an identifier")

        # A payload field named `body` or `payload` would compile to a view with
        # two columns of that name. Postgres rejects that at boot with a message
        # about the view, so it is caught here where the cause is visible.
        shadow = set(names) & set(NODE_COLUMNS)
        if shadow:
            raise ConfigError(
                f"{self.name}: field(s) {sorted(shadow)} shadow node columns"
            )

        missing = [k for k in self.identity_keys if k not in names]
        if missing:
            raise ConfigError(
                f"{self.name}: identity_keys {missing} are not identity fields"
            )

        known = {str(t) for t in NodeType}
        unknown = sorted(set(self.source_types) - known)
        if unknown:
            raise ConfigError(f"{self.name}: unknown source_types {unknown}")
        return self

    def to_node_spec(self) -> NodeTypeSpec:
        """Compile to the same spec shape a source type uses.

        The payload model is synthesised rather than written by hand because the
        field list is data. Everything downstream reads `model_fields`, which a
        `create_model` result populates identically to a declared class.
        """
        fields: dict[str, Any] = {
            f.name: _ANNOTATIONS[f.type] for f in self.identity
        }
        model = create_model(
            f"{self.name.title()}Identity",
            __config__=ConfigDict(frozen=True),
            **fields,
        )
        return NodeTypeSpec(
            node_type=self.name,
            payload_model=model,
            # The identity cascade is also the read path: resolution looks an
            # entity up by these keys on every extraction that mentions it, so
            # they are the one thing worth an index.
            indexes=(
                PayloadIndex(
                    f"semantic_{self.name}_identity", tuple(self.identity_keys)
                ),
            ),
        )


# ------------------------------------------------------------------ facts --

# The one built-in semantic type. Not configurable: its shape is what makes the
# permission model work, and a user who could redefine it could break that.
FACT_TYPE = "fact"

# fact -> entity. The relation the retrieval agent follows inward from an entity
# to reach what is known about it.
FACT_RELATION = "about"

# document -> entity, re-exported from `core.graph` rather than redeclared.
#
# The source layer already uses this relation for a document naming another
# ingested thing (a Slack permalink, a Notion @-mention), and the semantic layer
# uses it for a document naming an entity. One string, one constant: they mean
# the same thing to a reader — this refers to that — and two definitions of it
# would drift.
#
# Without such an edge a Slack message would have no link into the semantic
# layer at all: a fact's tie to its document is `permission_parent_id`, which is
# not an edge row, so `neighbors(message)` would return only `in_channel` and
# friends and the person the message is about would be unreachable.
MENTIONS_RELATION = MENTIONS


class FactPayload(BaseModel):
    """What a fact carries besides its text.

    Deliberately two fields. Everything this document says about the entity is
    the node's `body` — one text dump, full-text indexed, embeddable, and
    previewable by every reader the query layer already has. Structuring it
    further would be a second ontology to maintain, and the reader that matters
    is a language model.
    """

    model_config = ConfigDict(frozen=True)

    # The entity this is about. Also the seed for derived entity visibility, so
    # this is the one payload field in the system that is load-bearing for
    # access; see `query.visibility`.
    subject: str | None = None
    # The document this was read from. Equal to the permission parent, and
    # stored anyway so a citation can name it without a second lookup.
    source: str | None = None


FACT_SPEC = NodeTypeSpec(
    node_type=FACT_TYPE,
    payload_model=FactPayload,
    indexes=(
        # Entity -> its facts, the hot path for local search around an entity.
        PayloadIndex("semantic_fact_subject", ("subject",)),
    ),
)


# ----------------------------------------------------------------- config --


class SemanticConfig(BaseModel):
    """One version of the ontology."""

    model_config = ConfigDict(frozen=True)

    version: int = 1
    types: list[SemanticEntityType] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> SemanticConfig:
        names = [t.name for t in self.types]
        if len(set(names)) != len(names):
            raise ConfigError("duplicate entity type names")
        if len(names) > MAX_TYPES:
            raise ConfigError(f"over {MAX_TYPES} entity types")
        return self

    @property
    def type_names(self) -> frozenset[str]:
        return frozenset(t.name for t in self.types)

    def type_for(self, name: str) -> SemanticEntityType | None:
        return next((t for t in self.types if t.name == name), None)

    def source_types(self) -> list[str]:
        """Every mirrored type any declared entity is extracted from.

        The set a backfill may re-offer. Derived nodes — entities and facts —
        are never in it: nothing is extracted *from* a fact, and offering one
        would cost a consumer round trip to conclude exactly that.
        """
        return sorted({s for t in self.types for s in t.source_types})

    def types_from(self, source_type: str) -> list[SemanticEntityType]:
        """Types extractable from this source node type, in declared order."""
        return [t for t in self.types if source_type in t.source_types]

    def node_specs(self) -> dict[str, NodeTypeSpec]:
        """Every semantic type, entity types plus the built-in fact type."""
        specs = {t.name: t.to_node_spec() for t in self.types}
        specs[FACT_TYPE] = FACT_SPEC
        return specs


# ------------------------------------------------------------ persistence --

_LOAD_ACTIVE = """
SELECT version, config
FROM semantic_config
WHERE status = 'active'
ORDER BY version DESC
LIMIT 1
"""

_NEXT_VERSION = "SELECT coalesce(max(version), 0) + 1 FROM semantic_config"

_INSERT = """
INSERT INTO semantic_config (version, status, config)
VALUES ($1, 'active', $2::jsonb)
RETURNING version
"""

_RETIRE = "UPDATE semantic_config SET status = 'retired' WHERE status = 'active'"


def _document(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str | bytes | bytearray):
        return json.loads(raw)
    return dict(raw or {})


async def load_active(conn: asyncpg.Connection) -> SemanticConfig | None:
    """The active ontology, or None if none has been seeded."""
    row = await conn.fetchrow(_LOAD_ACTIVE)
    if row is None:
        return None
    return SemanticConfig.model_validate(
        {**_document(row["config"]), "version": row["version"]}
    )


async def publish(conn: asyncpg.Connection, config: SemanticConfig) -> int:
    """Retire the active version and install this one. Returns the new version.

    Append-only: the previous document stays readable, so an entity extracted
    under it can still be explained. One transaction, because a retire without
    an insert would leave the system with no ontology at all.
    """
    async with conn.transaction():
        version = await conn.fetchval(_NEXT_VERSION)
        await conn.execute(_RETIRE)
        # Dumped to a dict, not a string: the connection's jsonb codec does the
        # serialising, and doing it here too stores a jsonb string holding JSON
        # text rather than a JSON object.
        document = config.model_dump(mode="json", exclude={"version"})
        return await conn.fetchval(_INSERT, version, document)
