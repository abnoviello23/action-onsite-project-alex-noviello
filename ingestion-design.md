# Permissioned Knowledge Graph — Ingestion & Authorization Design

Multi-source ingestion (Slack, Notion, Google Drive) into a temporal knowledge
graph with a permission layer. Local demo, Docker Compose.

---

## 1. Source constraints

The three APIs are not symmetric. Design around the differences, not around an
imagined common denominator.

### Slack

The rate limit tier depends on **commercial distribution**, not on where your
code runs. A "Slack app" is a registration record that yields a token; your code
runs wherever you like.

| App type | `conversations.history` / `.replies` |
|---|---|
| Internal custom (installed only in the workspace that created it) | ~50 req/min, `limit` up to 1000 |
| Distributed outside Marketplace | 1 req/min, 15 objects max |
| Marketplace-approved | unaffected |

Effective May 29 2025 for new apps; September 2 2025 for existing installs.

**For this project:** create the app, never touch Manage Distribution → internal
tier. Note the cliff if this ever ships to other workspaces.

Scopes for a read pipeline: `channels:history`, `channels:read`, `users:read`,
`users:read.email`. Add `groups:*` for private channels. Seeder needs
`chat:write` — **use a separate token from the ingestor**.

Bots only read channels they've joined. A user token (`xoxp-`) sees all public
channels without joining; simpler for a solo demo workspace.

### Notion

Pin `Notion-Version` explicitly; treat bumps as migrations.

- `2025-09-03` — `/v1/databases` split into database (container) and
  `/v1/data_sources` (schema + rows). Query endpoints take `data_source_id`,
  discovered from the database. The ID in the URL is not the ID you query.
- `2026-02-01` — default pagination dropped 100 → 50, **silently**. Always loop
  on `has_more` / `next_cursor`.
- `2026-03-11` — `archived` → `in_trash`; `after` → `position` on Append Block
  Children; `transcription` → `meeting_notes`.

Cursors are opaque: pass back, never parse or persist long-term. Only record IDs
are stable UUIDs. File URLs expire ~hourly — store the block reference, resolve
the URL at use time.

### Google Drive

Quota model changed May 1 2026. Projects created on/after that date use quota
units: 1,000,000/min/project, 325,000/min/user/project, 400,000,000/day billing
threshold. Costs: read 5, list 100, edit 50, **download 200**. Projects that used
the API Nov 2025–Apr 2026 keep old quotas.

`changes.list` with a `pageToken` is a genuine CDC feed — the cleanest of the
three.

---

## 2. Ingestion topology

One work queue, multiple producers. Streaming is a producer, not an
architecture.

```
poller-slack   ─┐
poller-notion  ─┼─→  work queue  ─→  workers  ─→  raw store + graph
poller-drive   ─┘
seeder (one-shot)
```

Every producer emits the same item shape:

```json
{ "source": "notion", "entity_id": "abc-123", "reason": "poll|backfill|acl" }
```

Work items are **pointers, not payloads** — the worker fetches. And they are
**entity-grained**: "re-fetch page X", never "process block 47". Block-level
items would scatter one page's content across partitions.

### Polling over push

No public HTTPS locally, so:

| Source | Approach |
|---|---|
| Slack | Socket Mode — outbound WebSocket, no tunnel needed |
| Notion | Poll `data_sources.query` sorted by `last_edited_time` desc vs watermark |
| Drive | Poll `changes.list` against stored `pageToken` |

~2 list calls per cycle per source. Invisible against quota. Exercises the
identical downstream path as push would.

Push requires public HTTPS; Drive additionally requires Search Console domain
verification. `cloudflared` sidecar if you ever want it.

### Watermark ordering

Advance the watermark **after** a successful enqueue, never before. A crash then
re-emits events rather than losing them; the version guard makes duplicates
harmless.

### Seeding gotchas

- `chat.postMessage` ≈ 1 msg/sec per channel. One-shot init container.
- **Messages cannot be backdated.** Store your own `occurred_at`; treat Slack's
  `ts` as an ingestion artifact. Decide before seeding.
- Free-tier workspaces hide messages older than 90 days.

---

## 3. Storage: three layers, three jobs

The critical split, given the replay requirement.

| Layer | Store | Retention | Purpose |
|---|---|---|---|
| Work queue | Redis Streams | `MAXLEN ~ 100000` | transient dispatch |
| Raw payload log | Postgres | forever | replay source of truth |
| Graph | Postgres | current + versions | serving |

Redis Streams are memory-resident and cannot be the forever log. Every fetched
payload goes to an append-only Postgres table:

```sql
CREATE TABLE raw_payloads (
  id          bigserial PRIMARY KEY,
  source      text NOT NULL,
  entity_id   text NOT NULL,
  fetched_at  timestamptz NOT NULL DEFAULT now(),
  payload     jsonb NOT NULL
);
CREATE INDEX ON raw_payloads (source, entity_id, fetched_at);
```

**Replay** = truncate graph tables, read `raw_payloads` in `id` order, re-run the
normalizer. No API calls. This is what lets you change chunking, normalization,
or graph shape without re-ingesting — which matters enormously given Slack's
rate limits.

Never replay the *work* stream to rebuild: work items are pointers, so replaying
them re-hits the APIs and yields current state, not historical.

---

## 4. Consumer mechanics

### Startup

```
XGROUP CREATE stream:work:{p} cg $ MKSTREAM
XAUTOCLAIM stream:work:{p} cg {me} 60000 0 COUNT 100
```

- `MKSTREAM` creates the stream if absent (otherwise `XGROUP CREATE` errors on a
  missing key).
- `$` = start at the current end; group ignores pre-existing entries. Use `0` to
  consume history from the beginning.
- Errors `BUSYGROUP` if the group exists — catch and ignore; that's how you make
  it idempotent across restarts.
- `XAUTOCLAIM` reclaims entries pending longer than 60000ms, scanning from ID
  `0`. Reassigns ownership to `{me}`.

**Consumer name must be stable across restarts** (`w0`, not a UUID) — otherwise
a restarted container abandons its own pending entries under a name nothing will
ever claim.

### Loop

```
msgs = XREADGROUP GROUP cg {me} COUNT 10 BLOCK 5000 STREAMS stream:work:{p} >
```

- `>` = only entries never delivered to *any* consumer in this group. An explicit
  ID instead returns that consumer's own pending entries.
- `BLOCK 5000` = wait up to 5s for new entries; returns empty on timeout. Loop
  again. Avoids busy-polling.
- Delivery moves the entry into the group's **Pending Entries List** (PEL). It
  stays there until `XACK`.

Per message:

1. **Poison-pill check.** `delivery_count` (from `XAUTOCLAIM`/`XPENDING`) > 5 →
   push to `stream:dlq`, `XACK`, skip. Without this a permanently-failing message
   is reclaimed forever.
2. **Rate-limit token**, shared across all workers, keyed per source. K workers
   otherwise means K× the request rate. Acquire *before* the fetch.
3. **Fetch** from the source API.
4. **Persist raw**, then **normalize and upsert** version-guarded.
5. **`XACK`** — removes from PEL. Only after successful commit. Crash before ACK
   → another consumer reclaims it → at-least-once, which the version guard
   absorbs.

### Ordering

**Partition by entity id.** `p = hash(source + entity_id) % K`, one consumer per
partition stream. Per-entity total order; K-way parallelism across entities.

Compose can't assign ordinals — `--scale` gives identical containers. Declare
services explicitly with a YAML anchor:

```yaml
worker-0: { <<: *worker, environment: [PARTITION=0, CONSUMER_NAME=w0] }
worker-1: { <<: *worker, environment: [PARTITION=1, CONSUMER_NAME=w1] }
```

**Then assume ordering fails anyway.** Retries, reclaims, and partition count
changes all break it. The version guard is the correctness argument;
partitioning is the optimization.

```sql
INSERT INTO nodes (source, entity_id, body, content_version, ...)
VALUES (...)
ON CONFLICT (source, entity_id) DO UPDATE
  SET body = EXCLUDED.body, content_version = EXCLUDED.content_version, ...
  WHERE EXCLUDED.content_version > nodes.content_version;
```

This single clause makes duplicate delivery, out-of-order application, and
poller re-emission all safe simultaneously.

---

## 5. Authorization model

Five tables. Grants are stored, effective access is derived — never materialize
person→resource.

```
identity(id, type, can_authenticate)
membership(child_identity_id, parent_identity_id)
access_type(id, name, priority)          -- READ 10, WRITE 20, ADMIN 30
access(access_type_id, identity_id, resource_id)
nodes(...)                                -- resource == graph node
```

Indexes: both directions on `membership` and `access`.

### Resolution

Two independent traversals, unioned:

1. **Identity** — walk `membership` upward from the requester, collecting all
   ancestor identities.
2. **Containment** — walk `nodes.parent_id` upward from each candidate node.

Access exists if any ancestor node has a grant to any ancestor identity.
Effective level is `max(priority)` — **purely additive, no DENY**. Drive confirms
this: inherited permissions cannot be removed or reduced on an item, only
increased; restriction happens at the parent. Slack and Notion have no
reduce-on-child either.

Nothing is exploded onto descendants. Revoking a folder grant is one row delete.

### Query direction

Retrieval is "of these 200 vector hits, which can Alice see" — walk **up** from
candidates (bounded by tree depth), not down from grants (unbounded subtree).

```sql
WITH RECURSIVE ancestors AS (
    SELECT id, id AS root FROM nodes WHERE id = ANY(:candidate_ids)
    UNION
    SELECT n.parent_id, a.root FROM nodes n JOIN ancestors a ON n.id = a.id
    WHERE n.parent_id IS NOT NULL
)
SELECT DISTINCT a.root
FROM ancestors a
JOIN access ac ON ac.resource_id = a.id
WHERE ac.identity_id = ANY(:principal_ids);
```

Principal set is computed once per session and cached.

Post-filtering breaks top-k: if Alice can see 3 of 200 candidates you silently
return 3. Over-fetch, or push the principal set into the vector index as a
metadata pre-filter.

### Entity ids, not version ids

`access.resource_id` and `nodes.parent_id` must reference the **stable entity
id**. Otherwise every content edit mints a version with no access rows and you're
back to per-version grants. Auth and containment operate at the entity layer; the
version chain hangs off the entity.

---

## 6. Mapping source permissions

Non-enumerable principals become synthetic identities. This is what makes group
changes cheap — one `membership` row, not N file updates.

| Source reality | Identity |
|---|---|
| Slack public channel | `workspace:T0123` (all Slack users are members) |
| Slack private channel | explicit `user:U…` members |
| Drive `type: user` | `user:<email>` |
| Drive `type: group` | `group:<email>`, members not expanded |
| Drive `type: domain` | `domain:acme.com` |
| Drive `type: anyone` / Notion public | `public` |
| Notion, unresolvable | `unresolved:<node_id>` |

`unresolved` has **no members** → fail-closed by construction. Can't be forgotten
the way a nullable column or policy flag can.

The same construction covers Google Groups, deliberately. Drive names the group
on the grant but exposes no way to read its members — that needs the Admin SDK,
which needs domain-wide delegation and a Workspace super-admin. So a group is
mirrored as a real identity holding a real grant, with no membership rows
pointing at it, and nothing resolves through it. Not pending work: expansion is
declined until someone has both the admin access and a reason. Adding it later
is additive — `membership` rows, no schema or traversal change.

### Per-source notes

- **Slack:** no per-message ACL; a message's ACL is its channel's. Public channel
  membership is a social fact, not a permission — everyone in the workspace can
  read. Token scope (`channels:` vs `groups:` vs `im:`) gates entire conversation
  classes independently of ACLs.
- **Drive:** roles `owner|organizer|fileOrganizer|writer|commenter|reader`.
  Collapse to READ for retrieval but keep the original — comments inherit a
  *narrower* ACL than the file (plain readers may not see them).
- **Notion:** no permission-read endpoint exists. The integration's own
  visibility is the entire ACL surface. Listing users needs Enterprise + org key.

### Permission changes are events

Same stream, same consumers, `reason: "acl"`.

| Source | Signal |
|---|---|
| Slack | `member_joined_channel`, `member_left_channel`, `channel_archive`, `user_change` (`deleted: true` → revoke) |
| Drive | `changes.list`. Treat descendant propagation as unreliable — on a **folder** change, enqueue a subtree walk |
| Notion | nothing. Periodically re-enumerate; absence = revocation |

Revocation usually arrives as absence, not an event — you infer it by diffing.
Wipe-and-rewrite per `(source, resource)` in a transaction is sufficient. Add a
`source` column only once app-native grants exist alongside mirrored ones.

### Identity linkage

`app_user MEMBER_OF slack_user`. App user inherits Slack grants; Slack identity
never inherits app grants. Falls out of the existing traversal, no special case.

- **The link must be OAuth-proven.** Take the `user_id` from the token exchange.
  Email matching is a privilege-escalation hole via a profile text field — fine
  as a hint for *suggesting* a link, never sufficient to create one.
- Projected identities: `can_authenticate = false`.
- Slack deactivation (`deleted: true`) must revoke the edge.

---

## 7. Temporal semantics

Content is versioned. **Authorization is always evaluated at current state.** "As
of March" means current permissions applied to March's content — otherwise
someone removed from a project in June could time-travel to read it.

**Containment is versioned too, and this needs a deliberate choice.** A document
moved from a private to a public folder in July: evaluating against current
containment exposes its March content. Recommended — the move is the
organization's statement of current intent, and it matches Drive, where moving a
file exposes its revision history too. But a move is then effectively a bulk
permission change; surface that in the UI.

Lost capability: "who could see this in March?" needs temporal `access` rows.
Defer until asked.

---

## 8. Compose services

```
migrate       one-shot; everything depends_on service_completed_successfully
postgres      healthcheck; graph + raw_payloads + watermarks
redis         healthcheck; streams + cache + rate-limit buckets
seeder        one-shot; channels, messages, Notion pages, Drive files
poller-slack  Socket Mode (streaming) — separate token from seeder
poller-notion watermark poll
poller-drive  changes.list poll
worker-0..K   one per partition, stable consumer names
api-0..K      behind Caddy/nginx
```

Notes:

- `depends_on` alone only waits for container start. Use
  `condition: service_healthy` with real healthchecks.
- Compose DNS round-robin is not load balancing — put a proxy in front of the API
  replicas.
- Redis persistence is one setting for both queue and cache. `appendfsync
  everysec` loses up to 1s on hard crash. Acceptable: sources remain the truth
  and pollers re-emit.
- Sizing: K=3 partitions, 2 API replicas. Slack's partition idles regardless of
  worker count. Seed a few hundred Drive files if you want parallelism visible.

---

## 9. Canonical node schema

| Canonical | Slack | Notion | Drive |
|---|---|---|---|
| `container_id` | channel `C…` | `data_source_id` / parent page | folder / shared drive |
| `entity_id` | `C…:ts` | page id | file id |
| `parent_id` | `thread_ts` | `parent.page_id` | `parents[0]` |
| `title` | first line | `properties.Name` | `name` |
| `body` | resolved `text` | flattened block tree | exported / extracted text |
| `actor_id` | `user` `U…` | `created_by.id` | `lastModifyingUser` |
| `updated_at` | `edited.ts ?? ts` | `last_edited_time` | `modifiedTime` |
| `content_version` | `ts` | `last_edited_time` | `version` / `headRevisionId` |

**Identity join:** email, the only key all three share. Slack
`profile.email` (needs `users:read.email`), Notion `person.email`, Drive
`permissions[].emailAddress`. Bots have no email — keep an `unresolved_actor`
bucket rather than dropping rows.

**Semantic join:** extract URLs from every body into an edge table
`(from_node, to_node, relation)`. Match `app.notion.com/p/{id}` (moved from
`notion.so/{id}` in June 2026), `docs.google.com/*/d/{id}`,
`slack.com/archives/{channel}/p{ts}`. Store edges even when the target isn't
ingested yet; resolve later.

**`MENTIONS` must never propagate access** — only designated containment edges
do. One Slack message linking a doc must not grant access to it.

---

## 10. Demo suggestion

Seed a private channel and a Notion page not shared with the integration, then
demo that they are correctly *absent* for a user who shouldn't see them.
Permission-correct retrieval is the hard part of this problem, and showing it
work is more convincing than showing volume.
---

## 11. Semantic layer

Two graphs share one `node`/`edge` store, with a hard split in how they are
produced.

**Source layer (code).** Connectors emit envelopes; Python generators mint
`slack:message`, `drive:file`, and the *structural* edges between them. This is
the topology of the source systems, not meaning, and it stays deterministic.

**Semantic layer (config + one extractor).** Users declare the entity types they
care about in `semantic_config`. An extractor turns each ingested document into
entities, facts about them, and links between them.

### Entities and facts

The split is the whole design.

```
   slack:message ······mentions······> person:jane
        ^                                  ^   ^
        | permission_parent          about |   | about
        |                                  |   |
   fact-+                              fact-+   +-fact
                                     (private)   (public)
```

Solid is `permission_parent`, the only thing access flows along. Dotted is an
edge, which confers nothing and exists so the graph is walkable.

An **entity** holds only what says *which* thing it is, and carries **no access
of its own**. Its visibility is derived: you may know Jane exists exactly when
you may read something about Jane.

A **fact** holds everything one document says about one entity, and its
`permission_parent_id` is that document. So it inherits access along exactly the
path a Slack message does, decided by the same kernel walk, with nothing
materialised.

That is why content must never sit on an entity. The entity is a name the
workspace may know; the fact is a claim only some readers may read. Alice, in
the private channel, follows `about` inward from Jane and gets two notes. Bob
gets one. Neither can tell the other's exist.

**Deriving rather than granting is what keeps revocation honest.** An earlier
version copied each source's audience onto the entity as `access` rows, with a
`semantic_source` table and a recursive delete keeping the two in step. It
worked for deletion and silently failed for the case that matters more:
revoking a channel grant upstream never reached the copies. There are no copies
now. `semantic_source`, `semantic_run`, and the `semantic:derived` level are all
gone, and the entire access consequence of an extraction is the parent pointer
on each fact row.

The cost is one extra query. `Visibility.visible` runs the ancestor walk first,
which places every mirrored node and every fact; only candidates that come back
unplaced can be entities, and only then does the derived rule run. A request
that touches no entities pays exactly what it paid before.

### Documents link straight to entities

A fact's tie to its document is `permission_parent_id`, which is not an edge
row — so without something more, `neighbors(message)` returns `in_channel` and
friends and the person the message is about is unreachable by traversal. Every
extraction therefore also writes `document -mentions-> entity`, reusing the name
the Notion connector already uses for page links and the agent prompt already
documents as the backlink set. `follow(person, 'mentions', 'in')` is every
document that named them, filtered to the ones the caller may read.

### Inferred edges are filtered by their provenance

An edge between two *entities* is the one case the peer rule cannot cover. Both
ends are entities, and an entity is visible as soon as any document mentions it
— so a relation drawn from a private message would show to anyone who could see
both endpoints from public ones. `edge.source_node_id` names the document that
asserted it, and `SessionGraph.neighbors` admits such an edge only when that
document is visible too. Structural edges carry no source and are unaffected.

The same column is what `retract_from` deletes on: facts are owned through
`permission_parent_id` and drawn edges through `source_node_id`, and clearing
only the first is what let a stale `works_on` outlive the sentence that produced
it.

### Configured types, unconfigured relations

`semantic_config` declares each entity type's `description` (what it represents,
read by the extractor at extraction time), `extract_prompt`, `identity` fields,
and the `identity_keys` cascade that resolves one. Identity is capped small on
purpose: every field there is visible to everyone who can see the entity.

Relations between entities are **not** configured. The extractor names them as it
finds them (`works_on`, `owns`, `blocks`) and loops until it has drawn what the
document supports. Which relations matter is exactly what nobody knows in
advance, and unlike a payload field, an edge between two identity-only nodes
leaks nothing on its own.

`SemanticEntityType.to_node_spec()` compiles a declared type into the same
`NodeTypeSpec` a Slack message uses, so migrate builds it a view, `query.compile`
type-checks predicates against it, and the agent queries `person` exactly as it
queries `slack:message`. `fact` is a built-in spec, not configurable: its shape
is what makes the permission model work.

### The extractor is a loop

`upsert_entity` resolves against the graph and returns a **real** entity id plus
the facts already recorded there, so the model can see what it is adding to.
`find_entities`, `add_fact`, `link_entities`, then `finish`. Nothing is written
until the loop ends — resolution is a read, and the tools accumulate into one
`SemanticWrite` applied in a single transaction.

A free-searching loop is safe here precisely because of the fact split: whatever
the model reads while deciding, every fact it records inherits the permission
parent of the document being processed. Content cannot travel between audiences,
structurally.

### Reconciliation

Facts are **owned by their source**, which makes staleness an exact question
rather than a judgement call:

| change | what happens |
|---|---|
| created | extract |
| updated | delete every fact from the previous version, then re-extract |
| deleted | delete them, and replace nothing |

Entities survive all three, and need no attention: deleting a document's facts
*is* the access change. An entity whose last readable note has gone is already
invisible; one with notes from other documents is untouched.
`Store._tombstone` hard-deletes facts rather than tombstoning them — a fact is a
reading of text that no longer exists, and `query.visibility` deliberately checks
liveness on the candidate rather than its ancestors, so a soft-deleted fact under
a soft-deleted parent would stay visible.

### Watermark

`node.semantic_version` mirrors `content_version`'s role for ingest: it makes
redelivery idempotent, lets a sweeper find work lost to a crash between commit
and enqueue, and — set back to `''` — *is* the backfill mechanism.


---

## 12. Actions

What can be *done* to a node, as opposed to what can be read from it. Defined
separately from the graph and scoped by node type, because the verb is a
property of the kind of thing.

| Action | Node type | Requires | Effect |
|---|---|---|---|
| `slack.post_message` | `slack:channel` | `slack:member` | New top-level message |
| `slack.reply_in_thread` | `slack:message` | `slack:member` | Reply on that message's thread |
| `slack.dm` | `person` | — | Direct message, via the person's Slack id |
| `drive.replace_content` | `drive:file` | `drive:writer` | Overwrite the body, same file id |
| `drive.create_file` | `drive:folder` | `drive:writer` | New document in that folder |
| `notion.append_blocks` | `notion:page` | `notion:integration_visible` | Append markdown blocks |
| `notion.create_page` | `notion:page` | `notion:integration_visible` | New child page |

The catalog is code (`core.actions`) and the `action` table is a projection of
it, reflected on every boot the same way node-type views are. That direction
matters: an action is a function somebody has to have written, so a row without
an executor behind it would be a promise nothing can keep. Actions absent from
code are disabled rather than deleted, because `action_invocation` references
them.

Four properties, each enforced somewhere a caller cannot forget:

* **You may only act on what you can see.** `Runner.check` resolves the node
  through `query.visibility` first, and an invisible node reads as *absent*
  rather than forbidden — the same collapse `SessionGraph.get` makes.
* **Seeing it is not being allowed to change it.** A spec names the
  `access_level` it needs; `Visibility.strongest_level` returns the best grant
  the principal holds anywhere on the target's ancestor chain, and a shortfall
  is refused with the same "not available" as an invisible node. A
  `drive:commenter` reads every word of a document and cannot overwrite it.
* **Every attempt is recorded.** The `action_invocation` row is written before
  the call goes out and updated after, so a row left in `running` is a crash
  mid-flight rather than an invisible gap.
* **It is off unless switched on.** `ACTIONS_ENABLED` defaults to false.

Levels are comparable because they are only ever compared within one source: a
node belongs to exactly one source, so every grant on its chain speaks that
source's vocabulary. Drive is the case where this bites, since the connector
mirrors real roles. Slack has no read-only membership, so its check is real and
never disagrees with visibility. Notion is the honest gap — an internal
integration cannot read per-user page capabilities, so anyone who can see a page
may append to it, and narrowing that needs a finer grant from the connector
rather than a stricter constant here.

Executors reuse the seeder's writers rather than reimplementing them, which
keeps the ingest clients GET-only by construction.

Most inferred types have no actions. A `project` is a conclusion this system
drew, not a thing in a source with an id you can write to; acting on the
documents behind one means following its edges to them. **`person` is the one
exception**, and it is an exception to the reading of that rule rather than to
the rule itself: it carries a `slack_user_id` issued by Slack, so it is an
address that routes to a real conversation. The entity is still not what gets
written to — the DM is. It requires no level because there is none to require:
an entity holds no grants, and a Slack DM has no ACL, so any workspace member
may message any other and visibility of the person is the whole gate.

### Plans

One write is rarely the whole of an intent. "Prepare the document, file it, and
post where it went" is three writes that stand or fall together, and at the
moment the plan is written the document does not exist and has no link.

A plan is a list of `PlannedAction`s — an action, a resolved `entity_id`, its
parameters, and a rationale — and a later step may reference an earlier one's
result as `{{a1.web_view_link}}`. A reference must name an earlier step and a
field that step's action *declares* under `returns`; anything else is rejected
before the first write goes out. That is the rule the module is arranged around:
everything checkable is checked while the plan is still a proposal, because
discovering at step 3 that it was malformed — having already posted step 2 —
is the outcome no amount of logging makes good. After that line, failure is
fail-stop, and skipped steps are recorded rather than dropped.

**Plans are not stored**, and that is a consequence of the properties above
rather than a shortcut. Every gate is re-evaluated per step at dispatch, so a
plan handed back by a client is checked exactly as one loaded from our own table
would be. The trust boundary is the dispatcher, not the storage. What *is*
recorded is the run: `plan_id` is minted when execution starts and stamped on
each invocation, so the group is recoverable without a second table that could
disagree with the first.

`dry_run` takes the same path through the same checks and stops short of the
call — a preview that validated differently from the dispatcher would be worth
less than no preview at all.

A plan is capped at `MAX_PLAN_STEPS`, which lives in `core.actions` rather than
next to the runner: the number is quoted to the planner in its prompt, and the
planner must not import the module holding the executors.

### Who decides

The retrieval agent *plans* and does not execute. When a question asks for
something to be done, `finish` carries a `plan` alongside the answer, naming
targets it actually read; `/agent/query` cannot write, and running the plan is a
separate call to `/actions/invoke-plan`. So a model in a loop still cannot
decide to write on its own — what changed is that it can now say exactly what it
would do, in a form that executes verbatim once somebody agrees.

The plan field is offered only when the process can actually perform writes. With
`ACTIONS_ENABLED` false the retrieval prompt is byte-for-byte what it was before
any of this existed: a proposal nothing could run is worse than none, because
the answer would describe work that was never going to happen.

Acting costs turns, and `AGENT_MAX_TURNS` is sized for it rather than for asking.
A request to *do* something is two jobs out of one budget — working out the
answer and working out where it goes — and the second is the one that gets
squeezed, which is how a run ends with a good summary and nowhere to put it. The
prompt is told the number and told to spend it: resolve targets early alongside
the research, and by two thirds of the budget either have every target id or stop
researching and propose what is possible, saying what was left out. A partial
plan that names what it skipped beats exhausting the budget with nothing
proposed.
