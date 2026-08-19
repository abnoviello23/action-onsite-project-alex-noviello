"""One walker: read a seed node, move along its edges, report back.

Walkers are the depth half of the loop. Each gets one seed and one question,
sees only local graph moves, and returns a short note plus the entity ids it
actually read. They run concurrently under a semaphore; nothing they do is
shared, so a walker that fails or runs out of hops costs its own note and
nothing else.

The deliberate omission is search. A walker that discovers "this thread mentions
an RFC we don't have" cannot go looking for it — it says so in `needs_lookup`
and the orchestrator decides whether that is worth a turn. Letting walkers
search would move fan-out decisions off the one loop that has a budget.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from agent.client import AnthropicError, MessagesClient
from agent.tools import WALKER_TOOLS, run_walker_tool
from query.session import SessionGraph

log = logging.getLogger("agent.walker")

# Thinking is on by default and counts against this, so it is sized for a short
# note plus the reasoning that produced it, not for the note alone.
WALKER_MAX_TOKENS = 8_192

_SYSTEM = """\
You are exploring a mirrored knowledge graph of a company's Slack, Google Drive, \
and Notion. You have been given one node and one question. Answer only that \
question, from what you can actually read.

Tools: `get` reads a node in full; `neighbors` lists incident edges; `follow` \
narrows to one relation.

Direction is part of an edge's meaning, so read `relation` and `direction` \
together:
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

Some neighbours are inferred entities — a person, a task, a project — rather \
than documents. They have no text worth reading; treat them as junctions, and \
follow them to reach the documents they connect. Cite the documents, not the \
entity, when the answer is a fact about the world.

How to work:
  * Narrow with `node_types` when you want one slice whole rather than a mixed
    page. `neighbors(person, node_types=['fact'])` is everything the graph
    knows about that person, which is usually what you actually want.
  * When a node has more neighbours than you can read, pass `query` — the
    neighbours are then ranked by relevance to it and the ones it does not bear
    on are dropped. Without it you get the most recently updated, which for a
    busy channel or a well-documented person is rarely where the answer is.
    An empty ranked result means nothing there is about your question, which is
    itself an answer.
  * **Check `complete` on every neighbour result.** `false` means you were
    shown part of the list, not all of it — raise `limit` and ask again before
    concluding anything, and if you answer from a partial list, say so. Never
    report "there is nothing about X" from a result you know was clipped.
  * `get` the seed first. Decide what to follow from the relation, the peer's
    type, and its preview — do not open a node whose preview is clearly
    irrelevant just because it is adjacent.
  * Follow at most a few hops. Stop as soon as you can answer, or as soon as it
    is clear the answer is not near this seed.
  * A node that returns `found: false` is not there. Do not retry it, do not
    guess at what it might have been, and do not mention it.

When you are done, reply with plain text — no tool call. Give:
  * two or three sentences answering the question from what you read, or a
    plain statement that this seed does not answer it;
  * a line `CITED: <entity_id>, <entity_id>` listing only nodes you actually
    read;
  * if you saw a reference to something you could not reach from here — a named
    document, a channel, a person's page — a line `NEEDS: <what to look up>`.
    You cannot search; that line is how you ask for it.
"""


class WalkerNote(BaseModel):
    model_config = ConfigDict(frozen=True)

    seed_entity_id: str
    note: str
    citations: list[str] = Field(default_factory=list)
    # Something the walker saw referenced but could not reach by edge. The
    # orchestrator decides whether it is worth a turn.
    needs_lookup: str | None = None


async def walk(
    client: MessagesClient,
    graph: SessionGraph,
    seed_entity_id: str,
    question: str,
    *,
    max_hops: int,
    effort: str | None = None,
) -> WalkerNote:
    """Run one walker to completion. Never raises — a failed walker is a note."""
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": (
                f"Seed node: {seed_entity_id}\n"
                f"Question: {question}\n\n"
                f"Start by reading the seed."
            ),
        }
    ]

    for _hop in range(max_hops):
        try:
            turn = await client.create(
                system=_SYSTEM,
                messages=messages,
                tools=WALKER_TOOLS,
                max_tokens=WALKER_MAX_TOKENS,
                effort=effort,
            )
        except AnthropicError as exc:
            log.warning("walker %s failed: %s", seed_entity_id, exc)
            return WalkerNote(
                seed_entity_id=seed_entity_id,
                note=f"(walker failed: {exc.error_type})",
            )

        if turn.refused:
            return WalkerNote(
                seed_entity_id=seed_entity_id,
                note=f"(walker declined: {turn.refusal_detail})",
            )

        calls = turn.tool_uses()
        if not calls:
            return _parse(seed_entity_id, turn.text())

        # Verbatim: thinking blocks ride along here and the API rejects a turn
        # whose content has been rebuilt rather than echoed.
        messages.append({"role": "assistant", "content": turn.content})

        # Every result for this turn goes back in ONE user message. Splitting
        # them across messages is accepted but teaches the model to stop issuing
        # parallel calls, which is the opposite of what a walker should do.
        results = []
        for call in calls:
            text, is_error = await run_walker_tool(
                graph, call["name"], call.get("input") or {}
            )
            block: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": call["id"],
                "content": text,
            }
            if is_error:
                block["is_error"] = True
            results.append(block)
        messages.append({"role": "user", "content": results})

    log.info("walker %s hit the hop budget", seed_entity_id)
    return WalkerNote(
        seed_entity_id=seed_entity_id,
        note="(walker reached its hop budget without concluding)",
    )


async def walk_all(
    client: MessagesClient,
    graph: SessionGraph,
    seeds: list[str],
    question: str,
    *,
    max_hops: int,
    concurrency: int,
    effort: str | None = None,
) -> list[WalkerNote]:
    """Fan out over seeds. Concurrency is bounded because every walker shares
    one Postgres pool and one Anthropic rate limit."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(seed: str) -> WalkerNote:
        async with sem:
            return await walk(
                client, graph, seed, question, max_hops=max_hops, effort=effort
            )

    return list(await asyncio.gather(*(one(s) for s in seeds)))


def _parse(seed_entity_id: str, text: str) -> WalkerNote:
    """Split the note from its CITED / NEEDS lines.

    Tolerant on purpose: a walker that forgets the trailer still contributes its
    prose, and a malformed trailer costs the citations rather than the note.
    """
    note_lines: list[str] = []
    citations: list[str] = []
    needs: str | None = None

    for line in text.splitlines():
        stripped = line.strip()
        upper = stripped.upper()
        if upper.startswith("CITED:"):
            citations = [
                part.strip() for part in stripped[6:].split(",") if part.strip()
            ]
        elif upper.startswith("NEEDS:"):
            needs = stripped[6:].strip() or None
        else:
            note_lines.append(line)

    return WalkerNote(
        seed_entity_id=seed_entity_id,
        note="\n".join(note_lines).strip() or "(no findings)",
        citations=citations,
        needs_lookup=needs,
    )
