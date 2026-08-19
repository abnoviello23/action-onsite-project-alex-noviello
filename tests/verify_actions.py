"""Exercises the action layer against the real database, without writing anywhere.

    docker compose --profile verify run --rm verify-actions

Everything is real except the source APIs: the catalog is the one that ships,
the visibility kernel is the one `/agent/query` uses, grants are real rows, and
plans go through the same `Runner.check` a live invocation does. What is *not*
real is the last inch — no executor runs, because a suite that posted to Slack
to prove it could post to Slack is a suite nobody can run twice.

That inch is exactly what `dry_run` was built for, and testing through it is the
point rather than a compromise: a preview that validated differently from the
dispatcher would be worth less than no preview at all, so the suite asserting
against the preview is asserting against the dispatcher.

The fixture is synthetic and self-cleaning. It builds its own folder, its own
document, its own person, and three principals holding deliberately different
levels on the same node:

    drive:folder  <- organizer(owner) / writer(editor) / commenter(reader)
         |
         +-- drive:file

Reading is identical for all three. Writing is not, and the difference is the
whole of what `requires_level` exists to express — so the suite asserts that the
commenter, who can see every word of the document, is refused the overwrite.
"""

from __future__ import annotations

import asyncio

from actions import ActionError, ActionNotAvailable, PlanError, Runner, run_plan
from actions import plan as planning
from common import config, db
from core.actions import ACTIONS, PlannedAction, actions_for, address_of
from query.visibility import Visibility, level_priority
from semantic.registry import load as load_ontology

PREFIX = "verify-act"

OWNER = f"{PREFIX}:owner"
EDITOR = f"{PREFIX}:editor"
READER = f"{PREFIX}:reader"
OUTSIDER = f"{PREFIX}:outsider"

FOLDER = f"{PREFIX}:folder"
FILE = f"{PREFIX}:file"
CHANNEL = f"{PREFIX}:channel"
MESSAGE = f"{PREFIX}:message"
PERSON_WITH_SLACK = f"{PREFIX}:person:addressable"
PERSON_WITHOUT_SLACK = f"{PREFIX}:person:nameonly"

SLACK_USER = "U0VERIFYACT"

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    if not ok:
        FAILURES.append(label)


# ------------------------------------------------------------------ fixture --

_NODE = """
INSERT INTO node (entity_id, node_type, permission_parent_id, body,
                  created_at, updated_at, content_version, payload)
VALUES ($1, $2, $3, $4, now(), now(), '1', $5)
ON CONFLICT (entity_id) DO UPDATE SET
    node_type = EXCLUDED.node_type,
    permission_parent_id = EXCLUDED.permission_parent_id,
    body = EXCLUDED.body,
    payload = EXCLUDED.payload,
    deleted_at = NULL
RETURNING id
"""


async def seed(conn) -> None:
    """One Drive tree, one Slack channel, two people, three levels of access."""
    for identity in (OWNER, EDITOR, READER, OUTSIDER):
        await conn.execute(
            """
            INSERT INTO identity (id, display_name, is_active)
            VALUES ($1, $1, true)
            ON CONFLICT (id) DO UPDATE SET is_active = true
            """,
            identity,
        )

    folder = await conn.fetchval(
        _NODE, FOLDER, "drive:folder", None, "", {"file_id": "fld_verify", "name": "Reports"}
    )
    await conn.fetchval(
        _NODE,
        FILE,
        "drive:file",
        folder,
        "Quarterly numbers.",
        {
            "file_id": "fil_verify",
            "name": "Q3 Report",
            "mime_type": "application/vnd.google-apps.document",
            "web_view_link": "https://docs.google.com/document/d/fil_verify/edit",
        },
    )

    channel = await conn.fetchval(
        _NODE, CHANNEL, "slack:channel", None, "", {"channel_id": "C0VERIFY", "name": "rollout", "is_private": False}
    )
    await conn.fetchval(
        _NODE,
        MESSAGE,
        "slack:message",
        channel,
        "Kicking off the rollout thread.",
        {"channel_id": "C0VERIFY", "ts": "1700000000.000100"},
    )

    # Entities carry no permission parent: an entity is visible exactly when a
    # fact about it is, which is what the fact below arranges.
    await conn.fetchval(
        _NODE,
        PERSON_WITH_SLACK,
        "person",
        None,
        "Tony Verify-Frost",
        {"name": "Tony Verify-Frost", "slack_user_id": SLACK_USER},
    )
    await conn.fetchval(
        _NODE,
        PERSON_WITHOUT_SLACK,
        "person",
        None,
        "Dana Verify-Nameonly",
        {"name": "Dana Verify-Nameonly"},
    )
    for entity, statement in (
        (PERSON_WITH_SLACK, "Runs the rollout."),
        (PERSON_WITHOUT_SLACK, "Mentioned once in passing."),
    ):
        await conn.fetchval(
            _NODE,
            f"fact:{entity}",
            "fact",
            channel,
            statement,
            {"subject": entity, "source": CHANNEL},
        )

    # The same node, three different levels. This is the fixture's whole point.
    for identity, level, node in (
        (OWNER, "drive:organizer", folder),
        (EDITOR, "drive:writer", folder),
        (READER, "drive:commenter", folder),
        (OWNER, "slack:member", channel),
        (EDITOR, "slack:member", channel),
        (READER, "slack:member", channel),
    ):
        await conn.execute(
            """
            INSERT INTO access (identity_id, node_id, level) VALUES ($1, $2, $3)
            ON CONFLICT (identity_id, node_id) DO UPDATE SET level = EXCLUDED.level
            """,
            identity,
            node,
            level,
        )


async def cleanup(conn) -> None:
    await conn.execute(
        "DELETE FROM node WHERE entity_id LIKE $1", f"%{PREFIX}%"
    )
    await conn.execute(
        "DELETE FROM identity WHERE id LIKE $1", f"{PREFIX}:%"
    )


# -------------------------------------------------------------------- suite --


def step(id_: str, action: str, entity_id: str, **params) -> PlannedAction:
    return PlannedAction(id=id_, action=action, entity_id=entity_id, params=params)


async def main() -> None:
    if not config.ACTIONS_ENABLED:
        raise SystemExit(
            "ACTIONS_ENABLED must be true: this suite asserts what the runner "
            "refuses, and with actions off it refuses everything for one reason"
        )

    pool = await db.pool()
    async with pool.acquire() as conn:
        await load_ontology(conn)
        await cleanup(conn)
        await seed(conn)

        runner = Runner()
        owner = await Visibility.resolve(conn, OWNER)
        editor = await Visibility.resolve(conn, EDITOR)
        reader = await Visibility.resolve(conn, READER)
        outsider = await Visibility.resolve(conn, OUTSIDER)

        print("\n== catalog ==")
        check(
            "a folder can be written into",
            [s.name for s in actions_for("drive:folder")] == ["drive.create_file"],
        )
        check(
            "a person can be messaged and nothing else",
            [s.name for s in actions_for("person")] == ["slack.dm"],
        )
        check(
            "other inferred types still have no actions",
            actions_for("project") == () and actions_for("task") == (),
        )
        check(
            "every action declares what it returns",
            all(spec.returns for spec in ACTIONS.values()),
        )
        # A spec naming a level `access_level` does not define would be a
        # requirement nothing could ever satisfy, so this is the check that
        # keeps a typo from becoming a permanent, silent refusal.
        known_levels = True
        for spec in ACTIONS.values():
            if spec.requires_level:
                known_levels = known_levels and (
                    await level_priority(conn, spec.requires_level) > 0
                )
        check("every declared level exists in the catalog", known_levels)
        check(
            "a person's Slack id is reachable as an address",
            address_of(
                ACTIONS["slack.dm"], "person", {"slack_user_id": SLACK_USER}
            )
            == {"slack_user_id": SLACK_USER},
        )

        print("\n== read access is not write access ==")
        check(
            "the commenter can see the document",
            await reader.is_visible(
                conn, await node_id(conn, FILE)
            ),
        )
        await expect(
            "and is refused the overwrite",
            ActionNotAvailable,
            runner.check(
                conn, reader,
                action_name="drive.replace_content",
                entity_id=FILE,
                params={"content": "rewritten"},
            ),
        )
        await passes(
            "the writer is allowed it",
            runner.check(
                conn, editor,
                action_name="drive.replace_content",
                entity_id=FILE,
                params={"content": "rewritten"},
            ),
        )
        await passes(
            "and so is the organizer, whose level is stronger still",
            runner.check(
                conn, owner,
                action_name="drive.replace_content",
                entity_id=FILE,
                params={"content": "rewritten"},
            ),
        )
        check(
            "the level is read off the folder, not the file it is asked about",
            (await editor.strongest_level(conn, await node_id(conn, FILE)))[0]
            == "drive:writer",
        )

        print("\n== invisible and forbidden give one answer ==")
        await expect(
            "someone with no grants cannot act",
            ActionNotAvailable,
            runner.check(
                conn, outsider,
                action_name="slack.post_message",
                entity_id=CHANNEL,
                params={"text": "hello"},
            ),
        )
        await expect(
            "a node that never existed reads the same way",
            ActionNotAvailable,
            runner.check(
                conn, owner,
                action_name="slack.post_message",
                entity_id="slack:channel:does-not-exist",
                params={"text": "hello"},
            ),
        )

        print("\n== an action must match its node type ==")
        await expect(
            "posting a new message to a message is refused",
            ActionNotAvailable,
            runner.check(
                conn, owner,
                action_name="slack.post_message",
                entity_id=MESSAGE,
                params={"text": "hello"},
            ),
        )
        await passes(
            "replying to it is not",
            runner.check(
                conn, owner,
                action_name="slack.reply_in_thread",
                entity_id=MESSAGE,
                params={"text": "hello"},
            ),
        )

        print("\n== messaging a person ==")
        await passes(
            "a person carrying a Slack id can be messaged",
            runner.check(
                conn, owner,
                action_name="slack.dm",
                entity_id=PERSON_WITH_SLACK,
                params={"text": "hello"},
            ),
        )
        await expect(
            "one known only by name reports the missing id, not a refusal",
            ActionError,
            runner.check(
                conn, owner,
                action_name="slack.dm",
                entity_id=PERSON_WITHOUT_SLACK,
                params={"text": "hello"},
            ),
        )
        await expect(
            "a person nobody can see cannot be messaged",
            ActionNotAvailable,
            runner.check(
                conn, outsider,
                action_name="slack.dm",
                entity_id=PERSON_WITH_SLACK,
                params={"text": "hello"},
            ),
        )

        print("\n== parameters ==")
        await expect(
            "an empty message is rejected before dispatch",
            ActionError,
            runner.check(
                conn, owner,
                action_name="slack.post_message",
                entity_id=CHANNEL,
                params={"text": ""},
            ),
        )
        await expect(
            "so is an unknown action",
            ActionNotAvailable,
            runner.check(
                conn, owner,
                action_name="slack.telepathy",
                entity_id=CHANNEL,
                params={"text": "hi"},
            ),
        )

        print("\n== plan validation, before anything is sent ==")
        good = [
            step("a1", "drive.create_file", FOLDER, name="Q3 Summary", content="..."),
            step(
                "a2",
                "slack.reply_in_thread",
                MESSAGE,
                text="Summary is up: {{a1.web_view_link}}",
            ),
        ]
        try:
            planning.validate(good)
            check("a plan that writes then announces is valid", True)
        except PlanError as exc:
            check("a plan that writes then announces is valid", False, str(exc))

        for label, bad in (
            (
                "a forward reference",
                [
                    step("a1", "slack.post_message", CHANNEL, text="{{a2.ts}}"),
                    step("a2", "slack.post_message", CHANNEL, text="x"),
                ],
            ),
            (
                "a field the producing action does not return",
                [
                    step("a1", "slack.post_message", CHANNEL, text="x"),
                    step("a2", "slack.post_message", CHANNEL, text="{{a1.file_id}}"),
                ],
            ),
            (
                "a duplicate step id",
                [
                    step("a1", "slack.post_message", CHANNEL, text="x"),
                    step("a1", "slack.post_message", CHANNEL, text="y"),
                ],
            ),
            (
                "an unknown action",
                [step("a1", "slack.telepathy", CHANNEL, text="x")],
            ),
            ("an empty plan", []),
        ):
            try:
                planning.validate(bad)
                check(f"{label} is rejected", False, "validate accepted it")
            except PlanError:
                check(f"{label} is rejected", True)

        check(
            "a plan longer than the cap is rejected",
            not _validates(
                [
                    step(f"a{i}", "slack.post_message", CHANNEL, text="x")
                    for i in range(planning.MAX_PLAN_STEPS + 1)
                ]
            ),
        )

        print("\n== bindings ==")
        check(
            "a reference is substituted inside the sentence",
            planning._resolve(
                "Draft is up: {{a1.web_view_link}} — take a look",
                {"a1": {"web_view_link": "https://x/1"}},
                dry_run=False,
            )
            == "Draft is up: https://x/1 — take a look",
        )
        check(
            "nested parameters are not exempt",
            planning._resolve(
                {"blocks": [{"text": "{{a1.ts}}"}]},
                {"a1": {"ts": "1700.1"}},
                dry_run=False,
            )
            == {"blocks": [{"text": "1700.1"}]},
        )
        check(
            "a field the step did not return leaves no braces behind",
            planning._resolve(
                "link: {{a1.permalink}}", {"a1": {"ts": "1700.1"}}, dry_run=False
            )
            == "link: ",
        )
        check(
            "a dry run renders a legible stand-in rather than an empty string",
            planning._resolve(
                "link: {{a1.web_view_link}}", {}, dry_run=True
            )
            == "link: <a1.web_view_link>",
        )

        print("\n== dry run ==")
        before = await invocations(conn)
        result = await run_plan(conn, runner, editor, good, dry_run=True)
        check("every step is checked", result.status == "checked", result.status)
        check(
            "and none is reported as done",
            all(s.status == "checked" for s in result.steps),
        )
        check("nothing was recorded", await invocations(conn) == before)
        check(
            "the text that would be sent is shown resolved",
            "<a1.web_view_link>" in result.steps[1].params["text"],
        )

        denied = await run_plan(
            conn,
            runner,
            reader,
            [
                step("a1", "drive.create_file", FOLDER, name="X", content="..."),
                step("a2", "slack.reply_in_thread", MESSAGE, text="done"),
            ],
            dry_run=True,
        )
        check(
            "a dry run surfaces the refusal the real run would hit",
            denied.status == "failed" and denied.steps[0].status == "error",
            denied.status,
        )
        check(
            "and stops rather than checking past it",
            denied.steps[1].status == "skipped",
        )
        check("still nothing recorded", await invocations(conn) == before)

        await cleanup(conn)

    await runner.aclose()
    await pool.close()

    print()
    if FAILURES:
        print(f"{len(FAILURES)} FAILED: {FAILURES}")
        raise SystemExit(1)
    print("all checks passed")


# ------------------------------------------------------------------ helpers --


def _validates(plan: list[PlannedAction]) -> bool:
    try:
        planning.validate(plan)
        return True
    except PlanError:
        return False


async def node_id(conn, entity_id: str):
    return await conn.fetchval("SELECT id FROM node WHERE entity_id = $1", entity_id)


async def invocations(conn) -> int:
    return await conn.fetchval("SELECT count(*) FROM action_invocation")


async def expect(label: str, exc_type: type[Exception], coro) -> None:
    try:
        await coro
    except exc_type:
        check(label, True)
        return
    except Exception as exc:  # noqa: BLE001 - reporting the wrong type is the point
        check(label, False, f"raised {type(exc).__name__}: {exc}")
        return
    check(label, False, "no exception")


async def passes(label: str, coro) -> None:
    try:
        await coro
    except Exception as exc:  # noqa: BLE001 - any failure is the failure
        check(label, False, f"{type(exc).__name__}: {exc}")
        return
    check(label, True)


if __name__ == "__main__":
    asyncio.run(main())
