# Graph visualizer

Two pages over the same graph:

- **`/`** — the topology across every source: what was ingested and how it hangs
  together.
- **`/chat`** — natural-language search, answered as a chosen identity, so what
  comes back is only what that identity can see.

```bash
docker compose up -d           # postgres, redis, migrate, workers, api on :8000
npm install --prefix web
npm run dev --prefix web       # http://localhost:5173
```

If the graph tables are empty there is nothing to draw, and the view says so
rather than showing a blank. It draws whatever the ingestion path writes to
`node` and `edge`, so it fills in on its own — there is nothing to configure
here when that happens.

## What it draws

Trees, fully expanded, one group per source. Containers branch sideways —
folder into folder, database into data source — and a container's leaf children
hang beneath it off a shared trunk. Root trees wrap into rows so the whole
forest stays roughly square, and sources stack down the page with their name
above them.

Nothing floats and nothing drifts: positions are computed when the data or the
collapse set changes, and then they hold. A block moves only if you drag it.

Two things are edges here, and they mean different things:

- **Solid, branching or trunked** — `node.permission_parent_id`. The containment
  tree access inherits along. It is a column rather than an `edge` row, so the
  API synthesizes these; it is also the only thing the layout reads.
- **Dashed, labelled with its relation** — `edge` rows: `in_channel`, `next`,
  `in_thread`, a Notion mention. These carry no access semantics and are never
  allowed to influence position, which is what stops a link between two leaves
  from dragging the tree into a hairball.

Each block shows its node type as text, its label, and how many access grants
sit on it directly (`n ⚿` — grants on that node, not inherited). Colour follows
the source and is never the only carrier of anything — which is what lets a
fourth source render unhued: this is an all-pairs form, and no fourth hue clears
the colourblind and normal-vision separation floors against the three in use, so
past three a source leans on its labelled cluster and its type text instead. Zoomed out, blocks reduce
to marks and the source names counter-scale, so the shape of the forest survives
at any size; zoom in and the text comes back.

Click any block and the canvas becomes a view of that node's connections: every
other edge is removed outright rather than dimmed — dimming three thousand lines
still leaves three thousand lines — while the node's own edges are drawn thick
and in the colour of the source they run to. The node and everything one hop
from it grow and brighten; the rest of the graph drops back to context so the
shape is still there to place them in. Clicking empty space clears it.

The panel alongside carries everything stored on that node: the payload as
fields (or raw JSON), the body, its permission parent, the grants on it, and
every relation edge in and out. Every id in that panel is a link that selects
that node, and the view pans to it only when it is not already on screen.

`–` under a block collapses its subtree, `+n` brings it back, and **Expand all**
/ **Collapse all** are on the canvas. Nothing is collapsed by default.

The node limit defaults to **All** — every node the filters match, with no cap.
That is what makes the picture the whole graph rather than a recent slice; the
capped options are there for when a subset is genuinely wanted.

The sources list reads `drawn / total`. With no limit the gap is tombstoned
nodes, which are excluded until *Include deleted* is ticked. Under a cap it is
also whatever the cap cut — the limit takes the most recently updated nodes, so
a source that has not been written to lately can show `0 / 147` while still
having every one of those rows.

## Ask (`/chat`)

Questions go to the retrieval agent as an identity, which is the point rather
than a setting: the same question asked as two people is expected to come back
differently, because the answer is restricted to what each can see. The picker
is filled from `VITE_DEMO_IDENTITIES` in `web/.env.local` (copy `web/.env.local.demo`); the server's own
`AGENT_DEMO_IDENTITIES` allowlist is what actually decides, and it refuses
anything outside it.

Ask as an **app user** to see one person across every source at once. A mirrored
identity (`slack:user:U…`, `drive:user:alice@acme.com`) holds the grants from
one source and cannot log in; an app user holds none of its own and sits below
them as a member, inheriting each one's access:

```bash
docker compose --profile admin run --rm identity link --email alice@acme.com --name "Alice"
```

That creates `app:user:alice@acme.com` and gives it a membership edge into every
identity sharing the address — `--dry-run` prints the edges without writing
them. Re-run it after new ingestion to pick up identities that appeared since.
Add the id to `AGENT_DEMO_IDENTITIES` and restart the api to query as it.

A search runs for tens of seconds to a couple of minutes, so the page shows what
the agent is doing while it does it — each turn, each tool call with its
arguments, and the size of each result — and keeps that trace with the answer
under *How it searched*. The answer arrives with its citations, each one a link
that opens that node in the graph page, plus the run's own numbers: turns taken,
nodes opened, and whether any tool hit its scan cap.

The endpoint keeps no conversation state, so each question is answered on its
own and the page presents the transcript as a log rather than a thread. A
follow-up has to restate its subject.

## API

| Route | What it returns |
|---|---|
| `GET /graph` | nodes + edges + stats. `sources`, `node_types` (csv), `q`, `limit` (`0` = no cap), `include_deleted` |
| `GET /graph/meta` | node types, sources, relations with counts — the filter lists are built from this |
| `GET /graph/nodes/{entity_id}` | one node: payload, grants on it, its permission parent, its relation edges |
| `POST /agent/query` | the agent's answer in one piece. `X-Demo-Identity` header, `{text}` body |
| `POST /agent/stream` | the same run narrated over SSE — `turn`, `thinking`, `tool`, `tool_result`, then `answer` |

`/graph` seeds from the filtered rows and then closes upward over
`permission_parent_id`, because a limit that excludes the drives and channels
everything hangs off draws a field of orphans. `stats.ancestors_added` reports
how many nodes that pulled in, and `stats.truncated` says when the view is
partial.

The routes are unfiltered by principal — an operator's view of what was
ingested, not the permissioned retrieval path. CORS is localhost-only for that
reason, and anything beyond localhost needs visibility resolution first.

## Implementation notes

Rendering is [React Flow](https://reactflow.dev); the layout in `src/layout.js`
is written here rather than taken from a graph-drawing library, because the
shape of this data defeats the standard ones.

A tidy tree gives every leaf its own column. These trees are overwhelmingly
leaves — two hundred nodes measured **forty-eight thousand pixels wide**, which
frames as a row of dashes at any zoom that fits it. But the branching itself is
only ever a few dozen containers, and that is the part worth seeing as a shape.
So containers fan out and leaves stack, which makes width scale with the number
of folders rather than the number of files: the same graph is now ~3,700 ×
4,150. Leaf stacks wrap into further columns past twelve, and root trees wrap
into rows, so one node with forty children cannot drag the forest out of shape.

Two things are declared up front rather than measured: node dimensions and
handle positions (`src/GraphView.jsx`). React Flow normally discovers both from
the DOM and will not route an edge until it has, and it holds a queued `fitView`
until every node reports in — with a fixed-size, pre-computed layout there is
nothing to wait for, and declaring them means edges are drawn on the first paint
instead of a frame later. Framing is likewise computed and applied directly:
`fitView` in v12 only queues, and resolves on the next nodes update, which never
comes once a layout has settled. Edge geometry comes from node rectangles
(`src/edges.jsx`) rather than handle DOM, which is what lets a trunk run down
the gutter of a leaf stack instead of between block centres.

`/agent/stream` is the same orchestrator as `/agent/query` with a progress
listener attached: `answer_question` grew an optional `on_event` callback that
reports turns and tool calls, and the SSE route forwards it to the client. The
retrieval itself is untouched — with no listener the loop runs exactly as it
did, and `/agent/query` passes none. The chat page falls back to it if the
stream route is unavailable.

The dev server is not a Compose service. It runs from the host, where node
lives, and proxies `/api` to `http://localhost:8000` — point it elsewhere with
`VITE_API_TARGET`. `npm run build` produces a static bundle in `web/dist` if it
ever needs to be served from the API instead.
