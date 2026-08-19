"""Pydantic models of the Notion API payloads we consume.

Everything ignores unknown fields — Notion adds keys with every version bump and
a strict model would turn an additive change into an outage. The untouched dict
is what reaches the raw payload log, so nothing is lost by ignoring here.

Two version-specific details are load-bearing:

- `2025-09-03` split databases into a container (`database`) and one or more
  `data_source`s that hold the schema and the rows. A page's parent is a
  `data_source_id`, and the id in a database URL is not the id you query.
- `2026-03-11` renamed `archived` to `in_trash`. Both are accepted here so a
  pinned-version rollback does not silently start treating trashed pages as
  live.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Notion timestamps are ISO-8601 UTC with a fixed shape
# ("2026-08-17T22:04:00.000Z"), so lexical order matches chronological order and
# the string can be used directly as the monotonic content_version.
NotionTime = str


def notion_entity_id(object_id: str) -> str:
    """Databases, data sources and pages share one id space in Notion, so they
    share one here.

    Normalized to the hyphenless form: Notion returns UUIDs hyphenated from some
    endpoints and bare from others, and the same page reached both ways would
    otherwise mint two nodes.
    """
    return f"notion:{object_id.replace('-', '').lower()}"


def parse_time(value: NotionTime | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class NotionModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class Parent(NotionModel):
    """Where an object hangs. Exactly one id field is set, keyed by `type`.

    `block_id` appears when a page is nested inside a toggle, column, or synced
    block. That block belongs to a page, so containment needs one extra hop —
    see NotionRegistry.resolve_parent.
    """

    type: str
    page_id: str | None = None
    database_id: str | None = None
    data_source_id: str | None = None
    block_id: str | None = None
    workspace: bool | None = None

    @property
    def target_id(self) -> str | None:
        return getattr(self, self.type, None) if self.type != "workspace" else None

    @property
    def entity_id(self) -> str | None:
        """Namespaced id of a page/database/data_source parent.

        None for workspace roots, and for `block_id` parents — those are not
        graph nodes and have to be walked to the owning page first.
        """
        if self.is_workspace or self.type == "block_id" or not self.target_id:
            return None
        return notion_entity_id(self.target_id)

    @property
    def is_workspace(self) -> bool:
        return self.type == "workspace"


class PersonInfo(NotionModel):
    # Present only when the integration holds the "read user information
    # including email addresses" capability. This is the cross-source join key.
    email: str | None = None


class UserRef(NotionModel):
    """A user as returned by /v1/users, or inline on created_by/last_edited_by.

    Inline references carry only `object` and `id`; the rest arrives from the
    users list, which is why the registry loads it once per cycle.
    """

    id: str
    name: str | None = None
    # "person" or "bot". Absent on inline references.
    type: str | None = None
    person: PersonInfo | None = None
    avatar_url: str | None = None

    @property
    def is_bot(self) -> bool:
        return self.type == "bot"

    @property
    def email(self) -> str | None:
        return self.person.email if self.person else None


class RichText(NotionModel):
    """One span of formatted text.

    `plain_text` is authoritative for the body: it is what Notion renders,
    including the resolved label of a mention. `href` carries the link target,
    which is what the edge extractor reads.
    """

    type: str | None = None
    plain_text: str = ""
    href: str | None = None
    mention: dict[str, Any] | None = None


def plain(spans: list[RichText] | None) -> str:
    return "".join(s.plain_text for s in spans or [])


class Trashable(NotionModel):
    """`archived` before 2026-03-11, `in_trash` after. Accept both."""

    archived: bool = False
    in_trash: bool = False

    @property
    def trashed(self) -> bool:
        return self.in_trash or self.archived


class Page(Trashable):
    id: str
    created_time: NotionTime
    last_edited_time: NotionTime
    created_by: UserRef | None = None
    last_edited_by: UserRef | None = None
    parent: Parent
    # Property values. For a database row this is the row; for a plain page it
    # holds only the title.
    properties: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None
    # Non-null exactly when the page is published to the web. The only
    # machine-readable permission signal Notion exposes.
    public_url: str | None = None

    @property
    def entity_id(self) -> str:
        return notion_entity_id(self.id)

    @property
    def title(self) -> str | None:
        for prop in self.properties.values():
            if isinstance(prop, dict) and prop.get("type") == "title":
                spans = [RichText.model_validate(s) for s in prop.get("title", [])]
                return plain(spans) or None
        return None


class DataSource(Trashable):
    """Schema + rows container. Queried, unlike its parent database."""

    id: str
    created_time: NotionTime | None = None
    last_edited_time: NotionTime
    parent: Parent
    title: list[RichText] = Field(default_factory=list)
    properties: dict[str, Any] = Field(default_factory=dict)
    url: str | None = None
    public_url: str | None = None

    @property
    def entity_id(self) -> str:
        return notion_entity_id(self.id)

    @property
    def name(self) -> str | None:
        return plain(self.title) or None


class DatabaseDataSourceRef(NotionModel):
    id: str
    name: str | None = None


class Database(Trashable):
    """The container only. Rows live in its data sources."""

    id: str
    created_time: NotionTime | None = None
    last_edited_time: NotionTime
    parent: Parent
    title: list[RichText] = Field(default_factory=list)
    data_sources: list[DatabaseDataSourceRef] = Field(default_factory=list)
    url: str | None = None
    public_url: str | None = None

    @property
    def entity_id(self) -> str:
        return notion_entity_id(self.id)

    @property
    def name(self) -> str | None:
        return plain(self.title) or None


class Block(Trashable):
    """A content block.

    The payload for a block of type "paragraph" lives under the key
    "paragraph", so the body of every block is reached through `content`
    rather than modelled per type — there are ~30 types and they change.
    """

    id: str
    type: str
    has_children: bool = False
    parent: Parent | None = None
    last_edited_time: NotionTime | None = None
    raw: dict[str, Any] = Field(default_factory=dict, exclude=True)

    @property
    def entity_id(self) -> str:
        return notion_entity_id(self.id)

    @property
    def content(self) -> dict[str, Any]:
        value = self.raw.get(self.type)
        return value if isinstance(value, dict) else {}

    @property
    def rich_text(self) -> list[RichText]:
        return [RichText.model_validate(s) for s in self.content.get("rich_text", [])]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> Block:
        return cls.model_validate({**payload, "raw": payload})
