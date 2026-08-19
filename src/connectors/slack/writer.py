"""Write-scoped Slack access, used only by the seeder.

Kept out of SlackClient on purpose. That client is read-only by construction —
it issues nothing but GETs — and that property is worth more than the code it
would save to merge the two. The ingestion path cannot create channels or post
messages even if it is wrong; only this module can, and only the seeder imports it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from connectors.slack.client import (
    BASE_URL,
    DEFAULT_RPM,
    MAX_ATTEMPTS,
    PAGE_SIZE,
    RateLimiter,
    SlackError,
)
from connectors.slack.models import Channel, Message

log = logging.getLogger("connectors.slack.writer")

# chat.postMessage is also paced per channel (~1/s) in post(); the method RPM
# is the global cap. conversations.list is 20/min, so the seeder lists once.
WRITE_TIER_RPM: dict[str, int] = {
    "chat.postMessage": 50,
    "chat.getPermalink": 100,
    "conversations.open": 50,
    "conversations.create": 20,
    "conversations.setTopic": 50,
    "conversations.setPurpose": 50,
    "conversations.join": 50,
    "conversations.archive": 20,
    "conversations.list": 20,
    "conversations.history": 50,
    "conversations.replies": 50,
    "auth.test": 100,
}

POST_GAP_SECONDS = 1.05


class SlackWriter:
    """Creates channels and posts threads. Async context manager."""

    def __init__(self, bot_token: str, *, timeout: float = 30.0) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        self._token = bot_token
        self._timeout = timeout
        self._limiter = _WriteLimiter()
        self._http: httpx.AsyncClient | None = None
        self._last_post: dict[str, float] = {}
        self._slot_lock = asyncio.Lock()

    async def __aenter__(self) -> SlackWriter:
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=self._timeout,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def _call(
        self, method: str, *, http_method: str = "POST", **params: Any
    ) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("SlackWriter must be used as an async context manager")

        payload = {k: v for k, v in params.items() if v is not None}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire(method)
            try:
                if http_method == "GET":
                    resp = await self._http.get(f"/{method}", params=payload)
                else:
                    resp = await self._http.post(f"/{method}", json=payload)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                await self._backoff(attempt, f"transport error: {exc}")
                continue

            if resp.status_code == 429:
                delay = float(resp.headers.get("Retry-After", 1))
                log.warning("429 on %s, retrying in %.0fs", method, delay)
                await asyncio.sleep(delay + 0.5)
                continue

            if resp.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    resp.raise_for_status()
                await self._backoff(attempt, f"HTTP {resp.status_code}")
                continue

            resp.raise_for_status()
            body: dict[str, Any] = resp.json()
            if not body.get("ok"):
                error = body.get("error", "unknown_error")
                if error == "ratelimited" and attempt < MAX_ATTEMPTS:
                    await self._backoff(attempt, "ok:false ratelimited")
                    continue
                raise SlackError(method, error, body)
            return body

        raise SlackError(method, "retries_exhausted", {})

    @staticmethod
    async def _backoff(attempt: int, why: str) -> None:
        delay = min(2.0**attempt, 30.0)
        log.warning("%s; backing off %.1fs (attempt %d)", why, delay, attempt)
        await asyncio.sleep(delay)

    async def auth_test(self) -> dict[str, Any]:
        return await self._call("auth.test")

    # ----------------------------------------------------------- channels --

    async def list_channels(self) -> list[Channel]:
        channels: list[Channel] = []
        cursor: str | None = None
        while True:
            body = await self._call(
                "conversations.list",
                http_method="GET",
                types="public_channel,private_channel",
                exclude_archived="true",
                limit=PAGE_SIZE,
                cursor=cursor,
            )
            for raw in body.get("channels", []):
                channels.append(Channel.model_validate(raw))
            cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
            cursor = cursor.strip() if cursor else ""
            if not cursor:
                return channels

    async def create_channel(self, name: str, *, private: bool) -> Channel:
        body = await self._call(
            "conversations.create", name=name, is_private=private
        )
        return Channel.model_validate(body["channel"])

    async def join(self, channel_id: str) -> None:
        try:
            await self._call("conversations.join", channel=channel_id)
        except SlackError as exc:
            if exc.error not in {
                "already_in_channel",
                "method_not_supported_for_channel_type",
                "missing_scope",
            }:
                raise

    async def set_topic(self, channel_id: str, topic: str) -> None:
        await self._call("conversations.setTopic", channel=channel_id, topic=topic)

    async def set_purpose(self, channel_id: str, purpose: str) -> None:
        await self._call(
            "conversations.setPurpose", channel=channel_id, purpose=purpose
        )

    # ----------------------------------------------------------- messages --

    async def _pace_post(self, channel_id: str) -> None:
        """Reserve the next 1s slot for this channel without blocking others."""
        async with self._slot_lock:
            now = time.monotonic()
            wait = POST_GAP_SECONDS - (now - self._last_post.get(channel_id, 0.0))
            slot = now + max(wait, 0.0)
            self._last_post[channel_id] = slot
        if wait > 0:
            await asyncio.sleep(wait)

    async def history(self, channel_id: str) -> list[Message]:
        messages: list[Message] = []
        cursor: str | None = None
        while True:
            body = await self._call(
                "conversations.history",
                http_method="GET",
                channel=channel_id,
                limit=PAGE_SIZE,
                cursor=cursor,
            )
            for raw in body.get("messages", []):
                messages.append(Message.model_validate(raw))
            cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
            cursor = cursor.strip() if cursor else ""
            if not cursor:
                return messages

    async def replies(self, channel_id: str, thread_ts: str) -> list[Message]:
        messages: list[Message] = []
        cursor: str | None = None
        while True:
            body = await self._call(
                "conversations.replies",
                http_method="GET",
                channel=channel_id,
                ts=thread_ts,
                limit=PAGE_SIZE,
                cursor=cursor,
            )
            for raw in body.get("messages", []):
                messages.append(Message.model_validate(raw))
            cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
            cursor = cursor.strip() if cursor else ""
            if not cursor:
                return messages

    async def post(
        self, channel_id: str, text: str, *, thread_ts: str | None = None
    ) -> str:
        await self._pace_post(channel_id)
        body = await self._call(
            "chat.postMessage",
            channel=channel_id,
            text=text,
            thread_ts=thread_ts,
            unfurl_links=False,
            unfurl_media=False,
        )
        return body["ts"]

    async def open_dm(self, user_id: str) -> str:
        """The id of the DM conversation with this user, opening it if needed.

        `conversations.open` is idempotent: a DM channel between two parties is
        a single, permanent conversation, so this returns the same id every time
        rather than starting a new one. Posting to it is then an ordinary
        `chat.postMessage` against a channel id — which is why the DM executor
        needs no second code path.
        """
        body = await self._call("conversations.open", users=user_id)
        return body["channel"]["id"]

    async def permalink(self, channel_id: str, ts: str) -> str | None:
        """A stable public URL for a posted message, or None.

        Never raises. This is called *after* the message has already been
        posted, so letting a failure here propagate would report a completed
        write as a failed one — the worst outcome available, because the caller
        would reasonably retry it and post twice.
        """
        try:
            body = await self._call(
                "chat.getPermalink", http_method="GET", channel=channel_id, message_ts=ts
            )
        except (SlackError, httpx.HTTPError) as exc:
            log.warning("permalink for %s/%s unavailable: %s", channel_id, ts, exc)
            return None
        return body.get("permalink")


class _WriteLimiter(RateLimiter):
    """Same sliding window as the read client, with write-method RPMs filled in."""

    async def acquire(self, method: str) -> None:
        limit = WRITE_TIER_RPM.get(method, DEFAULT_RPM)
        while True:
            async with self._lock:
                now = time.monotonic()
                window = [t for t in self._calls.get(method, []) if now - t < 60.0]
                self._calls[method] = window
                if len(window) < limit:
                    window.append(now)
                    return
                sleep_for = 60.0 - (now - window[0])
            await asyncio.sleep(max(sleep_for, 0.05))
