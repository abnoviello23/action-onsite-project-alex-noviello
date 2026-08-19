"""Exercises the semantic layer and the action catalog against the real database.

Everything except the model call is real: the ontology loads from
`semantic_config`, the extractor's tool loop runs against live resolution, facts
are written with real permission parents, and visibility is decided by the same
kernel `/agent/query` uses. The model is a scripted client that plays a fixed
sequence of tool calls, so what is under test is the plumbing rather than the
prompt.

The fixture is synthetic and self-cleaning. It builds its own two channels, its
own principals, and its own messages, which is what makes the permission
assertions exact — the seeded workspace has whatever ACL it has, and a test that
depended on it would be asserting against data it did not control.

The shape being tested is the one the design turns on:

    person:jane                    identity only, and no access of its own
         ^   ^
   about |   | about
         |   |
   fact -+   +- fact               each inherits its own document's access
   (private)    (public)

Alice, in the private channel, reads both notes. Bob reads one. Neither can tell
what the other can read.

Entity visibility is *derived* from those notes rather than granted, which the
suite tests in the only way that distinguishes the two: after the private
document alone, Bob cannot see Jane at all. Once a public document mentions her,
he can. Nothing was written to `access` in between.

    docker compose --profile verify run --rm verify-semantic
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from actions import ActionError, ActionNotAvailable, Runner
from agent.client import Turn
from common import config, db
from core.actions import ACTIONS, actions_for
from core.message import ChangeKind
from embed import ChunkerClient, should_embed
from query.compile import TypeQuery
from query.session import SessionGraph
from query.visibility import Visibility
from semantic.__main__ import Outcome, _handle_job
from semantic.config import (
    FACT_RELATION,
    FACT_TYPE,
    MENTIONS_RELATION,
    SemanticConfig,
)
from semantic.extract import Extractor
from semantic.models import SemanticJob
from semantic.registry import ActiveConfig
from semantic.registry import load as load_ontology
from semantic.store import SemanticStore, mint_entity_id, slugify

PREFIX = "verify-sem"

ALICE = f"{PREFIX}:alice"
BOB = f"{PREFIX}:bob"
EVERYONE = f"{PREFIX}:everyone"

PRIVATE_CHANNEL = f"{PREFIX}:channel:private"
PUBLIC_CHANNEL = f"{PREFIX}:channel:public"
PRIVATE_MESSAGE = f"{PREFIX}:message:private"
PUBLIC_MESSAGE = f"{PREFIX}:message:public"

PERSON_NAME = "Marisol Verify-Okonkwo"
PROJECT_NAME = "Verify Atlas Migration"
PERSON_ID = mint_entity_id("person", "name", PERSON_NAME)
PROJECT_ID = mint_entity_id("project", "name", PROJECT_NAME)

PRIVATE_STATEMENT = "Is being moved off the migration after the incident review."
PRIVATE_STATEMENT_2 = "Was the escalation owner during the outage."
PUBLIC_STATEMENT = "Leads the Verify Atlas Migration rollout."
PROJECT_STATEMENT = "The rollout is scheduled for the end of the quarter."
UPDATED_STATEMENT = "Has returned to the migration as reviewer."

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(label)


# --------------------------------------------------------------- scripted --


# A refusal comes back as HTTP 200 with stop_reason 'refusal' and no content,
# which is why `Turn.refused` exists and why code that indexes content[0] breaks
# on it. The extractor must read this as "not evaluated", never as "nothing to
# say".
REFUSAL = "refusal"


class ScriptedClient:
    """Plays a fixed sequence of tool-call turns.

    Matches `MessagesClient.create`'s surface exactly, because the extractor's
    handling of refusals, prose-instead-of-tools, and the turn budget is part of
    what ships and should not be bypassed by a different-shaped double. Entity
    ids are hardcoded in the scripts rather than threaded from results, which is
    itself an assertion: they are deterministic, so a script can know them.
    """

    def __init__(self, turns: list[list[tuple[str, dict[str, Any]]]] | str) -> None:
        # The REFUSAL sentinel stands in for a safety decline, which arrives as
        # an HTTP 200 carrying stop_reason 'refusal' and no content blocks.
        self._refuse = turns == REFUSAL
        self._turns = [] if self._refuse else turns
        self.calls = 0
        self.last_system = ""
        self.results: list[Any] = []

    async def create(self, *, system: str, messages: list[dict], **_: Any) -> Turn:
        self.last_system = system
        if messages and isinstance(messages[-1].get("content"), list):
            self.results.append([b.get("content") for b in messages[-1]["content"]])
        index = self.calls
        self.calls += 1
        if self._refuse:
            return Turn(
                {
                    "content": [],
                    "stop_reason": "refusal",
                    "stop_details": {"category": "test", "explanation": "declined"},
                    "usage": {},
                }
            )
        if index < len(self._turns):
            blocks = [
                {
                    "type": "tool_use",
                    "id": f"toolu_{index}_{i}",
                    "name": name,
                    "input": args,
                }
                for i, (name, args) in enumerate(self._turns[index])
            ]
        else:
            blocks = [
                {"type": "tool_use", "id": "toolu_end", "name": "finish", "input": {}}
            ]
        return Turn({"content": blocks, "stop_reason": "tool_use", "usage": {}})


def private_script() -> list[list[tuple[str, dict[str, Any]]]]:
    """Search, find nothing, create, record two private claims, link."""
    return [
        [("search_entities", {"query": "Marisol"})],
        [
            ("create_entity", {"type": "person", "identity": {"name": PERSON_NAME}}),
            ("create_entity", {"type": "project", "identity": {"name": PROJECT_NAME}}),
        ],
        [
            (
                "add_fact",
                {"subject_entity_id": PERSON_ID, "statement": PRIVATE_STATEMENT},
            ),
            (
                "add_fact",
                {"subject_entity_id": PERSON_ID, "statement": PRIVATE_STATEMENT_2},
            ),
        ],
        [
            # Asserted only here, in the private channel. Both endpoints will
            # also be visible publicly, so this is the edge that leaked.
            (
                "link_entities",
                {
                    "from_entity_id": PERSON_ID,
                    "relation": "secretly_blocked_by",
                    "to_entity_id": PROJECT_ID,
                },
            )
        ],
        [("finish", {"note": "two private claims and a private link"})],
    ]


def public_script() -> list[list[tuple[str, dict[str, Any]]]]:
    """Search by short form, bind what already exists, claim, and link.

    The search is on "Marisol" and "Atlas" — neither is the stored name — which
    is the case exact matching could not resolve and the reason the tools were
    split. `use_entity` is the model committing to a result.
    """
    return [
        [
            ("search_entities", {"query": "Marisol"}),
            ("search_entities", {"query": "Atlas"}),
        ],
        [
            ("use_entity", {"entity_id": PERSON_ID}),
            ("use_entity", {"entity_id": PROJECT_ID}),
        ],
        [
            (
                "add_fact",
                {"subject_entity_id": PERSON_ID, "statement": PUBLIC_STATEMENT},
            ),
            (
                "add_fact",
                {"subject_entity_id": PROJECT_ID, "statement": PROJECT_STATEMENT},
            ),
            (
                "link_entities",
                {
                    "from_entity_id": PERSON_ID,
                    "relation": "works_on",
                    "to_entity_id": PROJECT_ID,
                },
            ),
        ],
        [("finish", {"note": "claim and link"})],
    ]


def updated_script() -> list[list[tuple[str, dict[str, Any]]]]:
    """Re-extraction of the private message after an edit."""
    return [
        [("search_entities", {"query": "Marisol"})],
        [("use_entity", {"entity_id": PERSON_ID})],
        [
            (
                "add_fact",
                {"subject_entity_id": PERSON_ID, "statement": UPDATED_STATEMENT},
            )
        ],
        [("finish", {})],
    ]


# ---------------------------------------------------------------- fixture --

_NOW = datetime.now(UTC)


async def cleanup(conn) -> None:
    await conn.execute(
        "DELETE FROM action_invocation WHERE node_id IN "
        "(SELECT id FROM node WHERE entity_id LIKE $1)",
        f"{PREFIX}%",
    )
    # Scoped two ways, and both are needed.
    #
    # A fact is owned by the document it was read out of, so this suite's facts
    # are exactly those whose permission parent is one of its own messages —
    # which is also how the retraction path finds them. But the suite writes
    # facts about `PERSON_ID` and `PROJECT_ID` too, whose ids are minted from
    # the fixture's names rather than prefixed, so those are named explicitly.
    #
    # This was once `DELETE FROM node WHERE node_type = 'fact'`, unscoped. On an
    # empty database that is indistinguishable from the correct statement; on a
    # database with a real corpus in it, it silently deletes every extracted
    # fact in the system — and because entity visibility is *derived* from
    # facts, that also makes every person, project, and deal invisible while
    # leaving the entity rows in place, so nothing looks obviously broken. The
    # watermarks stay advanced, so nothing re-extracts on its own either.
    # Recovery is a full backfill and a model call per document.
    await conn.execute(
        """
        DELETE FROM node
        WHERE node_type = $1
          AND (permission_parent_id IN (SELECT id FROM node WHERE entity_id LIKE $2)
               OR payload->>'subject' = ANY($3::text[]))
        """,
        FACT_TYPE,
        f"{PREFIX}%",
        [PERSON_ID, PROJECT_ID],
    )
    await conn.execute(
        "DELETE FROM node WHERE entity_id LIKE $1 OR entity_id = ANY($2::text[])",
        f"{PREFIX}%",
        [PERSON_ID, PROJECT_ID],
    )
    await conn.execute("DELETE FROM membership WHERE parent_identity_id = $1", EVERYONE)
    await conn.execute("DELETE FROM identity WHERE id LIKE $1", f"{PREFIX}%")


async def build(conn) -> None:
    """Two channels with different audiences, one message in each.

    Alice reaches the private channel directly and the public one through the
    group; Bob only through the group. That asymmetry is what makes "visible to
    Alice but not Bob" a meaningful assertion.
    """
    for identity_id, name in ((ALICE, "Alice"), (BOB, "Bob"), (EVERYONE, "Everyone")):
        await conn.execute(
            "INSERT INTO identity (id, display_name, is_active) VALUES ($1, $2, true) "
            "ON CONFLICT (id) DO NOTHING",
            identity_id,
            name,
        )
    for child in (ALICE, BOB):
        await conn.execute(
            "INSERT INTO membership (child_identity_id, parent_identity_id) "
            "VALUES ($1, $2) ON CONFLICT DO NOTHING",
            child,
            EVERYONE,
        )

    async def node(entity_id, node_type, parent, body, payload, version="0000000001"):
        parent_id = None
        if parent:
            parent_id = await conn.fetchval(
                "SELECT id FROM node WHERE entity_id = $1", parent
            )
        await conn.execute(
            """
            INSERT INTO node (entity_id, node_type, permission_parent_id, body,
                              created_at, updated_at, content_version, payload)
            VALUES ($1, $2, $3, $4, $5, $5, $6, $7::jsonb)
            """,
            entity_id,
            node_type,
            parent_id,
            body,
            _NOW,
            version,
            payload,
        )

    await node(
        PRIVATE_CHANNEL,
        "slack:channel",
        None,
        "",
        {"channel_id": "CVERIFYPRIV", "name": "verify-private", "is_private": True},
    )
    await node(
        PUBLIC_CHANNEL,
        "slack:channel",
        None,
        "",
        {"channel_id": "CVERIFYPUB", "name": "verify-public", "is_private": False},
    )

    for identity_id, channel in ((ALICE, PRIVATE_CHANNEL), (EVERYONE, PUBLIC_CHANNEL)):
        node_id = await conn.fetchval(
            "SELECT id FROM node WHERE entity_id = $1", channel
        )
        await conn.execute(
            "INSERT INTO access (identity_id, node_id, level) "
            "VALUES ($1, $2, 'slack:member') ON CONFLICT DO NOTHING",
            identity_id,
            node_id,
        )

    await node(
        PRIVATE_MESSAGE,
        "slack:message",
        PRIVATE_CHANNEL,
        f"Confidential: {PERSON_NAME} is off the migration.",
        {"channel_id": "CVERIFYPRIV", "ts": "1787000000.000100", "user_id": "UV1"},
    )
    await node(
        PUBLIC_MESSAGE,
        "slack:message",
        PUBLIC_CHANNEL,
        f"{PERSON_NAME} is leading {PROJECT_NAME}.",
        {"channel_id": "CVERIFYPUB", "ts": "1787000000.000200", "user_id": "UV2"},
    )


async def run_extraction(
    conn, entity_id, script, *, change=ChangeKind.CREATED, node_type=None
):
    """One full pass, through the worker's own handler.

    Deliberately not a reimplementation. The previous version of this helper
    copied `_handle_job`'s sequence, so the two could drift — and they did: the
    handler retracted unconditionally for a year of commits and the test that
    was supposed to cover it retracted unconditionally too, agreeing with the
    bug instead of catching it. Anything asserted here now constrains the code
    that actually runs in the worker.

    `node_type` overrides what the job claims, for the malformed-job case.
    """
    store = SemanticStore(conn)
    row = await store.source_node(entity_id)
    client = ScriptedClient(script)
    await load_ontology(conn)
    job = SemanticJob(
        entity_id=entity_id,
        node_type=node_type or row["node_type"],
        content_version=row["content_version"],
        change=change,
    )
    result = await _handle_job(conn, client, ActiveConfig(ttl_seconds=0.0), job)
    return result, client


async def facts_from_source(conn, entity_id) -> int:
    return await conn.fetchval(
        "SELECT count(*) FROM node f JOIN node p ON p.id = f.permission_parent_id "
        "WHERE f.node_type = $1 AND p.entity_id = $2",
        FACT_TYPE,
        entity_id,
    )


async def watermark(conn, entity_id) -> str:
    return await conn.fetchval(
        "SELECT semantic_version FROM node WHERE entity_id = $1", entity_id
    )


# ------------------------------------------------------------------ tests --


async def main() -> None:
    pool = await db.pool()
    async with pool.acquire() as conn:
        await load_ontology(conn)
        await cleanup(conn)
        await build(conn)
    try:
        await run_checks(pool)
    finally:
        async with pool.acquire() as conn:
            await cleanup(conn)
        await pool.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for name in FAILURES:
            print(f"  - {name}")
        raise SystemExit(1)
    print("all checks passed")


def note(*claims: str) -> str:
    """A note's preview: the entity name, then this document's claims.

    `body` is name-then-claims on separate lines and `preview` collapses
    whitespace, so the expected string is simply the two joined by a space.
    """
    return " ".join([PERSON_NAME, *claims])


async def facts_on(graph: SessionGraph, entity_id: str) -> set[str]:
    """What this principal can read about an entity, via the agent's own path."""
    rows = await graph.follow(entity_id, FACT_RELATION, direction="in")
    return {r.preview for r in rows}


async def run_checks(pool) -> None:
    print("\n== ontology ==")
    async with pool.acquire() as conn:
        cfg = await load_ontology(conn)
        check("types declared", {"person", "project", "task"} <= cfg.type_names)
        person = cfg.type_for("person")
        check("a type says what it represents", bool(person.description))
        check(
            "entity payload is identity only",
            {f.name for f in person.identity} == {"name", "email", "slack_user_id"},
        )
        views = await conn.fetch(
            "SELECT table_name FROM information_schema.views "
            "WHERE table_name = ANY($1::text[])",
            ["person", "project", "task", FACT_TYPE],
        )
        check("entity and fact views compiled", len(views) == 4, f"{len(views)}/4")

    print("\n== extraction from a private document ==")
    async with pool.acquire() as conn:
        result, client = await run_extraction(
            conn, PRIVATE_MESSAGE, private_script()
        )
        write, n_ent, n_fact = result.write, result.entities, result.facts
        check("the pass reports it read the document", result.outcome is Outcome.APPLIED)
        check("the loop ran to finish", client.calls == 5, f"{client.calls} turns")
        check(
            "two claims collapse into one note for this document",
            (n_ent, n_fact) == (2, 1),
            f"{n_ent} entities, {n_fact} note",
        )
        check(
            "the note names its entity, then holds both claims",
            write.facts[0].body.splitlines()
            == [PERSON_NAME, PRIVATE_STATEMENT, PRIVATE_STATEMENT_2],
            repr(write.facts[0].body),
        )
        check("entity id from the cascade", write.entities[0].entity_id == PERSON_ID)
        check(
            "entity carries identity only",
            set(write.entities[0].payload) == {"name"},
            str(write.entities[0].payload),
        )
        check(
            "entity has no permission parent",
            write.entities[0].permission_parent_entity_id is None,
        )
        check(
            "fact's permission parent is the document",
            write.facts[0].permission_parent_entity_id == PRIVATE_MESSAGE,
        )
        check(
            "the loop was shown the type description",
            "human being who acts in this workspace" in client.last_system,
        )

        parent = await conn.fetchval(
            "SELECT p.entity_id FROM node n JOIN node p ON p.id = n.permission_parent_id "
            "WHERE n.entity_id = $1",
            write.facts[0].entity_id,
        )
        check("stored fact inherits from the document", parent == PRIVATE_MESSAGE)

        # The whole access model, asserted negatively: nothing was written.
        stored_grants = await conn.fetchval(
            "SELECT count(*) FROM access a JOIN node n ON n.id = a.node_id "
            "WHERE n.entity_id = ANY($1::text[])",
            [PERSON_ID, write.facts[0].entity_id],
        )
        check("no grant was minted for the entity or its note", stored_grants == 0)
        leftovers = await conn.fetchval(
            "SELECT count(*) FROM information_schema.tables "
            "WHERE table_name IN ('semantic_source', 'semantic_run')"
        )
        check("the provenance and run tables are gone", leftovers == 0)

    print("\n== derived visibility: only the private channel knows her yet ==")
    async with pool.acquire() as conn:
        alice_only = await Visibility.resolve(conn, ALICE)
        bob_only = await Visibility.resolve(conn, BOB)
        person_uuid = await conn.fetchval(
            "SELECT id FROM node WHERE entity_id = $1", PERSON_ID
        )
        check("Alice sees the person", await alice_only.is_visible(conn, person_uuid))
        # The assertion that separates derived visibility from a stored grant:
        # nothing Bob can read mentions her, so she does not exist for him.
        check(
            "Bob cannot see her at all yet",
            not await bob_only.is_visible(conn, person_uuid),
        )
        mention = await conn.fetchrow(
            "SELECT e.relation FROM edge e "
            "JOIN node f ON f.id = e.from_node_id "
            "JOIN node t ON t.id = e.to_node_id "
            "WHERE f.entity_id = $1 AND t.entity_id = $2",
            PRIVATE_MESSAGE,
            PERSON_ID,
        )
        check(
            "the document has an edge straight to the entity",
            mention is not None and mention["relation"] == MENTIONS_RELATION,
            str(mention and mention["relation"]),
        )

    print("\n== the same person from a public document ==")
    async with pool.acquire() as conn:
        result2, client2 = await run_extraction(
            conn, PUBLIC_MESSAGE, public_script()
        )
        write2, n_ent2 = result2.write, result2.entities
        ids = {e.entity_id for e in write2.entities}
        check("resolved to the existing person", PERSON_ID in ids)
        check("minted the new project", PROJECT_ID in ids)
        check(
            "neither entity was minted again",
            n_ent2 == 0,
            f"{n_ent2} written",
        )
        check(
            "a short-form search found the stored entity",
            any(PERSON_ID in str(r) and "matches" in str(r) for r in client2.results),
        )
        check(
            "and binding it returned what was already recorded",
            any("already_recorded" in str(r) for r in client2.results),
        )
        link = await conn.fetchval(
            "SELECT count(*) FROM edge e JOIN node f ON f.id = e.from_node_id "
            "JOIN node t ON t.id = e.to_node_id "
            "WHERE f.entity_id = $1 AND t.entity_id = $2 AND e.relation = $3",
            PERSON_ID,
            PROJECT_ID,
            "works_on",
        )
        check("agent-drawn entity link persisted", link == 1, str(link))

        notes = await conn.fetchval(
            "SELECT count(*) FROM node WHERE node_type = $1 "
            "AND payload->>'subject' = $2",
            FACT_TYPE,
            PERSON_ID,
        )
        check("one note per document about her", notes == 2, f"{notes}")
        still_none = await conn.fetchval(
            "SELECT count(*) FROM access a JOIN node n ON n.id = a.node_id "
            "WHERE n.entity_id = $1",
            PERSON_ID,
        )
        check("still no grant anywhere on the entity", still_none == 0)

    print("\n== the point of the whole design ==")
    chunker = ChunkerClient(config.EMBED_URL)
    async with pool.acquire() as conn:
        alice_vis = await Visibility.resolve(conn, ALICE)
        bob_vis = await Visibility.resolve(conn, BOB)
    alice = SessionGraph(pool, alice_vis, chunker)
    bob = SessionGraph(pool, bob_vis, chunker)

    check("Alice sees the person", await alice.get(PERSON_ID) is not None)
    check(
        "Bob can now see her, on the strength of one public note",
        await bob.get(PERSON_ID) is not None,
    )

    alice_facts = await facts_on(alice, PERSON_ID)
    bob_facts = await facts_on(bob, PERSON_ID)
    check(
        "Alice reads both notes",
        alice_facts
        == {note(PRIVATE_STATEMENT, PRIVATE_STATEMENT_2), note(PUBLIC_STATEMENT)},
        str(sorted(alice_facts)),
    )
    check(
        "Bob reads only the public one",
        bob_facts == {note(PUBLIC_STATEMENT)},
        str(sorted(bob_facts)),
    )

    print("\n== traversal reaches the semantic layer from a document ==")
    peers = await bob.neighbors(PUBLIC_MESSAGE, relations=[MENTIONS_RELATION])
    check(
        "Bob walks from the public message to the person",
        PERSON_ID in {n.entity_id for n in peers},
        str(sorted(n.entity_id for n in peers)),
    )
    backlinks = await bob.follow(PERSON_ID, MENTIONS_RELATION, direction="in")
    check(
        "and back from the person to only the documents he can read",
        {n.entity_id for n in backlinks} == {PUBLIC_MESSAGE},
        str(sorted(n.entity_id for n in backlinks)),
    )
    alice_backlinks = await alice.follow(PERSON_ID, MENTIONS_RELATION, direction="in")
    check(
        "Alice sees both documents",
        {n.entity_id for n in alice_backlinks} == {PRIVATE_MESSAGE, PUBLIC_MESSAGE},
        str(sorted(n.entity_id for n in alice_backlinks)),
    )

    print("\n== an inferred link does not outlive its audience ==")
    # Both endpoints are visible to Bob from public notes, so the peer rule
    # alone would hand him a relation only the private channel ever stated.
    alice_links = await alice.follow(PERSON_ID, "secretly_blocked_by", direction="out")
    bob_links = await bob.follow(PERSON_ID, "secretly_blocked_by", direction="out")
    check("Bob can see both endpoints", await bob.get(PROJECT_ID) is not None)
    check(
        "Alice, who can read the source, sees the link",
        {n.entity_id for n in alice_links} == {PROJECT_ID},
        str(sorted(n.entity_id for n in alice_links)),
    )
    bob_seen = {n.entity_id for n in bob_links}
    check("Bob, who cannot, does not", not bob_seen, str(sorted(bob_seen)))

    print("\n== searching by short form ==")
    async with pool.acquire() as conn:
        store = SemanticStore(conn)
        hits = await store.search_entities(
            "Marisol", type_names=["person", "project", "task"]
        )
        check(
            "a first name reaches the full stored name",
            PERSON_ID in {h["entity_id"] for h in hits},
            str([h["entity_id"] for h in hits]),
        )
        check(
            "candidates carry a fact, so two same-named people are separable",
            any(PUBLIC_STATEMENT in (h.get("known") or "") for h in hits),
            str([h.get("known") for h in hits]),
        )
        by_key = await store.search_entities(
            PROJECT_NAME.split()[1], type_names=["project"]
        )
        check(
            "a mid-name word reaches the project",
            PROJECT_ID in {h["entity_id"] for h in by_key},
            str([h["entity_id"] for h in by_key]),
        )
        missing = await store.get_entity("person:name:nobody", type_names=["person"])
        check("binding an unknown id fails", missing is None)

    print("\n== facts are reachable by meaning ==")
    async with pool.acquire() as conn:
        chunks = await conn.fetchval(
            "SELECT count(*) FROM node WHERE node_type = $1", FACT_TYPE
        )
        check("facts exist to embed", chunks > 0, str(chunks))
    check(
        "a fact clears the embedding policy",
        should_embed(FACT_TYPE, PUBLIC_STATEMENT),
    )
    async with pool.acquire() as conn:
        bodies = [
            r["body"]
            for r in await conn.fetch(
                "SELECT body FROM node WHERE node_type = $1 AND payload->>'subject' = $2",
                FACT_TYPE,
                PERSON_ID,
            )
        ]
        check(
            "every note carries its entity's name, so the vector has a subject",
            bodies and all(b.startswith(PERSON_NAME) for b in bodies),
            str([b.split(chr(10))[0] for b in bodies]),
        )
    check(
        "an entity name does not",
        not should_embed("person", PERSON_NAME),
    )

    print("\n== the agent's query path ==")
    result = await bob.query_type(
        TypeQuery(
            node_type="person",
            predicates=[{"field": "name", "op": "eq", "value": PERSON_NAME}],
        )
    )
    check("query_type('person') finds the entity", len(result.results) == 1)
    if result.results:
        check("labelled by name", result.results[0].label == PERSON_NAME)
    facts_query = await bob.query_type(
        TypeQuery(
            node_type=FACT_TYPE,
            predicates=[{"field": "subject", "op": "eq", "value": PERSON_ID}],
        )
    )
    check(
        "query_type('fact') is permission-filtered too",
        len(facts_query.results) == 1,
        f"{len(facts_query.results)} of 2",
    )

    print("\n== reconciliation on change ==")
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE node SET body = $2, content_version = $3, semantic_version = '' "
            "WHERE entity_id = $1",
            PRIVATE_MESSAGE,
            f"Correction: {PERSON_NAME} is back on the migration.",
            "0000000002",
        )
        result3, _ = await run_extraction(
            conn, PRIVATE_MESSAGE, updated_script(), change=ChangeKind.UPDATED
        )
        n_fact3, retracted = result3.facts, result3.retracted
        check("the previous reading was retracted", retracted == 1, str(retracted))
        check("the new reading was written", n_fact3 == 1)
        stale = await conn.fetchval(
            "SELECT count(*) FROM edge WHERE relation = $1", "secretly_blocked_by"
        )
        check(
            "and the link that pass had drawn went with it",
            stale == 0,
            f"{stale} left",
        )

    alice_facts = await facts_on(alice, PERSON_ID)
    check(
        "the stale note is gone and the new one is there",
        alice_facts == {note(UPDATED_STATEMENT), note(PUBLIC_STATEMENT)},
        str(sorted(alice_facts)),
    )
    check(
        "Bob is unaffected by the edit",
        await facts_on(bob, PERSON_ID) == {note(PUBLIC_STATEMENT)},
    )

    print("\n== reconciliation on delete ==")
    async with pool.acquire() as conn:
        from store import Store

        store = SemanticStore(conn)
        affected = await store.entities_from(PRIVATE_MESSAGE)
        check("affected entities are known before retraction", affected == [PERSON_ID])
        async with conn.transaction():
            await Store(conn)._tombstone(PRIVATE_MESSAGE)

        left = await conn.fetchval(
            "SELECT count(*) FROM node f JOIN node s ON s.id = f.permission_parent_id "
            "WHERE f.node_type = $1 AND s.entity_id = $2",
            FACT_TYPE,
            PRIVATE_MESSAGE,
        )
        check("notes from the deleted document are gone", left == 0)

    check("the person survives the delete", await bob.get(PERSON_ID) is not None)
    check(
        "Alice now reads only what is left",
        await facts_on(alice, PERSON_ID) == {note(PUBLIC_STATEMENT)},
    )

    print("\n== an entity with nothing readable left ==")
    async with pool.acquire() as conn:
        # Drop the last remaining note. Nothing touches `access`, and the person
        # should vanish for everyone — the end state that a materialised grant
        # would have had to be swept up to reach.
        await conn.execute(
            "DELETE FROM node WHERE node_type = $1 AND payload->>'subject' = $2",
            FACT_TYPE,
            PERSON_ID,
        )
        still_there = await conn.fetchval(
            "SELECT count(*) FROM node WHERE entity_id = $1", PERSON_ID
        )
        check("the entity row is still present", still_there == 1)

    check("but nobody can see her any more", await bob.get(PERSON_ID) is None)
    check("not even Alice", await alice.get(PERSON_ID) is None)

    print("\n== action catalog ==")
    check(
        "every action is scoped to one node type",
        all(spec.node_type for spec in ACTIONS.values()),
        str(sorted(ACTIONS)),
    )
    check(
        "channel actions are channel-scoped",
        [a.name for a in actions_for("slack:channel")] == ["slack.post_message"],
    )
    # An inferred node is a conclusion this system drew, not a thing in a source
    # with an id you can write to — so the rule stands for `project`, `task`,
    # and `deal`, and acting on one means following its edges to the documents.
    #
    # `person` is the one exception, and it is an exception to the reading of
    # that rule rather than to the rule itself: it carries a `slack_user_id`
    # issued by Slack, so it is an address that routes to a real conversation.
    # The entity is still not what gets written to; the DM is.
    check("most entities have no actions", actions_for("project") == ())
    check(
        "a person can be messaged, because they carry a Slack address",
        [a.name for a in actions_for("person")] == ["slack.dm"],
    )
    check("facts have no actions", actions_for(FACT_TYPE) == ())

    print("\n== action guards ==")
    runner = Runner()
    async with pool.acquire() as conn:
        bob_vis = await Visibility.resolve(conn, BOB)
        enabled = config.ACTIONS_ENABLED
        try:
            config.ACTIONS_ENABLED = True
            await _expect(
                "invisible node reads as unavailable",
                ActionNotAvailable,
                runner.invoke(
                    conn,
                    bob_vis,
                    action_name="slack.reply_in_thread",
                    entity_id=PRIVATE_MESSAGE,
                    params={"text": "hi"},
                ),
            )
            await _expect(
                "wrong node type is rejected",
                ActionNotAvailable,
                runner.invoke(
                    conn,
                    bob_vis,
                    action_name="slack.post_message",
                    entity_id=PUBLIC_MESSAGE,
                    params={"text": "hi"},
                ),
            )
            await _expect(
                "invalid parameters are rejected",
                ActionError,
                runner.invoke(
                    conn,
                    bob_vis,
                    action_name="slack.reply_in_thread",
                    entity_id=PUBLIC_MESSAGE,
                    params={"text": ""},
                ),
            )
            config.ACTIONS_ENABLED = False
            await _expect(
                "disabled actions refuse to run",
                ActionError,
                runner.invoke(
                    conn,
                    bob_vis,
                    action_name="slack.reply_in_thread",
                    entity_id=PUBLIC_MESSAGE,
                    params={"text": "hi"},
                ),
            )
        finally:
            config.ACTIONS_ENABLED = enabled
            await runner.aclose()
        left_running = await conn.fetchval(
            "SELECT count(*) FROM action_invocation WHERE status = 'running'"
        )
        check("no invocation left mid-flight", left_running == 0, str(left_running))

    print("\n== a pass that did not read the document retracts nothing ==")
    # The defect this guards: `retract_from` and `mark_extracted` once ran
    # unconditionally, so a refusal or a config gap deleted the document's notes
    # and stamped it complete. Three situations reach the same code; only the
    # last one is a conclusion about the document.
    async with pool.acquire() as conn:
        before = await facts_from_source(conn, PUBLIC_MESSAGE)
        check("the public note is on disk to begin with", before > 0, str(before))

        # Re-offer the document, so a destructive path has something to destroy.
        await conn.execute(
            "UPDATE node SET semantic_version = '' WHERE entity_id = $1",
            PUBLIC_MESSAGE,
        )

        # B: the model refused. Nothing was evaluated.
        refused, _ = await run_extraction(conn, PUBLIC_MESSAGE, REFUSAL)
        after = await facts_from_source(conn, PUBLIC_MESSAGE)
        check(
            "a refusal is reported as unevaluated, not as an empty result",
            refused.outcome is Outcome.UNEVALUATED,
            str(refused.outcome),
        )
        check("and leaves the notes alone", after == before, f"{before} -> {after}")
        check(
            "and leaves the watermark unset, so it is offered again",
            await watermark(conn, PUBLIC_MESSAGE) == "",
        )

        # A: the job names a type the ontology declares nothing for. Under the
        # old code this reached `run()` as None and was read as "found nothing".
        healed, _ = await run_extraction(
            conn, PUBLIC_MESSAGE, [[("finish", {})]], node_type=FACT_TYPE
        )
        check(
            "a job naming the wrong type self-heals off the row",
            healed.outcome is Outcome.APPLIED,
            str(healed.outcome),
        )

        # And a document whose real type has no declared entity types: marked so
        # it stops being re-offered, but nothing deleted.
        await conn.execute(
            "UPDATE node SET semantic_version = '' WHERE entity_id = $1",
            PUBLIC_MESSAGE,
        )
        empty_cfg = SemanticConfig(types=[])
        store = SemanticStore(conn)
        extractor = Extractor(ScriptedClient([]), empty_cfg, max_turns=2)
        check(
            "an ontology with nothing for this type reports so",
            not extractor.applies_to("slack:message"),
        )

    print("\n== identity helpers ==")
    check(
        "slug normalises case and spacing", slugify("  Alex   BROOKS ") == "alex-brooks"
    )
    check("long values are hashed", len(slugify("x" * 400)) == 32)
    check(
        "entity ids name the key that produced them",
        mint_entity_id("person", "email", "A@B.com") == "person:email:a@b.com",
    )


async def _expect(label: str, exc_type: type[Exception], coro) -> None:
    try:
        await coro
    except exc_type:
        check(label, True)
        return
    except Exception as exc:  # noqa: BLE001 - reporting the wrong type is the point
        check(label, False, f"raised {type(exc).__name__}: {exc}")
        return
    check(label, False, "no exception")


if __name__ == "__main__":
    asyncio.run(main())
