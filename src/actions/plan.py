"""Running several actions as one intent.

A plan is a list of `PlannedAction`s and nothing else — no identifier issued in
advance, no row reserved, no state machine. It is produced by the retrieval
agent, handed to a caller, read by a person, and handed back to be run. Nothing
is stored in between, and nothing needs to be: `Runner.invoke` re-resolves the
target, re-checks visibility and level, and re-validates the parameters on every
step, so a plan arriving in a request body is worth exactly what one read out of
our own table would be. The trust boundary is the runner, which is why there is
no plan table to keep honest.

What a plan does add is **order and dataflow**, because the useful shape is
almost always "write the document, then post where it went" — and at the moment
the plan is written, the document does not exist and has no link. A step may
therefore reference an earlier step's result:

    {"action": "drive.create_file", "id": "a1", ...}
    {"action": "slack.reply_in_thread", "id": "a2",
     "params": {"text": "Draft is up: {{a1.web_view_link}}"}}

Deliberately not a template language. A reference names one earlier step and one
field that step's action *declared* it returns, and anything else is rejected
before the first write goes out. That last part is the rule the whole module is
arranged around:

**Everything that can be checked is checked before anything is sent.** Step
ordering, unknown actions, unresolvable references, malformed parameters — all
of it fails while the plan is still only a proposal. Discovering in the middle
that step 3 was malformed, having already posted step 2 to a channel, is the one
outcome no amount of logging makes good.

After that line, failure is **fail-stop**: the first error halts the plan and
every remaining step is recorded as skipped rather than dropped, because a plan
that half-ran must say so.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from uuid import uuid4

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from actions.runner import ActionError, ActionNotAvailable, Runner
from core.actions import ACTIONS, MAX_PLAN_STEPS, PlannedAction
from query.visibility import Visibility

log = logging.getLogger("actions.plan")

# `{{step_id.field}}`. Whitespace is tolerated inside the braces because a model
# writing one by hand will sooner or later add it; nothing else is.
_BINDING = re.compile(r"\{\{\s*([A-Za-z0-9_-]+)\.([a-z_]+)\s*\}\}")

# What a binding renders as in a dry run, where the producing step never ran.
# Non-empty on purpose: parameters are validated against the real params model
# during a preview, and several of them reject the empty string, so a blank
# stand-in would report a shape error that a real run would not have.
_UNRESOLVED = "<{step}.{field}>"


class PlanError(ValueError):
    """The plan cannot be run as written. Raised before anything is sent."""


class StepOutcome(BaseModel):
    """What became of one step."""

    model_config = ConfigDict(frozen=True)

    id: str
    action: str
    entity_id: str
    # 'ok' | 'error' | 'skipped' | 'checked'. `checked` is a dry run's verdict:
    # every gate passed and nothing was sent. Kept distinct from `ok` so a
    # preview can never be mistaken for a receipt.
    status: str
    invocation_id: str | None = None
    result: dict[str, str] = Field(default_factory=dict)
    # The parameters as they were actually dispatched, bindings resolved. This
    # is what a reader needs to answer "what did it post", and it is recorded
    # per invocation anyway; repeating it here keeps the plan's own account
    # readable without a join.
    params: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class PlanResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    # Issued at execution, not at planning: it identifies a *run*, which is the
    # thing `action_invocation` rows need to be grouped by. Planning something
    # twice and running it once should not leave two ids behind.
    plan_id: str
    # 'ok' | 'failed' | 'checked'
    status: str
    dry_run: bool = False
    steps: list[StepOutcome] = Field(default_factory=list)


def validate(plan: list[PlannedAction]) -> None:
    """Everything knowable without touching the database or the sources.

    Raises `PlanError` on the first problem, naming the step, because a plan is
    written by hand or by a model and both benefit more from one precise
    complaint than from a list.
    """
    if not plan:
        raise PlanError("the plan is empty")
    if len(plan) > MAX_PLAN_STEPS:
        raise PlanError(f"a plan may have at most {MAX_PLAN_STEPS} steps")

    seen: dict[str, PlannedAction] = {}
    for step in plan:
        if step.id in seen:
            raise PlanError(f"duplicate step id {step.id!r}")

        spec = ACTIONS.get(step.action)
        if spec is None:
            raise PlanError(
                f"step {step.id!r}: unknown action {step.action!r}; "
                f"available: {sorted(ACTIONS)}"
            )

        for source_id, field_name in _references(step.params):
            producer = seen.get(source_id)
            if producer is None:
                # Covers both "no such step" and "that step comes later". A
                # forward reference is unsatisfiable at the moment it is read,
                # and saying so in terms of ordering is the more useful error.
                raise PlanError(
                    f"step {step.id!r} references {source_id!r}, which is not "
                    f"an earlier step in this plan"
                )
            produced = ACTIONS[producer.action].returns
            if field_name not in produced:
                raise PlanError(
                    f"step {step.id!r} references {source_id}.{field_name}, but "
                    f"{producer.action} returns {list(produced)}"
                )

        seen[step.id] = step


async def run(
    conn: asyncpg.Connection,
    runner: Runner,
    vis: Visibility,
    plan: list[PlannedAction],
    *,
    dry_run: bool = False,
) -> PlanResult:
    """Run a validated plan in order, one step at a time.

    Sequential by construction. The steps of a plan are ordered because they are
    usually dependent, and running the independent ones concurrently would buy
    milliseconds in exchange for making "what had already happened when it
    failed" unanswerable.

    A dry run takes the same path through the same checks and stops short of the
    call. That is the point of it: a preview that validated differently from the
    dispatcher would be worth less than no preview at all.
    """
    validate(plan)

    plan_id = uuid4()
    results: dict[str, dict[str, str]] = {}
    outcomes: list[StepOutcome] = []
    failed = False

    for index, step in enumerate(plan):
        if failed:
            # Recorded, not dropped. A plan that stopped halfway has to be able
            # to say what it did not get to.
            outcomes.append(
                StepOutcome(
                    id=step.id,
                    action=step.action,
                    entity_id=step.entity_id,
                    status="skipped",
                    error="an earlier step failed",
                )
            )
            continue

        params = _resolve(step.params, results, dry_run=dry_run)
        try:
            if dry_run:
                await runner.check(
                    conn,
                    vis,
                    action_name=step.action,
                    entity_id=step.entity_id,
                    params=params,
                )
                outcomes.append(
                    StepOutcome(
                        id=step.id,
                        action=step.action,
                        entity_id=step.entity_id,
                        status="checked",
                        params=params,
                    )
                )
                continue

            outcome = await runner.invoke(
                conn,
                vis,
                action_name=step.action,
                entity_id=step.entity_id,
                params=params,
                plan_id=plan_id,
            )
        except (ActionError, ActionNotAvailable) as exc:
            log.info("plan %s stopped at step %s: %s", plan_id, step.id, exc)
            outcomes.append(
                StepOutcome(
                    id=step.id,
                    action=step.action,
                    entity_id=step.entity_id,
                    status="error",
                    params=params,
                    error=str(exc),
                )
            )
            failed = True
            continue

        results[step.id] = outcome.result
        outcomes.append(
            StepOutcome(
                id=step.id,
                action=step.action,
                entity_id=step.entity_id,
                status="ok",
                invocation_id=str(outcome.invocation_id),
                result=outcome.result,
                params=params,
            )
        )
        log.info(
            "plan %s step %d/%d: %s -> %s",
            plan_id,
            index + 1,
            len(plan),
            step.action,
            outcome.result_ref,
        )

    if dry_run:
        status = "failed" if failed else "checked"
    else:
        status = "failed" if failed else "ok"
    return PlanResult(
        plan_id=str(plan_id), status=status, dry_run=dry_run, steps=outcomes
    )


# ------------------------------------------------------------------ bindings --


def _references(value: Any) -> list[tuple[str, str]]:
    """Every `{{step.field}}` anywhere in a parameter tree."""
    if isinstance(value, str):
        return _BINDING.findall(value)
    if isinstance(value, dict):
        return [ref for item in value.values() for ref in _references(item)]
    if isinstance(value, list):
        return [ref for item in value for ref in _references(item)]
    return []


def _resolve(
    value: Any, results: dict[str, dict[str, str]], *, dry_run: bool
) -> Any:
    """Substitute earlier results into a parameter tree.

    Recursive over dicts and lists so a nested parameter is not quietly exempt.
    Substitution is textual and happens inside the string, which is what lets a
    link sit in the middle of a sentence.

    A reference that survives `validate` but resolves to nothing means the
    producing step ran and did not return that field — a Slack post whose
    permalink lookup failed, say. It renders as the empty string rather than
    leaving `{{a1.permalink}}` in a message: the braces are worse, because they
    reach a human as a visible artefact of the machinery.
    """
    if isinstance(value, dict):
        return {k: _resolve(v, results, dry_run=dry_run) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve(v, results, dry_run=dry_run) for v in value]
    if not isinstance(value, str):
        return value

    def replace(match: re.Match[str]) -> str:
        step_id, field_name = match.group(1), match.group(2)
        if dry_run:
            return _UNRESOLVED.format(step=step_id, field=field_name)
        return results.get(step_id, {}).get(field_name, "")

    return _BINDING.sub(replace, value)


__all__ = [
    "MAX_PLAN_STEPS",
    "PlanError",
    "PlanResult",
    "StepOutcome",
    "run",
    "validate",
]
