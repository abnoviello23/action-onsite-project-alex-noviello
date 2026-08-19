"""Fill a page with *visible* rows, not with rows that then get filtered.

The naive shape — `LIMIT 50`, then drop what the principal cannot see — returns
3 rows and calls it a complete answer. Whether that happens is a property of the
requester, not of the query: a workspace admin sees nearly everything and never
notices, while a contractor in one channel gets a silently truncated result for
the same question. The demo user is the contractor.

So the limit is applied after the visibility check, by pulling pages from the
ordered query until enough survive or the source runs dry. Page size doubles as
it goes: a principal who can see one row in fifty converges in a few round trips
instead of dozens, and one who can see everything never fetches a second page.

The scan cap is the backstop against a predicate that matches a million rows the
requester cannot see. When it fires, `truncated` says so — a capped result must
never be presented as an exhaustive one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import asyncpg

from query.visibility import Visibility

log = logging.getLogger("query.paging")

# Rows read from the ordered source before giving up on filling the page.
DEFAULT_MAX_SCAN = 2_000
# First page. Deliberately larger than a typical `limit`, because the common
# case is that some fraction of matches are invisible.
FIRST_PAGE_MULTIPLE = 4
MIN_PAGE = 64
MAX_PAGE = 512


@dataclass(frozen=True)
class Page:
    rows: list[asyncpg.Record]
    scanned: int
    # The ordered source was read to its end. `truncated` is then necessarily
    # False: there was nothing more to find.
    exhausted: bool
    # The scan cap stopped the fill before the page was full. More visible rows
    # may exist beyond it; callers must say so rather than implying completeness.
    truncated: bool


async def fill_visible(
    conn: asyncpg.Connection,
    vis: Visibility,
    sql: str,
    params: list[Any],
    limit: int,
    *,
    max_scan: int = DEFAULT_MAX_SCAN,
) -> Page:
    """Read `sql` in pages until `limit` visible rows are collected.

    `sql` must be ordered and must select an `id` column; LIMIT/OFFSET are
    appended here, so it must not carry its own.
    """
    if limit < 1:
        return Page(rows=[], scanned=0, exhausted=True, truncated=False)
    if vis.sees_nothing:
        # No grant reaches this principal, so no row can pass. Skipping the scan
        # entirely is both faster and indistinguishable from running it.
        return Page(rows=[], scanned=0, exhausted=True, truncated=False)

    limit_pos = len(params) + 1
    paged_sql = f"{sql}\nLIMIT ${limit_pos} OFFSET ${limit_pos + 1}"

    kept: list[asyncpg.Record] = []
    scanned = 0
    offset = 0
    page = max(MIN_PAGE, limit * FIRST_PAGE_MULTIPLE)
    exhausted = False

    while len(kept) < limit and scanned < max_scan:
        size = min(page, max_scan - scanned)
        rows = await conn.fetch(paged_sql, *params, size, offset)
        if not rows:
            exhausted = True
            break

        scanned += len(rows)
        offset += len(rows)
        visible = await vis.visible(conn, [r["id"] for r in rows])
        kept.extend(r for r in rows if r["id"] in visible)

        if len(rows) < size:
            exhausted = True
            break
        page = min(MAX_PAGE, page * 2)

    truncated = not exhausted and len(kept) < limit
    if truncated:
        log.info(
            "scan cap reached for %s: %d scanned, %d visible",
            vis.identity_id,
            scanned,
            len(kept),
        )
    return Page(
        rows=kept[:limit],
        scanned=scanned,
        exhausted=exhausted,
        truncated=truncated,
    )
