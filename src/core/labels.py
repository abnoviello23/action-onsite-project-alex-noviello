"""How a node is named and previewed when it is listed rather than opened.

Shared by the operator canvas and the permissioned session graph so both call
the same thing a node. Pure projection over `(node_type, payload, body,
entity_id)` — no database, no permission opinion. Callers that must not leak
invisible nodes filter *before* they project; nothing here checks access.

`native_keys` is the other half: the source-side identifiers an external tool
(a Slack/Drive/Notion MCP) needs to act on the thing we just cited. The graph
is a mirror, so a citation that only carried our `entity_id` would be a dead
end outside this service.
"""

from __future__ import annotations

from typing import Any

from core.types import NodeType

# Where a node type keeps its human-readable name, in preference order. Notion
# pages use `title`, everything else uses `name`; Slack messages have neither
# and fall through to the body.
LABEL_KEYS = ("name", "title")

LABEL_MAX_CHARS = 60
PREVIEW_MAX_CHARS = 280

UNMATERIALIZED = "(unmaterialized)"

# The bucket every inferred type reports as its source. Not a connector: nothing
# outside this system produced a `person`.
SEMANTIC_SOURCE = "semantic"

# Payload fields that identify the thing in its own source, per type. Order is
# the order a caller should read them: container first, then the item within it,
# because that is the argument order every source API wants.
_NATIVE_KEYS: dict[NodeType, tuple[str, ...]] = {
    NodeType.SLACK_CHANNEL: ("channel_id",),
    NodeType.SLACK_MESSAGE: ("channel_id", "ts", "thread_ts"),
    NodeType.DRIVE_DRIVE: ("drive_id",),
    NodeType.DRIVE_FOLDER: ("file_id",),
    NodeType.DRIVE_FILE: ("file_id", "web_view_link"),
    NodeType.NOTION_DATABASE: ("database_id",),
    NodeType.NOTION_DATA_SOURCE: ("data_source_id", "database_id"),
    NodeType.NOTION_PAGE: ("page_id", "url"),
}


def clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def source_of(node_type: str | None) -> str:
    """'slack:message' -> 'slack'. Unmaterialized rows have no type at all.

    Every mirrored type is `{source}:{thing}`, and every declared semantic type
    is a bare identifier — `safe_ident` rejects a colon — so the absence of one
    is a reliable test for an inferred node.

    All inferred types collapse to one source rather than becoming three. The
    canvas colours by source from a three-slot categorical palette that was
    validated all-pairs, and letting `person`, `task`, and `project` each claim
    a slot would push the real sources onto the muted ink. They also genuinely
    share an origin: the semantic layer produced them, and no external system
    did.
    """
    if not node_type:
        return "unknown"
    if ":" not in node_type:
        return SEMANTIC_SOURCE
    return node_type.split(":", 1)[0]


def label_of(payload: dict[str, Any], body: str, entity_id: str) -> str:
    """The one line shown next to a node in any list.

    Falls back through name/title -> body -> entity id, because a Slack message
    has no name and an unmaterialized row has nothing but its id, and a blank
    label is an unidentifiable row wherever it is rendered.
    """
    for key in LABEL_KEYS:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return clip(value.strip(), LABEL_MAX_CHARS)

    if body.strip():
        return clip(" ".join(body.split()), LABEL_MAX_CHARS)

    return clip(entity_id, LABEL_MAX_CHARS)


def native_keys(node_type: str | None, payload: dict[str, Any]) -> dict[str, str]:
    """Source-side identifiers for this node, for handoff to a write tool.

    Only keys the payload actually carries are returned: `thread_ts` is absent
    on a top-level message and a citation should not invent one.
    """
    if not node_type:
        return {}
    try:
        kind = NodeType(node_type)
    except ValueError:
        return {}
    out: dict[str, str] = {}
    for key in _NATIVE_KEYS.get(kind, ()):
        value = payload.get(key)
        if isinstance(value, str) and value:
            out[key] = value
    return out
