"""Dispatching one action: check, log, call, record.

The order is the design. Visibility is checked before anything else, then the
level the action demands, then the parameters; the invocation row is written
before the call leaves the process, and the outcome is recorded after it returns
— including when it raises. An action that changed something in Slack and then
failed to record that it did would be worse than one that never ran, because the
log is the only place the change is discoverable from this side until the poller
catches up.

**Reading and writing are separate questions.** `query.visibility` answers the
first with a boolean, which is the right shape for retrieval and the wrong shape
here: a `drive:commenter` can read every word of a document and may not change
one. So a spec names the level it needs and this module compares it against the
strongest grant the principal holds on the target. Refusal reuses the same
collapse an invisible node gets — unavailable, never forbidden — because "you
may see this but not write it" is a fact about the caller, while "this exists"
is a fact about the graph, and only the first is theirs to learn.

Writers are opened once and held. Each carries its own connection pool and, for
Drive, a service-account token with a lifetime; rebuilding them per invocation
would pay a full auth handshake to post one message.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

import asyncpg
from pydantic import ValidationError

from common import config
from connectors.drive.models import DOC_MIME
from connectors.drive.writer import DriveWriter
from connectors.notion.client import NotionClient
from connectors.notion.writer import NotionWriter, blocks_from_markdown
from connectors.slack.writer import SlackWriter
from core.actions import ACTIONS, ActionSpec, actions_for, address_of
from query.visibility import UnknownLevel, Visibility, level_priority

log = logging.getLogger("actions.runner")

_RESOLVE = """
SELECT id, entity_id, node_type, payload
FROM node
WHERE entity_id = $1 AND deleted_at IS NULL AND node_type IS NOT NULL
"""

_BEGIN = """
INSERT INTO action_invocation
    (action_name, node_id, identity_id, params, status, plan_id)
VALUES ($1, $2, $3, $4::jsonb, 'running', $5)
RETURNING id
"""

_FINISH = """
UPDATE action_invocation
SET status = $2, result_ref = $3, result = $4::jsonb, error = $5,
    finished_at = now()
WHERE id = $1
"""


class ActionError(RuntimeError):
    """The action cannot run, or ran and failed. The message is caller-facing."""


class ActionNotAvailable(ActionError):
    """The action does not apply to this node, or does not exist.

    Also what an invisible target and an insufficient level raise, deliberately:
    all three are answered "not available to you" so that none of them confirms
    anything the caller could not already establish.
    """


@dataclass(frozen=True)
class ActionResult:
    invocation_id: UUID
    action: str
    entity_id: str
    # The source's own id for what was written — a Slack ts, a Notion page id, a
    # Drive file id. The handle that makes the write findable from outside.
    result_ref: str | None
    # Everything the action declared it returns: the ref above plus the fields a
    # later step of a plan may bind to (`web_view_link`, `permalink`, …).
    result: dict[str, str] = field(default_factory=dict)


class Runner:
    """Executes actions against the sources. One per process; holds writers open."""

    def __init__(self) -> None:
        self._slack: SlackWriter | None = None
        self._notion: NotionWriter | None = None
        self._notion_client: NotionClient | None = None
        self._drive: DriveWriter | None = None

    async def aclose(self) -> None:
        if self._slack is not None:
            await self._slack.__aexit__(None, None, None)
            self._slack = None
        if self._notion_client is not None:
            await self._notion_client.__aexit__(None, None, None)
            self._notion_client = None
            self._notion = None
        if self._drive is not None:
            await self._drive.__aexit__(None, None, None)
            self._drive = None

    # ------------------------------------------------------------ dispatch --

    async def check(
        self,
        conn: asyncpg.Connection,
        vis: Visibility,
        *,
        action_name: str,
        entity_id: str,
        params: dict[str, Any],
    ) -> tuple[ActionSpec, UUID, dict[str, str], Any]:
        """Everything `invoke` decides before anything leaves the process.

        Split out so a dry run is the same decisions as a real one rather than a
        second implementation of them — a preview that validated differently
        from the dispatcher would be worth less than no preview at all.

        Returns `(spec, node_id, address, validated_params)`.
        """
        if not config.ACTIONS_ENABLED:
            raise ActionError("actions are disabled; set ACTIONS_ENABLED=true")

        spec = ACTIONS.get(action_name)
        if spec is None:
            raise ActionNotAvailable(
                f"unknown action {action_name!r}; available: {sorted(ACTIONS)}"
            )

        row = await conn.fetchrow(_RESOLVE, entity_id)
        # Absent and invisible give the same answer, exactly as `SessionGraph.get`
        # does: a distinguishable "exists but forbidden" would confirm the node
        # to anyone willing to ask for it.
        if row is None or not await vis.is_visible(conn, row["id"]):
            raise ActionNotAvailable(f"{entity_id} is not available")

        if row["node_type"] != str(spec.node_type):
            raise ActionNotAvailable(
                f"{action_name} applies to {spec.node_type}, not "
                f"{row['node_type']}; this node accepts "
                f"{[a.name for a in actions_for(row['node_type'])]}"
            )

        await self._check_level(conn, vis, spec, entity_id, row["id"])

        try:
            validated = spec.params.model_validate(params)
        except ValidationError as exc:
            raise ActionError(f"invalid parameters for {action_name}: {exc}") from exc

        address = address_of(spec, row["node_type"], row["payload"] or {})
        missing = [k for k in spec.requires_native if not address.get(k)]
        if missing:
            raise ActionError(
                f"{entity_id} is missing the source id(s) {missing} that "
                f"{action_name} needs"
            )

        return spec, row["id"], address, validated

    async def invoke(
        self,
        conn: asyncpg.Connection,
        vis: Visibility,
        *,
        action_name: str,
        entity_id: str,
        params: dict[str, Any],
        plan_id: UUID | None = None,
    ) -> ActionResult:
        """Run one action on one node, as one principal."""
        spec, node_id, address, validated = await self.check(
            conn,
            vis,
            action_name=action_name,
            entity_id=entity_id,
            params=params,
        )

        # Written before the call goes out. A row left in 'running' is a crash
        # mid-flight, which is the state worth being able to find.
        invocation_id = await conn.fetchval(
            _BEGIN,
            spec.name,
            node_id,
            vis.identity_id,
            validated.model_dump(mode="json"),
            plan_id,
        )

        try:
            result = await self._execute(spec, address, validated)
        except Exception as exc:
            await conn.execute(
                _FINISH, invocation_id, "error", None, None, str(exc)[:2000]
            )
            log.exception("%s failed on %s", action_name, entity_id)
            raise ActionError(f"{action_name} failed: {exc}") from exc

        ref_field = spec.result_ref_field
        result_ref = result.get(ref_field) if ref_field else None
        await conn.execute(_FINISH, invocation_id, "ok", result_ref, result, None)
        log.info(
            "%s on %s by %s -> %s",
            action_name,
            entity_id,
            vis.identity_id,
            result_ref,
        )
        return ActionResult(
            invocation_id=invocation_id,
            action=spec.name,
            entity_id=entity_id,
            result_ref=result_ref,
            result=result,
        )

    # --------------------------------------------------------- authorization --

    @staticmethod
    async def _check_level(
        conn: asyncpg.Connection,
        vis: Visibility,
        spec: ActionSpec,
        entity_id: str,
        node_id: UUID,
    ) -> None:
        """Refuse a principal who may read the target but not write it.

        Skipped entirely when the spec names no level, which is not laxity: a
        `person` holds no grants at all, and a Slack DM has no ACL to mirror, so
        there is nothing to compare and visibility is the whole gate.
        """
        if spec.requires_level is None:
            return

        try:
            required = await level_priority(conn, spec.requires_level)
        except UnknownLevel as exc:
            # A spec naming a level the catalog does not define is a bug here,
            # not a denial. Failing loudly stops a typo becoming a permanent and
            # invisible refusal.
            raise ActionError(
                f"{spec.name} requires the level {exc} which is not in "
                f"access_level; this is a catalog bug"
            ) from exc

        held = await vis.strongest_level(conn, node_id)
        if held is None or held[1] < required:
            log.info(
                "%s refused on %s for %s: holds %s, needs %s",
                spec.name,
                entity_id,
                vis.identity_id,
                held[0] if held else "nothing",
                spec.requires_level,
            )
            # Same collapse as an invisible node. The caller learns that they
            # cannot do this, and nothing about who can.
            raise ActionNotAvailable(f"{entity_id} is not available for {spec.name}")

    # ------------------------------------------------------------ executors --

    async def _execute(
        self, spec: ActionSpec, address: dict[str, str], params: Any
    ) -> dict[str, str]:
        if spec.name == "slack.post_message":
            return await self._post(address["channel_id"], params.text)

        if spec.name == "slack.reply_in_thread":
            # A reply on a top-level message opens its thread, so the thread key
            # is the parent's ts when there is one and this message's otherwise.
            thread_ts = address.get("thread_ts") or address["ts"]
            return await self._post(
                address["channel_id"], params.text, thread_ts=thread_ts
            )

        if spec.name == "slack.dm":
            writer = await self._slack_writer()
            # Idempotent: the conversation between two parties is permanent, so
            # this resolves to the same channel every time rather than opening a
            # new one per message.
            channel_id = await writer.open_dm(address["slack_user_id"])
            return await self._post(channel_id, params.text)

        if spec.name == "drive.replace_content":
            writer = await self._drive_writer()
            await writer.update_content(address["file_id"], params.content)
            # The id survives the edit — that is what makes this an edit rather
            # than a delete and a create — so the link the node already carries
            # still resolves, and no second call is needed to find it.
            return {
                "file_id": address["file_id"],
                "web_view_link": address.get("web_view_link", ""),
            }

        if spec.name == "drive.create_file":
            writer = await self._drive_writer()
            created = await writer.create_file(
                params.name,
                address["file_id"],
                params.content,
                upload_mime="text/plain",
                target_mime=DOC_MIME if params.as_document else None,
                if_exists=params.if_exists,
            )
            return {
                "file_id": created["id"],
                "web_view_link": created.get("webViewLink", ""),
            }

        if spec.name == "notion.append_blocks":
            writer = await self._notion_writer()
            blocks = blocks_from_markdown(params.markdown)
            if not blocks:
                raise ActionError("markdown produced no blocks")
            await writer.append(address["page_id"], blocks)
            return {"page_id": address["page_id"], "url": address.get("url", "")}

        if spec.name == "notion.create_page":
            writer = await self._notion_writer()
            created = await writer.page(
                address["page_id"],
                params.title,
                body=params.markdown,
                if_exists=params.if_exists,
            )
            return {"page_id": created["id"], "url": created.get("url", "")}

        raise ActionNotAvailable(f"no executor for {spec.name}")

    async def _post(
        self, channel_id: str, text: str, *, thread_ts: str | None = None
    ) -> dict[str, str]:
        """Post, then look up the permalink. Shared by all three Slack verbs.

        The permalink lookup cannot fail the action: the message is already sent
        by the time it runs, and reporting a completed write as a failure would
        invite a retry that posts it twice. `SlackWriter.permalink` swallows its
        own errors for that reason and returns None.
        """
        writer = await self._slack_writer()
        ts = await writer.post(channel_id, text, thread_ts=thread_ts)
        result = {"ts": ts, "channel_id": channel_id}
        if permalink := await writer.permalink(channel_id, ts):
            result["permalink"] = permalink
        return result

    # -------------------------------------------------------------- writers --

    async def _slack_writer(self) -> SlackWriter:
        if self._slack is None:
            if not config.SLACK_BOT_TOKEN:
                raise ActionError("SLACK_BOT_TOKEN is not set")
            self._slack = await SlackWriter(config.SLACK_BOT_TOKEN).__aenter__()
        return self._slack

    async def _notion_writer(self) -> NotionWriter:
        if self._notion is None:
            if not config.NOTION_TOKEN:
                raise ActionError("NOTION_TOKEN is not set")
            self._notion_client = await NotionClient(
                config.NOTION_TOKEN, config.NOTION_VERSION
            ).__aenter__()
            self._notion = NotionWriter(self._notion_client)
        return self._notion

    async def _drive_writer(self) -> DriveWriter:
        if self._drive is None:
            if not config.GOOGLE_APPLICATION_CREDENTIALS:
                raise ActionError("GOOGLE_APPLICATION_CREDENTIALS is not set")
            self._drive = await DriveWriter(
                config.GOOGLE_APPLICATION_CREDENTIALS
            ).__aenter__()
        return self._drive
