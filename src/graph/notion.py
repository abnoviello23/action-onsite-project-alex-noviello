"""Notion envelopes -> graph writes.

Workspace identities, database / data-source / page nodes, root grants,
containment `in` edges, and in-workspace mention edges all live here.
"""

from __future__ import annotations

from connectors.notion.envelopes import (
    NotionDatabaseFacts,
    NotionDataSourceFacts,
    NotionPageFacts,
    NotionWorkspaceFacts,
)
from connectors.notion.models import Database, DataSource, Page, UserRef, parse_time
from connectors.notion.registry import Workspace
from core.access import NOTION_PUBLIC, NOTION_VISIBLE, AccessGrant, Identity, Membership
from core.graph import Edge, Node
from core.identity import PUBLIC_ID
from core.message import ChangeKind, Envelope, GraphWrite, RosterMode
from core.payloads import (
    NotionDatabasePayload,
    NotionDataSourcePayload,
    NotionPagePayload,
)
from core.types import NodeType
from graph.containment import with_parent
from graph.links import with_url_mentions
from graph.protocol import GraphView

TITLE_MAX_CHARS = 120
UNTITLED = "(untitled)"


def notion_user(user_id: str) -> str:
    return f"notion:user:{user_id}"


def notion_workspace(bot_id: str) -> str:
    return f"notion:workspace:{bot_id}"


def identity_from_user(user: UserRef) -> Identity:
    return Identity(
        id=notion_user(user.id),
        display_name=user.name,
        email=user.email,
    )


def clip_title(title: str) -> str:
    return title if len(title) <= TITLE_MAX_CHARS else title[: TITLE_MAX_CHARS - 1] + "…"


def _notion_needles(*values: str | None) -> list[str]:
    out: list[str] = []
    for value in values:
        if not value:
            continue
        out.append(value)
        compact = value.replace("-", "")
        if compact != value:
            out.append(compact)
    return out


async def _deleted(env: Envelope, graph: GraphView) -> GraphWrite:
    return GraphWrite(
        node_type=env.node_type,
        entity_id=env.entity_id,
        change=ChangeKind.DELETED,
        retract_edges=await graph.edges_from(env.entity_id),
    )


def _root_grants(
    *, parent_entity_id: str | None, entity_id: str, public_url: str | None, bot_id: str
) -> list[AccessGrant]:
    if parent_entity_id:
        return []
    if public_url:
        return [
            AccessGrant(
                identity_id=PUBLIC_ID,
                resource_entity_id=entity_id,
                level=NOTION_PUBLIC,
            )
        ]
    return [
        AccessGrant(
            identity_id=notion_workspace(bot_id),
            resource_entity_id=entity_id,
            level=NOTION_VISIBLE,
        )
    ]


class NotionWorkspaceGenerator:
    node_type = NodeType.NOTION_WORKSPACE

    async def generate(self, env: Envelope, graph: GraphView) -> GraphWrite:
        facts = NotionWorkspaceFacts.model_validate(env.payload)
        workspace = Workspace(bot_id=facts.bot_id, name=facts.name)
        users = list(facts.users)
        identity = Identity(
            id=notion_workspace(workspace.bot_id),
            display_name=workspace.name,
        )
        return GraphWrite(
            node_type=NodeType.NOTION_WORKSPACE,
            entity_id=workspace.entity_id,
            roster=RosterMode.PARENT,
            identities=[
                identity,
                Identity(id=PUBLIC_ID, display_name="Anyone"),
                *(identity_from_user(u) for u in users),
            ],
            memberships=[
                Membership(
                    child_identity_id=notion_user(u.id),
                    parent_identity_id=identity.id,
                )
                for u in users
                if not u.is_bot
            ],
        )


class NotionDatabaseGenerator:
    node_type = NodeType.NOTION_DATABASE

    async def generate(self, env: Envelope, graph: GraphView) -> GraphWrite:
        if env.change is ChangeKind.DELETED:
            return await _deleted(env, graph)
        facts = NotionDatabaseFacts.model_validate(env.payload)
        database: Database = facts.database
        entity_id = database.entity_id
        return await with_parent(
            graph,
            await with_url_mentions(
                graph,
                GraphWrite(
                    node_type=NodeType.NOTION_DATABASE,
                    entity_id=entity_id,
                    node=Node(
                        node_type=NodeType.NOTION_DATABASE,
                        entity_id=entity_id,
                        permission_parent_entity_id=facts.parent_entity_id,
                        body=clip_title(database.name or UNTITLED),
                        created_at=parse_time(
                            database.created_time or database.last_edited_time
                        ),
                        updated_at=parse_time(database.last_edited_time),
                        content_version=database.last_edited_time,
                        payload=NotionDatabasePayload(
                            database_id=database.id,
                            name=database.name or UNTITLED,
                            data_source_ids=[ds.id for ds in database.data_sources],
                            parent_type=database.parent.type,
                            parent_id=database.parent.target_id,
                            in_trash=database.trashed,
                            url=database.url,
                            public_url=database.public_url,
                        ).model_dump(mode="json"),
                    ),
                    grants=_root_grants(
                        parent_entity_id=facts.parent_entity_id,
                        entity_id=entity_id,
                        public_url=database.public_url,
                        bot_id=facts.bot_id,
                    ),
                ),
                [],
                needles=_notion_needles(database.id, database.url, database.public_url),
            ),
        )


class NotionDataSourceGenerator:
    node_type = NodeType.NOTION_DATA_SOURCE

    async def generate(self, env: Envelope, graph: GraphView) -> GraphWrite:
        if env.change is ChangeKind.DELETED:
            return await _deleted(env, graph)
        facts = NotionDataSourceFacts.model_validate(env.payload)
        data_source: DataSource = facts.data_source
        entity_id = data_source.entity_id
        return await with_parent(
            graph,
            await with_url_mentions(
                graph,
                GraphWrite(
                    node_type=NodeType.NOTION_DATA_SOURCE,
                    entity_id=entity_id,
                    node=Node(
                        node_type=NodeType.NOTION_DATA_SOURCE,
                        entity_id=entity_id,
                        permission_parent_entity_id=facts.parent_entity_id,
                        body=facts.body or clip_title(data_source.name or UNTITLED),
                        created_at=parse_time(
                            data_source.created_time or data_source.last_edited_time
                        ),
                        updated_at=parse_time(data_source.last_edited_time),
                        content_version=data_source.last_edited_time,
                        payload=NotionDataSourcePayload(
                            data_source_id=data_source.id,
                            database_id=data_source.parent.target_id,
                            name=data_source.name or UNTITLED,
                            property_schema=facts.property_schema,
                            in_trash=data_source.trashed,
                            url=data_source.url,
                            public_url=data_source.public_url,
                        ).model_dump(mode="json"),
                    ),
                    grants=_root_grants(
                        parent_entity_id=facts.parent_entity_id,
                        entity_id=entity_id,
                        public_url=data_source.public_url,
                        bot_id=facts.bot_id,
                    ),
                ),
                [],
                needles=_notion_needles(
                    data_source.id, data_source.url, data_source.public_url
                ),
            ),
        )


class NotionPageGenerator:
    node_type = NodeType.NOTION_PAGE

    async def generate(self, env: Envelope, graph: GraphView) -> GraphWrite:
        if env.change is ChangeKind.DELETED:
            return await _deleted(env, graph)
        facts = NotionPageFacts.model_validate(env.payload)
        page: Page = facts.page
        entity_id = page.entity_id
        edges: list[Edge] = []
        unresolved: list[str] = []
        for target in facts.links:
            if target.entity_id and target.entity_id != entity_id:
                edges.append(
                    Edge(
                        from_entity_id=entity_id,
                        to_entity_id=target.entity_id,
                        relation=target.relation,
                    )
                )
            elif target.url:
                unresolved.append(target.url)

        return await with_parent(
            graph,
            await with_url_mentions(
                graph,
                GraphWrite(
                    node_type=NodeType.NOTION_PAGE,
                    entity_id=entity_id,
                    change=env.change,
                    node=Node(
                        node_type=NodeType.NOTION_PAGE,
                        entity_id=entity_id,
                        permission_parent_entity_id=facts.parent_entity_id,
                        body=facts.body,
                        created_at=parse_time(page.created_time),
                        updated_at=parse_time(page.last_edited_time),
                        content_version=page.last_edited_time,
                        payload=NotionPagePayload(
                            page_id=page.id,
                            title=page.title or UNTITLED,
                            parent_type=page.parent.type,
                            parent_id=page.parent.target_id,
                            is_row=page.parent.type == "data_source_id",
                            actor_id=(
                                notion_user(page.created_by.id)
                                if page.created_by
                                else None
                            ),
                            created_by_id=page.created_by.id if page.created_by else None,
                            last_edited_by_id=(
                                page.last_edited_by.id if page.last_edited_by else None
                            ),
                            properties=facts.properties,
                            file_block_ids=facts.file_block_ids,
                            link_urls=unresolved,
                            in_trash=page.trashed,
                            url=page.url,
                            public_url=page.public_url,
                        ).model_dump(mode="json"),
                    ),
                    edges=edges,
                    grants=_root_grants(
                        parent_entity_id=facts.parent_entity_id,
                        entity_id=entity_id,
                        public_url=page.public_url,
                        bot_id=facts.bot_id,
                    ),
                ),
                unresolved,
                needles=_notion_needles(page.id, page.url, page.public_url),
            ),
        )
