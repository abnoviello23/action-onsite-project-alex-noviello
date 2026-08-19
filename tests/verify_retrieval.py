"""End-to-end check of the permissioned retrieval layer against a seeded stack.

Run it after seeding:

    docker compose --profile verify run --rm verify

Ten groups, and the point of most of them is a *negative*: the principal who
should not see a private channel does not see it, its messages, or its edges,
and cannot tell the difference between "hidden" and "never existed". That
absence is the product, so it is asserted rather than eyeballed.

Reads only. Exits non-zero on the first failing group so it can gate a change.
"""

from __future__ import annotations

import asyncio

from common import db
from query.compile import Predicate, TypeQuery
from query.paging import fill_visible
from query.session import SessionGraph
from query.visibility import Visibility

APP = "slack:user:U0BQQ49NE9K"  # direct grants on 6 private channels, no workspace
ALEX = "slack:user:U0BQX6DQ1RA"  # workspace member + privatechannel1
TEST1 = "slack:user:U0BQRT4EVL6"  # workspace member only

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(label)


async def channels(conn, vis) -> set[str]:
    sql = (
        "SELECT id, entity_id, node_type, body, payload, created_at, updated_at "
        "FROM slack_channel WHERE deleted_at IS NULL "
        "ORDER BY updated_at DESC NULLS LAST, id DESC"
    )
    page = await fill_visible(conn, vis, sql, [], 100)
    return {r["payload"]["name"] for r in page.rows}


async def main() -> None:
    conn = await db.connect()
    pool = await db.pool()

    print("\n== 1. principal expansion (membership walk) ==")
    v_test1 = await Visibility.resolve(conn, TEST1)
    v_alex = await Visibility.resolve(conn, ALEX)
    v_app = await Visibility.resolve(conn, APP)
    check(
        "workspace member inherits the workspace principal",
        "slack:workspace:T0BQM8UA54K" in v_test1.principal_ids,
        ",".join(v_test1.principal_ids),
    )
    check("public is always a principal", "public" in v_test1.principal_ids)
    check(
        "non-member does not inherit the workspace",
        "slack:workspace:T0BQM8UA54K" not in v_app.principal_ids,
        ",".join(v_app.principal_ids),
    )

    print("\n== 2. granted roots ==")
    check("test1 reaches the workspace's channel grants", len(v_test1.granted_root_ids) >= 16,
          f"{len(v_test1.granted_root_ids)} roots")
    check("app reaches only its 6 direct grants", len(v_app.granted_root_ids) == 6,
          f"{len(v_app.granted_root_ids)} roots")

    print("\n== 3. channel visibility (the demo) ==")
    c_test1 = await channels(conn, v_test1)
    c_alex = await channels(conn, v_alex)
    c_app = await channels(conn, v_app)
    private = {"privatechannel1", "hl-legal", "hl-board", "hl-cobalt", "hl-meridian", "hl-exec"}
    check("test1 sees zero private channels", not (c_test1 & private), str(sorted(c_test1 & private)))
    check("test1 sees the public ones", "hl-general" in c_test1, f"{len(c_test1)} channels")
    check("alex sees privatechannel1", "privatechannel1" in c_alex)
    check("alex does NOT see hl-legal", "hl-legal" not in c_alex, str(sorted(c_alex & private)))
    check("app sees hl-legal", "hl-legal" in c_app)
    check("app sees no public channels", "hl-general" not in c_app, f"{len(c_app)} channels")

    print("\n== 4. inheritance: messages hang off channel grants ==")
    g_test1 = SessionGraph(pool, v_test1, None)
    g_app = SessionGraph(pool, v_app, None)
    priv_row = await conn.fetchrow(
        "SELECT payload->>'channel_id' AS cid FROM slack_channel "
        "WHERE payload->>'name' = 'hl-legal'"
    )
    q = TypeQuery(
        node_type="slack:message",
        predicates=[Predicate(field="channel_id", op="eq", value=priv_row["cid"])],
        limit=50,
    )
    r_test1 = await g_test1.query_type(q)
    r_app = await g_app.query_type(q)
    check("test1 sees no messages in hl-legal", len(r_test1.results) == 0)
    check("app sees messages in hl-legal", len(r_app.results) > 0, f"{len(r_app.results)} msgs")

    print("\n== 5. fts predicate routes to the stored tsvector ==")
    plan = await conn.fetchval(
        "EXPLAIN (FORMAT JSON) SELECT id FROM slack_message "
        "WHERE deleted_at IS NULL AND fts @@ plainto_tsquery('english', 'meeting')"
    )
    plan_text = str(plan)
    check("uses node_fts_idx, not a seq scan", "node_fts_idx" in plan_text,
          "Bitmap/Index scan" if "node_fts_idx" in plan_text else plan_text[:120])

    print("\n== 6. bidirectional traversal + direction labels ==")
    msg = await conn.fetchrow(
        "SELECT entity_id FROM slack_message WHERE payload->>'channel_id' = $1 LIMIT 1",
        priv_row["cid"],
    )
    out = await g_app.neighbors(msg["entity_id"], direction="out")
    inn = await g_app.neighbors(msg["entity_id"], direction="in")
    both = await g_app.neighbors(msg["entity_id"], direction="both")
    check("outbound returns only out-labelled rows", all(n.direction == "out" for n in out),
          f"{len(out)} rows")
    check("inbound returns only in-labelled rows", all(n.direction == "in" for n in inn),
          f"{len(inn)} rows")
    check("both == out + in", len(both) == len(out) + len(inn),
          f"{len(both)} vs {len(out)}+{len(inn)}")
    chan_hop = [n for n in out if n.relation == "in_channel"]
    check("message -> channel via `in_channel` outward", bool(chan_hop),
          chan_hop[0].label if chan_hop else "none")

    print("\n== 7. peer visibility on traversal (the leak check) ==")
    chan = await conn.fetchrow(
        "SELECT entity_id FROM slack_channel WHERE payload->>'name' = 'hl-legal'"
    )
    # test1 cannot see the channel: traversing from it must yield nothing, and
    # `get` must be indistinguishable from a node that never existed.
    n_test1 = await g_test1.neighbors(chan["entity_id"], direction="both")
    got = await g_test1.get(chan["entity_id"])
    check("invisible origin yields no neighbors", len(n_test1) == 0, f"{len(n_test1)} rows")
    check("invisible node reads as absent, not forbidden", got is None)
    # app CAN see it: inbound `in` should surface its messages, all visible.
    n_app = await g_app.neighbors(chan["entity_id"], direction="in", limit=50)
    check("visible origin yields inbound children", len(n_app) > 0, f"{len(n_app)} rows")
    peers = {n.entity_id for n in n_app}
    vis_peers = await v_app.visible(
        conn, [r["id"] for r in await conn.fetch(
            "SELECT id FROM node WHERE entity_id = ANY($1::text[])", list(peers))]
    )
    check("every returned peer passes the kernel", len(vis_peers) == len(peers),
          f"{len(vis_peers)}/{len(peers)}")

    print("\n== 8. LIMIT applies after visibility ==")
    q_all = TypeQuery(node_type="slack:message", limit=25)
    r = await g_app.query_type(q_all)
    # Full recursive count, not depth-1: a thread reply's permission parent is
    # its thread parent, so the chain to the channel is two hops.
    all_visible = await conn.fetchval(
        """
        WITH RECURSIVE anc AS (
            SELECT n.id AS root, n.id, n.permission_parent_id, 0 d FROM node n
             WHERE n.node_type='slack:message' AND n.deleted_at IS NULL
            UNION ALL
            SELECT a.root, p.id, p.permission_parent_id, a.d+1
              FROM node p JOIN anc a ON p.id = a.permission_parent_id WHERE a.d < 32)
        SELECT count(DISTINCT root) FROM anc WHERE id = ANY($1::uuid[])
        """,
        list(v_app.granted_root_ids),
    )
    check("returns a full page of visible rows, not a filtered remnant",
          len(r.results) == min(25, all_visible),
          f"{len(r.results)} of {all_visible} visible")

    print("\n== 9. tombstones and unmaterialized rows are invisible ==")
    dead = await conn.fetchval(
        "SELECT count(*) FROM node WHERE deleted_at IS NOT NULL OR node_type IS NULL"
    )
    if dead:
        ids = [r["id"] for r in await conn.fetch(
            "SELECT id FROM node WHERE deleted_at IS NOT NULL OR node_type IS NULL LIMIT 20")]
        seen = await v_app.visible(conn, ids)
        check("none pass the kernel", seen == set(), f"{len(seen)} of {len(ids)}")
    else:
        print("  SKIP  no tombstones in this dataset")

    print("\n== 10. cross-source mention edges ==")
    # Slack/Drive/Notion documents that name each other mint `mentions` to the
    # live node, not a new one. The Harborline seeder plants those URLs.
    cross = await conn.fetchval(
        """
        SELECT count(*)
        FROM edge e
        JOIN node f ON f.id = e.from_node_id
        JOIN node t ON t.id = e.to_node_id
        WHERE e.relation = 'mentions'
          AND f.node_type IS NOT NULL AND t.node_type IS NOT NULL
          AND f.deleted_at IS NULL AND t.deleted_at IS NULL
          AND split_part(f.node_type, ':', 1) <> split_part(t.node_type, ':', 1)
        """
    )
    check(
        "many Slack/Drive/Notion mentions edges (seeded counterparts)",
        cross >= 50,
        f"{cross} edges",
    )

    await conn.close()
    await pool.close()

    print("\n" + ("=" * 60))
    if FAILURES:
        print(f"{len(FAILURES)} FAILURE(S): " + "; ".join(FAILURES))
        raise SystemExit(1)
    print("all checks passed")


if __name__ == "__main__":
    asyncio.run(main())
