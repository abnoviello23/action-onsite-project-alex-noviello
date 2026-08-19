"""App user administration.

  python -m identity link --email abn52@cornell.edu --name "Alex Noviello"
  python -m identity link --email abn52@cornell.edu --dry-run
  python -m identity show  --id app:user:abn52@cornell.edu

`link` creates the app user if it is missing and gives it a membership edge into
every identity that shares its email — the app user as the child, so grants flow
down into it. Safe to re-run: it adds only the edges that are missing, which is
how an identity ingested after the first run gets picked up.
"""

from __future__ import annotations

import argparse
import asyncio

from common import db
from common.logging import setup
from identity import app_user_id, describe_app_user, link_app_user, preview_app_user

log = setup("identity")


def _print(report, *, dry_run: bool = False) -> None:
    label = report.display_name or report.app_user
    print()
    print(f"  {label}  <{report.email}>")
    print(f"  {report.app_user}")
    if report.created:
        print("  created")

    if not report.matched:
        print()
        print("  No identity shares this email.")
        print("  Nothing to link — check the address, or wait for ingestion to")
        print("  mirror the accounts that use it.")
        return

    print()
    print(f"  member of {len(report.matched)} identit{'y' if len(report.matched) == 1 else 'ies'}:")
    for mirrored_id, name in report.matched:
        if dry_run:
            state = "would link"
        elif mirrored_id in report.linked:
            state = "linked"
        else:
            state = "already linked"
        print(f"    {mirrored_id:<56} {name or '':<22} {state}")

    if dry_run:
        print()
        print("  Dry run — nothing was written.")
        return

    print()
    print(
        f"  reaches {report.principals} principals and "
        f"{report.granted_nodes} directly granted nodes"
    )
    print()
    print("  To query as this identity, add it to AGENT_DEMO_IDENTITIES and")
    print("  restart the api:")
    print(f"    AGENT_DEMO_IDENTITIES=...,{report.app_user}")


async def _link(args: argparse.Namespace) -> None:
    conn = await db.connect()
    try:
        if args.dry_run:
            report = await preview_app_user(
                conn, email=args.email, display_name=args.name, user_id=args.id
            )
            _print(report, dry_run=True)
            return

        report = await link_app_user(
            conn, email=args.email, display_name=args.name, user_id=args.id
        )
        _print(report)
    finally:
        await conn.close()


async def _show(args: argparse.Namespace) -> None:
    identity_id = args.id or app_user_id(args.email or "")
    conn = await db.connect()
    try:
        report = await describe_app_user(conn, identity_id)
        _print(report)
    except LookupError:
        raise SystemExit(f"no identity {identity_id!r}")
    finally:
        await conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(prog="identity")
    sub = parser.add_subparsers(dest="command", required=True)

    link = sub.add_parser("link", help="create an app user and link it by email")
    link.add_argument("--email", required=True)
    link.add_argument("--name", help="display name")
    link.add_argument("--id", help=f"identity id (default: {app_user_id('<email>')})")
    link.add_argument(
        "--dry-run",
        action="store_true",
        help="print the edges that would be created, write nothing",
    )

    show = sub.add_parser("show", help="what an app user is linked to")
    show.add_argument("--id")
    show.add_argument("--email")

    args = parser.parse_args()
    if args.command == "link":
        asyncio.run(_link(args))
    elif args.command == "show":
        if not (args.id or args.email):
            raise SystemExit("show needs --id or --email")
        asyncio.run(_show(args))


if __name__ == "__main__":
    main()
