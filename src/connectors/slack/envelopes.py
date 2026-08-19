"""Self-contained Slack facts for the worker. No graph shape."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from connectors.slack.models import Channel, Message, User
from core.message import ChangeKind, Envelope
from core.types import NodeType


class SlackWorkspaceFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    team_id: str
    team_name: str
    domain: str | None = None
    users: list[User] = Field(default_factory=list)
    replace_roster: bool = True


class SlackChannelFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    channel: Channel
    team_id: str
    member_ids: list[str] = Field(default_factory=list)


class SlackMessageFacts(BaseModel):
    model_config = ConfigDict(frozen=True)

    message: Message
    channel: Channel
    user_names: dict[str, str] = Field(default_factory=dict)
    channel_names: dict[str, str] = Field(default_factory=dict)


def workspace_envelope(
    *,
    team_id: str,
    team_name: str,
    domain: str | None,
    users: list[User],
    replace_roster: bool = True,
) -> Envelope:
    return Envelope(
        node_type=NodeType.SLACK_WORKSPACE,
        entity_id=f"slack:{team_id}",
        partition_key=f"slack:{team_id}",
        payload=SlackWorkspaceFacts(
            team_id=team_id,
            team_name=team_name,
            domain=domain,
            users=users,
            replace_roster=replace_roster,
        ).model_dump(mode="json"),
    )


def channel_envelope(
    channel: Channel, *, team_id: str, member_ids: list[str]
) -> Envelope:
    return Envelope(
        node_type=NodeType.SLACK_CHANNEL,
        entity_id=channel.entity_id,
        partition_key=channel.entity_id,
        payload=SlackChannelFacts(
            channel=channel, team_id=team_id, member_ids=member_ids
        ).model_dump(mode="json"),
    )


def message_envelope(
    message: Message,
    channel: Channel,
    *,
    user_names: dict[str, str],
    channel_names: dict[str, str],
    change: ChangeKind = ChangeKind.CREATED,
) -> Envelope:
    return Envelope(
        node_type=NodeType.SLACK_MESSAGE,
        entity_id=message.entity_id(channel),
        partition_key=channel.entity_id,
        change=change,
        payload=SlackMessageFacts(
            message=message,
            channel=channel,
            user_names=user_names,
            channel_names=channel_names,
        ).model_dump(mode="json"),
    )


def delete_envelope(*, entity_id: str, partition_key: str) -> Envelope:
    return Envelope(
        node_type=NodeType.SLACK_MESSAGE,
        entity_id=entity_id,
        partition_key=partition_key,
        change=ChangeKind.DELETED,
    )
