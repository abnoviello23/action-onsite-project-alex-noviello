"""Exercises the agent loop with a scripted model, against the real database.

Everything except the model is real: the orchestrator loop, tool dispatch,
walker fan-out, the visibility kernel, and citation resolution all run against
seeded Postgres. The model is replaced by a small state machine that reads each
tool result and emits the next call — so the transcript it produces is a
transcript the real model could produce, and the plumbing under it is the
plumbing that ships.

That makes this the test for the parts a live run would not isolate anyway:
that two principals running the *same* plan get different answers, that
parallel tool results go back in one user message, and that a citation the
principal cannot see is dropped rather than rendered.

    docker compose --profile verify run --rm verify-agent
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from agent.client import Turn
from agent.orchestrator import answer_question
from common import db
from query.session import SessionGraph
from query.visibility import Visibility

APP = "slack:user:U0BQQ49NE9K"  # sees hl-legal, no public channels
TEST1 = "slack:user:U0BQRT4EVL6"  # workspace member only — must not see hl-legal

TARGET_CHANNEL = "hl-legal"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(label)


def _block(name: str, args: dict[str, Any], idx: int) -> dict[str, Any]:
    return {"type": "tool_use", "id": f"toolu_{idx}", "name": name, "input": args}


def _turn(content: list[dict[str, Any]], stop: str) -> Turn:
    return Turn({"content": content, "stop_reason": stop, "usage": {}})


def _last_results(messages: list[dict[str, Any]]) -> list[Any]:
    """Parse the tool_result payloads from the most recent user message."""
    for message in reversed(messages):
        if message["role"] != "user" or not isinstance(message["content"], list):
            continue
        out = []
        for block in message["content"]:
            if block.get("type") != "tool_result":
                continue
            try:
                out.append(json.loads(block["content"]))
            except (json.JSONDecodeError, TypeError):
                out.append(block["content"])
        return out
    return []


class ScriptedClient:
    """Stands in for `MessagesClient`. Records every request it is handed."""

    def __init__(self) -> None:
        self.model = "scripted"
        self.orchestrator_calls = 0
        self.walker_calls = 0
        # Every `messages` list this was asked to complete, for shape assertions.
        self.seen: list[list[dict[str, Any]]] = []

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
    ) -> Turn:
        self.seen.append([dict(m) for m in messages])
        # By name, not by identity: the orchestrator's schemas are built per
        # call so their node-type enums pick up semantic types registered after
        # import, which means no two calls share a dict object.
        offered = {t["name"] for t in tools or ()}
        is_orchestrator = "query_type" in offered
        if is_orchestrator:
            return self._orchestrate(messages)
        return self._walk(messages)

    # ---- orchestrator script -------------------------------------------------

    def _orchestrate(self, messages: list[dict[str, Any]]) -> Turn:
        step = self.orchestrator_calls
        self.orchestrator_calls += 1
        results = _last_results(messages)

        if step == 0:
            # Resolve the container by name.
            return _turn(
                [
                    _block(
                        "query_type",
                        {
                            "node_type": "slack:channel",
                            "predicates": [
                                {"field": "name", "op": "eq", "value": TARGET_CHANNEL}
                            ],
                        },
                        0,
                    )
                ],
                "tool_use",
            )

        if step == 1:
            hits = (results[0] or {}).get("results", []) if results else []
            if not hits:
                # Nothing visible. This is the branch the restricted principal
                # takes, and it must terminate rather than keep probing.
                return _turn(
                    [
                        _block(
                            "finish",
                            {
                                "answer": (
                                    f"I could not find a channel named "
                                    f"{TARGET_CHANNEL} in what is visible to you."
                                ),
                                "citations": [],
                            },
                            1,
                        )
                    ],
                    "tool_use",
                )
            channel_id = hits[0]["native"]["channel_id"]
            # Two calls in one turn: exercises the parallel path.
            return _turn(
                [
                    _block(
                        "query_type",
                        {
                            "node_type": "slack:message",
                            "predicates": [
                                {"field": "channel_id", "op": "eq", "value": channel_id}
                            ],
                            "limit": 5,
                        },
                        2,
                    ),
                    _block(
                        "query_type",
                        {
                            "node_type": "slack:message",
                            "predicates": [
                                {"field": "channel_id", "op": "eq", "value": channel_id},
                                {"field": "body", "op": "fts", "value": "the"},
                            ],
                            "limit": 3,
                        },
                        3,
                    ),
                ],
                "tool_use",
            )

        if step == 2:
            seeds = [
                r["entity_id"]
                for payload in results
                for r in (payload or {}).get("results", [])
            ][:2]
            return _turn(
                [
                    _block(
                        "explore",
                        {"entity_ids": seeds, "question": "What is this about?"},
                        4,
                    )
                ],
                "tool_use",
            )

        cited = [
            c
            for payload in results
            for note in (payload or {}).get("notes", [])
            for c in note.get("citations", [])
        ]
        return _turn(
            [
                _block(
                    "finish",
                    {
                        "answer": f"Summary of {TARGET_CHANNEL}.",
                        # A deliberately unresolvable id rides along: it must be
                        # dropped rather than rendered as a citation.
                        "citations": cited + ["slack:C_DOES_NOT_EXIST"],
                    },
                    5,
                )
            ],
            "tool_use",
        )

    # ---- walker script -------------------------------------------------------

    def _walk(self, messages: list[dict[str, Any]]) -> Turn:
        self.walker_calls += 1
        seed = messages[0]["content"].split("\n", 1)[0].removeprefix("Seed node: ")
        tool_turns = sum(1 for m in messages if m["role"] == "assistant")

        if tool_turns == 0:
            return _turn([_block("get", {"entity_id": seed}, 10)], "tool_use")
        if tool_turns == 1:
            return _turn(
                [_block("neighbors", {"entity_id": seed, "direction": "both"}, 11)],
                "tool_use",
            )
        return _turn(
            [{"type": "text", "text": f"Read the seed.\nCITED: {seed}"}], "end_turn"
        )


async def run(pool, conn, identity: str) -> tuple[Any, ScriptedClient]:
    vis = await Visibility.resolve(conn, identity)
    graph = SessionGraph(pool, vis, None)
    client = ScriptedClient()
    answer = await answer_question(
        client, graph, f"What is discussed in {TARGET_CHANNEL}?",
        max_turns=8, max_walkers=4, walker_hops=6,
    )
    return answer, client


async def main() -> None:
    conn = await db.connect()
    pool = await db.pool()

    print("\n== principal WITH access to the private channel ==")
    a_app, c_app = await run(pool, conn, APP)
    check("reached the finish branch", a_app.answer.startswith("Summary of"))
    check("ran multiple turns", a_app.turns_used >= 4, f"{a_app.turns_used} turns")
    check("walkers ran", c_app.walker_calls > 0, f"{c_app.walker_calls} walker calls")
    check("opened nodes in full", a_app.nodes_opened > 0, f"{a_app.nodes_opened}")
    check("produced citations", len(a_app.citations) > 0, f"{len(a_app.citations)}")
    check(
        "dropped the unresolvable citation",
        all(c.entity_id != "slack:C_DOES_NOT_EXIST" for c in a_app.citations),
    )
    check(
        "citations carry native source ids",
        all(c.native for c in a_app.citations),
        str(a_app.citations[0].native) if a_app.citations else "",
    )

    print("\n== same plan, principal WITHOUT access ==")
    a_t1, c_t1 = await run(pool, conn, TEST1)
    check("took the nothing-visible branch", "could not find" in a_t1.answer)
    check("no citations", a_t1.citations == [], str(len(a_t1.citations)))
    check("no walkers spawned", c_t1.walker_calls == 0, f"{c_t1.walker_calls}")
    check("no nodes opened", a_t1.nodes_opened == 0, f"{a_t1.nodes_opened}")

    print("\n== transcript shape ==")
    # The turn that issued two calls must be answered by ONE user message
    # holding both results. Splitting them is accepted by the API but trains
    # the model out of issuing parallel calls at all.
    parallel_ok = False
    for messages in c_app.seen:
        for i, m in enumerate(messages):
            if m["role"] != "assistant" or not isinstance(m["content"], list):
                continue
            uses = [b for b in m["content"] if b.get("type") == "tool_use"]
            if len(uses) < 2 or i + 1 >= len(messages):
                continue
            nxt = messages[i + 1]
            results = [
                b for b in nxt["content"] if b.get("type") == "tool_result"
            ]
            if nxt["role"] == "user" and len(results) == len(uses):
                parallel_ok = True
    check("parallel tool results returned in one user message", parallel_ok)

    ids_ok = True
    for messages in c_app.seen:
        pending: set[str] = set()
        for m in messages:
            if not isinstance(m["content"], list):
                continue
            if m["role"] == "assistant":
                pending = {
                    b["id"] for b in m["content"] if b.get("type") == "tool_use"
                }
            elif m["role"] == "user":
                got = {
                    b["tool_use_id"]
                    for b in m["content"]
                    if b.get("type") == "tool_result"
                }
                if pending and got != pending:
                    ids_ok = False
    check("every tool_use has a matching tool_result id", ids_ok)

    await conn.close()
    await pool.close()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
