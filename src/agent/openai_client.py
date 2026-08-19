"""An OpenAI-backed stand-in for `MessagesClient`, for evaluating extraction.

The repo speaks one wire format — Anthropic's `/v1/messages` — everywhere it
matters. This is a compatibility shim, not a second supported backend: it exists
so the extraction loop can be run end to end against a live model when an
Anthropic key is unavailable, and so prompt changes can be evaluated without a
billing dependency on one vendor.

It presents exactly `MessagesClient.create(...) -> Turn` and returns Anthropic-
shaped content blocks, so nothing downstream knows the difference. Three
translations do the work:

  * **Messages.** Anthropic keeps tool calls and their results inside `content`
    block lists on `assistant` and `user` turns. OpenAI puts calls on the
    assistant message as `tool_calls` and results in their own `tool` messages.
    `_to_openai` unrolls one into the other.
  * **Tools.** `{name, description, input_schema}` becomes
    `{"type": "function", "function": {name, description, parameters}}`.
  * **Arguments.** OpenAI returns them as a JSON *string*; Anthropic returns a
    parsed object. Parsed here, because a malformed one is a model error the
    caller should see as an empty input rather than as an exception.

What it deliberately does not carry over: `effort` has no OpenAI equivalent and
is dropped, and thinking blocks do not exist, so a turn's content is only text
and tool calls. Neither matters to the extractor.
"""

from __future__ import annotations

import asyncio
import json
import logging
import random
from typing import Any

import httpx

from agent.client import AnthropicError, Turn

log = logging.getLogger("agent.openai")

MAX_ATTEMPTS = 5
DEFAULT_TIMEOUT = 300.0
MAX_BACKOFF = 30.0

_RETRY_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504})


def _to_openai_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("input_schema") or {"type": "object"},
            },
        }
        for t in tools
    ]


def _to_openai_messages(
    system: str, messages: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Anthropic block lists to OpenAI's flat message sequence."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for message in messages:
        role = message["role"]
        content = message["content"]

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        if role == "assistant":
            text = "".join(
                b.get("text", "") for b in content if b.get("type") == "text"
            )
            calls = [
                {
                    "id": b["id"],
                    "type": "function",
                    "function": {
                        "name": b["name"],
                        "arguments": json.dumps(b.get("input") or {}),
                    },
                }
                for b in content
                if b.get("type") == "tool_use"
            ]
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue

        # A user turn carrying tool results becomes one `tool` message each;
        # anything else in it stays as user text.
        text_parts: list[str] = []
        for block in content:
            if block.get("type") == "tool_result":
                out.append(
                    {
                        "role": "tool",
                        "tool_call_id": block["tool_use_id"],
                        "content": str(block.get("content") or ""),
                    }
                )
            elif block.get("type") == "text":
                text_parts.append(block.get("text", ""))
        if text_parts:
            out.append({"role": "user", "content": "\n".join(text_parts)})
    return out


def _to_anthropic_turn(payload: dict[str, Any]) -> Turn:
    """One OpenAI choice back into the block shape the callers expect."""
    choice = (payload.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks: list[dict[str, Any]] = []

    if message.get("content"):
        blocks.append({"type": "text", "text": message["content"]})

    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        try:
            parsed = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError:
            # Surfaced as an empty input rather than raised: the dispatcher
            # already answers a missing argument with a message the model can
            # read and retry against, which is a better outcome than a crash.
            log.warning("unparseable tool arguments from %s", function.get("name"))
            parsed = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": call.get("id") or "call_0",
                "name": function.get("name", ""),
                "input": parsed,
            }
        )

    finish = choice.get("finish_reason")
    return Turn(
        {
            "content": blocks,
            "stop_reason": "tool_use" if finish == "tool_calls" else "end_turn",
            "usage": payload.get("usage") or {},
        }
    )


class OpenAIMessagesClient:
    """Async client for `POST /v1/chat/completions`. Use as a context manager."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required")
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout
        self._http: httpx.AsyncClient | None = None

    @property
    def model(self) -> str:
        return self._model

    async def __aenter__(self) -> OpenAIMessagesClient:
        self._http = httpx.AsyncClient(
            base_url=self._base_url,
            timeout=self._timeout,
            headers={
                "Authorization": f"Bearer {self._api_key}",
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
        # Accepted and ignored. This shim exists so extraction can be evaluated
        # without an Anthropic key, which means it has to take the same calls
        # the real client does — and caching there is a request-shaping detail
        # with no counterpart here. Dropping the parameter instead would make
        # `SEMANTIC_PROVIDER=openai` fail with a TypeError on the first call.
        cache_system: bool = False,
    ) -> Turn:
        if self._http is None:
            raise RuntimeError("client must be used as an async context manager")

        body: dict[str, Any] = {
            "model": model or self._model,
            "messages": _to_openai_messages(system, messages),
            "max_completion_tokens": max_tokens,
        }
        openai_tools = _to_openai_tools(tools)
        if openai_tools:
            body["tools"] = openai_tools
            if tool_choice and tool_choice.get("type") == "tool":
                body["tool_choice"] = {
                    "type": "function",
                    "function": {"name": tool_choice["name"]},
                }

        last: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                resp = await self._http.post("/v1/chat/completions", json=body)
            except httpx.RequestError as exc:
                last = exc
                await self._backoff(attempt, None, str(exc))
                continue

            if resp.status_code < 300:
                return _to_anthropic_turn(resp.json())

            error_type, message = _detail(resp)
            # Raised as `AnthropicError` on purpose: callers catch that type, and
            # a shim that threw a different one would need every caller changed
            # to use it.
            if resp.status_code not in _RETRY_STATUS or attempt == MAX_ATTEMPTS - 1:
                raise AnthropicError(resp.status_code, error_type, message)

            last = AnthropicError(resp.status_code, error_type, message)
            await self._backoff(attempt, resp.headers.get("retry-after"), message)

        raise AnthropicError(0, "exhausted", f"no attempts left: {last}")

    async def _backoff(self, attempt: int, retry_after: str | None, why: str) -> None:
        delay = min(2.0**attempt + random.random(), MAX_BACKOFF)
        if retry_after:
            try:
                delay = min(float(retry_after), MAX_BACKOFF)
            except ValueError:
                pass
        log.warning("openai retry %d in %.1fs: %s", attempt + 1, delay, why)
        await asyncio.sleep(delay)


def _detail(resp: httpx.Response) -> tuple[str, str]:
    try:
        payload = resp.json().get("error") or {}
        return payload.get("type", "unknown"), payload.get("message", resp.text[:200])
    except Exception:
        return "unknown", resp.text[:200]
