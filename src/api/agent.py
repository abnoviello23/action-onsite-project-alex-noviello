"""`POST /agent/query` — the permissioned retrieval endpoint.

This module's only real job beyond wiring is deciding **who the caller is**,
which is the one question the visibility kernel deliberately does not answer.
The kernel takes a seed identity and expands it; proving the caller is entitled
to that identity belongs here.

Until OAuth-proven `app_user MEMBER_OF slack:user:…` links exist, that proof is
a header — which is a "become anyone" switch, so it is gated rather than
merely present:

  * `AGENT_DEMO_IDENTITIES` is an explicit allowlist of identities the header
    may assume. Empty is the default, and an empty list disables the header
    outright rather than allowing everything.
  * An identity outside the list is refused whether or not it exists, so the
    header cannot be used to enumerate the identity table.

That leaves exactly one line to delete when real authentication lands: replace
`_identity` with the authenticated principal. Nothing downstream changes,
because the kernel never cared how the seed was proven.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from agent.client import AnthropicError, MessagesClient
from agent.orchestrator import AgentAnswer, answer_question
from common import config
from embed import ChunkerClient
from query.session import SessionGraph
from query.visibility import UnknownIdentity, Visibility

log = logging.getLogger("api.agent")

router = APIRouter(prefix="/agent", tags=["agent"])

DEMO_IDENTITY_HEADER = "X-Demo-Identity"

MAX_QUESTION_CHARS = 2_000


class AgentQuery(BaseModel):
    text: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


def seed_identity(header_value: str | None) -> str:
    """The seed identity for this request, or an HTTP error.

    Public because `api.actions` calls it too. Whatever proves who the caller is
    has to prove it identically for reads and writes, or the two drift and the
    weaker one becomes the way in.

    Replace this function — and only this function — when OAuth lands.
    """
    allowed = config.AGENT_DEMO_IDENTITIES
    if not allowed:
        raise HTTPException(
            status_code=503,
            detail=(
                "no authentication is configured; set AGENT_DEMO_IDENTITIES to "
                "the identities the demo header may assume"
            ),
        )
    if not header_value:
        raise HTTPException(
            status_code=401, detail=f"{DEMO_IDENTITY_HEADER} header is required"
        )
    if header_value not in allowed:
        # Same answer for "not allowed" and "does not exist": the allowlist is
        # operator-controlled, and a distinguishable response would turn this
        # header into an identity-enumeration oracle.
        raise HTTPException(
            status_code=403, detail=f"{header_value!r} is not a demo identity"
        )
    return header_value


def _messages_client(request: Request) -> MessagesClient:
    client = getattr(request.app.state, "messages", None)
    if client is None:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not set; the agent endpoint is disabled",
        )
    return client


def _can_act(request: Request) -> bool:
    """Whether a plan proposed by this run could ever be executed.

    Read off the same `app.state.actions` that `/actions/invoke` checks rather
    than from config directly, so "the agent may propose writes" and "this
    process can perform writes" cannot disagree. With actions off the retrieval
    prompt is byte-for-byte what it was before any of this existed, and no plan
    field is offered — a proposal nothing could run is worse than none, because
    the answer would describe work that was never going to happen.
    """
    return getattr(request.app.state, "actions", None) is not None


def _chunker(request: Request) -> ChunkerClient:
    client = getattr(request.app.state, "chunker", None)
    if client is None:
        raise HTTPException(status_code=503, detail="chunker client not ready")
    return client


@router.post("/query", response_model=AgentAnswer)
async def query(
    request: Request,
    body: AgentQuery,
    x_demo_identity: str | None = Header(default=None, alias=DEMO_IDENTITY_HEADER),
) -> AgentAnswer:
    identity_id = seed_identity(x_demo_identity)

    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="database pool not ready")

    # Resolved once per request and reused by every tool call, orchestrator and
    # walkers alike. Two queries, then it is a value object.
    async with pool.acquire() as conn:
        try:
            vis = await Visibility.resolve(conn, identity_id)
        except UnknownIdentity as exc:
            raise HTTPException(
                status_code=400, detail=f"unknown identity {exc}"
            ) from exc

    if vis.sees_nothing:
        log.info("%s holds no grants; answering empty", identity_id)

    graph = SessionGraph(pool, vis, _chunker(request))
    try:
        return await answer_question(
            _messages_client(request),
            graph,
            body.text,
            max_turns=config.AGENT_MAX_TURNS,
            max_walkers=config.AGENT_MAX_WALKERS,
            walker_hops=config.AGENT_WALKER_MAX_HOPS,
            effort=config.ANTHROPIC_EFFORT or None,
            can_act=_can_act(request),
        )
    except AnthropicError as exc:
        # Surface the upstream class rather than a blanket 500: a 429 upstream
        # should read as a 429 here, not as a bug in this service.
        status = 429 if exc.status == 429 else 502
        raise HTTPException(
            status_code=status, detail=f"model call failed: {exc.error_type}"
        ) from exc


# --------------------------------------------------------------- streaming --


async def _prepare(request: Request, header_value: str | None) -> SessionGraph:
    """Everything `/query` does before calling the orchestrator.

    Shared so the two endpoints cannot drift on the part that matters: who the
    caller is, and what that identity can see.
    """
    identity_id = seed_identity(header_value)

    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="database pool not ready")

    async with pool.acquire() as conn:
        try:
            vis = await Visibility.resolve(conn, identity_id)
        except UnknownIdentity as exc:
            raise HTTPException(
                status_code=400, detail=f"unknown identity {exc}"
            ) from exc

    if vis.sees_nothing:
        log.info("%s holds no grants; answering empty", identity_id)

    return SessionGraph(pool, vis, _chunker(request))


@router.post("/stream")
async def stream(
    request: Request,
    body: AgentQuery,
    x_demo_identity: str | None = Header(default=None, alias=DEMO_IDENTITY_HEADER),
) -> StreamingResponse:
    """The same run as `/query`, narrated over SSE.

    Identical retrieval — same orchestrator, same visibility, same answer — with
    the progress hook wired to a queue so a caller can watch turns and tool
    calls arrive instead of waiting two minutes at a blank screen. The final
    `answer` event carries exactly the `AgentAnswer` that `/query` would return.

    Auth is resolved before the response starts. A 401/403 has to be a status
    code, not an error event inside a 200 stream.
    """
    graph = await _prepare(request, x_demo_identity)
    client = _messages_client(request)
    can_act = _can_act(request)

    # Bounded: a stalled reader must not let the queue grow without limit. The
    # producer is the orchestrator, which is slow enough that this never fills
    # in practice.
    events: asyncio.Queue[dict | None] = asyncio.Queue(maxsize=256)
    loop = asyncio.get_running_loop()

    def emit(event: dict) -> None:
        # Called from the orchestrator's own coroutine, so a plain put_nowait
        # is safe; a full queue drops the narration rather than the answer.
        try:
            events.put_nowait(event)
        except asyncio.QueueFull:
            log.debug("progress queue full; dropping %s", event.get("type"))

    async def run() -> None:
        try:
            answer = await answer_question(
                client,
                graph,
                body.text,
                max_turns=config.AGENT_MAX_TURNS,
                max_walkers=config.AGENT_MAX_WALKERS,
                walker_hops=config.AGENT_WALKER_MAX_HOPS,
                effort=config.ANTHROPIC_EFFORT or None,
                can_act=can_act,
                on_event=emit,
            )
            await events.put({"type": "answer", "answer": answer.model_dump(mode="json")})
        except AnthropicError as exc:
            await events.put(
                {"type": "error", "message": f"model call failed: {exc.error_type}"}
            )
        except Exception as exc:  # noqa: BLE001 - the stream reports, never 500s
            log.exception("agent stream failed")
            await events.put({"type": "error", "message": str(exc)})
        finally:
            await events.put(None)

    async def body_iter() -> AsyncIterator[str]:
        task = asyncio.create_task(run())
        try:
            while True:
                event = await events.get()
                if event is None:
                    break
                # SSE frame: one `data:` line, terminated by a blank line.
                yield f"data: {json.dumps(event)}\n\n"
        finally:
            # The client hung up (or the generator was closed): stop the run
            # rather than leaving an orchestrator burning turns for nobody.
            if not task.done():
                task.cancel()

    return StreamingResponse(
        body_iter(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            # Proxies that buffer would defeat the entire point of this route.
            "X-Accel-Buffering": "no",
        },
    )
