"""Applies the .sql files in migrations/, in filename order, exactly once each.

Deliberately not a migration framework. There is no down-migration and no
autogeneration: `--reset` recreates the schema from scratch and the pollers
re-emit everything on their next cycle, so the escape hatch for a bad migration
is to fix the file and reset, not to unwind it.

Each file runs inside its own transaction together with the ledger insert, so a
half-applied migration cannot be recorded as done.

Node-type views and partial indexes are not files. They are compiled from the
registry and reapplied on every boot — for semantic types as well as source
ones, which is why the ontology is loaded here before the views are built. A
`person` view is DDL like any other, and this is the only process that writes
DDL.
"""

from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

from core.registry import all_specs
from semantic.registry import load as load_ontology

log = logging.getLogger("migrate.runner")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

# Created here rather than in 001, because the runner has to read it before any
# migration has run.
LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename   text PRIMARY KEY,
    applied_at timestamptz NOT NULL DEFAULT now()
)
"""

def discover() -> list[Path]:
    """Sorted by filename, so the numeric prefix is the ordering."""
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


async def migrate(conn: asyncpg.Connection) -> int:
    """Apply anything not yet in the ledger. Safe to run on every boot."""
    await conn.execute(LEDGER_DDL)
    applied = {
        r["filename"]
        for r in await conn.fetch("SELECT filename FROM schema_migrations")
    }

    count = 0
    for path in discover():
        if path.name in applied:
            continue
        log.info("applying %s", path.name)
        async with conn.transaction():
            # asyncpg sends a parameterless execute() over the simple query
            # protocol, so a file may contain many statements.
            await conn.execute(path.read_text(encoding="utf-8"))
            await conn.execute(
                "INSERT INTO schema_migrations (filename) VALUES ($1)", path.name
            )
        count += 1

    if count:
        log.info("applied %d migration(s)", count)
    else:
        log.info("schema up to date (%d already applied)", len(applied))

    # After the files, before the views: `semantic_config` has to exist for the
    # ontology to load, and the ontology has to be registered for its types to
    # get views.
    await load_ontology(conn)
    await apply_node_types(conn)
    await apply_actions(conn)
    return count


async def apply_node_types(conn: asyncpg.Connection) -> None:
    """CREATE VIEW / CREATE INDEX IF NOT EXISTS for every queryable node type.

    Views are dropped and rebuilt so a payload-field change cannot leave a
    stale column list. Indexes are named and idempotent; a column-list change
    needs a new name on the spec.

    Semantic types are included. A type retired from the ontology keeps its view
    until the next reset — dropping views for types no longer declared would
    make a config typo silently destroy the ability to query real rows, and the
    rows themselves outlive the declaration either way.
    """
    specs = all_specs()
    async with conn.transaction():
        for spec in specs.values():
            for name in spec.drop_indexes:
                await conn.execute(f"DROP INDEX IF EXISTS {name}")
            await conn.execute(f"DROP VIEW IF EXISTS {spec.view_name}")
            await conn.execute(spec.get_view())
            for stmt in spec.get_indexes():
                await conn.execute(stmt)
    log.info("applied %d node-type view(s)", len(specs))


async def apply_actions(conn: asyncpg.Connection) -> None:
    """Reflect the action catalog from `core.actions` into the `action` table.

    Same idea as the views: the specs in code are the source of truth and the
    table is a queryable projection of them. Actions absent from code are
    disabled rather than deleted, because `action_invocation` references them
    and an audit trail must not lose its subject.
    """
    from core.actions import ACTIONS

    async with conn.transaction():
        for spec in ACTIONS.values():
            await conn.execute(
                """
                INSERT INTO action (name, node_type, summary, params, is_enabled,
                                    requires_level, destructive, returns)
                VALUES ($1, $2, $3, $4::jsonb, true, $5, $6, $7::jsonb)
                ON CONFLICT (name) DO UPDATE SET
                    node_type = EXCLUDED.node_type,
                    summary = EXCLUDED.summary,
                    params = EXCLUDED.params,
                    is_enabled = true,
                    requires_level = EXCLUDED.requires_level,
                    destructive = EXCLUDED.destructive,
                    returns = EXCLUDED.returns
                """,
                spec.name,
                str(spec.node_type),
                spec.summary,
                spec.json_schema(),
                # The FK to access_level is doing real work here: a spec naming
                # a level the catalog does not define fails this boot rather
                # than installing a requirement nothing could ever satisfy.
                spec.requires_level,
                spec.destructive,
                list(spec.returns),
            )
        await conn.execute(
            "UPDATE action SET is_enabled = false WHERE name <> ALL($1::text[])",
            list(ACTIONS),
        )
    log.info("applied %d action(s)", len(ACTIONS))


async def drop_schema(conn: asyncpg.Connection) -> None:
    """DESTRUCTIVE. Drops everything, including the migration ledger.

    Extensions live in `public` and go with it; migrate recreates them.
    """
    log.warning("dropping schema public")
    await conn.execute("DROP SCHEMA public CASCADE")
    await conn.execute("CREATE SCHEMA public")
