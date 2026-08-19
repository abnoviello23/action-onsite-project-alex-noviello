"""Slack non-content filter. Identity keys are minted in `graph.slack`."""

from __future__ import annotations

from connectors.slack.models import Message

NON_CONTENT_SUBTYPES: frozenset[str] = frozenset(
    {
        "channel_join",
        "channel_leave",
        "channel_topic",
        "channel_purpose",
        "channel_name",
        "channel_archive",
        "channel_unarchive",
        "channel_convert_to_private",
        "channel_convert_to_public",
        "pinned_item",
        "unpinned_item",
        "bot_add",
        "bot_remove",
        "tombstone",
    }
)


def is_content(message: Message) -> bool:
    if message.subtype in NON_CONTENT_SUBTYPES:
        return False
    return bool(message.text.strip() or message.files)
