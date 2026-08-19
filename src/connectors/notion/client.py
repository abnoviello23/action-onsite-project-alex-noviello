"""Notion API transport: auth, version pinning, pagination, rate limiting.

Only the endpoints this pipeline needs are exposed. Read methods return parsed
models paired with the untouched dict, because replay depends on storing exactly
what the API said rather than our view of it.

Three Notion-specific rules are enforced here rather than left to call sites:

- `Notion-Version` is sent on every request and comes from config. Bumps are
  migrations, never implicit.
- `page_size` is always explicit. The 2026-02-01 version dropped the default
  from 100 to 50 silently, so relying on the default changes behaviour under a
  version bump you did not otherwise care about.
- Cursors are opaque: passed back untouched, never parsed, never persisted.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator
from typing import Any

import httpx

from connectors.notion.models import Block, Database, DataSource, Page, UserRef

log = logging.getLogger("connectors.notion.client")

BASE_URL = "https://api.notion.com"

# Notion publishes ~3 requests/second per integration, averaged, with short
# bursts tolerated. The limiter paces to that average; 429s are still handled
# because a concurrent seeder shares the same budget.
REQUESTS_PER_SECOND = 3.0

MAX_ATTEMPTS = 5
PAGE_SIZE = 100


class NotionError(RuntimeError):
    """A non-2xx response. Notion puts a stable `code` in the body."""

    def __init__(self, method: str, path: str, status: int, body: Any):
        code = body.get("code") if isinstance(body, dict) else None
        message = body.get("message") if isinstance(body, dict) else str(body)
        super().__init__(f"{method} {path} -> {status} {code}: {message}")
        self.status = status
        self.code = code or "unknown_error"
        self.body = body

    @property
    def is_missing(self) -> bool:
        """404 covers both a genuinely absent object and one the integration was
        never connected to. Notion refuses to distinguish them, by design."""
        return self.status == 404


class RateLimiter:
    """Token-bucket pacing at a fixed average rate.

    In-process, like the Slack one. All Notion traffic goes through the poller,
    so a single instance is the whole budget; that stops being true the moment a
    worker calls Notion directly, which is why fetching stays here.
    """

    def __init__(self, rate_per_second: float = REQUESTS_PER_SECOND) -> None:
        self._interval = 1.0 / rate_per_second
        self._next_at = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                if now >= self._next_at:
                    self._next_at = max(now, self._next_at) + self._interval
                    return
                sleep_for = self._next_at - now
            await asyncio.sleep(sleep_for)


class NotionClient:
    """Async Notion client. Use as an async context manager."""

    def __init__(self, token: str, version: str, *, timeout: float = 30.0) -> None:
        if not token:
            raise ValueError("token is required")
        if not version:
            raise ValueError("version is required; pin it explicitly")
        self._token = token
        self._version = version
        self._timeout = timeout
        self._limiter = RateLimiter()
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> NotionClient:
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._token}",
                "Notion-Version": self._version,
                "Content-Type": "application/json",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    # ---------------------------------------------------------------- core --

    async def call(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        if self._http is None:
            raise RuntimeError("NotionClient must be used as an async context manager")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            await self._limiter.acquire()
            try:
                resp = await self._http.request(method, path, json=json)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                await self._backoff(attempt, f"transport error: {exc}")
                continue

            if resp.status_code == 429:
                # Retry-After is authoritative, and is seconds rather than a
                # timestamp.
                delay = float(resp.headers.get("Retry-After", 1))
                log.warning("429 on %s %s, retrying in %.0fs", method, path, delay)
                await asyncio.sleep(delay + 0.5)
                continue

            if resp.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise NotionError(method, path, resp.status_code, resp.text)
                await self._backoff(attempt, f"HTTP {resp.status_code}")
                continue

            body = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                raise NotionError(method, path, resp.status_code, body)
            return body

        raise NotionError(method, path, 0, {"code": "retries_exhausted"})

    @staticmethod
    async def _backoff(attempt: int, why: str) -> None:
        delay = min(2.0**attempt, 30.0)
        log.warning("%s; backing off %.1fs (attempt %d)", why, delay, attempt)
        await asyncio.sleep(delay)

    async def paginate(
        self, method: str, path: str, *, json: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield raw items across every page.

        `has_more` is the loop condition rather than a non-empty `next_cursor`:
        a page can come back with fewer results than page_size and still have
        more behind it.
        """
        cursor: str | None = None
        while True:
            if method == "GET":
                query = f"?page_size={PAGE_SIZE}"
                if cursor:
                    query += f"&start_cursor={cursor}"
                payload = await self.call("GET", path + query)
            else:
                body = dict(json or {})
                body["page_size"] = PAGE_SIZE
                if cursor:
                    body["start_cursor"] = cursor
                payload = await self.call(method, path, json=body)

            for item in payload.get("results", []):
                yield item

            if not payload.get("has_more"):
                return
            cursor = payload.get("next_cursor")
            if not cursor:
                return

    # ------------------------------------------------------------- methods --

    async def bot_info(self) -> dict[str, Any]:
        """The full /v1/users/me payload.

        `bot.workspace_name` is the only workspace label the API exposes: there
        is no workspace id outside the OAuth token exchange, which an internal
        integration never performs.
        """
        return await self.call("GET", "/v1/users/me")

    async def me(self) -> UserRef:
        return UserRef.model_validate(await self.bot_info())

    async def list_users(self) -> list[UserRef]:
        return [
            UserRef.model_validate(item)
            async for item in self.paginate("GET", "/v1/users")
        ]

    async def search(
        self, *, object_type: str | None = None, newest_first: bool = True
    ) -> AsyncIterator[dict[str, Any]]:
        """Everything the integration is connected to.

        Sorted by last_edited_time descending, which makes it the closest thing
        Notion has to a change feed. It is eventually consistent and it never
        returns trashed objects — both of which the poller compensates for.
        """
        body: dict[str, Any] = {}
        if object_type:
            body["filter"] = {"property": "object", "value": object_type}
        if newest_first:
            body["sort"] = {"direction": "descending", "timestamp": "last_edited_time"}
        async for item in self.paginate("POST", "/v1/search", json=body):
            yield item

    async def page(self, page_id: str) -> tuple[Page, dict[str, Any]]:
        raw = await self.call("GET", f"/v1/pages/{page_id}")
        return Page.model_validate(raw), raw

    async def database(self, database_id: str) -> tuple[Database, dict[str, Any]]:
        raw = await self.call("GET", f"/v1/databases/{database_id}")
        return Database.model_validate(raw), raw

    async def data_source(
        self, data_source_id: str
    ) -> tuple[DataSource, dict[str, Any]]:
        raw = await self.call("GET", f"/v1/data_sources/{data_source_id}")
        return DataSource.model_validate(raw), raw

    async def query_data_source(
        self, data_source_id: str, *, newest_first: bool = True
    ) -> AsyncIterator[tuple[Page, dict[str, Any]]]:
        """Rows of a data source.

        Search alone is not sufficient for rows: it is eventually consistent and
        lags on database content, so the poller sweeps both.
        """
        body: dict[str, Any] = {}
        if newest_first:
            body["sorts"] = [
                {"timestamp": "last_edited_time", "direction": "descending"}
            ]
        async for item in self.paginate(
            "POST", f"/v1/data_sources/{data_source_id}/query", json=body
        ):
            yield Page.model_validate(item), item

    async def block(self, block_id: str) -> Block:
        return Block.from_payload(await self.call("GET", f"/v1/blocks/{block_id}"))

    async def block_children(self, block_id: str) -> list[dict[str, Any]]:
        return [
            item
            async for item in self.paginate("GET", f"/v1/blocks/{block_id}/children")
        ]
