CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;


-- ------------------------------------------------------------------ nodes --

CREATE TABLE IF NOT EXISTS node (
    id         uuid PRIMARY KEY DEFAULT gen_random_uuid(),

    -- '{source}:{native_id}', e.g. 'slack:C0123:1699999999.000100'. Namespaced
    -- so it is globally unique without a compound key, and so the source stays
    -- recoverable without a column duplicating it.
    entity_id  text NOT NULL UNIQUE,

    -- '{source}:{thing}', e.g. 'slack:message'. NULL marks an unmaterialized
    -- row: one minted purely so a uuid could be referenced before the real
    -- event arrived. See Store.ensure_node_id.
    node_type  text,

    -- The node whose grants govern this one. Access inherits along it and
    -- nothing else, so it is named for what it controls. A NULL parent on a
    -- non-root node means no grants are reachable, which fails closed.
    permission_parent_id uuid REFERENCES node(id),

    body text NOT NULL DEFAULT '',

    -- Source semantics, both of them: when the thing was created and last
    -- edited in the source, never when we saw it. NULL while unmaterialized.
    created_at timestamptz,
    updated_at timestamptz,

    -- The one timestamp that cannot be source semantics: Drive reports
    -- `trashed` as a boolean and Slack sends a delete event, neither carrying a
    -- time. A tombstone: inbound edges stay (a mention still points here),
    -- outgoing membership is retracted so a folder or channel does not keep
    -- listing this node as a neighbor.
    deleted_at timestamptz,

    -- The guarded upsert's ordering key. Compared lexicographically, which the
    -- connectors already accommodate by zero-padding or using ISO-8601.
    -- Defaults to '' so any real version sorts above an unmaterialized row and
    -- the first genuine event always wins.
    content_version text NOT NULL DEFAULT '',

    payload jsonb NOT NULL DEFAULT '{}',

    -- Two-argument to_tsvector with a literal config: the one-argument form
    -- depends on a session GUC and is not IMMUTABLE, so it cannot be generated.
    fts tsvector GENERATED ALWAYS AS
        (to_tsvector('english', coalesce(body, ''))) STORED
);

-- The visibility walk descends this every session; it is the hottest index here.
CREATE INDEX IF NOT EXISTS node_parent_idx  ON node (permission_parent_id);
CREATE INDEX IF NOT EXISTS node_type_idx    ON node (node_type);
CREATE INDEX IF NOT EXISTS node_updated_idx ON node (updated_at DESC);
CREATE INDEX IF NOT EXISTS node_fts_idx     ON node USING gin (fts);
-- Trigram, because a leading-wildcard ILIKE cannot use a btree and the agent
-- query language exposes substring filters.
CREATE INDEX IF NOT EXISTS node_body_trgm   ON node USING gin (body gin_trgm_ops);
CREATE INDEX IF NOT EXISTS node_payload_idx ON node USING gin (payload jsonb_path_ops);


-- ------------------------------------------------------------------ edges --

-- Edges have nothing to do with permissions. There is no propagation flag, and
-- `relation` is a natural-language label of the edge kind ("mentions", a Notion
-- property name), not a closed vocabulary that confers access. Access is
-- decided entirely by permission_parent_id and the grant tables. An edge is
-- traversable exactly when its target node is visible, which the visibility
-- walk already answers without reading this table at all.
CREATE TABLE IF NOT EXISTS edge (
    from_node_id uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    to_node_id   uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    relation     text NOT NULL,
    created_at   timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (from_node_id, to_node_id, relation)
);

CREATE INDEX IF NOT EXISTS edge_to_idx ON edge (to_node_id, relation);

-- Tombstones keep inbound edges (a mention, a remaining reply's in_thread).
-- They must not keep outgoing membership: a channel or folder should not still
-- list this node as a neighbor. Store._tombstone drops these per entity;
-- this catches any already-tombstoned rows (a no-op on a fresh schema).
DELETE FROM edge e
USING node f
WHERE e.from_node_id = f.id
  AND f.deleted_at IS NOT NULL;


-- ------------------------------------------------------------- identities --

CREATE TABLE IF NOT EXISTS identity (
    -- Namespaced: 'slack:user:U123', 'drive:group:eng@acme.com',
    -- 'slack:workspace:T1', 'public', 'drive:unresolved:<permission_id>'.
    -- The unresolved bucket has no members by
    -- construction, so a grant to an unnameable principal grants nothing.
    id   text PRIMARY KEY,
    display_name text,
    -- Correspondence hint: the one key Slack, Notion, and Drive share. Used to
    -- suggest which app user matches this identity, never to create the link
    -- (that is OAuth-proven membership). Not an auth decision.
    email        text,

    -- Mirrored identities cannot log in. An app user reaches these grants via an
    -- OAuth-proven membership edge, never via email match, which is a profile
    -- field the user controls.
    can_authenticate boolean NOT NULL DEFAULT false,
    is_active        boolean NOT NULL DEFAULT true
);

-- child is a member of parent; grants flow from parent down to child.
CREATE TABLE IF NOT EXISTS membership (
    child_identity_id  text NOT NULL,
    parent_identity_id text NOT NULL,
    PRIMARY KEY (child_identity_id, parent_identity_id)
);

-- The principal walk climbs child -> parent, so the reverse direction needs its
-- own index.
CREATE INDEX IF NOT EXISTS membership_parent_idx ON membership (parent_identity_id);


-- ----------------------------------------------------------------- access --

-- Closed vocabulary of source roles. A grant's `level` is one of these names.
-- Priorities are comparable only within a source: a permission chain never
-- crosses sources, so Drive 60 vs Slack 10 is never a comparison.
CREATE TABLE IF NOT EXISTS access_level (
    name     text PRIMARY KEY,
    priority int  NOT NULL
);

INSERT INTO access_level (name, priority) VALUES
    ('drive:reader', 10),
    ('drive:commenter', 20),
    ('drive:writer', 30),
    ('drive:fileOrganizer', 40),
    ('drive:organizer', 50),
    ('drive:owner', 60),
    ('slack:member', 10),
    ('notion:integration_visible', 10),
    ('notion:public', 10)
ON CONFLICT (name) DO NOTHING;

-- No authorship column: every grant here is mirrored from a source ACL and a
-- node belongs to exactly one source, so wipe-and-rewrite per node_id is
-- unambiguous. That changes only if the app ever mints its own grants.
CREATE TABLE IF NOT EXISTS access (
    identity_id text NOT NULL REFERENCES identity(id),
    node_id     uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,

    -- The source's own role, namespaced: 'drive:commenter', 'slack:member'.
    -- Not collapsed to READ/WRITE/ADMIN, because 'commenter' and 'reader' both
    -- read and only one can annotate — a distinction unrecoverable once
    -- discarded. Catalog and priorities live in access_level; comparable
    -- only within a source, which is all that is ever needed: a permission
    -- chain never crosses sources.
    level text NOT NULL REFERENCES access_level(name),

    PRIMARY KEY (identity_id, node_id)
);

CREATE INDEX IF NOT EXISTS access_identity_idx ON access (identity_id);
CREATE INDEX IF NOT EXISTS access_node_idx     ON access (node_id);


-- ----------------------------------------------------------------- search --

-- One embedding per node would be an average of everything in it, matching
-- nothing strongly. Chunking preserves local meaning; a short Slack message
-- simply produces one chunk.
CREATE TABLE IF NOT EXISTS node_chunk (
    node_id   uuid NOT NULL REFERENCES node(id) ON DELETE CASCADE,
    ord       int  NOT NULL,
    text      text NOT NULL,
    embedding vector(384),          -- BAAI/bge-small-en-v1.5
    PRIMARY KEY (node_id, ord)
);

CREATE INDEX IF NOT EXISTS node_chunk_vec
    ON node_chunk USING hnsw (embedding vector_cosine_ops);
