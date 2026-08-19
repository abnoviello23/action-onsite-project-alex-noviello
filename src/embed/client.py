"""HTTP client for the BGE chunker container.

Same shape as the source clients: async context manager, bounded retries, no
SDK. Given document text, returns passages with embeddings. Query-time ANN
uses `embed_query` so the instruction prefix stays on the server.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from embed.models import Chunk, ChunkResponse, EmbedQueryResponse
from embed.policy import should_embed

log = logging.getLogger("embed.client")

MAX_ATTEMPTS = 5
# A 200k Drive export on CPU can sit in encode for tens of seconds.
DEFAULT_TIMEOUT = 120.0


class ChunkerError(RuntimeError):
    """The chunker answered with a non-success we will not retry."""

    def __init__(self, path: str, status: int, detail: str):
        super().__init__(f"{path} -> {status}: {detail}")
        self.path = path
        self.status = status
        self.detail = detail


class ChunkerClient:
    """Async client for `/chunk` and `/embed_query`. Use as a context manager."""

    def __init__(self, base_url: str, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    async def __aenter__(self) -> ChunkerClient:
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def chunk(
        self, content: str, *, title: str | None = None, kind: str | None = None
    ) -> list[Chunk]:
        """Passages for one document. Empty if the text is a label, not a body.

        `kind` is the node type the content came from, and passing it is what
        lets a semantic fact through. A one-line claim is indistinguishable from
        a channel name by shape alone; only the type says which it is.
        """
        if not should_embed(kind, content):
            return []
        body = await self._post(
            "/chunk",
            {"content": content, "title": title, "kind": kind},
        )
        return ChunkResponse.model_validate(body).chunks

    async def embed_query(self, text: str) -> list[float]:
        """Query vector for ANN. Empty text is a client error, not an empty hit."""
        body = await self._post("/embed_query", {"text": text})
        return EmbedQueryResponse.model_validate(body).embedding

    async def _post(self, path: str, payload: dict) -> dict:
        if self._http is None:
            raise RuntimeError("ChunkerClient must be used as an async context manager")

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                resp = await self._http.post(path, json=payload)
            except httpx.TransportError as exc:
                if attempt == MAX_ATTEMPTS:
                    raise
                await _backoff(attempt, f"transport error: {exc}")
                continue

            if resp.status_code == 503:
                if attempt == MAX_ATTEMPTS:
                    raise ChunkerError(path, 503, "model loading")
                await _backoff(attempt, "model loading")
                continue

            if resp.status_code >= 500:
                if attempt == MAX_ATTEMPTS:
                    raise ChunkerError(path, resp.status_code, _detail(resp))
                await _backoff(attempt, f"HTTP {resp.status_code}")
                continue

            if resp.status_code >= 400:
                raise ChunkerError(path, resp.status_code, _detail(resp))

            return resp.json()

        raise ChunkerError(path, 0, "retries_exhausted")


def _detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:200]
    if isinstance(body, dict):
        return str(body.get("detail") or body)
    return str(body)[:200]


async def _backoff(attempt: int, why: str) -> None:
    delay = min(2.0**attempt, 30.0)
    log.warning("%s; backing off %.1fs (attempt %d)", why, delay, attempt)
    await asyncio.sleep(delay)
