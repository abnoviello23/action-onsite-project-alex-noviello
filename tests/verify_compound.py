"""The compound-question demo, asserted rather than screenshotted.

    docker compose --profile verify run --rm verify-compound

One person, two facts, two different channels:

    person:demo-jane
       ▲ about                    ▲ about
    fact "works on Atlas"      fact "was offboarded"
    parent = a PUBLIC message  parent = a PRIVATE message

Then the same call — "people who work on Atlas *and* left recently" — is made as
two principals:

  * the one who can read the private channel gets Jane at **2/2**
  * the one who cannot gets Jane at **1/2**, and the honest answer is no one

Nothing is special-cased to produce that. Each constraint is retrieved
separately through the visibility kernel, so losing access to the second
document does not merely hide a fact — it dissolves the conjunction.

Facts are inserted directly rather than extracted. That is deliberate: this
pins the *retrieval* contract, which must hold whatever the extractor happens to
write, and it keeps the check runnable without a model key or a drained sweeper.

Cleans up after itself; safe to re-run.
"""

from __future__ import annotations

import asyncio

from common import db
from query.entities import find_entities
from query.session import SessionGraph
from query.visibility import Visibility
from semantic.registry import load as load_ontology

# The pair that differs by exactly one channel. Both are workspace members, so
# both read the public side; only Alex holds the grant on `privatechannel1`.
# That single difference is what the whole demo turns on.
ALEX = "slack:user:U0BQX6DQ1RA"
TEST1 = "slack:user:U0BQRT4EVL6"

PRIVATE_CHANNEL = "privatechannel1"

ENTITY = "person:demo-jane-compound"
FACT_PUBLIC = "fact:demo-compound:public"
FACT_PRIVATE = "fact:demo-compound:private"

# Filler facts, all newer than the two above and all irrelevant. They exist to
# push the interesting facts off a recency-ordered page.
FILLER = 8
FILLER_PREFIX = "fact:demo-compound:filler"

PUBLIC_STATEMENT = (
    "Jane Doe is leading the Atlas migration project and owns the rollout plan "
    "for the Atlas workstream."
)
PRIVATE_STATEMENT = (
    "Jane Doe was offboarded last week; her departure from the company is "
    "final and her access has been revoked."
)

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(label)


_UPSERT_FACT = """
INSERT INTO node (entity_id, node_type, permission_parent_id, body,
                  created_at, updated_at, content_version, payload)
VALUES ($1, 'fact', $2, $3, now(), now(), '1', $4)
ON CONFLICT (entity_id) DO UPDATE SET
    permission_parent_id = EXCLUDED.permission_parent_id,
    body = EXCLUDED.body,
    payload = EXCLUDED.payload,
    deleted_at = NULL
RETURNING id
"""


async def seed(conn) -> None:
    """Two facts about one person, from documents with different audiences."""
    await conn.execute(
        """
        INSERT INTO node (entity_id, node_type, body, created_at, updated_at,
                          content_version, payload)
        VALUES ($1, 'person', 'Jane Doe', now(), now(), '1', $2)
        ON CONFLICT (entity_id) DO UPDATE SET deleted_at = NULL
        """,
        ENTITY,
        {"name": "Jane Doe"},
    )
    entity_id = await conn.fetchval("SELECT id FROM node WHERE entity_id = $1", ENTITY)

    # A message in a channel the whole workspace can read, and one in a channel
    # only the app can. Picked from live data so the parents carry real grants.
    public_msg = await conn.fetchval(
        """
        SELECT m.id FROM slack_message m
        JOIN slack_channel c ON c.channel_id = m.channel_id
        WHERE c.is_private = false AND m.deleted_at IS NULL
        LIMIT 1
        """
    )
    private_msg = await conn.fetchval(
        """
        SELECT m.id FROM slack_message m
        JOIN slack_channel c ON c.channel_id = m.channel_id
        WHERE c.name = $1 AND m.deleted_at IS NULL
        LIMIT 1
        """,
        PRIVATE_CHANNEL,
    )
    if public_msg is None or private_msg is None:
        raise SystemExit(
            f"seed data missing: need a public message and one in "
            f"{PRIVATE_CHANNEL}"
        )

    for fact_id, parent, text in (
        (FACT_PUBLIC, public_msg, PUBLIC_STATEMENT),
        (FACT_PRIVATE, private_msg, PRIVATE_STATEMENT),
    ):
        node_id = await conn.fetchval(
            _UPSERT_FACT,
            fact_id,
            parent,
            text,
            # A dict, not a JSON string: the connection registers a jsonb
            # codec whose encoder is json.dumps, so a string would be encoded
            # twice and land as a jsonb *string*. `payload->>'subject'` then
            # reads null, the fact is unattributable, and entity search finds
            # nothing — which is exactly what this got wrong the first time.
            {"subject": ENTITY, "source": "seeded"},
        )
        await conn.execute(
            """
            INSERT INTO edge (from_node_id, to_node_id, relation)
            VALUES ($1, $2, 'about') ON CONFLICT DO NOTHING
            """,
            node_id,
            entity_id,
        )


async def seed_filler(conn) -> None:
    """Bury the interesting facts under newer, irrelevant ones.

    All on the public parent, so both principals see them, and all updated after
    the two real facts — which is exactly the shape that makes a recency-ordered
    page the wrong page.
    """
    entity_id = await conn.fetchval("SELECT id FROM node WHERE entity_id = $1", ENTITY)
    parent = await conn.fetchval(
        """
        SELECT m.id FROM slack_message m
        JOIN slack_channel c ON c.channel_id = m.channel_id
        WHERE c.is_private = false AND m.deleted_at IS NULL LIMIT 1
        """
    )
    for i in range(FILLER):
        node_id = await conn.fetchval(
            _UPSERT_FACT,
            f"{FILLER_PREFIX}:{i}",
            parent,
            f"Routine status note number {i} about scheduling and logistics.",
            {"subject": ENTITY, "source": "seeded"},
        )
        await conn.execute(
            "UPDATE node SET updated_at = now() + ($2 || ' seconds')::interval "
            "WHERE id = $1",
            node_id,
            str(i + 10),
        )
        await conn.execute(
            "INSERT INTO edge (from_node_id, to_node_id, relation) "
            "VALUES ($1, $2, 'about') ON CONFLICT DO NOTHING",
            node_id,
            entity_id,
        )


async def cleanup(conn) -> None:
    await conn.execute(
        "DELETE FROM node WHERE entity_id = ANY($1::text[])",
        [ENTITY, FACT_PUBLIC, FACT_PRIVATE]
        + [f"{FILLER_PREFIX}:{i}" for i in range(FILLER)],
    )


def find(result, entity_id: str):
    return next((m for m in result.matches if m.entity_id == entity_id), None)


async def main() -> None:
    conn = await db.connect()
    pool = await db.pool()
    await load_ontology(conn)

    try:
        await seed(conn)

        constraints = [
            "works on the Atlas project",
            "has left the company or been offboarded recently",
        ]

        print("\n== principal WHO CAN read the private channel ==")
        vis_alex = await Visibility.resolve(conn, ALEX)
        r_alex = await find_entities(
            pool, vis_alex, None, node_type="person", constraints=constraints
        )
        jane = find(r_alex, ENTITY)
        check("Jane is returned", jane is not None)
        if jane:
            check("satisfies BOTH constraints", jane.matched == 2, f"{jane.matched}/2")
            check("complete match", jane.complete)
            check(
                "evidence cites both facts",
                {e.fact_entity_id for e in jane.evidence}
                == {FACT_PUBLIC, FACT_PRIVATE},
                str(sorted(e.fact_entity_id for e in jane.evidence)),
            )

        print("\n== same call, principal WHO CANNOT ==")
        vis_t1 = await Visibility.resolve(conn, TEST1)
        r_t1 = await find_entities(
            pool, vis_t1, None, node_type="person", constraints=constraints
        )
        jane_t1 = find(r_t1, ENTITY)
        check("Jane is still visible (public fact)", jane_t1 is not None)
        if jane_t1:
            check(
                "satisfies ONLY the public constraint",
                jane_t1.matched == 1,
                f"{jane_t1.matched}/2",
            )
            check("not a complete match", not jane_t1.complete)
            check(
                "the private fact is not cited",
                all(e.fact_entity_id != FACT_PRIVATE for e in jane_t1.evidence),
                str([e.fact_entity_id for e in jane_t1.evidence]),
            )
        check(
            "the offboarding constraint matched nothing visible",
            constraints[1] in r_t1.unmatched_constraints,
            str(r_t1.unmatched_constraints),
        )
        check(
            "nobody satisfies the full question",
            not any(m.complete for m in r_t1.matches),
            f"{sum(1 for m in r_t1.matches if m.complete)} complete",
        )

        print("\n== derived entity visibility still holds ==")
        g_t1 = SessionGraph(pool, vis_t1, None)
        page = await g_t1.neighbors(ENTITY, node_types=["fact"], direction="in")
        ids = {n.entity_id for n in page.neighbors}
        check("only the public fact is reachable", ids == {FACT_PUBLIC}, str(sorted(ids)))
        check("and the page says it is complete", page.complete)

        g_alex = SessionGraph(pool, vis_alex, None)
        page_alex = await g_alex.neighbors(ENTITY, node_types=["fact"], direction="in")
        check(
            "the permitted principal sees both",
            {n.entity_id for n in page_alex.neighbors}
            == {FACT_PUBLIC, FACT_PRIVATE},
        )

        print("\n== local search: relevance beats recency ==")
        await seed_filler(conn)
        # Ten facts about Jane now, and the offboarding one is the oldest. A
        # page of three ordered by recency cannot reach it.
        recent = await g_alex.neighbors(
            ENTITY, node_types=["fact"], direction="in", limit=3
        )
        recent_ids = {n.entity_id for n in recent.neighbors}
        check("recency page is full", len(recent.neighbors) == 3)
        check("and says it is incomplete", not recent.complete)
        check(
            "recency MISSES the offboarding fact",
            FACT_PRIVATE not in recent_ids,
            str(sorted(recent_ids)),
        )

        ranked = await g_alex.neighbors(
            ENTITY,
            node_types=["fact"],
            direction="in",
            query="offboarded departure left the company",
            limit=3,
        )
        ranked_ids = [n.entity_id for n in ranked.neighbors]
        check("relevance FINDS it", FACT_PRIVATE in ranked_ids, str(ranked_ids))
        check(
            "and ranks it first",
            bool(ranked_ids) and ranked_ids[0] == FACT_PRIVATE,
            ranked_ids[0] if ranked_ids else "empty",
        )
        check(
            "irrelevant filler is dropped, not merely reordered",
            not any(i.startswith(FILLER_PREFIX) for i in ranked_ids),
            str(ranked_ids),
        )

        print("\n== ranking cannot promote what access denied ==")
        denied = await SessionGraph(pool, vis_t1, None).neighbors(
            ENTITY,
            node_types=["fact"],
            direction="in",
            query="offboarded departure left the company",
            limit=10,
        )
        check(
            "the restricted principal still cannot see it",
            all(n.entity_id != FACT_PRIVATE for n in denied.neighbors),
            str([n.entity_id for n in denied.neighbors]),
        )

        print("\n== truncation is reported, not hidden ==")
        clipped = await g_alex.neighbors(
            ENTITY, node_types=["fact"], direction="in", limit=1
        )
        check("limit=1 returns one", len(clipped.neighbors) == 1)
        check("and reports itself incomplete", not clipped.complete)
    finally:
        await cleanup(conn)
        await conn.close()
        await pool.close()

    print("\n" + "=" * 60)
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
