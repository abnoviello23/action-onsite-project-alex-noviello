"""Publish an ontology revision: `python -m semantic.publish`.

The reason `semantic_config` is a versioned table rather than a constant is that
what an organisation tracks changes on a different clock from the pipeline. This
is the smallest thing that actually exercises that: it appends the current
`DEFAULT_CONFIG` as a new active version and retires the previous one.

    python -m semantic.publish            # show the active version
    python -m semantic.publish --apply    # publish DEFAULT_CONFIG as the next

Append-only, so the version a given fact was extracted under stays readable.
Prompt and description edits take effect on running workers within one
`ActiveConfig` TTL; adding or removing an identity *field* also needs `migrate`,
because a view is DDL.

`--reextract` resets the extraction watermark so the sweeper re-offers every
document under the new ontology. That is a model call per document, so it is a
separate flag rather than something publishing does on its own.
"""

from __future__ import annotations

import argparse
import asyncio

from common import db
from common.logging import setup
from semantic.config import load_active, publish
from semantic.registry import DEFAULT_CONFIG

log = setup("semantic.publish")


async def main() -> None:
    parser = argparse.ArgumentParser(prog="semantic.publish")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="publish DEFAULT_CONFIG as the next active version",
    )
    parser.add_argument(
        "--reextract",
        action="store_true",
        help="reset semantic_version so every document is re-offered (costly)",
    )
    args = parser.parse_args()

    conn = await db.connect()
    try:
        active = await load_active(conn)
        if active is None:
            log.info("no active ontology")
        else:
            log.info(
                "active: v%d, types %s",
                active.version,
                ", ".join(sorted(active.type_names)),
            )

        if args.apply:
            version = await publish(conn, DEFAULT_CONFIG)
            log.info(
                "published v%d with types %s",
                version,
                ", ".join(sorted(DEFAULT_CONFIG.type_names)),
            )

        if args.reextract:
            status = await conn.execute(
                "UPDATE node SET semantic_version = '' "
                "WHERE semantic_version <> '' AND node_type IS NOT NULL"
            )
            log.warning(
                "reset the extraction watermark on %s row(s); the sweeper will "
                "re-offer them, one model call each",
                str(status).rsplit(" ", 1)[-1],
            )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
