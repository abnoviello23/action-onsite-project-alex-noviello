/**
 * The API client.
 *
 * Everything goes through Vite's /api proxy, so the browser only ever talks to
 * one origin. Point it elsewhere with VITE_API_TARGET when the API is not on
 * localhost:8000.
 */

async function get(path, params) {
  const url = new URL(`/api${path}`, window.location.origin);
  for (const [key, value] of Object.entries(params ?? {})) {
    if (value === undefined || value === null || value === "") continue;
    url.searchParams.set(key, value);
  }

  const response = await fetch(url, { headers: { accept: "application/json" } });
  if (!response.ok) {
    // FastAPI puts the useful part in `detail`; falling back to the status text
    // keeps a proxy error (API down) from surfacing as "undefined".
    let detail = response.statusText;
    try {
      detail = (await response.json()).detail ?? detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(`${response.status} ${detail}`);
  }
  return response.json();
}

export function fetchMeta() {
  return get("/graph/meta");
}

export function fetchGraph({ sources, nodeTypes, q, limit, includeDeleted }) {
  return get("/graph", {
    sources: sources?.length ? sources.join(",") : undefined,
    node_types: nodeTypes?.length ? nodeTypes.join(",") : undefined,
    q: q || undefined,
    limit,
    include_deleted: includeDeleted ? "true" : undefined,
  });
}

export function fetchNode(entityId) {
  // Not encoded: entity ids carry colons and dots, the route is declared
  // `:path` for exactly that, and encoding them turns a valid id into a 404.
  return get(`/graph/nodes/${entityId}`);
}

/**
 * The useful part of a failed response, as an Error carrying its status.
 *
 * FastAPI puts the message in `detail`, but validation errors arrive as a list
 * of per-field objects instead — joining those is the difference between a
 * readable complaint and "[object Object]".
 */
async function failure(response) {
  let detail = response.statusText;
  try {
    const body = await response.json();
    detail = body.detail ?? detail;
    if (Array.isArray(detail)) {
      detail = detail.map((d) => d.msg ?? JSON.stringify(d)).join("; ");
    }
  } catch {
    /* non-JSON error body */
  }
  const error = new Error(String(detail));
  error.status = response.status;
  return error;
}

/**
 * Ask the retrieval agent.
 *
 * `POST /agent/query` answers in one shot — it is not a stream, and this client
 * deliberately does not pretend otherwise. It also has no conversation state:
 * every question is answered on its own, so the transcript in the UI is a log
 * of independent questions rather than a thread the model can see.
 *
 * The identity header is what the answer is scoped to. The endpoint refuses
 * anything outside its allowlist, and its refusal text is worth showing
 * verbatim — it is the difference between "no header", "not allowed", and "no
 * authentication configured at all".
 */
export async function askAgent({ text, identity, signal }) {
  const response = await fetch("/api/agent/query", {
    method: "POST",
    signal,
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "X-Demo-Identity": identity,
    },
    body: JSON.stringify({ text }),
  });

  if (!response.ok) throw await failure(response);
  return response.json();
}

export function fetchHealth() {
  return get("/health");
}

/**
 * Ask the agent and watch the run.
 *
 * `POST /agent/stream` narrates the same orchestrator loop `/agent/query`
 * runs — turns, tool calls, tool results — and finishes with the identical
 * answer object. Server-sent events over fetch rather than EventSource,
 * because the identity travels as a header and EventSource cannot send one.
 *
 * Errors arrive two ways and both matter: a non-2xx before the stream opens is
 * auth or config (the caller should see the status), while an `error` event
 * inside a 200 is the run itself failing partway.
 */
export async function streamAgent({ text, identity, signal, onEvent }) {
  const response = await fetch("/api/agent/stream", {
    method: "POST",
    signal,
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
      "X-Demo-Identity": identity,
    },
    body: JSON.stringify({ text }),
  });

  if (!response.ok) throw await failure(response);

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    // Frames are separated by a blank line; a partial tail stays in the buffer
    // until the rest of it arrives.
    let split;
    while ((split = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);

      const line = frame.split("\n").find((l) => l.startsWith("data:"));
      if (!line) continue;

      const event = JSON.parse(line.slice(5).trim());
      if (event.type === "answer") answer = event.answer;
      else if (event.type === "error") throw new Error(event.message);
      else onEvent?.(event);
    }
  }

  if (!answer) throw new Error("the stream ended without an answer");
  return answer;
}


/**
 * The action catalog: what each action does, what it needs, what it returns.
 *
 * Read once and used to annotate a plan — an action name alone does not tell a
 * reviewer that `drive.replace_content` overwrites a document they may care
 * about, and that is exactly what approval hinges on.
 */
export function fetchActions() {
  return get("/actions");
}

/**
 * Run a plan the agent proposed.
 *
 * The plan travels in the request body because nothing on the server is holding
 * it: the agent hands one out, a person reads it, and it comes back here to be
 * run. That round trip is safe rather than merely convenient — every step is
 * re-resolved and re-checked against the acting identity at dispatch, so a plan
 * returning from a browser is worth exactly what one loaded from a table would
 * be.
 *
 * `identity` must be the identity the plan was *proposed* as. The targets were
 * resolved under that principal, and running them as someone else would be a
 * different question with the same answer text.
 *
 * A rejected plan is a 400 and nothing was sent. A plan that fails partway is a
 * 200 whose `status` is 'failed' — by then something has happened, and the body
 * is the account of what.
 */
export async function invokePlan({ plan, identity, dryRun, signal }) {
  const response = await fetch("/api/actions/invoke-plan", {
    method: "POST",
    signal,
    headers: {
      "content-type": "application/json",
      accept: "application/json",
      "X-Demo-Identity": identity,
    },
    body: JSON.stringify({ plan, dry_run: !!dryRun }),
  });

  if (!response.ok) throw await failure(response);
  return response.json();
}
