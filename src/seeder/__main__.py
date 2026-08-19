"""One-shot seeder.

Generates synthetic source-side data — real files in a real shared drive, real
pages in a real Notion workspace, real messages in a real Slack workspace —
rather than inserting rows directly. The poller then ingests them through the
identical path production content takes, so seeding exercises the whole pipeline
instead of only its last step.

The corpus is one company (Harborline) told three times: the same people, GTM
deals, meetings, projects, and tasks appear in Drive docs, Notion pages, and
Slack threads.

Safe to run repeatedly: every fixture is looked up by name (or message text)
before it is created. `--reset` trashes the previous Drive/Notion fixture tree
first (trash, never a hard delete). Slack is idempotent and is not archived on
reset, because archived channel names stay occupied.
"""

from __future__ import annotations

import argparse
import asyncio

from common import config
from common.logging import setup

log = setup("seeder")


def _drive_configured() -> bool:
    return bool(config.GOOGLE_APPLICATION_CREDENTIALS and config.DRIVE_IDS)


def _notion_configured() -> bool:
    # The root page id is not optional here, unlike in the poller: an internal
    # integration cannot create a workspace-level page, so there is nowhere to
    # put fixtures without one.
    return bool(config.NOTION_TOKEN and config.NOTION_ROOT_PAGE_ID)


def _slack_configured() -> bool:
    return bool(config.SLACK_BOT_TOKEN)


async def _seed_drive(args: argparse.Namespace) -> dict[str, str]:
    from connectors.drive.writer import DriveWriter
    from seeder import drive as drive_fixtures

    async with DriveWriter(config.GOOGLE_APPLICATION_CREDENTIALS) as writer:
        log.info("drive: seeding as %s", writer.client_email)
        # Fixtures land in the first configured drive only. Seeding every drive
        # would duplicate one corpus across them, which teaches nothing and
        # costs quota.
        drive_id = config.DRIVE_IDS[0]
        return await drive_fixtures.seed(writer, drive_id, reset=args.reset)


async def _seed_notion(args: argparse.Namespace) -> dict[str, str]:
    from connectors.notion.client import NotionClient
    from connectors.notion.writer import NotionWriter
    from seeder import notion as notion_fixtures

    async with NotionClient(config.NOTION_TOKEN, config.NOTION_VERSION) as client:
        info = await client.bot_info()
        log.info(
            "notion: seeding as %r into workspace %r",
            info.get("name"),
            (info.get("bot") or {}).get("workspace_name"),
        )
        return await notion_fixtures.seed(
            NotionWriter(client),
            config.NOTION_ROOT_PAGE_ID,
            reset=args.reset,
        )


async def _seed_slack(
    args: argparse.Namespace,
    *,
    drive_urls: dict[str, str] | None = None,
    notion_urls: dict[str, str] | None = None,
) -> int:
    from connectors.slack.writer import SlackWriter
    from seeder import slack as slack_fixtures

    async with SlackWriter(config.SLACK_BOT_TOKEN) as writer:
        return await slack_fixtures.seed(
            writer,
            reset=args.reset,
            drive_urls=drive_urls,
            notion_urls=notion_urls,
        )


async def main() -> None:
    parser = argparse.ArgumentParser(prog="seeder")
    parser.add_argument(
        "--reset",
        action="store_true",
        help="trash the previous Drive/Notion fixture tree before seeding",
    )
    parser.add_argument(
        "--source",
        choices=["slack", "drive", "notion"],
        help="seed a single source instead of everything configured",
    )
    args = parser.parse_args()

    wanted = {args.source} if args.source else {"slack", "drive", "notion"}
    seeded = False
    drive_ids: dict[str, str] = {}
    notion_ids: dict[str, str] = {}
    drive_urls: dict[str, str] = {}
    notion_urls: dict[str, str] = {}

    if "drive" in wanted:
        if _drive_configured():
            from seeder.drive import urls_from_ids as drive_urls_from_ids

            drive_ids = await _seed_drive(args)
            drive_urls = drive_urls_from_ids(drive_ids)
            seeded = True
        else:
            log.warning(
                "drive: skipped, set GOOGLE_APPLICATION_CREDENTIALS and DRIVE_IDS"
            )

    if "notion" in wanted:
        if _notion_configured():
            from seeder.notion import urls_from_ids as notion_urls_from_ids

            notion_ids = await _seed_notion(args)
            notion_urls = notion_urls_from_ids(notion_ids)
            seeded = True
        else:
            log.warning(
                "notion: skipped, set NOTION_TOKEN and NOTION_ROOT_PAGE_ID "
                "(the root must be a page a human connected to the integration)"
            )

    if (
        "drive" in wanted
        and "notion" in wanted
        and _drive_configured()
        and drive_ids
        and notion_urls
    ):
        from connectors.drive.writer import DriveWriter
        from seeder import drive as drive_fixtures

        async with DriveWriter(config.GOOGLE_APPLICATION_CREDENTIALS) as writer:
            n = await drive_fixtures.attach_notion_links(
                writer, drive_ids, notion_urls
            )
            log.info("cross-source: %d Drive docs now point at Notion", n)

    if (
        "notion" in wanted
        and "drive" in wanted
        and _notion_configured()
        and notion_ids
        and drive_urls
    ):
        from connectors.notion.client import NotionClient
        from connectors.notion.writer import NotionWriter
        from seeder import notion as notion_fixtures

        async with NotionClient(config.NOTION_TOKEN, config.NOTION_VERSION) as client:
            n = await notion_fixtures.attach_drive_links(
                NotionWriter(client), notion_ids, drive_urls
            )
            log.info("cross-source: %d Notion pages now bookmark Drive", n)

    if "slack" in wanted:
        if _slack_configured():
            await _seed_slack(args, drive_urls=drive_urls, notion_urls=notion_urls)
            seeded = True
        else:
            log.warning("slack: skipped, set SLACK_BOT_TOKEN")

    if not seeded:
        log.info("nothing seeded")


if __name__ == "__main__":
    asyncio.run(main())
