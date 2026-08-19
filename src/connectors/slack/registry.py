"""Local channel and user lookups.

Slack does not return channel metadata with messages. conversations.history says
nothing about the channel — you know which one it is because you asked for it —
and Socket Mode message events carry a bare channel id. `is_private`, which
decides the ACL, comes only from conversations.list/info. So this registry is
required for correctness, not a caching optimization.
"""

from __future__ import annotations

from dataclasses import dataclass

from connectors.slack.client import SlackClient
from connectors.slack.models import Channel, User
from connectors.slack.text import TextResolver


@dataclass(frozen=True)
class Workspace:
    team_id: str
    team_name: str
    # Subdomain from auth.test's url; permalinks are built from it and it is not
    # derivable from team_name.
    domain: str | None

    @property
    def entity_id(self) -> str:
        return f"slack:{self.team_id}"


class SlackRegistry:
    """Workspace facts loaded once per poll cycle."""

    def __init__(
        self,
        workspace: Workspace,
        channels: dict[str, Channel],
        users: dict[str, User],
    ) -> None:
        self.workspace = workspace
        self.channels = channels
        self.users = users
        self.resolver = TextResolver(
            user_names={uid: u.best_name for uid, u in users.items()},
            channel_names={cid: c.display for cid, c in channels.items()},
        )

    @classmethod
    async def load(
        cls, client: SlackClient, *, include_private: bool = True
    ) -> SlackRegistry:
        auth = await client.auth_test()
        users = await client.list_users()
        channels = await client.list_channels(include_private=include_private)
        return cls(
            workspace=Workspace(
                team_id=auth.team_id,
                team_name=auth.team,
                domain=auth.team_domain,
            ),
            channels={c.id: c for c in channels},
            users={u.id: u for u in users},
        )

    async def refresh(self, client: SlackClient, *, include_private: bool = True) -> None:
        """Reload in place so the poller and the socket listener share one view."""
        fresh = await self.load(client, include_private=include_private)
        self.workspace = fresh.workspace
        self.channels = fresh.channels
        self.users = fresh.users
        self.resolver = fresh.resolver

    async def refresh_channel(self, client: SlackClient, channel_id: str) -> None:
        channel = await client.channel_info(channel_id)
        self.channels[channel.id] = channel
        self.resolver.set_channel_name(channel.id, channel.display)

    def upsert_user(self, user: User) -> None:
        self.users[user.id] = user
        self.resolver.set_user_name(user.id, user.best_name)

    def readable_channels(self) -> list[Channel]:
        """Channels the bot can actually call history on.

        A public channel is listed whether or not the bot joined it, but reading
        it would fail with not_in_channel; private channels are only listed at
        all when the bot is a member.
        """
        return [c for c in self.channels.values() if c.is_member]
