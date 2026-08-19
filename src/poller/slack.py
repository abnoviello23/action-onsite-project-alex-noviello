"""Slack ingestion service: backfill poll + live Socket Mode.

Both paths are needed and neither is redundant:

  poll    seeds history and heals gaps, because Socket Mode delivers nothing
          from before the connection opened and never replays what was missed
          while disconnected.
  socket  is the only source of edits, deletions, replies to old threads, and
          ACL events — the Web API has no "changed since" filter, since its
          oldest/latest bound the creation ts, which never changes.

All Slack API access lives here. Workers never call Slack, so scaling workers
does not scale the request rate and the client's rate limiter is authoritative.
"""

from __future__ import annotations

import asyncio
import logging

from common.stream import Watermarks, WorkStream
from connectors.slack.client import SlackClient, SlackError
from connectors.slack.events import SlackEventMapper
from connectors.slack.models import Channel, Message
from connectors.slack.registry import SlackRegistry
from connectors.slack.socket import SocketModeClient
from core.message import ChangeKind, Envelope

log = logging.getLogger("poller.slack")

SOURCE = "slack"


class SlackService:
    def __init__(
        self,
        client: SlackClient,
        registry: SlackRegistry,
        stream: WorkStream,
        watermarks: Watermarks,
        *,
        include_private: bool = True,
    ) -> None:
        self._client = client
        self._registry = registry
        self._stream = stream
        self._watermarks = watermarks
        self._include_private = include_private
        self._mapper = SlackEventMapper(client, registry)

    # ------------------------------------------------------------ backfill --

    async def poll_once(self) -> int:
        """One pass over every readable channel, incremental by watermark."""
        await self._registry.refresh(
            self._client, include_private=self._include_private
        )

        # The roster goes first. Public channels grant to the workspace
        # identity, so until its memberships land that grant resolves to nobody
        # and every public channel reads as invisible.
        await self._stream.publish(self._mapper.workspace_message())
        published = 1

        for channel in self._registry.readable_channels():
            published += await self._poll_channel(channel)
        log.info("poll cycle complete: %d messages", published)
        return published

    async def run_poll_loop(self, interval_seconds: float) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception:
                log.exception("poll cycle failed")
            await asyncio.sleep(interval_seconds)

    async def _poll_channel(self, channel: Channel) -> int:
        # The channel carries the ACL every message inherits, so it is published
        # before anything that depends on it.
        await self._stream.publish(await self._mapper.channel_message(channel))
        published = 1

        since = await self._watermarks.get(channel.id)
        newest = since
        thread_parents: list[str] = []

        try:
            async for message, _raw in self._client.history(channel.id, oldest=since):
                if message.is_thread_parent:
                    thread_parents.append(message.ts)
                if newest is None or message.ts > newest:
                    newest = message.ts
                published += await self._publish_message(message, channel)

            for thread_ts in thread_parents:
                async for message, _raw in self._client.replies(channel.id, thread_ts):
                    # Slack echoes the parent as the first item of every replies
                    # page; it was already published from history.
                    if message.ts == thread_ts:
                        continue
                    published += await self._publish_message(message, channel)
        except SlackError as exc:
            # Leave the watermark unmoved so the next cycle retries this range.
            log.error("#%s: %s", channel.display, exc.error)
            return published

        # Advanced only after every publish succeeded. A crash before this
        # re-emits rather than loses; the version guard absorbs duplicates.
        if newest and newest != since:
            await self._watermarks.set(channel.id, newest)

        if published > 1:
            log.info("#%s: %d events", channel.display, published)
        return published

    async def _publish_message(self, message: Message, channel: Channel) -> int:
        # Join/leave bookkeeping is not content; the mapper returns None.
        write = self._mapper.message_message(
            message, channel, change=ChangeKind.CREATED
        )
        if write is None:
            return 0
        await self._stream.publish(write)
        return 1

    # -------------------------------------------------------------- socket --

    async def run_socket_loop(self, socket: SocketModeClient) -> None:
        async for event in socket.listen():
            try:
                message = await self._mapper.from_socket(event)
            except Exception:
                log.exception("failed to map event %s", event.get("type"))
                continue
            if message is not None:
                await self._stream.publish(message)
                _log(message)


def _log(envelope: Envelope) -> None:
    log.info(
        "live %s %s %s",
        envelope.change,
        envelope.node_type,
        envelope.entity_id,
    )
