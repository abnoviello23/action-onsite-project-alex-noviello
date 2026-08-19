"""Notion property values -> text.

Pure functions only. A database row's meaning lives in its properties, not in
its blocks — a row often has no blocks at all — so without this a row node would
have a title and an empty body, and retrieval over the database would return
nothing useful.

Every property type is rendered to a flat string. Losing the structure is
deliberate: the raw record travels on GraphEvent.raw, and what the graph needs
is text a chunker and an embedding model can consume.
"""

from __future__ import annotations

from typing import Any

from connectors.notion.models import RichText, plain

# Rendered as their own columns already, and duplicating them into the body
# makes every row start with the same two timestamps.
SKIP_TYPES: frozenset[str] = frozenset(
    {"created_time", "last_edited_time", "created_by", "last_edited_by"}
)


def render_all(properties: dict[str, Any]) -> dict[str, str]:
    """Property name -> rendered value, dropping empties.

    The title property is emitted first. Notion returns properties in no
    meaningful order, and a row body that opens with "Due: 2026-09-30" instead
    of its name reads badly and chunks worse.
    """
    ordered = sorted(
        (
            (name, prop)
            for name, prop in properties.items()
            if isinstance(prop, dict) and prop.get("type") not in SKIP_TYPES
        ),
        key=lambda item: item[1].get("type") != "title",
    )
    out: dict[str, str] = {}
    for name, prop in ordered:
        value = render(prop)
        if value:
            out[name] = value
    return out


def as_body(rendered: dict[str, str]) -> str:
    """The row's properties as body text, one per line."""
    return "\n".join(f"{name}: {value}" for name, value in rendered.items())


def render(prop: dict[str, Any]) -> str:
    kind = prop.get("type")
    value = prop.get(kind) if kind else None

    if kind in {"title", "rich_text"}:
        return plain([RichText.model_validate(s) for s in value or []])
    if kind in {"number", "url", "email", "phone_number"}:
        return "" if value is None else str(value)
    if kind == "checkbox":
        return "yes" if value else "no"
    if kind in {"select", "status"}:
        return (value or {}).get("name", "")
    if kind == "multi_select":
        return ", ".join(opt.get("name", "") for opt in value or [])
    if kind == "date":
        return _date(value)
    if kind == "people":
        # Names only. The ids are on the raw record; a body full of UUIDs helps
        # nobody, and person->identity edges come from created_by, not prose.
        return ", ".join(p.get("name") or p.get("id", "") for p in value or [])
    if kind == "files":
        return ", ".join(f.get("name", "") for f in value or [])
    if kind == "relation":
        # Ids only — Notion does not expand related pages inline. The edge
        # extractor turns these into edges labelled with the property name.
        return ", ".join(r.get("id", "") for r in value or [])
    if kind == "unique_id":
        prefix = (value or {}).get("prefix") or ""
        number = (value or {}).get("number")
        return f"{prefix}-{number}" if prefix else str(number or "")
    if kind == "formula":
        return _formula(value)
    if kind == "rollup":
        return _rollup(value)
    if kind == "verification":
        return (value or {}).get("state", "")

    # Unknown or newly added type: keep something rather than silently dropping
    # a column that may carry the row's only content.
    if isinstance(value, str):
        return value
    return ""


def relation_targets(properties: dict[str, Any]) -> list[tuple[str, str]]:
    """(property name, related page id) across every relation property.

    The property name is the edge label: "Blocked by" is a different
    relationship from a prose mention of the same page, and the two must not
    collapse into one.
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for name, prop in properties.items():
        if isinstance(prop, dict) and prop.get("type") == "relation":
            for ref in prop.get("relation") or []:
                rid = ref.get("id")
                pair = (name, rid)
                if rid and pair not in seen:
                    seen.add(pair)
                    out.append(pair)
    return out


def schema_types(properties: dict[str, Any]) -> dict[str, str]:
    """Property name -> type, for the data source node."""
    return {
        name: prop.get("type", "")
        for name, prop in properties.items()
        if isinstance(prop, dict)
    }


# --------------------------------------------------------------- internals --


def _date(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    start, end = value.get("start"), value.get("end")
    return f"{start} to {end}" if end else (start or "")


def _formula(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    kind = value.get("type")
    inner = value.get(kind) if kind else None
    if kind == "date":
        return _date(inner)
    if kind == "boolean":
        return "yes" if inner else "no"
    return "" if inner is None else str(inner)


def _rollup(value: dict[str, Any] | None) -> str:
    if not value:
        return ""
    kind = value.get("type")
    inner = value.get(kind) if kind else None
    if kind == "array":
        return ", ".join(render(item) for item in inner or [] if isinstance(item, dict))
    if kind == "date":
        return _date(inner)
    return "" if inner is None else str(inner)
