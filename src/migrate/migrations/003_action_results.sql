-- What an action returned, and which run it belonged to.
--
-- Two additions to the action layer, from the same observation: a single write
-- is rarely the whole of an intent. "Prepare the document, file it, and post
-- where it went" is three writes that stand or fall together, and the log as it
-- stood could describe each of them and not the thing they were.
--
--
-- WHY THERE IS NO PLAN TABLE
--
-- A plan is a list of actions with resolved targets. It is produced by the
-- retrieval agent, read by a person, and handed back to be run — and at no
-- point does storing it make it more trustworthy, because `Runner.invoke`
-- re-resolves the node, re-checks visibility and level, and re-validates the
-- parameters on every step regardless of where the plan came from. The trust
-- boundary is the dispatcher, not the storage.
--
-- So a plan is not a record; a *run* is. `plan_id` is minted when execution
-- starts and stamped on each invocation, which makes the group recoverable
-- without a second table that could disagree with the first.


-- Everything the action declared it returns, not just the canonical reference:
-- a Drive file's `web_view_link`, a Slack message's `permalink`. `result_ref`
-- stays as it was — the one id worth reading at a glance — and this carries the
-- rest, which is what a later step of a plan binds to when it needs to say
-- where the thing it just made ended up.
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS result jsonb;

-- The run this invocation was part of, NULL for a direct /actions/invoke.
-- Minted per execution rather than per plan: planning something twice and
-- running it once should leave one id behind, not two.
ALTER TABLE action_invocation ADD COLUMN IF NOT EXISTS plan_id uuid;

-- Ascending, unlike the other two invocation indexes. Those answer "what
-- happened most recently to this node / by this principal"; this one answers
-- "replay this run in the order it went out", and reversing that would be
-- reading a story backwards.
CREATE INDEX IF NOT EXISTS action_invocation_plan_idx
    ON action_invocation (plan_id, created_at)
    WHERE plan_id IS NOT NULL;


-- ------------------------------------------------------- catalog projection --

-- The `action` table mirrors `core.actions`, and the specs grew three fields
-- that a caller reading the catalog out of the database would otherwise have to
-- guess at.

-- The access level a principal must hold on the target. NULL where the source
-- has no such notion — a Slack DM has no ACL, and the `person` it is addressed
-- through carries no grants — which is a real answer rather than a missing one.
--
-- Referencing access_level is what keeps a spec from naming a level that does
-- not exist: the reflection at boot fails loudly instead of installing a row
-- whose requirement can never be satisfied.
ALTER TABLE action ADD COLUMN IF NOT EXISTS requires_level text
    REFERENCES access_level(name);

-- Whether this replaces content rather than adding to it. Already on the spec
-- and already surfaced by the API; missing here only because the table predates
-- anything reading the catalog back out of it.
ALTER TABLE action ADD COLUMN IF NOT EXISTS destructive boolean NOT NULL
    DEFAULT false;

-- The fields the executor promises to put in its result, in order; the first is
-- the one recorded as `result_ref`. This is the vocabulary a plan's `{{step.
-- field}}` references are checked against, so it belongs beside the params
-- schema rather than only in code.
ALTER TABLE action ADD COLUMN IF NOT EXISTS returns jsonb NOT NULL DEFAULT '[]';
