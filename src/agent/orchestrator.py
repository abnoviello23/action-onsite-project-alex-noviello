"""The ReAct loop: query, search, and explore, in any order, until it can answer.

Not a plan-then-walk pipeline. Every tool stays available on every turn, because
the useful shape is a cycle: resolve a channel by name, bind its id, query its
messages, explore the best few, learn from a walker that a Drive document is
involved, query for that, explore again. A fixed phase order would have to
guess how many rounds of that a question needs.

Budgets are the only thing stopping it: turns, parallel walkers, and nodes
opened in full. Parallel tool calls inside one assistant turn cost one turn, so
the model is free to fan out within a turn and pays only for going around again.

Everything it can see has been through the visibility kernel. It is told to say
so plainly when nothing visible answers the question — a permission-correct
retrieval layer that speculates about the material it filtered out has leaked
exactly what it was built to withhold.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from agent.client import AnthropicError, MessagesClient
from agent.tools import (
    action_digest,
    orchestrator_tools,
    registry_digest,
    run_orchestrator_query,
    semantic_relation_digest,
)
from agent.walker import WalkerNote, walk_all
from core.actions import ACTIONS, MAX_PLAN_STEPS, PlannedAction
from query.models import NodeSummary
from query.session import NodeBudgetExceeded, SessionGraph

log = logging.getLogger("agent.orchestrator")

ORCHESTRATOR_MAX_TOKENS = 16_000

_SYSTEM_TEMPLATE = """\
You answer questions over a mirrored knowledge graph of one company's Slack, \
Google Drive, and Notion.

Everything you can reach has already been filtered to what this user is allowed \
to see. You will never be shown anything else, and you must never speculate \
about material you could not see or imply that anything exists beyond what you \
found. If the visible graph does not answer the question, say that plainly.

## Tools

`query_type` filters one node type by its columns — precise, and the right tool \
once you know what you are looking for. `semantic_search` finds documents by \
meaning — the right tool when you do not. `explore` sends parallel workers to \
read specific nodes and their neighbourhoods. `finish` answers and ends the run.

All four are available on every turn. The productive shape is usually a cycle: \
resolve a container by name, bind its id, query inside it, explore the best \
results, and query again for whatever those turn up. Several calls in one turn \
run in parallel and cost one turn — going around again costs another, so fan \
out when you can.

## Schema

Column names per node type; prefer the indexed ones, since filtering on them is \
what keeps a query from being capped.

Types marked `[inferred]` are not documents. They are entities this system \
extracted from documents — a person, a task, a project — and they are how you \
get from a name to everything about it without guessing at search terms. They \
carry no text of their own worth reading; their value is entirely in what they \
connect to, so query one to get its id and then follow its edges.

{registry}

## Relations

Edges are directed and carry no access. Read relation and direction together;
the same name means different things each way round:

  in / out          -> the container this node sits in (Drive folder, Notion
                       parent page). Slack messages do NOT use this.
  in / in           -> the things inside that container
  in_channel / out  -> the channel a Slack message is in
  in_channel / in   -> the messages in a channel
  in_thread / out   -> the message this reply hangs off
  in_thread / in    -> replies to this message
  next / out        -> the following message; next / in -> the previous one
  mentions / in     -> everything that links here (backlinks — often the richest
                       signal, and easy to forget to check)

{semantic_relations}

## How to work

Resolve containers before their contents: find the channel named 'eng', bind its \
`channel_id`, then query its messages — do not scan every message in the graph. \
Short Slack messages are not in the semantic index, so reach those with \
`query_type` and an `fts` predicate on `body`.

Questions about a person, a task, or a project are usually fastest through the \
inferred types: resolve the entity by name, then follow its edges to the \
documents. An inferred node is a claim this system made from documents, so cite \
the documents behind it rather than the entity itself when the answer is a fact \
about the world.

**When a question has more than one condition, split it.** "Who works on Atlas \
and was let go recently" is two conditions, and they are almost never recorded \
together — the project work is in one document and the departure in another. \
One search for the whole sentence matches neither condition well. Pass them to \
`find_entities` as separate constraints and let it intersect them:

    find_entities(person, ["works on the Atlas project",
                           "has left the company or been offboarded recently"])

Read `matched` against `total` before you conclude. An entity at 2/2 answers \
the question; one at 1/2 does not, however good its evidence looks. If nothing \
reaches the full count, the honest answer is that no one visible to you meets \
all of it — say that rather than promoting the best partial match. And if a \
constraint comes back in `unmatched_constraints`, nothing visible satisfied it \
at all, which is worth stating plainly.

Workers cannot search. When one reports that it needs something looked up, that \
is a request for you to issue on your next turn.

If a result says it was truncated, or a worker reports an incomplete neighbour \
list, say so in your answer — a capped search is not an exhaustive one.
{acting}
Budget: {max_turns} turns. Call `finish` before you run out.
"""

# Appended to the system prompt only when actions are switched on. Everything
# above it is unchanged by this section's presence, deliberately: a plain
# question must be answered the same way whether or not this process happens to
# be able to write.
_ACTING_TEMPLATE = """
## Acting

When the user asks you to *do* something — write a document, post a message, \
message a person — answer the question part as usual and also return a `plan`: \
the actions that would carry it out.

**A plan does not run when you write it.** It goes back to the person who asked, \
who reads it and decides whether to execute it. So write it to be read by \
someone who cannot see your search: name the target, say why you chose it, and \
put the exact text you would send in `params`. Never describe a write in prose \
instead of putting it in the plan, and never claim in your answer that you have \
done something — you have proposed it.

Actions available to you:

{actions}

Four rules, and the first is the one that goes wrong most often.

**Act only on a node you actually opened, and only on the right kind of node.** \
Every `entity_id` in a plan must come from your own results. An action names the \
node type it applies to: post a new message to a *channel*, reply to a \
*message*, create a file in a *folder*, append to a *page*. A plausible-looking \
id of the wrong type is the most common way a plan fails.

**Resolve "the right place" by looking, not by guessing.** "The correct folder", \
"the main thread", "the relevant channel" are questions about this graph, and \
you have the tools to answer them: find the folder that holds the documents of \
that kind, find the thread that is actually about the thing. Then say what you \
found in `rationale`. If nothing visible is a good target, do not invent one — \
return no plan and say what you could not find. A write to the wrong place is \
worse than no write, because somebody has to undo it.

**To use something a previous step produced, reference it.** At the moment you \
write the plan, a document you are about to create does not exist and has no \
link. Write `{{{{a1.web_view_link}}}}` inside a later step's text and it is \
substituted with the real value when the plan runs. You may only reference a \
field the earlier action lists under `returns`.

    a1  drive.create_file   -> returns file_id, web_view_link
    a2  slack.reply_in_thread  params.text: "Q3 summary is up: {{{{a1.web_view_link}}}}"

**Check the address before proposing to message a person.** `slack.dm` needs the \
person's `slack_user_id`, which is on the person node's payload — many people in \
this graph are known only by name, and a DM to one of those cannot be sent. Open \
the person first. If they have no Slack id, say so instead of planning the \
message.

## Spending your turns when you are acting

An acting request is two jobs — working out the answer, and working out where it \
goes — and you are paying for both out of the same {max_turns} turns. The second \
job is the one that gets squeezed, which is how a run ends with a good summary \
and nowhere to put it.

So resolve your targets early, alongside the research rather than after it. \
Finding the folder, the thread, or the person is usually one `query_type` call \
you can issue in the same turn as a search — and once you have their ids, you \
know the plan is possible and everything after that is content.

By roughly two thirds of your budget you should have every target id you need. \
If you do not, stop researching and write the plan with what you have: a plan \
covering most of what was asked, saying plainly what it left out, is worth far \
more than running out of turns with nothing proposed at all. Content is easier to \
shorten than targets are to find.

A plan may have at most {max_plan_steps} steps, and fewer is better. Keep it to \
the smallest number that does what was asked; if the request genuinely needs more \
than that, do the part you can and say what you left for a second pass.
"""


class Citation(BaseModel):
    """A cited node, with the ids an external tool needs to act on it.

    The graph is a mirror, so `entity_id` is only meaningful inside this
    service. `native` carries the source's own identifiers — `channel_id` +
    `ts`, `page_id`, `file_id` — which is what a Slack, Notion, or Drive MCP
    call needs as arguments.
    """

    model_config = ConfigDict(frozen=True)

    entity_id: str
    node_type: str | None = None
    label: str = ""
    native: dict[str, str] = Field(default_factory=dict)


class AgentAnswer(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str
    citations: list[Citation] = Field(default_factory=list)
    # What the agent proposes doing about it, empty unless the question asked
    # for something to be done. Nothing here has run: this endpoint cannot
    # write, and executing a plan is a separate, deliberate call to
    # `/actions/invoke-plan`. That separation is the reason the retrieval path
    # can be handed a request from anywhere and still be read-only.
    plan: list[PlannedAction] = Field(default_factory=list)
    turns_used: int = 0
    nodes_opened: int = 0
    # At least one tool hit its scan cap. Surfaced so a capped run is never
    # presented as an exhaustive one.
    truncated: bool = False


def _emit(on_event: Callable[[dict[str, Any]], None] | None, **payload: Any) -> None:
    """Report progress, if anyone is listening.

    Purely an observation channel: it never decides anything, and with no
    listener the loop below runs exactly as it did before. `/agent/query`
    passes nothing and is byte-for-byte unchanged; `/agent/stream` passes a
    queue writer so a caller can watch the run instead of waiting blind for two
    minutes. Failures here are swallowed — a broken listener must not take down
    a search that is otherwise fine.
    """
    if on_event is None:
        return
    try:
        on_event(payload)
    except Exception:  # noqa: BLE001 - a watcher is never worth an answer
        log.debug("progress listener raised; continuing", exc_info=True)


async def answer_question(
    client: MessagesClient,
    graph: SessionGraph,
    question: str,
    *,
    max_turns: int,
    max_walkers: int,
    walker_hops: int,
    effort: str | None = None,
    can_act: bool = False,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> AgentAnswer:
    system = _SYSTEM_TEMPLATE.format(
        registry=registry_digest(),
        semantic_relations=semantic_relation_digest(),
        acting=(
            _ACTING_TEMPLATE.format(
                actions=action_digest(),
                max_turns=max_turns,
                max_plan_steps=MAX_PLAN_STEPS,
            )
            if can_act
            else ""
        ),
        max_turns=max_turns,
    )
    messages: list[dict[str, Any]] = [{"role": "user", "content": question}]
    truncated = False

    for turn_index in range(max_turns):
        _emit(on_event, type="turn", index=turn_index + 1, of=max_turns)
        try:
            turn = await client.create(
                system=system,
                messages=messages,
                tools=orchestrator_tools(can_act),
                max_tokens=ORCHESTRATOR_MAX_TOKENS,
                effort=effort,
            )
        except AnthropicError as exc:
            log.error("orchestrator failed on turn %d: %s", turn_index, exc)
            raise

        if turn.refused:
            return AgentAnswer(
                answer=f"The request was declined: {turn.refusal_detail}",
                turns_used=turn_index + 1,
                nodes_opened=graph.nodes_opened,
                truncated=truncated,
            )

        calls = turn.tool_uses()
        if text := turn.text():
            _emit(on_event, type="thinking", text=text)
        if not calls:
            # Answered in prose without calling `finish`. Take it rather than
            # spending a turn insisting on the ceremony.
            return await _answer(
                graph,
                turn.text() or "No answer was produced.",
                [],
                [],
                turn_index + 1,
                truncated,
            )

        messages.append({"role": "assistant", "content": turn.content})

        # One tool call per slot, filled in place so the results keep the order
        # the model asked in. Pairing is by `tool_use_id` rather than position,
        # but a transcript that reads in a different order than it was written
        # is needlessly hard to follow when something goes wrong.
        results: list[dict[str, Any] | None] = [None] * len(calls)
        finish_args: dict[str, Any] | None = None
        pending: list[int] = []

        for index, call in enumerate(calls):
            name = call["name"]
            args = call.get("input") or {}
            _emit(on_event, type="tool", name=name, args=args)

            if name == "finish":
                finish_args = args
                # Still answered, so the transcript stays well-formed if a
                # sibling call in this turn needs its own result block.
                results[index] = {
                    "type": "tool_result",
                    "tool_use_id": call["id"],
                    "content": "ok",
                }
                continue
            pending.append(index)

        # Concurrently, because they are independent by construction: separate
        # `tool_use` blocks with separate results, none of which can observe
        # another. Running them in sequence made the system prompt's promise
        # that parallel calls "cost one turn" true about billing and false
        # about latency — and worse, it put `explore` in the same queue as
        # everything else, so a millisecond-scale query issued alongside a
        # fan-out waited on the whole walker pool before it started.
        if pending:
            outcomes = await asyncio.gather(
                *(_run_call(client, graph, calls[i], on_event,
                            max_walkers=max_walkers,
                            walker_hops=walker_hops,
                            effort=effort)
                  for i in pending)
            )
            for index, (block, hit_cap) in zip(pending, outcomes):
                results[index] = block
                truncated = truncated or hit_cap

        if finish_args is not None:
            return await _answer(
                graph,
                str(finish_args.get("answer") or "").strip() or "No answer was given.",
                list(finish_args.get("citations") or []),
                list(finish_args.get("plan") or []) if can_act else [],
                turn_index + 1,
                truncated,
            )

        messages.append({"role": "user", "content": [r for r in results if r]})

    log.info("orchestrator exhausted %d turns", max_turns)
    return AgentAnswer(
        answer=(
            "I ran out of turns before reaching a conclusion. What I found is "
            "partial; narrowing the question would help."
        ),
        turns_used=max_turns,
        nodes_opened=graph.nodes_opened,
        truncated=truncated,
    )


async def _run_call(
    client: MessagesClient,
    graph: SessionGraph,
    call: dict[str, Any],
    on_event: Callable[[dict[str, Any]], None] | None,
    *,
    max_walkers: int,
    walker_hops: int,
    effort: str | None,
) -> tuple[dict[str, Any], bool]:
    """Run one tool call to a result block. Returns `(block, hit_a_cap)`.

    Never raises: a tool that fails returns an error block the model can read
    and retry against, because these run under `gather` and an exception would
    take its siblings down with it.
    """
    name = call["name"]
    args = call.get("input") or {}
    hit_cap = False

    if name == "explore":
        text, is_error, hit_cap = await _explore(
            client, graph, args,
            max_walkers=max_walkers, walker_hops=walker_hops, effort=effort,
        )
    else:
        text, is_error = await run_orchestrator_query(graph, name, args)
        hit_cap = _reports_truncation(text)

    _emit(on_event, type="tool_result", name=name, chars=len(text), is_error=is_error)

    block: dict[str, Any] = {
        "type": "tool_result",
        "tool_use_id": call["id"],
        "content": text,
    }
    if is_error:
        block["is_error"] = True
    return block, hit_cap


async def _explore(
    client: MessagesClient,
    graph: SessionGraph,
    args: dict[str, Any],
    *,
    max_walkers: int,
    walker_hops: int,
    effort: str | None,
) -> tuple[str, bool, bool]:
    """Fan out walkers. Returns `(result_text, is_error, seeds_were_capped)`."""
    seeds = [s for s in (args.get("entity_ids") or []) if isinstance(s, str)]
    question = str(args.get("question") or "").strip()
    if not seeds:
        return "explore needs at least one entity_id", True, False
    if not question:
        return "explore needs a question for the workers", True, False

    capped = len(seeds) > max_walkers
    if capped:
        log.info("explore capped from %d to %d seeds", len(seeds), max_walkers)
        seeds = seeds[:max_walkers]

    notes = await walk_all(
        client,
        graph,
        seeds,
        question,
        max_hops=walker_hops,
        concurrency=max_walkers,
        effort=effort,
    )
    payload: dict[str, Any] = {"notes": [n.model_dump() for n in notes]}
    if capped:
        # Named rather than silently dropped: a truncated fan-out that reads as
        # complete is how an agent concludes from half the evidence.
        payload["note"] = (
            f"only the first {max_walkers} seeds were explored; "
            f"the rest were dropped"
        )
    return json.dumps(payload), False, capped


def _reports_truncation(payload: str) -> bool:
    try:
        return bool(json.loads(payload).get("truncated"))
    except (json.JSONDecodeError, AttributeError):
        return False


async def _answer(
    graph: SessionGraph,
    text: str,
    cited: list[Any],
    proposed: list[Any],
    turns_used: int,
    truncated: bool,
) -> AgentAnswer:
    """Resolve cited entity ids into citations carrying native source keys."""
    citations: list[Citation] = []
    seen: set[str] = set()

    for raw in cited:
        entity_id = raw if isinstance(raw, str) else None
        if not entity_id or entity_id in seen:
            continue
        seen.add(entity_id)
        try:
            node = await graph.get(entity_id)
        except NodeBudgetExceeded:
            # The model cited more than it opened. Drop the unresolvable tail
            # rather than failing an answer that is otherwise sound.
            log.info("citation budget exhausted at %s", entity_id)
            break
        if node is None:
            # Invisible or never existed — either way it is not citable.
            continue
        citations.append(
            Citation(
                entity_id=node.entity_id,
                node_type=node.node_type,
                label=node.label,
                native=node.native,
            )
        )

    plan, complaint = await _plan(graph, proposed)
    if complaint:
        text = f"{text}\n\n{complaint}"

    return AgentAnswer(
        answer=text,
        citations=citations,
        plan=plan,
        turns_used=turns_used,
        nodes_opened=graph.nodes_opened,
        truncated=truncated,
    )


async def _plan(
    graph: SessionGraph, proposed: list[Any]
) -> tuple[list[PlannedAction], str]:
    """Validate the proposed actions and label their targets for review.

    **A bad step withdraws the whole plan**, unlike a bad citation, which is
    dropped on its own. The difference is what the two are for: citations are
    evidence and a missing one weakens an answer, while a plan is a sequence
    somebody is about to authorise, and the steps of it are related. Silently
    executing four fifths of "write the document and post where it went" would
    post a link to nothing.

    Targets are resolved through the same permissioned `get` the walkers use, so
    a plan aimed at something invisible is caught here — and its label goes into
    the plan, because "post to C0421" is not something a person can approve and
    "post to #eng-rollout" is. None of this is a security check: `Runner.invoke`
    re-resolves every target at dispatch and does not care what is written here.

    Returns `(plan, complaint)`, where a non-empty complaint is appended to the
    answer. A withdrawn plan has to say so; the alternative is an answer that
    describes work nobody is going to do.
    """
    if not proposed:
        return [], ""

    steps: list[PlannedAction] = []
    for raw in proposed:
        if not isinstance(raw, dict):
            return [], "A plan was proposed in an unreadable form and withheld."
        try:
            step = PlannedAction.model_validate(raw)
        except ValidationError as exc:
            log.info("withdrew a malformed plan: %s", exc)
            return [], (
                "A plan was proposed but one of its steps was malformed, so the "
                "whole plan has been withheld rather than half-run."
            )
        if step.action not in ACTIONS:
            return [], (
                f"A plan was proposed using {step.action!r}, which is not an "
                f"action this system has, so it has been withheld."
            )
        steps.append(step)

    labelled: list[PlannedAction] = []
    for step in steps:
        try:
            node = await graph.get(step.entity_id)
        except NodeBudgetExceeded:
            # Out of budget to look, not evidence of a bad target. Pass it
            # through unlabelled; the runner checks it for real either way.
            log.info("node budget exhausted before labelling %s", step.entity_id)
            labelled.append(step)
            continue
        if node is None:
            return [], (
                f"A plan was proposed targeting {step.entity_id}, which is not "
                f"available, so it has been withheld."
            )
        labelled.append(
            step.model_copy(
                update={"target_label": node.label, "target_type": node.node_type}
            )
        )

    return labelled, ""


__all__ = [
    "AgentAnswer",
    "Citation",
    "NodeSummary",
    "PlannedAction",
    "WalkerNote",
    "answer_question",
]
