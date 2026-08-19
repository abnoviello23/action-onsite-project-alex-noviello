"""Typed jsonb payloads, keyed by `NodeType.kind`.

These are graph shapes, not API records. Connectors dump source facts into
envelopes; generators build these models and put `.model_dump()` on `Node`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from core.types import NodeType


class PayloadBase(BaseModel):
    model_config = ConfigDict(frozen=True)


class SlackChannelPayload(PayloadBase):
    kind: Literal[NodeType.SLACK_CHANNEL] = NodeType.SLACK_CHANNEL
    channel_id: str
    team_id: str | None = None
    name: str
    is_private: bool
    is_archived: bool = False
    is_general: bool = False
    is_shared: bool = False
    is_ext_shared: bool = False
    created: int | None = None
    creator: str | None = None
    topic: str = ""
    purpose: str = ""
    member_count: int | None = None
    # Roster, not ACL. Public channels still grant to the workspace identity;
    # this list is who has actually joined, so "who's in #eng" is answerable.
    member_ids: list[str] = Field(default_factory=list)


class SlackMessagePayload(PayloadBase):
    kind: Literal[NodeType.SLACK_MESSAGE] = NodeType.SLACK_MESSAGE
    channel_id: str
    ts: str
    thread_ts: str | None = None
    user_id: str | None = None
    bot_id: str | None = None
    actor_id: str | None = None
    text_raw: str = ""
    subtype: str | None = None
    edited_ts: str | None = None
    reply_count: int = 0
    file_ids: list[str] = Field(default_factory=list)
    mentioned_user_ids: list[str] = Field(default_factory=list)
    link_urls: list[str] = Field(default_factory=list)


class DriveDrivePayload(PayloadBase):
    kind: Literal[NodeType.DRIVE_DRIVE] = NodeType.DRIVE_DRIVE
    drive_id: str
    name: str


class DriveFolderPayload(PayloadBase):
    kind: Literal[NodeType.DRIVE_FOLDER] = NodeType.DRIVE_FOLDER
    file_id: str
    name: str
    drive_id: str | None = None
    parent_id: str | None = None
    version: int = 0
    trashed: bool = False
    web_view_link: str | None = None


class DriveFilePayload(PayloadBase):
    kind: Literal[NodeType.DRIVE_FILE] = NodeType.DRIVE_FILE
    file_id: str
    name: str
    mime_type: str
    drive_id: str | None = None
    parent_id: str | None = None
    actor_id: str | None = None
    version: int = 0
    head_revision_id: str | None = None
    md5_checksum: str | None = None
    size: int | None = None
    trashed: bool = False
    body_source: str = "none"
    last_modifying_email: str | None = None
    web_view_link: str | None = None
    # URLs in the exported body, so a later Notion/Slack ingest can find this
    # file via `mentioning` and mint the reverse `mentions` edge.
    link_urls: list[str] = Field(default_factory=list)


class NotionDatabasePayload(PayloadBase):
    kind: Literal[NodeType.NOTION_DATABASE] = NodeType.NOTION_DATABASE
    database_id: str
    name: str
    data_source_ids: list[str] = Field(default_factory=list)
    parent_type: str = "workspace"
    parent_id: str | None = None
    in_trash: bool = False
    url: str | None = None
    public_url: str | None = None


class NotionDataSourcePayload(PayloadBase):
    kind: Literal[NodeType.NOTION_DATA_SOURCE] = NodeType.NOTION_DATA_SOURCE
    data_source_id: str
    database_id: str | None = None
    name: str
    property_schema: dict[str, str] = Field(default_factory=dict)
    in_trash: bool = False
    url: str | None = None
    public_url: str | None = None


class NotionPagePayload(PayloadBase):
    kind: Literal[NodeType.NOTION_PAGE] = NodeType.NOTION_PAGE
    page_id: str
    title: str
    parent_type: str
    parent_id: str | None = None
    is_row: bool = False
    actor_id: str | None = None
    created_by_id: str | None = None
    last_edited_by_id: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    file_block_ids: list[str] = Field(default_factory=list)
    link_urls: list[str] = Field(default_factory=list)
    in_trash: bool = False
    url: str | None = None
    public_url: str | None = None
