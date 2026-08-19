"""Self-contained Notion facts for the worker. No graph shape."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from connectors.notion.models import (
    Database,
    DataSource,
    Page,
    UserRef,
    notion_entity_id,
)
from core.message import ChangeKind, Envelope
from core.types import NodeType


class LinkFact(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str | None = None
    entity_id: str | None = None
    label: str | None = None
    relation: str = "mentions"


class NotionWorkspaceFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    bot_id: str
    name: str | None = None
    users: list[UserRef] = Field(default_factory=list)


class NotionDatabaseFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    database: Database
    parent_entity_id: str | None = None
    bot_id: str


class NotionDataSourceFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    data_source: DataSource
    parent_entity_id: str | None = None
    property_schema: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    bot_id: str


class NotionPageFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    page: Page
    body: str = ""
    parent_entity_id: str | None = None
    properties: dict[str, str] = Field(default_factory=dict)
    file_block_ids: list[str] = Field(default_factory=list)
    links: list[LinkFact] = Field(default_factory=list)
    bot_id: str


def workspace_envelope(
    *, bot_id: str, name: str | None, users: list[UserRef]
) -> Envelope:
    entity_id = notion_entity_id(bot_id)
    return Envelope(
        node_type=NodeType.NOTION_WORKSPACE,
        entity_id=entity_id,
        partition_key=entity_id,
        payload=NotionWorkspaceFacts(
            bot_id=bot_id, name=name, users=users
        ).model_dump(mode="json"),
    )


def database_envelope(
    database: Database, *, parent_entity_id: str | None, bot_id: str
) -> Envelope:
    return Envelope(
        node_type=NodeType.NOTION_DATABASE,
        entity_id=database.entity_id,
        partition_key=database.entity_id,
        payload=NotionDatabaseFacts(
            database=database,
            parent_entity_id=parent_entity_id,
            bot_id=bot_id,
        ).model_dump(mode="json"),
    )


def data_source_envelope(
    data_source: DataSource,
    *,
    parent_entity_id: str | None,
    schema: dict[str, str],
    body: str,
    bot_id: str,
) -> Envelope:
    return Envelope(
        node_type=NodeType.NOTION_DATA_SOURCE,
        entity_id=data_source.entity_id,
        partition_key=data_source.entity_id,
        payload=NotionDataSourceFacts(
            data_source=data_source,
            parent_entity_id=parent_entity_id,
            property_schema=schema,
            body=body,
            bot_id=bot_id,
        ).model_dump(mode="json"),
    )


def page_envelope(
    page: Page,
    *,
    body: str,
    parent_entity_id: str | None,
    properties: dict[str, str],
    file_block_ids: list[str],
    links: list[LinkFact],
    bot_id: str,
    change: ChangeKind = ChangeKind.UPDATED,
) -> Envelope:
    return Envelope(
        node_type=NodeType.NOTION_PAGE,
        entity_id=page.entity_id,
        partition_key=page.entity_id,
        change=change,
        payload=NotionPageFacts(
            page=page,
            body=body,
            parent_entity_id=parent_entity_id,
            properties=properties,
            file_block_ids=file_block_ids,
            links=links,
            bot_id=bot_id,
        ).model_dump(mode="json"),
    )


def delete_envelope(node_type: NodeType, entity_id: str) -> Envelope:
    if not entity_id.startswith("notion:"):
        entity_id = notion_entity_id(entity_id)
    return Envelope(
        node_type=node_type,
        entity_id=entity_id,
        partition_key=entity_id,
        change=ChangeKind.DELETED,
    )
