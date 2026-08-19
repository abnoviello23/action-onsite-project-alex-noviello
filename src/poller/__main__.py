"""Poller service.

Hosts every source connector in one process. Each runs as an independently
supervised task, so one source failing cannot take the others down, and each is
skipped with a warning rather than a crash when its credentials are absent —
running Slack-only or Drive-only is a normal local configuration.

Splitting these into separate containers later is a compose change, not a
rewrite: the services share nothing but the Redis work stream.
"""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Coroutine
from contextlib import AsyncExitStack
from typing import Any

from common import config, redis_client
from common.logging import setup
from common.stream import Watermarks, WorkStream
from connectors.slack.client import SlackClient
from connectors.slack.registry import SlackRegistry
from connectors.slack.socket import SocketModeClient
from poller.slack import SOURCE as SLACK_SOURCE
from poller.slack import SlackService

log = setup("poller")

Task = Coroutine[Any, Any, None]


async def supervise(name: str, coro_factory) -> None:
    """Restart a long-running task on failure without taking down its siblings."""
    while True:
        try:
            await coro_factory()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("%s crashed; restarting in 5s", name)
        await asyncio.sleep(5)


def _drive_configured() -> bool:
    return bool(config.GOOGLE_APPLICATION_CREDENTIALS and config.DRIVE_IDS)


def _notion_configured() -> bool:
    # No root page id needed to *read*: the sweep discovers everything the
    # integration is connected to. It is required only for seeding.
    return bool(config.NOTION_TOKEN)


async def _start_slack(
    stack: AsyncExitStack, stream: WorkStream, redis, args: argparse.Namespace
) -> list[Task]:
    client = await stack.enter_async_context(SlackClient(config.SLACK_BOT_TOKEN))
    registry = await SlackRegistry.load(
        client, include_private=config.SLACK_INCLUDE_PRIVATE
    )
    service = SlackService(
        client,
        registry,
        stream,
        Watermarks(redis, SLACK_SOURCE),
        include_private=config.SLACK_INCLUDE_PRIVATE,
    )

    log.info(
        "slack: %d channels, %d users, poll=%ss",
        len(registry.channels),
        len(registry.users),
        config.SLACK_POLL_INTERVAL_SECONDS,
    )

    # Backfill first so history is present before live events layer changes on
    # top of it.
    await service.poll_once()
    if args.once:
        return []

    tasks: list[Task] = [
        supervise(
            "slack.poll",
            lambda: service.run_poll_loop(config.SLACK_POLL_INTERVAL_SECONDS),
        )
    ]
    if not args.no_socket and config.SLACK_APP_TOKEN:
        socket = SocketModeClient(config.SLACK_APP_TOKEN)
        tasks.append(supervise("slack.socket", lambda: service.run_socket_loop(socket)))
    else:
        log.warning("slack socket mode disabled: no SLACK_APP_TOKEN")
    return tasks


async def _start_drive(
    stack: AsyncExitStack, stream: WorkStream, redis, args: argparse.Namespace
) -> list[Task]:
    # Imported here, not at module scope: the Drive SDK is an optional
    # dependency and a Slack-only deployment must not fail to boot on it.
    from connectors.drive.auth import ServiceAccountAuth
    from connectors.drive.client import DriveClient
    from poller.drive import SOURCE as DRIVE_SOURCE
    from poller.drive import DriveService

    auth = ServiceAccountAuth(config.GOOGLE_APPLICATION_CREDENTIALS)
    client = await stack.enter_async_context(DriveClient(auth))
    service = DriveService(
        client,
        stream,
        Watermarks(redis, DRIVE_SOURCE),
        config.DRIVE_IDS,
        poll_interval_seconds=config.DRIVE_POLL_INTERVAL_SECONDS,
    )

    log.info(
        "drive: %s, %d drive(s), poll=%ss",
        auth.client_email,
        len(config.DRIVE_IDS),
        config.DRIVE_POLL_INTERVAL_SECONDS,
    )

    # Backfills a drive on first contact and drains changes on every cycle after,
    # so this one call covers both.
    await service.poll_once()
    if args.once:
        return []

    return [supervise("drive.poll", service.run_poll_loop)]


async def _start_notion(
    stack: AsyncExitStack, stream: WorkStream, redis, args: argparse.Namespace
) -> list[Task]:
    from connectors.notion.client import NotionClient
    from connectors.notion.registry import NotionRegistry
    from poller.notion import SOURCE as NOTION_SOURCE
    from poller.notion import KnownEntities, NotionService

    client = await stack.enter_async_context(
        NotionClient(config.NOTION_TOKEN, config.NOTION_VERSION)
    )
    registry = await NotionRegistry.load(client)
    service = NotionService(
        client,
        registry,
        stream,
        Watermarks(redis, NOTION_SOURCE),
        KnownEntities(redis, NOTION_SOURCE),
        poll_interval_seconds=config.NOTION_POLL_INTERVAL_SECONDS,
        reconcile_every=config.NOTION_RECONCILE_EVERY,
    )

    log.info(
        "notion: workspace %r, %d users, api=%s, poll=%ss",
        registry.workspace.name,
        len(registry.users),
        config.NOTION_VERSION,
        config.NOTION_POLL_INTERVAL_SECONDS,
    )

    # The first cycle reconciles, which is a full enumeration — so this single
    # call is both the backfill and the steady-state path.
    await service.poll_once()
    if args.once:
        return []

    return [supervise("notion.poll", service.run_poll_loop)]


async def main() -> None:
    parser = argparse.ArgumentParser(prog="poller")
    parser.add_argument(
        "--once", action="store_true", help="run one backfill cycle and exit"
    )
    parser.add_argument(
        "--no-socket", action="store_true", help="backfill only, no live events"
    )
    parser.add_argument(
        "--source",
        choices=["slack", "drive", "notion"],
        help="run a single source instead of everything configured",
    )
    args = parser.parse_args()

    wanted = {args.source} if args.source else {"slack", "drive", "notion"}
    run_slack = "slack" in wanted and bool(config.SLACK_BOT_TOKEN)
    run_drive = "drive" in wanted and _drive_configured()
    run_notion = "notion" in wanted and _notion_configured()

    if not (run_slack or run_drive or run_notion):
        raise SystemExit(
            "no source configured: set SLACK_BOT_TOKEN, NOTION_TOKEN, or "
            "GOOGLE_APPLICATION_CREDENTIALS together with DRIVE_IDS"
        )

    redis = redis_client.client()
    stream = WorkStream(redis)
    try:
        async with AsyncExitStack() as stack:
            tasks: list[Task] = []

            if run_slack:
                tasks += await _start_slack(stack, stream, redis, args)
            elif "slack" in wanted:
                log.warning("slack disabled: no SLACK_BOT_TOKEN")

            if run_drive:
                tasks += await _start_drive(stack, stream, redis, args)
            elif "drive" in wanted:
                log.warning(
                    "drive disabled: set GOOGLE_APPLICATION_CREDENTIALS and DRIVE_IDS"
                )

            if run_notion:
                tasks += await _start_notion(stack, stream, redis, args)
            elif "notion" in wanted:
                log.warning("notion disabled: no NOTION_TOKEN")

            if not tasks:
                return
            await asyncio.gather(*tasks)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(main())
