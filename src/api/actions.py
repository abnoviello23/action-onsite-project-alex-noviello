"""`GET /actions`, `POST /actions/invoke`, `POST /actions/invoke-plan`.

The write surface. Same identity seam as `/agent/query`, deliberately: whatever
proves who the caller is has to prove it identically for reads and writes, or
the two drift and the weaker one becomes the way in. `seed_identity` is imported
from `api.agent` rather than duplicated for exactly that reason — there is one
function to replace when OAuth lands.

`GET /actions` is catalog-only and needs no node, so it is safe without a
principal. The two write routes resolve visibility first and report an
unavailable node as absent, never as forbidden.

`invoke-plan` takes the plan in the request body. It is not a session, and there
is no plan the server is holding on the caller's behalf: the retrieval agent
hands a plan out, a person reads it, and it comes back here to be run. Nothing
is trusted about the return trip, because every step is re-resolved and
re-checked at dispatch — which is what makes the round trip safe rather than
merely convenient. Pass `dry_run` to run every check and send nothing.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from actions import (
    ActionError,
    ActionNotAvailable,
    PlanError,
    PlanResult,
    Runner,
    run_plan,
)
from api.agent import DEMO_IDENTITY_HEADER, seed_identity
from core.actions import ACTIONS, ActionSpec, PlannedAction, actions_for
from query.visibility import UnknownIdentity, Visibility

log = logging.getLogger("api.actions")

router = APIRouter(prefix="/actions", tags=["actions"])


class ActionInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    node_type: str
    summary: str
    destructive: bool
    # Null where the source has no notion of one — see `core.actions`.
    requires_level: str | None = None
    returns: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)


class InvokeRequest(BaseModel):
    action: str
    entity_id: str
    params: dict[str, Any] = Field(default_factory=dict)


class InvokeResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    invocation_id: str
    action: str
    entity_id: str
    result_ref: str | None = None
    result: dict[str, str] = Field(default_factory=dict)


class PlanRequest(BaseModel):
    plan: list[PlannedAction]
    # Runs every gate — visibility, level, parameters, source ids, bindings —
    # and sends nothing. The cheapest way to find out whether a plan the agent
    # wrote would go through, and the only one that costs no writes.
    dry_run: bool = False


def _info(spec: ActionSpec) -> ActionInfo:
    return ActionInfo(
        name=spec.name,
        node_type=str(spec.node_type),
        summary=spec.summary,
        destructive=spec.destructive,
        requires_level=spec.requires_level,
        returns=list(spec.returns),
        params=spec.json_schema(),
    )


@router.get("", response_model=list[ActionInfo])
async def list_actions(node_type: str | None = None) -> list[ActionInfo]:
    """The catalog, optionally narrowed to one node type."""
    specs = actions_for(node_type) if node_type else tuple(ACTIONS.values())
    return [_info(s) for s in specs]


def _runner(request: Request) -> Runner:
    runner = getattr(request.app.state, "actions", None)
    if runner is None:
        raise HTTPException(
            status_code=503,
            detail="actions are disabled; set ACTIONS_ENABLED=true",
        )
    return runner


def _pool(request: Request):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="database pool not ready")
    return pool


async def _visibility(conn, identity_id: str) -> Visibility:
    try:
        return await Visibility.resolve(conn, identity_id)
    except UnknownIdentity as exc:
        raise HTTPException(status_code=400, detail=f"unknown identity {exc}") from exc


@router.post("/invoke", response_model=InvokeResponse)
async def invoke(
    request: Request,
    body: InvokeRequest,
    x_demo_identity: str | None = Header(default=None, alias=DEMO_IDENTITY_HEADER),
) -> InvokeResponse:
    identity_id = seed_identity(x_demo_identity)
    runner = _runner(request)

    async with _pool(request).acquire() as conn:
        vis = await _visibility(conn, identity_id)
        try:
            result = await runner.invoke(
                conn,
                vis,
                action_name=body.action,
                entity_id=body.entity_id,
                params=body.params,
            )
        except ActionNotAvailable as exc:
            # 404, not 403. The node may simply not exist, and the two must not
            # be distinguishable from outside.
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ActionError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return InvokeResponse(
        invocation_id=str(result.invocation_id),
        action=result.action,
        entity_id=result.entity_id,
        result_ref=result.result_ref,
        result=result.result,
    )


@router.post("/invoke-plan", response_model=PlanResult)
async def invoke_plan(
    request: Request,
    body: PlanRequest,
    x_demo_identity: str | None = Header(default=None, alias=DEMO_IDENTITY_HEADER),
) -> PlanResult:
    """Run a plan, in order, as one run.

    Two failure shapes, and they mean different things. A `PlanError` is a 400:
    the plan is unrunnable as written and nothing was sent. A plan that passes
    validation and then fails at step two is a **200** carrying
    `status='failed'` — because by then something has happened, the response is
    the account of what, and an error status would invite a retry that repeats
    the steps that already succeeded.
    """
    identity_id = seed_identity(x_demo_identity)
    runner = _runner(request)

    async with _pool(request).acquire() as conn:
        vis = await _visibility(conn, identity_id)
        try:
            return await run_plan(conn, runner, vis, body.plan, dry_run=body.dry_run)
        except PlanError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
