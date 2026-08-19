"""Slack message text resolution.

Raw message text is markup, not prose: `<@U123>`, `<#C123|general>`,
`<https://x|label>`. Storing it unresolved makes bodies unreadable and poisons
retrieval, so every body goes through here before it becomes a node.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# One pass over every <...> token. Alternatives are ordered so the sigil-bearing
# forms win before the bare-link fallback.
_TOKEN = re.compile(
    r"<"
    r"(?:"
    r"@(?P<user>[UW][A-Z0-9]+)"
    r"|#(?P<channel>C[A-Z0-9]+)"
    r"|!subteam\^(?P<subteam>S[A-Z0-9]+)"
    r"|!(?P<special>here|channel|everyone)"
    r"|(?P<url>[^|>]+)"
    r")"
    r"(?:\|(?P<label>[^>]*))?"
    r">"
)

# Slack escapes exactly these three in user-typed text. Unescaping must happen
# after token parsing, or a literal &lt; would be mistaken for markup.
_ENTITIES = (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"))

_URL_LINK = re.compile(r"https?://[^\s<>|]+")


@dataclass(frozen=True)
class LinkTarget:
    """A URL found in a body. Stored on the message payload; the generator
    turns ones that resolve to a live node into a `mentions` edge."""

    url: str
    label: str | None = None


class TextResolver:
    """Renders Slack markup using cached user and channel names.

    Names are a display concern only — the ids extracted alongside are what the
    graph stores, so a stale cache degrades readability, never correctness.
    """

    def __init__(
        self,
        user_names: dict[str, str] | None = None,
        channel_names: dict[str, str] | None = None,
    ) -> None:
        self._users = user_names or {}
        self._channels = channel_names or {}

    def resolve(self, text: str) -> str:
        if not text:
            return ""
        return _unescape(_TOKEN.sub(self._render, text))

    def links(self, text: str) -> list[LinkTarget]:
        """URLs in the body, from both markup tokens and bare URLs."""
        found: dict[str, LinkTarget] = {}
        for match in _TOKEN.finditer(text or ""):
            url = match.group("url")
            if url and not url.startswith("mailto:"):
                found.setdefault(url, LinkTarget(url=url, label=match.group("label")))
        for bare in _URL_LINK.finditer(_TOKEN.sub("", text or "")):
            found.setdefault(bare.group(0), LinkTarget(url=bare.group(0)))
        return list(found.values())

    def set_user_name(self, user_id: str, name: str) -> None:
        self._users[user_id] = name

    def set_channel_name(self, channel_id: str, name: str) -> None:
        self._channels[channel_id] = name

    def mentioned_user_ids(self, text: str) -> list[str]:
        return _mentioned(text, "user")

    def mentioned_channel_ids(self, text: str) -> list[str]:
        return _mentioned(text, "channel")

    def name_map(self, text: str) -> tuple[dict[str, str], dict[str, str]]:
        """Names for only the ids this text mentions, as (users, channels).

        The generator needs these to render a body, and it is pure, so they have
        to travel with the message. Narrowing to what one message references is
        what keeps that from meaning a copy of the workspace roster on every
        envelope — most messages mention nobody and carry two empty dicts.
        """
        users = {
            uid: self._users[uid]
            for uid in self.mentioned_user_ids(text)
            if uid in self._users
        }
        channels = {
            cid: self._channels[cid]
            for cid in self.mentioned_channel_ids(text)
            if cid in self._channels
        }
        return users, channels

    # ------------------------------------------------------------ internal --

    def _render(self, match: re.Match[str]) -> str:
        label = match.group("label")

        if uid := match.group("user"):
            return f"@{self._users.get(uid) or label or uid}"

        if cid := match.group("channel"):
            return f"#{self._channels.get(cid) or label or cid}"

        if match.group("subteam"):
            # Group names need usergroups:read, which this app does not hold;
            # the label Slack embeds is the only name available.
            return label or f"@{match.group('subteam')}"

        if special := match.group("special"):
            return f"@{special}"

        url = match.group("url") or ""
        if url.startswith("mailto:"):
            return label or url.removeprefix("mailto:")
        # Keep the URL when the label merely repeats it, so links survive in
        # the body and in the message payload.
        if label and label != url:
            return f"{label} ({url})"
        return url


def _mentioned(text: str, group: str) -> list[str]:
    """Ordered and deduplicated, so envelopes are byte-stable across runs."""
    seen: list[str] = []
    for match in _TOKEN.finditer(text or ""):
        value = match.group(group)
        if value and value not in seen:
            seen.append(value)
    return seen


def _unescape(text: str) -> str:
    for entity, char in _ENTITIES:
        text = text.replace(entity, char)
    return text
