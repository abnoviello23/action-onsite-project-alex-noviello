"""Anthropic Messages API over httpx.

Same shape as the source clients — async context manager, bounded retries, no
SDK. That is a repo convention with a stated reason (`requirements.txt`): a
handful of endpoints does not justify a vendor dependency, and the connectors
already pay the cost of hand-rolling.

Three things about this API that are easy to get wrong and expensive to debug:

**It is not the OpenAI wire format.** `POST /v1/messages`, `x-api-key` (not
`Authorization: Bearer`), and a required `anthropic-version` header. There is no
`/v1/chat/completions` here and an OpenAI client cannot talk to it.

**Adaptive thinking is on by default** on the current models — omitting the
`thinking` field does not mean "no thinking". Two consequences: `max_tokens`
bounds thinking *plus* visible text, so a budget sized for the answer alone
truncates mid-response; and the assistant turn comes back carrying thinking
blocks that must be echoed to the next request **unchanged**. `Turn.content`
is the raw block list for exactly that reason — never rebuild it from the parts
you cared about.

**`temperature`, `top_p`, and `top_k` are rejected.** Steer with the prompt.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any

import httpx

log = logging.getLogger("agent.client")

MAX_ATTEMPTS = 5
DEFAULT_TIMEOUT = 300.0
MAX_BACKOFF = 30.0

# Retried with backoff. 408/409 are transport-ish, 429 is rate limiting, 5xx and
# 529 (overloaded) are the server's problem. Everything else in 4xx is a bug in
# the request and retrying it just burns the budget.
_RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})


class AnthropicError(RuntimeError):
    """A non-success the client will not retry."""

    def __init__(self, status: int, error_type: str, message: str) -> None:
        super().__init__(f"{status} {error_type}: {message}")
        self.status = status
        self.error_type = error_type
        self.message = message


class Turn:
    """One assistant response, kept whole.

    `content` is the raw block list exactly as returned. Echo it back verbatim
    as the next assistant message — thinking blocks included, whether or not
    their text is populated. Reconstructing the list from the blocks you read
    drops the ones you did not, and the API rejects a modified turn.
    """

    __slots__ = ("raw",)

    def __init__(self, raw: dict[str, Any]) -> None:
        self.raw = raw

    @property
    def content(self) -> list[dict[str, Any]]:
        return self.raw.get("content") or []

    @property
    def stop_reason(self) -> str | None:
        return self.raw.get("stop_reason")

    @property
    def refused(self) -> bool:
        """Safety classifiers declined. `content` is empty or partial.

        Checked before reading text anywhere: a refusal is an HTTP 200, so code
        that indexes `content[0]` unconditionally raises on it.
        """
        return self.stop_reason == "refusal"

    @property
    def refusal_detail(self) -> str:
        details = self.raw.get("stop_details") or {}
        category = details.get("category") or "unspecified"
        return details.get("explanation") or f"declined ({category})"

    def text(self) -> str:
        return "\n".join(
            b.get("text", "") for b in self.content if b.get("type") == "text"
        ).strip()

    def tool_uses(self) -> list[dict[str, Any]]:
        return [b for b in self.content if b.get("type") == "tool_use"]

    def usage(self) -> dict[str, int]:
        return self.raw.get("usage") or {}


class MessagesClient:
    """Async client for `POST /v1/messages`. Use as a context manager."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        version: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._version = version
        self._model = model
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def model(self) -> str:
        return self._model

    async def __aenter__(self) -> MessagesClient:
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={
                "x-api-key": self._api_key,
                "anthropic-version": self._version,
                "content-type": "application/json",
            },
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._http is not None:
            await self._http.aclose()
            self._http = None

    async def create(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: dict[str, Any] | None = None,
        effort: str | None = None,
        model: str | None = None,
        cache_system: bool = False,
    ) -> Turn:
        if self._http is None:
            raise RuntimeError("MessagesClient must be used as an async context manager")

        body: dict[str, Any] = {
            "model": model or self._model,
            "max_tokens": max_tokens,
            "system": _system_blocks(system) if cache_system else system,
            "messages": messages,
        }
        if tools:
            body["tools"] = tools
        if tool_choice:
            # `{"type": "tool", "name": ...}` forces one specific tool, which is
            # how a structured-output call is made on this API: there is no
            # response_format field, so the schema travels as a tool the model
            # is required to call.
            body["tool_choice"] = tool_choice
        if effort:
            # Nested under output_config, not top level. A top-level `effort` is
            # silently ignored rather than rejected, which is worse.
            body["output_config"] = {"effort": effort}

        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = await self._http.post("/v1/messages", json=body)
            except httpx.RequestError as exc:
                last = exc
                await self._backoff(attempt, None, str(exc))
                continue

            if resp.status_code < 300:
                return Turn(resp.json())

            error_type, message = _detail(resp)
            if resp.status_code not in _RETRY_STATUS or attempt == MAX_ATTEMPTS - 1:
                raise AnthropicError(resp.status_code, error_type, message)

            last = AnthropicError(resp.status_code, error_type, message)
            await self._backoff(attempt, resp.headers.get("retry-after"), message)

        raise AnthropicError(0, "exhausted", f"no attempts left: {last}")

    async def _backoff(self, attempt: int, retry_after: str | None, why: str) -> None:
        # `retry-after` is the server telling us when it will be ready; honour it
        # over our own curve, and jitter the fallback so a fleet of workers that
        # hit the limit together does not retry in lockstep.
        delay = min(2.0**attempt + random.random(), MAX_BACKOFF)
        if retry_after:
            try:
                delay = min(float(retry_after), MAX_BACKOFF)
            except ValueError:
                pass
        log.warning("anthropic retry %d in %.1fs: %s", attempt + 1, delay, why)
        await asyncio.sleep(delay)


def _system_blocks(system: str) -> list[dict[str, Any]]:
    """The system prompt as one cacheable block.

    Caching is a prefix match over the rendered request, and the render order is
    `tools` -> `system` -> `messages`. A breakpoint on the last system block
    therefore caches the tool definitions with it — which is the whole stable
    half of an extraction request, re-sent identically for every document in the
    corpus and otherwise re-billed at full rate each time.

    Only worth setting where the prefix really is stable. Anything interpolated
    into `system` per call — a timestamp, the document's own id — moves the
    bytes and every request writes a fresh entry instead of reading one, which
    costs more than not caching at all. The extraction prompt is built from the
    ontology alone and holds still; the retrieval agent's is built the same way
    but is issued a handful of times per session rather than hundreds, so it is
    left uncached.

    A prefix below the model's minimum (1024 tokens on current Sonnet) silently
    does not cache — no error, just `cache_creation_input_tokens: 0`.
    """
    return [
        {
            "type": "text",
            "text": system,
            "cache_control": {"type": "ephemeral"},
        }
    ]


def _detail(resp: httpx.Response) -> tuple[str, str]:
    try:
        payload = resp.json().get("error") or {}
        return payload.get("type", "unknown"), payload.get("message", resp.text[:200])
    except Exception:
        return "unknown", resp.text[:200]
