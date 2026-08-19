"""Workspace facts and parent resolution.

Two jobs, both required for correctness rather than for speed.

**Identities.** Page objects reference users by bare id — `created_by` is
`{"object": "user", "id": "..."}` and nothing else. Names and, critically,
emails come only from /v1/users, and email is the key that joins a Notion person
to the same human in Slack and Drive.

**Containment.** A page nested inside a toggle, a column, or a synced block has
`parent.type == "block_id"`, pointing at the block rather than the page. Access
inherits along parent_id, so leaving it pointing at a block would hang the page
off a node that does not exist in the graph. Resolving it costs one call per
distinct block, cached for the life of the process — block parents never change
without the page moving, which re-fetches anyway.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from connectors.notion.client import NotionClient, NotionError
from connectors.notion.models import Parent, UserRef, notion_entity_id

log = logging.getLogger("connectors.notion.registry")


@dataclass(frozen=True)
class Workspace:
    """The integration's own view of the workspace.

    `bot_id` stands in for a workspace id, which the API exposes only through
    the OAuth token exchange that an internal integration never performs. The
    name is renameable and so is a display concern only.
    """

    bot_id: str
    name: str | None

    @property
    def entity_id(self) -> str:
        return notion_entity_id(self.bot_id)


class NotionRegistry:
    """Workspace facts, loaded once per poll cycle."""

    def __init__(self, workspace: Workspace, users: dict[str, UserRef]) -> None:
        self.workspace = workspace
        self.users = users
        self._block_parents: dict[str, str | None] = {}

    @classmethod
    async def load(cls, client: NotionClient) -> NotionRegistry:
        info = await client.bot_info()
        bot = info.get("bot") or {}
        users = await client.list_users()
        return cls(
            workspace=Workspace(
                bot_id=info.get("id", ""),
                name=bot.get("workspace_name"),
            ),
            users={u.id: u for u in users},
        )

    async def refresh(self, client: NotionClient) -> None:
        """Reload in place so every holder of this registry sees one view."""
        fresh = await self.load(client)
        self.workspace = fresh.workspace
        self.users = fresh.users

    def humans(self) -> list[UserRef]:
        return [u for u in self.users.values() if not u.is_bot]

    async def resolve_parent(
        self, client: NotionClient, parent: Parent
    ) -> str | None:
        """The namespaced entity id a node hangs off, or None for a workspace
        root.

        Returns None rather than raising when a block parent cannot be read: an
        unresolvable parent makes the page a root, and a root carries its own
        grants, so nothing silently inherits access it should not have.
        """
        if parent.is_workspace:
            return None
        if parent.type != "block_id":
            return parent.entity_id

        block_id = parent.block_id
        if not block_id:
            return None
        if block_id in self._block_parents:
            return self._block_parents[block_id]

        resolved = await self._owning_page(client, block_id)
        self._block_parents[block_id] = resolved
        return resolved

    async def _owning_page(self, client: NotionClient, block_id: str) -> str | None:
        """Walk up from a block until something that is not a block.

        Bounded: a synced block inside a column inside a toggle is three hops,
        and a cycle would otherwise spin forever against the rate limiter.
        """
        current = block_id
        for _ in range(10):
            try:
                block = await client.block(current)
            except NotionError as exc:
                log.warning("cannot resolve block parent %s: %s", current, exc.code)
                return None
            parent = block.parent
            if parent is None or parent.is_workspace:
                return None
            if parent.type != "block_id":
                return parent.entity_id
            if not parent.block_id or parent.block_id == current:
                return None
            current = parent.block_id
        log.warning("block parent chain from %s exceeded depth limit", block_id)
        return None
