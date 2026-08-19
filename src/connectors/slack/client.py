"""Slack Web API transport: auth, pagination, rate limiting, retries.

Only the handful of methods this pipeline needs are exposed. Each returns parsed
models, but `raw_*` variants hand back the untouched dict for the raw payload
log — replay depends on storing exactly what the API said, not our view of it.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from connectors.slack.models import AuthTest, Channel, Message, User

log = logging.getLogger("connectors.slack.client")

BASE_URL = "https://slack.com/api"

# Slack's published tiers, in requests per minute, for an *internal* app.
# Activating public distribution collapses conversations.history to 1/min, which
# would make backfill take days — hence the warning in the setup notes.
METHOD_TIER_RPM: dict[str, int] = {
    "conversations.history": 50,
    "conversations.replies": 50,
    "conversations.list": 20,
    "conversations.info": 50,
    "conversations.members": 100,
    "users.list": 20,
    "auth.test": 100,
}
DEFAULT_RPM = 20

MAX_ATTEMPTS = 5
PAGE_SIZE = 200


class SlackError(RuntimeError):
    """Slack answered HTTP 200 with ok:false."""

    def __init__(self, method: str, error: str, response: dict[str, Any]):
        super().__init__(f"{method} failed: {error}")
        self.method = method
        self.error = error
        self.response = response


class RateLimiter:
    """Sliding-window limiter, one window per method tier.

    In-process only. K workers each running one of these means K times the real
    request rate, so the shared Redis-backed bucket replaces this before the
    worker fleet grows past one — the call sites do not change.
    """

    def __init__(self) -> None:
        self._calls: dict[str, list[float]] = {}
        self._lock = asyncio.Lock()

    async def acquire(self, method: str) -> None:
        limit = METHOD_TIER_RPM.get(method, DEFAULT_RPM)
        while True:
            async with self._lock:
                now = time.monotonic()
                window = [t for t in self._calls.get(method, []) if now - t < 60.0]
                self._calls[method] = window
                if len(window) < limit:
                    window.append(now)
                    return
                sleep_for = 60.0 - (now - window[0])
            log.debug("rate limit on %s, sleeping %.1fs", method, sleep_for)
            await asyncio.sleep(max(sleep_for, 0.05))


class SlackClient:
    """Async Slack Web API client. Use as an async context manager."""

    def __init__(self, bot_token: str, *, timeout: float = 30.0) -> None:
        if not bot_token:
            raise ValueError("bot_token is required")
        self._token = bot_token
        self._timeout = timeout
        self._limiter = RateLimiter()
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> SlackClient:
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

    # ---------------------------------------------------------------- core --

    async def call(self, method: str, **params: Any) -> dict[str, Any]:
        """One Web API call, with tier limiting and bounded retries."""
        if self._http is None:
            raise RuntimeError("SlackClient must be used as an async context manager")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire(method)
            try:
                resp = await self._http.get(
                    f"/{method}",
                    params={k: v for k, v in params.items() if v is not None},
                )
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                await self._backoff(attempt, f"transport error: {exc}")
                continue

            # Slack signals throttling with 429 and an authoritative Retry-After.
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
                # 'ratelimited' can arrive in the body rather than as a 429.
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

    async def paginate(
        self, method: str, key: str, **params: Any
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield raw items across every page.

        Slack's cursor is opaque — pass it back untouched, never parse it. An
        empty-string next_cursor means the end, and it is easy to mistake for a
        present value, so the check is on truthiness after stripping.
        """
        cursor: str | None = None
        while True:
            body = await self.call(method, cursor=cursor, limit=PAGE_SIZE, **params)
            for item in body.get(key, []):
                yield item

            cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
            cursor = cursor.strip() if cursor else ""
            if not cursor:
                return

    # ------------------------------------------------------------- methods --

    async def auth_test(self) -> AuthTest:
        return AuthTest.model_validate(await self.call("auth.test"))

    async def list_users(self) -> list[User]:
        return [
            User.model_validate(item)
            async for item in self.paginate("users.list", "members")
        ]

    async def list_channels(
        self, *, include_private: bool = True, include_archived: bool = False
    ) -> list[Channel]:
        types = "public_channel,private_channel" if include_private else "public_channel"
        channels = [
            Channel.model_validate(item)
            async for item in self.paginate(
                "conversations.list",
                "channels",
                types=types,
                exclude_archived=not include_archived,
            )
        ]
        return channels

    async def channel_info(self, channel_id: str) -> Channel:
        body = await self.call("conversations.info", channel=channel_id)
        return Channel.model_validate(body["channel"])

    async def channel_member_ids(self, channel_id: str) -> list[str]:
        return [
            member
            async for member in self.paginate(
                "conversations.members", "members", channel=channel_id
            )
        ]

    async def history(
        self, channel_id: str, *, oldest: str | None = None
    ) -> AsyncIterator[tuple[Message, dict[str, Any]]]:
        """Top-level messages, newest first. Thread replies are not included —
        conversations.replies is a separate call per parent."""
        async for raw in self.paginate(
            "conversations.history", "messages", channel=channel_id, oldest=oldest
        ):
            yield Message.model_validate(raw), raw

    async def replies(
        self, channel_id: str, thread_ts: str
    ) -> AsyncIterator[tuple[Message, dict[str, Any]]]:
        """Replies in a thread.

        Slack echoes the parent back as the first item; callers filter it out
        rather than having it silently emitted twice.
        """
        async for raw in self.paginate(
            "conversations.replies", "messages", channel=channel_id, ts=thread_ts
        ):
            yield Message.model_validate(raw), raw
