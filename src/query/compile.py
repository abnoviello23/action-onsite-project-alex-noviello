"""The agent's query language, and its compiler to view SQL.

The model never writes SQL. It emits a `TypeQuery` — a node type plus a list of
`Predicate`s — and this module compiles it against the type's view, which
`migrate` regenerates from `NodeTypeSpec` on every boot. Both halves are
allowlisted from the registry: an unknown type or a field that is not a real
view column is a rejection, not a query. Values are always bound parameters.

Source and semantic types compile identically. `spec_for` resolves a name
against both registries and hands back the same spec shape, so `person` accepts
predicates on `email` for exactly the reason `slack:message` accepts them on
`channel_id` — there is no second code path here for inferred types.

Column names are interpolated into the SQL text, which is only sound because
they cannot originate with the caller: every name here comes from
`payload_model.model_fields` or `NODE_COLUMNS`, both of which the registry has
already checked with `safe_ident`. The assertion in `_column_sql` keeps that
invariant local rather than assumed.

What this deliberately cannot express: joins, subqueries, ordering, aggregates,
and filters on the unindexed jsonb maps (`properties`, `property_schema`). Those
maps stay readable in results and are reachable through `body` text search or
`semantic_search`; letting the model filter on them invites a full scan of the
largest table in the database to answer a question the FTS index already covers.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import UnionType
from typing import Any, Union, get_args, get_origin

from pydantic import BaseModel, ConfigDict, Field

from core.registry import all_specs, safe_ident, spec_for

# Per-call ceiling. An unfiltered scan of `slack:message` is a bug in the tool
# arguments, not a supported query, and the orchestrator is told to resolve a
# container by name and bind its id first.
MAX_QUERY_LIMIT = 50
DEFAULT_QUERY_LIMIT = 20

MAX_PREDICATES = 8
MAX_IN_VALUES = 50
# Longest ILIKE fragment. The trigram index stops helping well before this;
# beyond it the pattern is a description, not a filter.
MAX_PATTERN_CHARS = 200


class Op(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    IN = "in"
    ILIKE = "ilike"
    FTS = "fts"
    HAS = "has"
    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    IS_NULL = "is_null"


class Kind(StrEnum):
    """SQL shape of a view column, which decides the ops it accepts."""

    TEXT = "text"
    INT = "int"
    BOOL = "bool"
    TIMESTAMP = "timestamp"
    TEXT_ARRAY = "text[]"
    JSON = "json"


_OPS_BY_KIND: dict[Kind, frozenset[Op]] = {
    Kind.TEXT: frozenset(
        {Op.EQ, Op.NEQ, Op.IN, Op.ILIKE, Op.IS_NULL}
    ),
    Kind.INT: frozenset(
        {Op.EQ, Op.NEQ, Op.IN, Op.GT, Op.GTE, Op.LT, Op.LTE, Op.IS_NULL}
    ),
    Kind.BOOL: frozenset({Op.EQ, Op.IS_NULL}),
    Kind.TIMESTAMP: frozenset(
        {Op.EQ, Op.NEQ, Op.GT, Op.GTE, Op.LT, Op.LTE, Op.IS_NULL}
    ),
    Kind.TEXT_ARRAY: frozenset({Op.HAS, Op.IS_NULL}),
    Kind.JSON: frozenset(),
}

# Node columns the agent may filter on. The rest of `NODE_COLUMNS` is internal:
# `id` and `permission_parent_id` are uuids no other layer speaks, `node_type`
# is already fixed by the view, `deleted_at` is always NULL here because every
# compiled query excludes tombstones, and `content_version` is an ordering key
# for the ingest guard with no meaning to a reader. `fts` is reachable only
# through `op: fts` on `body`.
_AGENT_NODE_COLUMNS: dict[str, Kind] = {
    "entity_id": Kind.TEXT,
    "body": Kind.TEXT,
    "created_at": Kind.TIMESTAMP,
    "updated_at": Kind.TIMESTAMP,
}

# Ops a specific field accepts beyond what its SQL kind implies. `body` is text
# like any other, but it is the only column with a stored tsvector behind it, so
# it is the only one where ranked word search is an index scan rather than a
# sequential recompute. Restricting `fts` this way is what keeps the model from
# asking for it on `name` and getting a table scan that looks like it worked.
_FIELD_EXTRA_OPS: dict[str, frozenset[Op]] = {
    "body": frozenset({Op.FTS}),
}


def ops_for(field: str, kind: Kind) -> frozenset[Op]:
    return _OPS_BY_KIND[kind] | _FIELD_EXTRA_OPS.get(field, frozenset())


class Predicate(BaseModel):
    model_config = ConfigDict(frozen=True)

    field: str
    op: Op
    # None is meaningful only for `is_null`; every other op rejects it.
    value: Any = None


class TypeQuery(BaseModel):
    model_config = ConfigDict(frozen=True)

    # A string, not `NodeType`: semantic types are declared in `semantic_config`
    # and have no enum member. `spec_for` is what decides whether a name is real,
    # and it consults both registries.
    node_type: str
    predicates: list[Predicate] = Field(default_factory=list)
    limit: int = DEFAULT_QUERY_LIMIT


class QueryError(ValueError):
    """The query is not expressible. The message is returned to the model."""


@dataclass(frozen=True)
class Compiled:
    """A parameterized SELECT, minus its LIMIT/OFFSET.

    Kept open at the tail because visibility filtering happens in Python between
    pages: the caller must be able to keep asking for more rows until it has
    `limit` *visible* ones, which a baked-in LIMIT would prevent.
    """

    sql: str
    params: list[Any]
    limit: int


def _unwrap(annotation: Any) -> Any:
    origin = get_origin(annotation)
    if origin is Union or origin is UnionType:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _kind_of(annotation: Any) -> Kind:
    inner = _unwrap(annotation)
    origin = get_origin(inner)
    if inner is bool:
        return Kind.BOOL
    if inner is int:
        return Kind.INT
    if inner is str:
        return Kind.TEXT
    if origin is list:
        return Kind.TEXT_ARRAY
    if origin is dict:
        return Kind.JSON
    raise QueryError(f"no query mapping for annotation {annotation!r}")


def _unknown_type(node_type: str) -> QueryError:
    return QueryError(
        f"{node_type} is not a queryable node type; "
        f"choose one of {sorted(all_specs())}"
    )


def columns_for(node_type: str) -> dict[str, Kind]:
    """Filterable view columns for a type: shared node columns, then payload."""
    spec = spec_for(node_type)
    if spec is None:
        raise _unknown_type(node_type)
    cols = dict(_AGENT_NODE_COLUMNS)
    for name, info in spec.payload_model.model_fields.items():
        if name == "kind":
            continue
        cols[name] = _kind_of(info.annotation)
    return cols


def _column_sql(name: str, emittable: frozenset[str]) -> str:
    """Guard the one identifier that reaches the SQL text uninterpolated.

    `emittable` is the column set of the type being compiled, derived from its
    spec — so the check is not just "some type has this column" but "this type
    has it". Names never originate with the caller, and `safe_ident` has already
    passed on every registered field; re-checking here keeps that guarantee next
    to the interpolation rather than two modules away.
    """
    if not safe_ident(name) or name not in emittable:
        raise QueryError(f"unsafe column {name!r}")
    return name


def _escape_like(value: str) -> str:
    """Substring match on a literal, not a pattern the model authored.

    `%` and `_` inside the value are escaped so a search for "50%" does not
    silently become a wildcard, and the caller cannot smuggle in a leading `%`
    that turns an indexed prefix scan into a full one.
    """
    out = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{out}%"


def _as_timestamp(value: Any, field: str) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise QueryError(
                f"{field}: {value!r} is not an ISO-8601 timestamp"
            ) from exc
    else:
        raise QueryError(f"{field}: expected an ISO-8601 timestamp, got {value!r}")
    # Naive input is read as UTC rather than as the server's zone, so the same
    # query text means the same instant wherever it runs.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _coerce_scalar(value: Any, kind: Kind, field: str) -> Any:
    if kind is Kind.TIMESTAMP:
        return _as_timestamp(value, field)
    if kind is Kind.INT:
        if isinstance(value, bool) or not isinstance(value, int):
            raise QueryError(f"{field}: expected an integer, got {value!r}")
        return value
    if kind is Kind.BOOL:
        if not isinstance(value, bool):
            raise QueryError(f"{field}: expected true or false, got {value!r}")
        return value
    if not isinstance(value, str):
        raise QueryError(f"{field}: expected a string, got {value!r}")
    return value


def _render(
    pred: Predicate, kind: Kind, params: list[Any], emittable: frozenset[str]
) -> str:
    """One predicate to a SQL fragment, appending its bound parameters."""
    col = _column_sql(pred.field, emittable)

    def bind(value: Any) -> str:
        params.append(value)
        return f"${len(params)}"

    if pred.op is Op.IS_NULL:
        if not isinstance(pred.value, bool):
            raise QueryError(f"{pred.field}: is_null takes true or false")
        return f"{col} IS NULL" if pred.value else f"{col} IS NOT NULL"

    if pred.value is None:
        raise QueryError(f"{pred.field}: {pred.op} needs a value")

    if pred.op is Op.FTS:
        # Routed to the stored generated column, which is what node_fts_idx is
        # built on. `body` is the field the model names because `fts` is an
        # implementation detail of how that text is indexed.
        if pred.field != "body":
            raise QueryError("fts applies to `body` only")
        if not isinstance(pred.value, str) or not pred.value.strip():
            raise QueryError("fts needs non-empty text")
        return f"fts @@ plainto_tsquery('english', {bind(pred.value)})"

    if pred.op is Op.ILIKE:
        if not isinstance(pred.value, str) or not pred.value.strip():
            raise QueryError(f"{pred.field}: ilike needs non-empty text")
        if len(pred.value) > MAX_PATTERN_CHARS:
            raise QueryError(
                f"{pred.field}: ilike value over {MAX_PATTERN_CHARS} chars"
            )
        return f"{col} ILIKE {bind(_escape_like(pred.value))} ESCAPE '\\'"

    if pred.op is Op.HAS:
        value = _coerce_scalar(pred.value, Kind.TEXT, pred.field)
        return f"{bind(value)} = ANY({col})"

    if pred.op is Op.IN:
        if not isinstance(pred.value, list) or not pred.value:
            raise QueryError(f"{pred.field}: in needs a non-empty list")
        if len(pred.value) > MAX_IN_VALUES:
            raise QueryError(f"{pred.field}: in takes at most {MAX_IN_VALUES} values")
        values = [_coerce_scalar(v, kind, pred.field) for v in pred.value]
        cast = "bigint[]" if kind is Kind.INT else "text[]"
        return f"{col} = ANY({bind(values)}::{cast})"

    value = _coerce_scalar(pred.value, kind, pred.field)
    sql_op = {
        Op.EQ: "=",
        Op.NEQ: "<>",
        Op.GT: ">",
        Op.GTE: ">=",
        Op.LT: "<",
        Op.LTE: "<=",
    }[pred.op]
    return f"{col} {sql_op} {bind(value)}"


def compile_query(query: TypeQuery) -> Compiled:
    """`TypeQuery` -> parameterized SELECT over the type's view.

    Raises `QueryError` with a message written for the model to read and retry.
    """
    spec = spec_for(query.node_type)
    if spec is None:
        raise _unknown_type(query.node_type)
    if len(query.predicates) > MAX_PREDICATES:
        raise QueryError(f"at most {MAX_PREDICATES} predicates per query")
    if query.limit < 1:
        raise QueryError("limit must be at least 1")

    columns = columns_for(query.node_type)
    emittable = frozenset(columns)
    params: list[Any] = []
    # Tombstones are never queryable. Not a predicate the model can drop.
    clauses = ["deleted_at IS NULL"]

    for pred in query.predicates:
        kind = columns.get(pred.field)
        if kind is None:
            raise QueryError(
                f"{pred.field!r} is not a column of {query.node_type}; "
                f"available: {sorted(columns)}"
            )
        allowed = ops_for(pred.field, kind)
        if pred.op not in allowed:
            if not allowed:
                raise QueryError(
                    f"{pred.field!r} is a json map and cannot be filtered; "
                    f"search `body` instead, or use semantic_search"
                )
            raise QueryError(
                f"{pred.field!r} is {kind}; it accepts "
                f"{sorted(str(o) for o in allowed)}, not {pred.op}"
            )
        clauses.append(_render(pred, kind, params, emittable))

    # Deterministic and stable across pages: `updated_at` alone ties on bulk
    # imports that share a timestamp, and an unstable sort would let the same
    # row appear on two pages while another never appears at all.
    sql = (
        f"SELECT id, entity_id, node_type, body, payload, created_at, updated_at\n"
        f"FROM {spec.view_name}\n"
        f"WHERE {' AND '.join(clauses)}\n"
        f"ORDER BY updated_at DESC NULLS LAST, id DESC"
    )
    return Compiled(sql=sql, params=params, limit=min(query.limit, MAX_QUERY_LIMIT))


def describe_types() -> list[dict[str, Any]]:
    """The registry as the orchestrator's system prompt sees it.

    Emitted from the same specs the views are compiled from, so a payload change
    updates the model's picture of the schema on the next boot rather than
    drifting against a hand-maintained copy. `indexes` is included because it is
    the difference between binding a container id and scanning a table: the
    orchestrator is told to prefer indexed fields and it can only do that if it
    knows which they are.
    """
    out: list[dict[str, Any]] = []
    for node_type, spec in all_specs().items():
        columns = columns_for(node_type)
        indexed = {col for idx in spec.indexes for col in idx.columns}
        out.append(
            {
                "node_type": node_type,
                "columns": {
                    name: {
                        "type": str(kind),
                        "ops": sorted(str(o) for o in ops_for(name, kind)),
                        "indexed": name in indexed,
                    }
                    for name, kind in sorted(columns.items())
                },
                "composite_indexes": [list(idx.columns) for idx in spec.indexes],
            }
        )
    return out
