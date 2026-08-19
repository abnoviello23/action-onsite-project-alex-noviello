"""Writing back to the sources the graph mirrors.

The read path treats Slack, Drive, and Notion as immutable: pollers observe,
generators normalise, nothing calls a source API outside the poller. This
package is the one exception, and it is deliberately narrow.

Four properties hold for every invocation, and each is enforced in a different
place so none of them depends on a caller remembering it:

  * **You may only act on what you can see.** `Runner.invoke` resolves the node
    through `query.visibility` before dispatch, and an invisible node is
    reported as absent rather than forbidden — the same collapse `SessionGraph`
    makes, for the same reason.
  * **Seeing it is not the same as being allowed to change it.** A spec names
    the `access_level` it needs and the runner compares it against the strongest
    grant the principal holds on the target, so a `drive:commenter` who can read
    every word of a document is still refused the overwrite.
  * **Every attempt is recorded.** The `action_invocation` row is written before
    the call goes out and updated after, so a row left in `running` is a crash
    mid-flight rather than an invisible gap.
  * **It is off unless switched on.** `ACTIONS_ENABLED` gates the whole surface,
    and defaults to false: reading a mirrored graph is safe, and posting to a
    real Slack channel is not.

The retrieval agent *plans* but does not execute. It answers with citations
carrying `native` source ids (`core.labels.native_keys`) and, when the question
asked for something to be done, a list of `PlannedAction`s naming targets it
actually read. Nothing in that path can write: the plan comes back to a caller,
who decides whether to send it to `actions.plan.run`. So a model in a loop still
cannot decide to write on its own — what changed is that it can now say exactly
what it would do, in a form that executes verbatim once somebody agrees.

Plans are not stored, and that is a consequence of the first two properties
rather than a shortcut. Every gate is re-evaluated per step at dispatch, so a
plan handed back by a client is checked exactly as one loaded from our own table
would be, and persisting it would add a second copy of the truth without adding
a guarantee.

Executors reuse the seeder's writers rather than reimplementing them. Those
modules were already the only write-scoped clients in the repo, already handle
this rate limiting and this pagination, and keeping the read clients GET-only is
a property worth preserving on both sides.
"""

from actions.plan import PlanError, PlanResult, StepOutcome
from actions.plan import run as run_plan
from actions.plan import validate as validate_plan
from actions.runner import ActionError, ActionNotAvailable, ActionResult, Runner

__all__ = [
    "ActionError",
    "ActionNotAvailable",
    "ActionResult",
    "PlanError",
    "PlanResult",
    "Runner",
    "StepOutcome",
    "run_plan",
    "validate_plan",
]
