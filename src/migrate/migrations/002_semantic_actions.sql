-- The semantic layer, and the actions that can be taken on a node.
--
-- Two additions that share one property: neither is produced by a connector.
-- The semantic layer is what an agent infers *from* ingested source nodes; the
-- action layer is what an agent may do *to* the sources those nodes mirror.
--
-- Everything the semantic layer needs is here except the type views and their
-- partial indexes, which are compiled from `core.registry` on every boot —
-- `person`, `deal`, and `fact` are declared data, not DDL, and a config change
-- rebuilds them without a migration.
--
--
-- THE SHAPE, because the schema alone does not show it
--
--   person:jane                 identity only. No permission parent and no
--        ^                      grants: an entity is visible exactly when a
--        | about                fact about it is, which `query.visibility`
--        |                      derives at read time.
--   fact:… "moved off Atlas"    permission_parent = the private message.
--        |
--        | permission_parent
--        v
--   slack:message               ordinary mirrored node, ordinary ACL.
--
-- A fact inherits access along `permission_parent_id` like any mirrored node,
-- so the visibility kernel decides it with no new machinery and nothing
-- materialised. Two people asking the same entity the same question get
-- different facts back, and neither can tell the other's exist.
--
-- Deriving entity visibility rather than granting it is what keeps revocation
-- honest: nothing is copied down, so nothing is left behind when a channel
-- grant upstream is removed.


-- -------------------------------------------------------------- ontology --

-- The user-declared vocabulary: which entity types exist, what they represent,
-- and what identifies one. A table rather than a repo file so the descriptions
-- and prompts an extractor reads can be revised without a deploy, and so every
-- revision is kept.
--
-- Versioned by append. Nothing updates a row: a change inserts a new version
-- and retires the old one, so an entity written under version 3 stays
-- explicable after version 4 lands.
CREATE TABLE IF NOT EXISTS semantic_config (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    version    int  NOT NULL UNIQUE,
    -- 'active' | 'retired'. Exactly one active row; the partial unique index
    -- below enforces that rather than convention.
    status     text NOT NULL DEFAULT 'active',
    config     jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS semantic_config_one_active
    ON semantic_config ((status)) WHERE status = 'active';


-- ------------------------------------------------------------ provenance --

-- The document an extractor read to justify an inferred edge. NULL means a
-- structural edge minted by a connector, which is every edge the ingest path
-- writes.
--
-- It carries two jobs. It is the audit trail behind an inferred claim, and it
-- is the only thing that can decide access on an edge whose *both* endpoints
-- are entities: an entity is visible as soon as any document mentions it, so a
-- relation drawn from a private message would otherwise show to anyone who
-- could see both endpoints from public ones. `SessionGraph.neighbors` admits
-- such an edge only when this document is visible too.
--
-- It is also what retraction deletes on. Facts are owned through
-- `permission_parent_id` and drawn edges through this column; clearing only the
-- first is what once let a stale `works_on` outlive the sentence that produced
-- it.
--
-- Not part of the primary key: one (from, to, relation) is one row and the
-- first justification recorded wins, which keeps the edge PK and every index
-- the query layer plans against unchanged. The cost is that a second document
-- asserting the same relation does not get its own row, so retracting the first
-- source retracts the edge even though other evidence existed. Acceptable while
-- edges are cheap to re-derive on the next pass; it is the thing to revisit if
-- edge provenance ever has to be exhaustive.
ALTER TABLE edge ADD COLUMN IF NOT EXISTS source_node_id uuid
    REFERENCES node(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS edge_source_idx ON edge (source_node_id)
    WHERE source_node_id IS NOT NULL;


-- The extraction watermark, mirroring `content_version`'s role for ingest.
-- Compared lexicographically against it: below means this node's current body
-- has not been through extraction yet.
--
-- Three jobs at once. It makes redelivery idempotent, it lets the sweeper find
-- work lost to a crash between commit and enqueue, and setting it back to ''
-- is the whole of a backfill.
ALTER TABLE node ADD COLUMN IF NOT EXISTS semantic_version text NOT NULL DEFAULT '';

-- Partial: the sweeper asks only for rows that are behind, which is a vanishing
-- fraction of the table in steady state.
CREATE INDEX IF NOT EXISTS node_semantic_pending_idx ON node (updated_at)
    WHERE semantic_version < content_version AND node_type IS NOT NULL;


-- Facts sourced from one document, for reconciliation. When an ingested record
-- changes, everything it previously implied has to be found and re-derived; the
-- parent pointer is that handle. `node_parent_idx` from 001 would serve, but a
-- thread-parent message has every reply hanging off it too, and this asks the
-- narrower question against a much smaller set.
--
-- The other direction — entity to its facts — is `semantic_fact_subject`,
-- declared on the fact spec in `semantic.config` because it belongs next to the
-- type. That one is load-bearing for *access*, not just for search: it is the
-- join behind derived entity visibility.
CREATE INDEX IF NOT EXISTS node_fact_parent_idx
    ON node (permission_parent_id)
    WHERE node_type = 'fact' AND deleted_at IS NULL;


-- --------------------------------------------------------------- actions --

-- What can be done to a node, as opposed to what can be read from it. Scoped by
-- node type, because the verb is a property of the kind of thing: a Slack
-- channel can be posted to, a Drive file can be rewritten, and neither
-- statement makes sense about the other.
--
-- Reflected from `core.actions` on every boot, the same way node-type views are
-- compiled from the registry. The table exists so the catalog is queryable and
-- so invocations can reference it; the specs remain the source of truth.
CREATE TABLE IF NOT EXISTS action (
    name        text PRIMARY KEY,
    node_type   text NOT NULL,
    summary     text NOT NULL,
    -- JSON Schema for the action's arguments, as the API and the model see it.
    params      jsonb NOT NULL DEFAULT '{}',
    -- False retires an action without dropping the invocations that reference
    -- it. Set by the boot-time reflection when a spec disappears from code.
    is_enabled  boolean NOT NULL DEFAULT true
);

CREATE INDEX IF NOT EXISTS action_node_type_idx ON action (node_type)
    WHERE is_enabled;


-- Every attempt, not every success. An action leaves the system and changes
-- something a person will see, so the row is written before the call goes out
-- and updated after — a row stuck in 'running' is a crash mid-flight, which is
-- exactly the state worth being able to find.
CREATE TABLE IF NOT EXISTS action_invocation (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    action_name  text NOT NULL REFERENCES action(name),

    -- The graph node acted on, and the principal that asked. Both recorded as
    -- ids rather than names: this is an audit trail, and it has to survive a
    -- rename in the source.
    node_id      uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    identity_id  text NOT NULL REFERENCES identity(id),

    params       jsonb NOT NULL DEFAULT '{}',

    -- 'running' | 'ok' | 'error'. Not an enum type: statuses are read by humans
    -- reading the log, and a new one should not need a migration.
    status       text NOT NULL DEFAULT 'running',
    -- The source's own id for whatever was created — a Slack ts, a Notion page
    -- id. What makes the write findable again from outside this system.
    result_ref   text,
    error        text,

    created_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz
);

CREATE INDEX IF NOT EXISTS action_invocation_node_idx
    ON action_invocation (node_id, created_at DESC);
CREATE INDEX IF NOT EXISTS action_invocation_identity_idx
    ON action_invocation (identity_id, created_at DESC);
