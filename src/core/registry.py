"""Node type registry: payload model, query view, and type-scoped indexes.

Two registries, one spec shape.

`NODE_TYPES` is the **source** vocabulary — the closed `NodeType` enum in
`core.types`, one entry per thing a connector mints. Adding one is a visible
edit here (enum + spec). Payload models live in `core.payloads`; connectors do
not declare types.

`SEMANTIC_TYPES` is the **inferred** vocabulary — `person`, `task`, whatever the
active `semantic_config` declares. It is empty until `register_semantic` is
called at boot, because its contents live in the database rather than in this
file. Core still imports nothing: `semantic.registry` reads the config and
pushes the compiled specs down here.

Both produce a `NodeTypeSpec`, which is the whole point of the arrangement.
Views, partial indexes, the agent's query compiler, and the schema digest in its
system prompt all consume specs and none of them care which registry a spec came
from — so a semantic type is queryable on exactly the same path as a Slack
message, with no second code path to keep correct.

Views and partial indexes are not SQL migrations. Each spec compiles them, and
migrate reapplies them on every boot so a payload-field change updates the
overlay without a ledgered file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel

from core.payloads import (
    DriveDrivePayload,
    DriveFilePayload,
    DriveFolderPayload,
    NotionDatabasePayload,
    NotionDataSourcePayload,
    NotionPagePayload,
    SlackChannelPayload,
    SlackMessagePayload,
)
from core.types import IDENTITY_ONLY, NodeType

__all__ = [
    "NODE_COLUMNS",
    "NODE_TYPES",
    "RESERVED_RELATION_NAMES",
    "SEMANTIC_TYPES",
    "NodeTypeSpec",
    "PayloadIndex",
    "all_specs",
    "is_semantic",
    "register_semantic",
    "safe_ident",
    "spec_for",
]


# Shared `node` columns every type view projects. Payload fields follow.
#
# `fts` is projected deliberately. It is the STORED generated tsvector that
# `node_fts_idx` is built on, and the planner will not rewrite an inline
# `to_tsvector('english', body)` into that column — so a view without it forces
# every text search to recompute the vector per row and skip the index. With it,
# `WHERE fts @@ plainto_tsquery(...)` on any type view is an index scan.
NODE_COLUMNS = (
    "id",
    "entity_id",
    "node_type",
    "permission_parent_id",
    "body",
    "fts",
    # The raw jsonb alongside its exploded fields. Readers project a node's
    # label and its native source ids (`channel_id`+`ts`, `page_id`, `file_id`)
    # from the whole object rather than from a per-type column list, so a view
    # that only exposed the individual fields would force every caller to join
    # back to `node` for what it just read.
    "payload",
    "created_at",
    "updated_at",
    "deleted_at",
    "content_version",
)


@dataclass(frozen=True)
class PayloadIndex:
    """Partial btree on payload keys, scoped to one node type.

    Lives on `node`, not the view (views cannot hold indexes). `columns` are
    payload field names, compiled to `(payload->>'col')` with
    `WHERE node_type = '…'`.
    """

    name: str
    columns: tuple[str, ...]


@dataclass(frozen=True)
class NodeTypeSpec:
    """One queryable node type. Identity-only types are not entries.

    `node_type` is a `NodeType` for source specs and a plain string for semantic
    ones. Everything downstream stringifies it, and `NodeType` is a `StrEnum`,
    so the two are interchangeable at every use site — the union is stated here
    rather than hidden behind a cast.
    """

    node_type: NodeType | str
    payload_model: type[BaseModel]
    indexes: tuple[PayloadIndex, ...] = field(default_factory=tuple)
    # Names to drop on apply. Used when an index moves onto this spec from a
    # retired SQL file; CREATE INDEX IF NOT EXISTS cannot rename.
    drop_indexes: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        fields = self.payload_model.model_fields
        # A payload field sharing a name with a projected node column would
        # compile to a view with two columns of that name, which Postgres
        # rejects at boot with a message that points at the view rather than at
        # the model. Fail here, where the cause is visible.
        clash = (set(fields) - {"kind"}) & set(NODE_COLUMNS)
        if clash:
            raise RuntimeError(
                f"{self.node_type}: payload field(s) {sorted(clash)} shadow "
                f"node columns"
            )
        for name in self.drop_indexes:
            if not safe_ident(name):
                raise RuntimeError(f"{self.node_type}: bad drop index {name!r}")
        for idx in self.indexes:
            if not safe_ident(idx.name):
                raise RuntimeError(f"{self.node_type}: bad index name {idx.name!r}")
            if not idx.columns:
                raise RuntimeError(f"{self.node_type}: index {idx.name} has no columns")
            for col in idx.columns:
                if col not in fields or col == "kind":
                    raise RuntimeError(
                        f"{self.node_type}: index {idx.name} column {col!r} "
                        f"is not a payload field"
                    )
                if not safe_ident(col):
                    raise RuntimeError(
                        f"{self.node_type}: index {idx.name} bad column {col!r}"
                    )

    @property
    def view_name(self) -> str:
        name = str(self.node_type).replace(":", "_")
        if not safe_ident(name):
            raise RuntimeError(f"{self.node_type}: unsound view name {name!r}")
        return name

    def get_view(self) -> str:
        """CREATE VIEW SQL: shared node columns, then each payload field."""
        cols = [f"    {c}" for c in NODE_COLUMNS]
        for name, info in self.payload_model.model_fields.items():
            if name == "kind":
                continue
            if not safe_ident(name):
                raise RuntimeError(f"{self.node_type}: bad payload field {name!r}")
            cols.append(f"    {_payload_sql(name, info.annotation)}")
        joined = ",\n".join(cols)
        node_type = str(self.node_type).replace("'", "''")
        return (
            f"CREATE VIEW {self.view_name} AS\n"
            f"SELECT\n{joined}\n"
            f"FROM node\n"
            f"WHERE node_type = '{node_type}'"
        )

    def get_indexes(self) -> tuple[str, ...]:
        """CREATE INDEX IF NOT EXISTS statements for this type's lookup keys."""
        node_type = str(self.node_type).replace("'", "''")
        stmts = []
        for idx in self.indexes:
            exprs = ", ".join(f"(payload->>'{col}')" for col in idx.columns)
            stmts.append(
                f"CREATE INDEX IF NOT EXISTS {idx.name}\n"
                f"    ON node ({exprs})\n"
                f"    WHERE node_type = '{node_type}'"
            )
        return tuple(stmts)


def safe_ident(name: str) -> bool:
    return bool(name) and name.isidentifier() and not name.startswith("_")


def _unwrap(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _payload_sql(name: str, annotation: Any) -> str:
    inner = _unwrap(annotation)
    origin = get_origin(inner)
    if inner is bool:
        return f"(payload->>'{name}')::boolean AS {name}"
    if inner is int:
        return f"(payload->>'{name}')::bigint AS {name}"
    if inner is str:
        return f"payload->>'{name}' AS {name}"
    if origin is list:
        return (
            f"ARRAY(SELECT jsonb_array_elements_text(payload->'{name}')) "
            f"AS {name}"
        )
    if origin is dict:
        return f"payload->'{name}' AS {name}"
    raise RuntimeError(f"no view column mapping for {name}: {annotation}")


_SPECS: tuple[NodeTypeSpec, ...] = (
    NodeTypeSpec(NodeType.SLACK_CHANNEL, SlackChannelPayload),
    NodeTypeSpec(
        NodeType.SLACK_MESSAGE,
        SlackMessagePayload,
        indexes=(
            PayloadIndex("slack_message_channel_ts", ("channel_id", "ts")),
            PayloadIndex(
                "slack_message_channel_thread_ts",
                ("channel_id", "thread_ts", "ts"),
            ),
        ),
        drop_indexes=("node_slack_channel_ts",),
    ),
    NodeTypeSpec(NodeType.DRIVE_DRIVE, DriveDrivePayload),
    NodeTypeSpec(NodeType.DRIVE_FOLDER, DriveFolderPayload),
    NodeTypeSpec(NodeType.DRIVE_FILE, DriveFilePayload),
    NodeTypeSpec(NodeType.NOTION_DATABASE, NotionDatabasePayload),
    NodeTypeSpec(NodeType.NOTION_DATA_SOURCE, NotionDataSourcePayload),
    NodeTypeSpec(NodeType.NOTION_PAGE, NotionPagePayload),
)

NODE_TYPES: dict[NodeType, NodeTypeSpec] = {spec.node_type: spec for spec in _SPECS}

_covered = set(NODE_TYPES) | IDENTITY_ONLY
if _covered != set(NodeType):
    raise RuntimeError(
        f"NodeType registry gap: extra={_covered - set(NodeType)} "
        f"missing={set(NodeType) - _covered}"
    )


# --------------------------------------------------------------- semantic --

# Filled by `semantic.registry.register` at boot, from the active
# `semantic_config`. Empty in any process that has not loaded it — the poller
# and the ingest worker never do, because neither reads semantic types.
SEMANTIC_TYPES: dict[str, NodeTypeSpec] = {}

# Names a semantic type may not take. The source vocabulary is excluded because
# a collision would make `node_type` ambiguous; the rest are the tables and
# views a compiled `CREATE VIEW person` would collide with, which fails at boot
# with a message about the view rather than about the config that caused it.
RESERVED_TYPE_NAMES: frozenset[str] = frozenset(
    {
        "node",
        "edge",
        "identity",
        "membership",
        "access",
        "access_level",
        "node_chunk",
        "action",
        "action_invocation",
        "semantic_config",
        "schema_migrations",
    }
)

# Relations the source layer owns. The semantic extractor names its own
# relations freely — which relations matter between entities is exactly what
# nobody knows in advance — but it may not reuse one of these, or
# `follow(node, 'in')` would mean two different things depending on which layer
# wrote the row, and the agent's prompt documents exactly one of them.
RESERVED_RELATION_NAMES: frozenset[str] = frozenset(
    {"in", "in_channel", "in_thread", "next"}
)


def register_semantic(specs: dict[str, NodeTypeSpec]) -> None:
    """Install the semantic vocabulary for this process.

    Replaces rather than merges: the active config is one document, and a
    partial update would leave a retired type queryable until restart.

    Validated before anything is mutated, so a rejected config leaves the
    previous vocabulary intact rather than half-swapped.
    """
    clash = set(specs) & {str(t) for t in NodeType}
    if clash:
        raise RuntimeError(f"semantic type(s) shadow source types: {sorted(clash)}")
    reserved = set(specs) & RESERVED_TYPE_NAMES
    if reserved:
        raise RuntimeError(f"semantic type(s) use reserved names: {sorted(reserved)}")
    SEMANTIC_TYPES.clear()
    SEMANTIC_TYPES.update(specs)


def is_semantic(node_type: str) -> bool:
    return node_type in SEMANTIC_TYPES


def spec_for(node_type: str) -> NodeTypeSpec | None:
    """The spec for a type from either registry, or None if it is neither.

    The single lookup every reader should use. `NODE_TYPES` is keyed by enum
    member and `SEMANTIC_TYPES` by string; both are reachable with a string here
    because `NodeType` is a `StrEnum` and hashes as its value.
    """
    spec = NODE_TYPES.get(node_type)  # type: ignore[arg-type]
    if spec is not None:
        return spec
    return SEMANTIC_TYPES.get(node_type)


def all_specs() -> dict[str, NodeTypeSpec]:
    """Every queryable type, source first. Order is what the agent's schema
    digest is rendered in, so it stays deterministic."""
    out: dict[str, NodeTypeSpec] = {str(k): v for k, v in NODE_TYPES.items()}
    out.update(SEMANTIC_TYPES)
    return out
