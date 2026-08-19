"""Slack events -> stream envelopes.

Every Slack event resolves to exactly one entity id. Graph shape is the
worker's job. This module gathers what a generator needs — which can require a
Slack call, because a join event carries one user, not the resulting roster,
and history does not include `is_private`.
"""

from __future__ import annotations

import logging
from typing import Any

from connectors.slack import envelopes
from connectors.slack.client import SlackClient
from connectors.slack.identities import is_content
from connectors.slack.models import Channel, Message, User
from connectors.slack.registry import SlackRegistry
from core.message import ChangeKind, Envelope

log = logging.getLogger("connectors.slack.events")

CHANNEL_EVENTS = frozenset(
    {
        "member_joined_channel",
        "member_left_channel",
        "channel_rename",
        "channel_archive",
        "channel_unarchive",
        "channel_created",
    }
)


class SlackEventMapper:
    """Turns Socket Mode events and polled messages into envelopes."""

    def __init__(self, client: SlackClient, registry: SlackRegistry) -> None:
        self._client = client
        self._registry = registry

    def workspace_message(
        self, users: list[User] | None = None, *, replace_roster: bool = True
    ) -> Envelope:
        workspace = self._registry.workspace
        return envelopes.workspace_envelope(
            team_id=workspace.team_id,
            team_name=workspace.team_name,
            domain=workspace.domain,
            users=users if users is not None else list(self._registry.users.values()),
            replace_roster=replace_roster,
        )

    async def channel_message(self, channel: Channel) -> Envelope:
        # Roster is channel metadata for every channel. ACL still treats public
        # vs private differently in the generator; this call is who has joined.
        member_ids = await self._client.channel_member_ids(channel.id)
        return envelopes.channel_envelope(
            channel,
            team_id=self._registry.workspace.team_id,
            member_ids=member_ids,
        )

    def message_message(
        self,
        message: Message,
        channel: Channel,
        *,
        change: ChangeKind = ChangeKind.CREATED,
    ) -> Envelope | None:
        if not is_content(message):
            return None
        user_names, channel_names = self._registry.resolver.name_map(message.text)
        return envelopes.message_envelope(
            message,
            channel,
            user_names=user_names,
            channel_names=channel_names,
            change=change,
        )

    async def from_socket(self, event: dict[str, Any]) -> Envelope | None:
        kind = event.get("type")

        if kind == "message":
            return await self._from_message_event(event)
        if kind in CHANNEL_EVENTS:
            return await self._from_channel_event(event)
        if kind == "user_change":
            return self._from_user_change(event)

        log.debug("ignoring event type %s", kind)
        return None

    async def _from_message_event(self, event: dict[str, Any]) -> Envelope | None:
        subtype = event.get("subtype")
        channel_id = event.get("channel")
        channel = self._registry.channels.get(channel_id) if channel_id else None
        if channel is None:
            log.warning("message in unknown channel %s, deferring", channel_id)
            return None

        if subtype == "message_deleted":
            deleted_ts = event.get("deleted_ts")
            if not deleted_ts:
                return None
            return envelopes.delete_envelope(
                entity_id=channel.message_entity_id(deleted_ts),
                partition_key=channel.entity_id,
            )

        if subtype == "message_changed":
            inner = event.get("message") or {}
            return self.message_message(
                Message.model_validate(inner), channel, change=ChangeKind.UPDATED
            )

        return self.message_message(
            Message.model_validate(event), channel, change=ChangeKind.CREATED
        )

    async def _from_channel_event(self, event: dict[str, Any]) -> Envelope | None:
        raw_channel = event.get("channel")
        channel_id = (
            raw_channel.get("id") if isinstance(raw_channel, dict) else raw_channel
        )
        if not channel_id:
            return None

        await self._registry.refresh_channel(self._client, channel_id)
        channel = self._registry.channels.get(channel_id)
        if channel is None or not channel.is_member:
            return None
        return await self.channel_message(channel)

    def _from_user_change(self, event: dict[str, Any]) -> Envelope | None:
        raw_user = event.get("user")
        if not isinstance(raw_user, dict):
            return None
        user = User.model_validate(raw_user)
        self._registry.upsert_user(user)
        return self.workspace_message(users=[user], replace_roster=False)
