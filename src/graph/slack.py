"""Slack envelopes -> graph writes.

Workspace identities, channel ACL, message nodes, and the channel/thread
`next` topology all live here. Pollers never shape the graph.
"""

from __future__ import annotations

from datetime import UTC, datetime

from connectors.slack.envelopes import (
    SlackChannelFacts,
    SlackMessageFacts,
    SlackWorkspaceFacts,
)
from connectors.slack.identities import is_content
from connectors.slack.models import Channel, Message, User
from connectors.slack.registry import Workspace
from connectors.slack.text import TextResolver
from core.access import SLACK_MEMBER, AccessGrant, Identity, Membership
from core.graph import IN_CHANNEL, IN_THREAD, NEXT, Edge, Node
from core.message import ChangeKind, Envelope, GraphWrite, RosterMode
from core.payloads import SlackChannelPayload, SlackMessagePayload
from core.types import NodeType
from graph.links import with_url_mentions
from graph.protocol import SlackGraphView


def slack_user(user_id: str) -> str:
    return f"slack:user:{user_id}"


def slack_workspace(team_id: str) -> str:
    return f"slack:workspace:{team_id}"


def slack_unresolved(bot_id: str) -> str:
    return f"slack:unresolved:{bot_id}"


def identity_from_user(user: User) -> Identity:
    return Identity(
        id=slack_user(user.id),
        display_name=user.best_name,
        email=user.profile.email,
        is_active=not user.deleted,
    )


def _is_principal(user: User) -> bool:
    return not user.deleted and not user.is_bot and user.id != "USLACKBOT"


def _actor_id(message: Message) -> str | None:
    if message.user:
        return slack_user(message.user)
    if message.bot_id:
        return slack_unresolved(message.bot_id)
    return None


async def _splice(
    graph: SlackGraphView,
    *,
    entity_id: str,
    channel_id: str,
    ts: str,
    thread_ts: str | None,
) -> tuple[list[Edge], list[Edge]]:
    pred, succ = await graph.slack_neighbors(
        channel_id=channel_id, ts=ts, thread_ts=thread_ts, exclude=entity_id
    )
    edges: list[Edge] = []
    retract: list[Edge] = []
    if pred:
        for old in await graph.outgoing(pred, NEXT):
            retract.append(Edge(from_entity_id=pred, to_entity_id=old, relation=NEXT))
        edges.append(Edge(from_entity_id=pred, to_entity_id=entity_id, relation=NEXT))
    if succ:
        for old in await graph.outgoing(entity_id, NEXT):
            retract.append(
                Edge(from_entity_id=entity_id, to_entity_id=old, relation=NEXT)
            )
        edges.append(Edge(from_entity_id=entity_id, to_entity_id=succ, relation=NEXT))
    return edges, retract


class SlackWorkspaceGenerator:
    node_type = NodeType.SLACK_WORKSPACE

    async def generate(self, env: Envelope, graph: SlackGraphView) -> GraphWrite:
        facts = SlackWorkspaceFacts.model_validate(env.payload)
        workspace = Workspace(
            team_id=facts.team_id,
            team_name=facts.team_name,
            domain=facts.domain,
        )
        users = list(facts.users)
        identity = Identity(
            id=slack_workspace(workspace.team_id),
            display_name=workspace.team_name,
        )
        people = [u for u in users if _is_principal(u)]
        if facts.replace_roster:
            identities = [identity, *(identity_from_user(u) for u in users)]
            roster = RosterMode.PARENT
        else:
            identities = [identity_from_user(u) for u in users]
            roster = RosterMode.CHILDREN
        return GraphWrite(
            node_type=NodeType.SLACK_WORKSPACE,
            entity_id=workspace.entity_id,
            roster=roster,
            identities=identities,
            memberships=[
                Membership(
                    child_identity_id=slack_user(u.id),
                    parent_identity_id=identity.id,
                )
                for u in people
            ],
        )


def _advance_version(stored: str | None, source_stamp: int, changed: bool) -> str:
    stamp = f"{source_stamp:020d}"
    if stored is None:
        return stamp
    if not changed:
        return stored
    try:
        prev = int(stored)
    except ValueError:
        prev = 0
    return f"{max(prev + 1, source_stamp):020d}"


def _channel_snapshot(
    channel: Channel, member_ids: list[str]
) -> tuple[object, ...]:
    return (
        channel.display,
        channel.is_private,
        channel.is_archived,
        channel.is_general,
        channel.is_shared,
        channel.is_ext_shared,
        channel.num_members,
        channel.creator,
        channel.topic.value,
        channel.purpose.value,
        tuple(sorted(member_ids)),
    )


def _channel_body(channel: Channel) -> str:
    lines = [f"#{channel.display}"]
    if channel.purpose.value:
        lines.append(channel.purpose.value)
    if channel.topic.value and channel.topic.value != channel.purpose.value:
        lines.append(channel.topic.value)
    return "\n".join(lines)


class SlackChannelGenerator:
    node_type = NodeType.SLACK_CHANNEL

    async def generate(self, env: Envelope, graph: SlackGraphView) -> GraphWrite:
        facts = SlackChannelFacts.model_validate(env.payload)
        channel = facts.channel
        created = datetime.fromtimestamp(channel.created or 0, tz=UTC)
        updated = datetime.fromtimestamp(channel.source_stamp, tz=UTC)
        entity_id = channel.entity_id
        member_ids = sorted(facts.member_ids)

        stored = await graph.node_payload(entity_id)
        stored_snap = None
        if stored is not None:
            stored_snap = (
                stored.get("name"),
                stored.get("is_private"),
                stored.get("is_archived"),
                stored.get("is_general"),
                stored.get("is_shared"),
                stored.get("is_ext_shared"),
                stored.get("member_count"),
                stored.get("creator"),
                stored.get("topic") or "",
                stored.get("purpose") or "",
                tuple(sorted(stored.get("member_ids") or [])),
            )
        changed = stored_snap != _channel_snapshot(channel, member_ids)
        version = _advance_version(
            await graph.content_version(entity_id), channel.source_stamp, changed
        )

        # ACL is not the roster. Public channels grant to the workspace; private
        # channels grant to each member. member_ids still land on the payload
        # either way, so "who's in this channel" does not depend on is_private.
        if channel.is_private:
            grants = [
                AccessGrant(
                    identity_id=slack_user(member_id),
                    resource_entity_id=entity_id,
                    level=SLACK_MEMBER,
                )
                for member_id in member_ids
            ]
        else:
            grants = [
                AccessGrant(
                    identity_id=slack_workspace(facts.team_id),
                    resource_entity_id=entity_id,
                    level=SLACK_MEMBER,
                )
            ]

        node = Node(
            node_type=NodeType.SLACK_CHANNEL,
            entity_id=entity_id,
            permission_parent_entity_id=None,
            body=_channel_body(channel),
            created_at=created,
            updated_at=updated,
            content_version=version,
            payload=SlackChannelPayload(
                channel_id=channel.id,
                team_id=facts.team_id,
                name=channel.display,
                is_private=channel.is_private,
                is_archived=channel.is_archived,
                is_general=channel.is_general,
                is_shared=channel.is_shared,
                is_ext_shared=channel.is_ext_shared,
                created=channel.created,
                creator=channel.creator,
                topic=channel.topic.value,
                purpose=channel.purpose.value,
                member_count=channel.num_members,
                member_ids=member_ids,
            ).model_dump(mode="json"),
        )
        return GraphWrite(
            node_type=NodeType.SLACK_CHANNEL,
            entity_id=entity_id,
            node=node,
            grants=grants,
        )


class SlackMessageGenerator:
    node_type = NodeType.SLACK_MESSAGE

    async def generate(self, env: Envelope, graph: SlackGraphView) -> GraphWrite | None:
        if env.change is ChangeKind.DELETED:
            return await self._deleted(env, graph)

        facts = SlackMessageFacts.model_validate(env.payload)
        message = facts.message
        channel = facts.channel
        if not is_content(message):
            return None

        resolver = TextResolver(
            user_names=facts.user_names, channel_names=facts.channel_names
        )
        entity_id = message.entity_id(channel)
        link_urls = [link.url for link in resolver.links(message.text)]
        node = Node(
            node_type=NodeType.SLACK_MESSAGE,
            entity_id=entity_id,
            permission_parent_entity_id=message.permission_parent_entity_id(channel),
            body=resolver.resolve(message.text),
            created_at=message.occurred_at,
            updated_at=message.updated_at,
            content_version=message.effective_ts,
            payload=SlackMessagePayload(
                channel_id=channel.id,
                ts=message.ts,
                thread_ts=message.thread_ts,
                user_id=message.user,
                bot_id=message.bot_id,
                actor_id=_actor_id(message),
                text_raw=message.text,
                subtype=message.subtype,
                edited_ts=message.edited.ts if message.edited else None,
                reply_count=message.reply_count or 0,
                file_ids=[f.id for f in message.files],
                mentioned_user_ids=resolver.mentioned_user_ids(message.text),
                link_urls=link_urls,
            ).model_dump(mode="json"),
        )

        edges = [
            Edge(
                from_entity_id=entity_id,
                to_entity_id=channel.entity_id,
                relation=IN_CHANNEL,
            )
        ]
        retract: list[Edge] = []
        if message.is_thread_reply and message.thread_ts:
            edges.append(
                Edge(
                    from_entity_id=entity_id,
                    to_entity_id=channel.message_entity_id(message.thread_ts),
                    relation=IN_THREAD,
                )
            )
            thread_scope = message.thread_ts
        else:
            thread_scope = None

        if env.change is not ChangeKind.UPDATED:
            extra, drop = await _splice(
                graph,
                entity_id=entity_id,
                channel_id=channel.id,
                ts=message.ts,
                thread_ts=thread_scope,
            )
            edges.extend(extra)
            retract.extend(drop)

        return await with_url_mentions(
            graph,
            GraphWrite(
                node_type=NodeType.SLACK_MESSAGE,
                entity_id=entity_id,
                change=env.change,
                node=node,
                edges=edges,
                retract_edges=retract,
            ),
            link_urls,
            needles=[
                f"archives/{channel.id}/p{message.ts.replace('.', '')}",
            ],
            retract_stale=True,
        )

    async def _deleted(self, env: Envelope, graph: SlackGraphView) -> GraphWrite:
        # Same as Drive/Notion: drop every outgoing edge (`in_channel`,
        # `in_thread`, `next`). The splice below only rewires the predecessor.
        payload = await graph.node_payload(env.entity_id)
        edges: list[Edge] = []
        retract = await graph.edges_from(env.entity_id)
        if payload:
            channel_id = payload.get("channel_id")
            ts = payload.get("ts")
            thread_ts = payload.get("thread_ts")
            is_reply = bool(thread_ts and ts and thread_ts != ts)
            if channel_id and ts:
                pred, succ = await graph.slack_neighbors(
                    channel_id=channel_id,
                    ts=ts,
                    thread_ts=thread_ts if is_reply else None,
                    exclude=env.entity_id,
                )
                if pred:
                    for old in await graph.outgoing(pred, NEXT):
                        retract.append(
                            Edge(from_entity_id=pred, to_entity_id=old, relation=NEXT)
                        )
                    if succ:
                        edges.append(
                            Edge(
                                from_entity_id=pred,
                                to_entity_id=succ,
                                relation=NEXT,
                            )
                        )
        return GraphWrite(
            node_type=NodeType.SLACK_MESSAGE,
            entity_id=env.entity_id,
            change=ChangeKind.DELETED,
            edges=edges,
            retract_edges=retract,
        )
