"""Notion objects -> stream envelopes.

Gathering requires I/O. Graph shape is the worker's job.
"""

from __future__ import annotations

import logging

from connectors.notion import blocks, envelopes, props
from connectors.notion.blocks import BlockNode
from connectors.notion.client import NotionClient, NotionError
from connectors.notion.envelopes import LinkFact
from connectors.notion.models import (
    Block,
    Database,
    DataSource,
    Page,
    notion_entity_id,
)
from connectors.notion.registry import NotionRegistry
from core.message import ChangeKind, Envelope
from core.types import NodeType

log = logging.getLogger("connectors.notion.events")

MAX_BLOCK_DEPTH = 4

FILE_BLOCK_TYPES: frozenset[str] = frozenset(
    {"image", "file", "pdf", "video", "audio"}
)


class NotionEventMapper:
    """Turns Notion objects into envelopes, fetching what they need."""

    def __init__(self, client: NotionClient, registry: NotionRegistry) -> None:
        self._client = client
        self._registry = registry

    async def page_event(
        self,
        page: Page,
        *,
        change: ChangeKind = ChangeKind.UPDATED,
    ) -> Envelope:
        parent_id = await self._registry.resolve_parent(self._client, page.parent)
        tree = await self._block_tree(page.id)

        rendered = props.render_all(page.properties)
        body_parts = []
        if page.parent.type == "data_source_id":
            body_parts.append(props.as_body(rendered))
        body_parts.append(blocks.render(tree))
        body = "\n\n".join(part for part in body_parts if part).strip()

        targets = blocks.links(tree)
        targets += [
            blocks.LinkTarget(entity_id=notion_entity_id(rid), relation=name)
            for name, rid in props.relation_targets(page.properties)
        ]

        links = [
            LinkFact(
                url=t.url,
                entity_id=t.entity_id,
                label=t.label,
                relation=t.relation,
            )
            for t in targets
        ]

        return envelopes.page_envelope(
            page,
            body=body,
            parent_entity_id=parent_id,
            properties=rendered,
            file_block_ids=[
                n.block.id for n in _walk(tree) if n.block.type in FILE_BLOCK_TYPES
            ],
            links=links,
            bot_id=self._registry.workspace.bot_id,
            change=change,
        )

    async def data_source_event(
        self,
        data_source: DataSource,
        *,
        change: ChangeKind = ChangeKind.UPDATED,
    ) -> Envelope:
        parent_id = await self._registry.resolve_parent(
            self._client, data_source.parent
        )
        schema = props.schema_types(data_source.properties)
        body = ", ".join(f"{name} ({kind})" for name, kind in schema.items())
        return envelopes.data_source_envelope(
            data_source,
            parent_entity_id=parent_id,
            schema=schema,
            body=body,
            bot_id=self._registry.workspace.bot_id,
        )

    async def database_event(
        self,
        database: Database,
        *,
        change: ChangeKind = ChangeKind.UPDATED,
    ) -> Envelope:
        parent_id = await self._registry.resolve_parent(self._client, database.parent)
        return envelopes.database_envelope(
            database,
            parent_entity_id=parent_id,
            bot_id=self._registry.workspace.bot_id,
        )

    def workspace_event(self) -> Envelope:
        ws = self._registry.workspace
        return envelopes.workspace_envelope(
            bot_id=ws.bot_id,
            name=ws.name,
            users=list(self._registry.users.values()),
        )

    @staticmethod
    def delete_event(node_type: NodeType, entity_id: str) -> Envelope:
        return envelopes.delete_envelope(node_type, entity_id)

    async def _block_tree(self, block_id: str, depth: int = 0) -> list[BlockNode]:
        if depth >= MAX_BLOCK_DEPTH:
            return []
        try:
            payloads = await self._client.block_children(block_id)
        except NotionError as exc:
            log.warning("blocks for %s unavailable: %s", block_id, exc.code)
            return []

        tree: list[BlockNode] = []
        for payload in payloads:
            block = Block.from_payload(payload)
            children: list[BlockNode] = []
            if block.has_children and block.type not in {
                "child_page",
                "child_database",
            }:
                children = await self._block_tree(block.id, depth + 1)
            tree.append(BlockNode(block=block, children=children))
        return tree


def _walk(tree: list[BlockNode]):
    for node in tree:
        yield node
        yield from _walk(node.children)
