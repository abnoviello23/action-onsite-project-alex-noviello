"""App users, and the membership edges that connect them to mirrored identities.

A mirrored identity — `slack:user:U…`, `drive:user:alice@acme.com`,
`notion:user:…` — is a copy of somebody in a source. It holds the grants, and it
cannot log in. An **app user** is the account that logs in, holds no grants of
its own, and reaches a source's grants only by being a member of the mirrored
identity that holds them:

    membership(child = app:user:alice@acme.com, parent = slack:user:U123)

The direction is the one `query.visibility` walks: principals expand child ->
parent, so the child inherits what the parent was granted. Reversing it would
hand every Slack user whatever the app user could see.

**On matching by email.** The schema is explicit that email is a correspondence
hint and never the link, because an OAuth flow proves an account and a profile
field does not — in most of these sources the user types their own email. This
module therefore does the match *on demand and on the record*: an operator runs
it, it prints every edge it is about to create, and `--dry-run` shows the same
list while writing nothing. What it deliberately is not is a trigger or a
default: nothing here fires during ingestion, so an identity appearing later
with a matching email confers nothing until somebody runs this again.

That is the whole security posture of it. In a single-tenant demo where the
operator knows the people involved, linking on email is reasonable. In a
multi-tenant deployment it is not, and the replacement is the same edge written
by an OAuth callback instead — which is why the edge, not the matching, is what
the rest of the system is built on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import asyncpg

log = logging.getLogger("identity")

# App users live in their own namespace so nothing can confuse one with the
# mirrored identity it links to.
APP_PREFIX = "app:user:"


def app_user_id(email: str) -> str:
    """`app:user:alice@acme.com`. Lowercased, because an id that differs only by
    case is two accounts holding one person's access."""
    return f"{APP_PREFIX}{email.strip().lower()}"


@dataclass
class LinkReport:
    app_user: str
    email: str
    display_name: str | None = None
    created: bool = False
    # Mirrored identities sharing the email, and which of them are new edges.
    matched: list[tuple[str, str | None]] = field(default_factory=list)
    linked: list[str] = field(default_factory=list)
    already_linked: list[str] = field(default_factory=list)
    # What the app user can reach once the edges exist.
    principals: int = 0
    granted_nodes: int = 0


_UPSERT_APP_USER = """
INSERT INTO identity (id, display_name, email, can_authenticate, is_active)
VALUES ($1, $2, $3, true, true)
ON CONFLICT (id) DO UPDATE SET
    display_name = EXCLUDED.display_name,
    email = EXCLUDED.email,
    -- An app user is the one identity that may authenticate; a mirrored one
    -- never can.
    can_authenticate = true,
    is_active = true
RETURNING (xmax = 0) AS created
"""

# Case-insensitive and trimmed: sources disagree about both, and
# `Alice@Acme.com` is the same mailbox as `alice@acme.com`.
#
# Inactive identities are skipped — a deactivated account's grants should not
# come back through a new link — and app users are skipped so this can never
# chain one person's account onto another's.
_MATCHES = """
SELECT id, display_name
FROM identity
WHERE lower(btrim(email)) = lower(btrim($1))
  AND id <> $2
  AND id NOT LIKE $3
  AND is_active
ORDER BY id
"""

_LINK = """
INSERT INTO membership (child_identity_id, parent_identity_id)
VALUES ($1, $2)
ON CONFLICT DO NOTHING
RETURNING child_identity_id
"""

# The same expansion `query.visibility` performs, run here purely to report what
# the link bought. Kept as its own copy rather than imported: this is a check on
# that module's behaviour, and a check that shares the code it verifies checks
# nothing.
_REACH = """
WITH RECURSIVE principals AS (
    SELECT seed.id
    FROM identity seed
    WHERE seed.id = $1 AND seed.is_active
  UNION
    SELECT parent.id
    FROM membership m
    JOIN principals child ON child.id = m.child_identity_id
    JOIN identity parent
      ON parent.id = m.parent_identity_id AND parent.is_active
)
SELECT
    (SELECT count(*) FROM principals)::int AS principals,
    (SELECT count(DISTINCT a.node_id)
       FROM access a
       JOIN principals p ON p.id = a.identity_id)::int AS granted
"""


async def link_app_user(
    conn: asyncpg.Connection,
    *,
    email: str,
    display_name: str | None = None,
    user_id: str | None = None,
    dry_run: bool = False,
) -> LinkReport:
    """Create (or refresh) an app user and link it to identities sharing its email.

    Idempotent: re-running adds only edges that are missing, which is also how a
    mirrored identity ingested after the first run gets picked up.
    """
    email = email.strip()
    identity_id = user_id or app_user_id(email)

    report = LinkReport(app_user=identity_id, email=email, display_name=display_name)

    async with conn.transaction():
        if not dry_run:
            report.created = await conn.fetchval(
                _UPSERT_APP_USER, identity_id, display_name, email
            )

        rows = await conn.fetch(_MATCHES, email, identity_id, f"{APP_PREFIX}%")
        report.matched = [(r["id"], r["display_name"]) for r in rows]

        for mirrored_id, _ in report.matched:
            if dry_run:
                report.linked.append(mirrored_id)
                continue
            # RETURNING is empty when the row already existed, which is how new
            # edges are told from ones that were already there.
            written = await conn.fetchval(_LINK, identity_id, mirrored_id)
            (report.linked if written else report.already_linked).append(mirrored_id)

        if dry_run:
            # Nothing was written, so a reach count would describe a graph that
            # does not exist. Roll back and leave the counters at zero.
            raise _Rollback(report)

        reach = await conn.fetchrow(_REACH, identity_id)
        report.principals = reach["principals"]
        report.granted_nodes = reach["granted"]

    return report


class _Rollback(Exception):
    """Aborts the transaction on a dry run, carrying the report out with it."""

    def __init__(self, report: LinkReport) -> None:
        super().__init__("dry run")
        self.report = report


async def preview_app_user(
    conn: asyncpg.Connection,
    *,
    email: str,
    display_name: str | None = None,
    user_id: str | None = None,
) -> LinkReport:
    """`link_app_user` with nothing committed."""
    try:
        return await link_app_user(
            conn,
            email=email,
            display_name=display_name,
            user_id=user_id,
            dry_run=True,
        )
    except _Rollback as rolled_back:
        return rolled_back.report


async def describe_app_user(conn: asyncpg.Connection, identity_id: str) -> LinkReport:
    """What an existing app user is linked to, and what that reaches."""
    row = await conn.fetchrow(
        "SELECT id, display_name, email FROM identity WHERE id = $1", identity_id
    )
    if row is None:
        raise LookupError(identity_id)

    report = LinkReport(
        app_user=row["id"], email=row["email"] or "", display_name=row["display_name"]
    )
    linked = await conn.fetch(
        """
        SELECT m.parent_identity_id AS id, i.display_name
        FROM membership m
        LEFT JOIN identity i ON i.id = m.parent_identity_id
        WHERE m.child_identity_id = $1
        ORDER BY 1
        """,
        identity_id,
    )
    report.matched = [(r["id"], r["display_name"]) for r in linked]
    report.already_linked = [r["id"] for r in linked]

    reach = await conn.fetchrow(_REACH, identity_id)
    report.principals = reach["principals"]
    report.granted_nodes = reach["granted"]
    return report
