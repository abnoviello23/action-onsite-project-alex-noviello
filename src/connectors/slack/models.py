"""Pydantic models of the Slack Web API payloads we consume.

Every model ignores unknown fields: Slack adds keys continuously and a strict
model would turn a harmless API addition into an outage. The untouched dict is
what gets persisted to the raw payload log, so nothing is lost by ignoring here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

# Slack's "ts" is simultaneously a message id and a timestamp. It must be
# compared and transported as the original string — parsing to float loses
# precision and breaks id equality.
SlackTs = Annotated[str, StringConstraints(pattern=r"^\d+\.\d+$")]


def ts_to_datetime(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=UTC)


class SlackModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class AuthTest(SlackModel):
    team: str
    team_id: str
    user: str
    user_id: str
    bot_id: str | None = None
    # e.g. "https://acme-corp.slack.com/" — the subdomain is not derivable from
    # `team`, which is a display name and may contain spaces or differ entirely.
    url: str | None = None

    @property
    def team_domain(self) -> str | None:
        if not self.url:
            return None
        host = self.url.removeprefix("https://").removeprefix("http://").split("/")[0]
        subdomain = host.split(".")[0]
        return subdomain or None


class UserProfile(SlackModel):
    real_name: str | None = None
    display_name: str | None = None
    # Requires users:read.email. Bots and Slackbot never have one.
    email: str | None = None


class User(SlackModel):
    id: str
    name: str | None = None
    deleted: bool = False
    is_bot: bool = False
    profile: UserProfile = Field(default_factory=UserProfile)

    @property
    def best_name(self) -> str:
        return (
            self.profile.display_name
            or self.profile.real_name
            or self.name
            or self.id
        )


class ChannelTopic(SlackModel):
    value: str = ""
    last_set: int | None = None


class Channel(SlackModel):
    id: str
    name: str | None = None
    is_private: bool = False
    is_archived: bool = False
    is_general: bool = False
    is_shared: bool = False
    is_ext_shared: bool = False
    is_member: bool = False
    created: int | None = None
    # Slack user id of whoever created the channel. Stable; not an ACL signal.
    creator: str | None = None
    # conversations.info/list: last rename / topic / purpose. Membership
    # changes do not always advance it. Slack sends this one in MILLISECONDS
    # while every other channel stamp is in seconds; `_updated_seconds` below
    # normalizes it on the way in so nothing downstream has to remember that.
    updated: int | None = None
    num_members: int | None = None
    topic: ChannelTopic = Field(default_factory=ChannelTopic)
    purpose: ChannelTopic = Field(default_factory=ChannelTopic)

    @field_validator("topic", "purpose", mode="before")
    @classmethod
    def _topic(cls, value: object) -> object:
        return value or {}

    @field_validator("updated", mode="before")
    @classmethod
    def _updated_seconds(cls, value: object) -> object:
        """Milliseconds -> seconds, so `source_stamp` compares like with like.

        Mixing the two units made `max()` return the millisecond value every
        time, which is ~1000x too large: datetime.fromtimestamp then raised
        `year 58597 is out of range` and every channel envelope went to the DLQ.
        """
        if value is None:
            return None
        return int(value) // 1000

    @property
    def display(self) -> str:
        return self.name or self.id

    @property
    def entity_id(self) -> str:
        return f"slack:{self.id}"

    @property
    def source_stamp(self) -> int:
        """Best Slack-provided monotonic clock for this channel object."""
        return max(
            self.created or 0,
            self.updated or 0,
            self.topic.last_set or 0,
            self.purpose.last_set or 0,
        )

    def message_entity_id(self, ts: str) -> str:
        """Stable across edits — an edit changes content_version, never identity."""
        return f"slack:{self.id}:{ts}"


class MessageEdit(SlackModel):
    user: str | None = None
    ts: SlackTs


class MessageFile(SlackModel):
    id: str
    name: str | None = None
    mimetype: str | None = None
    # Slack file URLs require the bot token to fetch; they are not public links.
    url_private: str | None = None


class Message(SlackModel):
    """A message from conversations.history or conversations.replies.

    `user` and `bot_id` are mutually exclusive in practice: app-posted messages
    carry bot_id and no user.
    """

    ts: SlackTs
    text: str = ""
    # Absent on ordinary user messages; present on joins, renames, file shares.
    subtype: str | None = None
    user: str | None = None
    bot_id: str | None = None
    # Set on both the thread parent and its replies. Parent has thread_ts == ts.
    thread_ts: SlackTs | None = None
    reply_count: int | None = None
    edited: MessageEdit | None = None
    files: list[MessageFile] = Field(default_factory=list)

    def entity_id(self, channel: Channel) -> str:
        return channel.message_entity_id(self.ts)

    def permission_parent_entity_id(self, channel: Channel) -> str:
        """A reply hangs off its thread parent, a top-level message off the channel."""
        if self.is_thread_reply and self.thread_ts:
            return channel.message_entity_id(self.thread_ts)
        return channel.entity_id

    @property
    def is_thread_parent(self) -> bool:
        return self.thread_ts is not None and self.thread_ts == self.ts

    @property
    def is_thread_reply(self) -> bool:
        return self.thread_ts is not None and self.thread_ts != self.ts

    @property
    def effective_ts(self) -> str:
        """The timestamp that reflects the current content.

        An edit must advance this, otherwise the version guard rejects the
        updated body and edits silently never land.
        """
        return self.edited.ts if self.edited else self.ts

    @property
    def occurred_at(self) -> datetime:
        return ts_to_datetime(self.ts)

    @property
    def updated_at(self) -> datetime:
        return ts_to_datetime(self.effective_ts)
