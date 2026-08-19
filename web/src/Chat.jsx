import { useCallback, useEffect, useRef, useState } from "react";
import {
  askAgent,
  fetchActions,
  fetchHealth,
  invokePlan,
  streamAgent,
} from "./api.js";
import { navigate } from "./router.js";

/**
 * Natural-language search over the graph, as the identity you are asking as.
 *
 * Runs against `POST /agent/stream`, which narrates the same orchestrator loop
 * `/agent/query` runs: every turn, every tool call with its arguments, every
 * result. That trace is the interesting part of a two-minute search, so it is
 * shown as it arrives and kept with the answer afterwards rather than thrown
 * away. If the stream route is missing, the page falls back to the plain
 * endpoint and simply waits.
 *
 * The endpoint has no memory. Each question is answered on its own, so the
 * transcript below is a log of independent questions rather than a conversation
 * the model can see — a follow-up has to restate its subject.
 *
 * That is why a proposed plan is approved with a **button rather than a
 * sentence**. When a question asks for something to be done, the answer carries
 * a `plan`: resolved targets and the exact text that would be written, having
 * changed nothing. Saying "yes, do it" in the next message could not work — the
 * model would not see what it was agreeing to. So the plan stays here, in the
 * turn that produced it, and this page hands it back to `/actions/invoke-plan`
 * verbatim. Holding it between proposal and approval is the client's job by
 * design: nothing is stored server-side, because every step is re-checked
 * against the acting identity at dispatch anyway.
 *
 * The identity is the whole point rather than a setting: the answer is
 * restricted to what that identity can see, so the same question asked as two
 * people is expected to come back different.
 */

// Client-side convenience only — the server's own allowlist is what actually
// decides, and it refuses anything outside it whether or not this list agrees.
const CONFIGURED_IDENTITIES = (import.meta.env.VITE_DEMO_IDENTITIES ?? "")
  .split(",")
  .map((s) => s.trim())
  .filter(Boolean);

const IDENTITY_KEY = "pkg.chat.identity";

const EXAMPLES = [
  "What did we decide about the ingestion pipeline?",
  "Which deals mention pricing concerns?",
  "Summarise what happened in engineering last week",
  "Who owns the Harborline account?",
];

export default function Chat() {
  const [identity, setIdentity] = useState(
    () => localStorage.getItem(IDENTITY_KEY) ?? CONFIGURED_IDENTITIES[0] ?? ""
  );
  const [question, setQuestion] = useState("");
  const [turns, setTurns] = useState([]);
  const [pending, setPending] = useState(null);
  const [health, setHealth] = useState(null);
  const [catalog, setCatalog] = useState(null);

  const abortRef = useRef(null);
  const endRef = useRef(null);

  useEffect(() => {
    fetchHealth().then(setHealth).catch(() => setHealth({ agent: false }));
    // Catalog-only, so it needs no identity and is safe to read up front. Used
    // to say what an action does next to the step proposing it.
    fetchActions().then(setCatalog).catch(() => setCatalog([]));
  }, []);

  useEffect(() => {
    if (identity) localStorage.setItem(IDENTITY_KEY, identity);
  }, [identity]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns, pending]);

  const ask = useCallback(
    async (text) => {
      const trimmed = text.trim();
      if (!trimmed || pending) return;

      const controller = new AbortController();
      abortRef.current = controller;
      const startedAt = Date.now();
      setPending({ question: trimmed, identity, startedAt, events: [] });
      setQuestion("");

      const events = [];
      const onEvent = (event) => {
        events.push(event);
        // A fresh array each time, so React sees the change.
        setPending((p) => (p ? { ...p, events: [...events] } : p));
      };

      try {
        let answer;
        try {
          answer = await streamAgent({
            text: trimmed,
            identity,
            signal: controller.signal,
            onEvent,
          });
        } catch (error) {
          // A backend without the stream route still answers the plain one.
          if (error.status === 404 || error.status === 405) {
            answer = await askAgent({
              text: trimmed,
              identity,
              signal: controller.signal,
            });
          } else {
            throw error;
          }
        }
        setTurns((prev) => [
          ...prev,
          {
            question: trimmed,
            identity,
            answer,
            events,
            elapsedMs: Date.now() - startedAt,
          },
        ]);
      } catch (error) {
        if (error.name === "AbortError") return;
        setTurns((prev) => [
          ...prev,
          {
            question: trimmed,
            identity,
            error: error.message,
            status: error.status,
            events,
            elapsedMs: Date.now() - startedAt,
          },
        ]);
      } finally {
        abortRef.current = null;
        setPending(null);
      }
    },
    [identity, pending]
  );

  const agentOff = health && health.agent === false;

  return (
    <div className="chat">
      <header className="chat-head">
        <div>
          <h1>Ask the graph</h1>
          <p className="subtle">
            Answers are restricted to what the chosen identity can see. Each
            question is answered on its own — the endpoint keeps no conversation
            state, so follow-ups need to restate their subject.
          </p>
        </div>
        <IdentityPicker identity={identity} onChange={setIdentity} />
      </header>

      {agentOff && (
        <div className="error">
          The agent is disabled on the server: <code>ANTHROPIC_API_KEY</code> is
          not set. <code>/agent/query</code> will answer 503 until it is.
        </div>
      )}

      <div className="chat-log">
        {!turns.length && !pending && (
          <div className="chat-empty">
            <p className="subtle">Ask something about the ingested graph.</p>
            <ul className="examples">
              {EXAMPLES.map((example) => (
                <li key={example}>
                  <button className="example" onClick={() => ask(example)}>
                    {example}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        )}

        {turns.map((turn, i) => (
          <Turn key={i} turn={turn} catalog={catalog} />
        ))}

        {pending && (
          <Pending
            pending={pending}
            onCancel={() => abortRef.current?.abort()}
          />
        )}

        <div ref={endRef} />
      </div>

      <form
        className="chat-form"
        onSubmit={(event) => {
          event.preventDefault();
          ask(question);
        }}
      >
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            // Enter sends, shift+enter breaks the line — the convention for a
            // box that is a message rather than a document.
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              ask(question);
            }
          }}
          placeholder={
            identity
              ? "Ask a question…"
              : "Choose an identity first — answers are scoped to it"
          }
          rows={2}
          disabled={!identity}
        />
        <button type="submit" disabled={!question.trim() || !!pending || !identity}>
          {pending ? "Asking…" : "Ask"}
        </button>
      </form>
    </div>
  );
}

function IdentityPicker({ identity, onChange }) {
  const [custom, setCustom] = useState(
    () => !!identity && !CONFIGURED_IDENTITIES.includes(identity)
  );

  return (
    <div className="identity">
      <label className="field">
        <span>Asking as</span>
        {custom || !CONFIGURED_IDENTITIES.length ? (
          <input
            value={identity}
            onChange={(e) => onChange(e.target.value)}
            placeholder="slack:user:U…"
            spellCheck={false}
          />
        ) : (
          <select value={identity} onChange={(e) => onChange(e.target.value)}>
            {CONFIGURED_IDENTITIES.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        )}
      </label>
      {!!CONFIGURED_IDENTITIES.length && (
        <button className="link" onClick={() => setCustom(!custom)}>
          {custom ? "pick from the configured list" : "type another identity"}
        </button>
      )}
    </div>
  );
}

function Pending({ pending, onCancel }) {
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    const id = setInterval(
      () => setElapsed(Math.round((Date.now() - pending.startedAt) / 100) / 10),
      100
    );
    return () => clearInterval(id);
  }, [pending.startedAt]);

  return (
    <article className="turn">
      <Question text={pending.question} identity={pending.identity} />
      <div className="answer">
        <div className="pending-head">
          <span className="pulse" />
          <span>Searching the graph — {elapsed.toFixed(1)}s</span>
          <button className="link" onClick={onCancel}>
            cancel
          </button>
        </div>
        <Trace events={pending.events} live />
      </div>
    </article>
  );
}

function Turn({ turn, catalog }) {
  return (
    <article className="turn">
      <Question text={turn.question} identity={turn.identity} />

      {turn.error ? (
        <div className="answer error">
          <strong>{turn.status ? `${turn.status} — ` : ""}</strong>
          {turn.error}
        </div>
      ) : (
        <div className="answer">
          <Prose text={turn.answer.answer} />

          {turn.answer.truncated && (
            <p className="warn">
              A tool hit its scan cap during this run, so this answer is drawn
              from part of the graph rather than all of it.
            </p>
          )}

          {!!turn.answer.plan?.length && (
            <PlanPanel
              plan={turn.answer.plan}
              identity={turn.identity}
              catalog={catalog}
            />
          )}

          {!!turn.answer.citations?.length && (
            <section className="citations">
              <h3>Cited ({turn.answer.citations.length})</h3>
              <ul>
                {turn.answer.citations.map((citation) => (
                  <li key={citation.entity_id}>
                    <span className="pill">
                      {citation.node_type ?? "unknown"}
                    </span>
                    <button
                      className="link grow"
                      title={`Open ${citation.entity_id} in the graph`}
                      onClick={() =>
                        navigate(
                          `/?node=${encodeURIComponent(citation.entity_id)}`
                        )
                      }
                    >
                      {citation.label || citation.entity_id}
                    </button>
                    {!!Object.keys(citation.native ?? {}).length && (
                      <span className="subtle native">
                        {Object.entries(citation.native)
                          .map(([k, v]) => `${k}=${v}`)
                          .join(" · ")}
                      </span>
                    )}
                  </li>
                ))}
              </ul>
            </section>
          )}

          <footer className="run-stats">
            {turn.answer.turns_used} turns · {turn.answer.nodes_opened} nodes
            opened · {(turn.elapsedMs / 1000).toFixed(1)}s
          </footer>

          {!!turn.events?.length && (
            <details className="trace-details">
              <summary>
                How it searched (
                {turn.events.filter((e) => e.type === "tool").length} tool calls)
              </summary>
              <Trace events={turn.events} />
            </details>
          )}
        </div>
      )}
    </article>
  );
}

/**
 * A proposed plan, and the two buttons that decide its fate.
 *
 * Everything here is about making approval an informed act. The target is shown
 * by label rather than by id, the agent's reason for choosing it is quoted, and
 * the exact text that would be written is displayed in full — because "post a
 * summary to #hl-sales" is not something a person can meaningfully approve and
 * the actual paragraph is.
 *
 * **Dry run** takes the identical path through visibility, level, parameter and
 * binding checks and stops short of the call, so it answers "would this work"
 * without spending a write. It is the safe first click and costs nothing.
 *
 * Once a real run succeeds the buttons are gone rather than merely disabled.
 * The plan is still on screen, and a second click would post the whole thing
 * again — there is no idempotency key, and Slack would happily take a duplicate.
 */
function PlanPanel({ plan, identity, catalog }) {
  const [busy, setBusy] = useState(null);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);

  const specs = new Map((catalog ?? []).map((a) => [a.name, a]));
  const done = result && !result.dry_run && result.status === "ok";

  const run = async (dryRun) => {
    setBusy(dryRun ? "dry" : "real");
    setError(null);
    try {
      setResult(await invokePlan({ plan, identity, dryRun }));
    } catch (e) {
      // A 400 means the plan was rejected before anything was sent, which is
      // worth saying plainly: nothing happened.
      setError(e.message);
      setResult(null);
    } finally {
      setBusy(null);
    }
  };

  const outcomes = new Map((result?.steps ?? []).map((s) => [s.id, s]));

  return (
    <section className="plan">
      <h3>
        Proposed {plan.length === 1 ? "action" : `actions (${plan.length})`}
        <span className="subtle"> · nothing has run yet</span>
      </h3>

      <ol className="plan-steps">
        {plan.map((step) => {
          const spec = specs.get(step.action);
          const outcome = outcomes.get(step.id);
          return (
            <li className="plan-step" key={step.id}>
              <div className="plan-step-head">
                <span className="pill">{step.action}</span>
                {spec?.destructive && (
                  <span className="pill danger-pill">overwrites</span>
                )}
                <span className="subtle">→</span>
                <button
                  className="link grow"
                  title={`Open ${step.entity_id} in the graph`}
                  onClick={() =>
                    navigate(`/?node=${encodeURIComponent(step.entity_id)}`)
                  }
                >
                  {step.target_label || step.entity_id}
                </button>
                {step.target_type && (
                  <span className="pill">{step.target_type}</span>
                )}
                {outcome && (
                  <span className={`pill status-${outcome.status}`}>
                    {outcome.status}
                  </span>
                )}
              </div>

              {spec && <p className="plan-summary">{spec.summary}</p>}
              {step.rationale && (
                <p className="plan-why">
                  <strong>Why here:</strong> {step.rationale}
                </p>
              )}

              <PlanParams params={step.params} />

              {outcome?.error && (
                <p className="plan-error">{outcome.error}</p>
              )}
              {outcome?.result?.permalink && (
                <p className="plan-link">
                  <a
                    href={outcome.result.permalink}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    open the posted message ↗
                  </a>
                </p>
              )}
              {outcome?.result?.web_view_link && (
                <p className="plan-link">
                  <a
                    href={outcome.result.web_view_link}
                    target="_blank"
                    rel="noreferrer noopener"
                  >
                    open the document ↗
                  </a>
                </p>
              )}
            </li>
          );
        })}
      </ol>

      {error && <p className="plan-error">{error}</p>}

      {result && (
        <p className={result.status === "ok" ? "plan-done" : "warn"}>
          {result.dry_run
            ? result.status === "checked"
              ? "Every check passed. Nothing was sent."
              : "A step would fail — see above. Nothing was sent."
            : result.status === "ok"
              ? "Done. Every step ran."
              : "Stopped at the first failure; later steps were skipped."}
        </p>
      )}

      {!done && (
        <div className="plan-actions">
          <button
            className="ghost"
            disabled={!!busy}
            onClick={() => run(true)}
          >
            {busy === "dry" ? "checking…" : "Dry run"}
          </button>
          <button
            className="primary"
            disabled={!!busy}
            onClick={() => run(false)}
          >
            {busy === "real" ? "running…" : "Approve & run"}
          </button>
          <span className="subtle plan-as">as {identity}</span>
        </div>
      )}
    </section>
  );
}

/**
 * The arguments, with the long one given room.
 *
 * A message body or a document is the substance of the decision, so it is shown
 * whole and in a monospace block rather than truncated into a summary. The
 * short scalars — `if_exists`, `as_document` — ride along on one line, since
 * they change what happens but are read at a glance.
 */
function PlanParams({ params }) {
  const entries = Object.entries(params ?? {});
  const long = entries.filter(
    ([, v]) => typeof v === "string" && v.length > 80
  );
  const short = entries.filter(([k]) => !long.some(([lk]) => lk === k));

  return (
    <div className="plan-params">
      {long.map(([key, value]) => (
        <div key={key}>
          <div className="plan-param-label">{key}</div>
          <pre className="plan-text">{value}</pre>
        </div>
      ))}
      {!!short.length && (
        <p className="plan-scalars">
          {short.map(([key, value]) => (
            <span key={key} className="native">
              {key}={String(value)}
            </span>
          ))}
        </p>
      )}
    </div>
  );
}

/**
 * What the agent did, as it did it.
 *
 * Tool arguments are shown because they *are* the search: which node type,
 * which predicate, which seed ids. Results are summarised by size rather than
 * printed — their content is already in the answer, and a fifteen-thousand
 * character dump between two lines of narration helps nobody.
 */
function Trace({ events, live }) {
  if (!events?.length) {
    return live ? (
      <p className="subtle trace-empty">waiting for the first turn…</p>
    ) : null;
  }

  return (
    <ol className="trace">
      {events.map((event, i) => {
        if (event.type === "turn") {
          return (
            <li className="trace-turn" key={i}>
              turn {event.index} of {event.of}
            </li>
          );
        }
        if (event.type === "thinking") {
          return (
            <li className="trace-thinking" key={i}>
              {event.text}
            </li>
          );
        }
        if (event.type === "tool") {
          return (
            <li className="trace-tool" key={i}>
              <span className="pill">{event.name}</span>
              <code>{summariseArgs(event.name, event.args)}</code>
            </li>
          );
        }
        if (event.type === "tool_result") {
          return (
            <li
              className={
                event.is_error ? "trace-result is-error" : "trace-result"
              }
              key={i}
            >
              {event.is_error
                ? "error"
                : `${event.chars.toLocaleString()} chars`}
            </li>
          );
        }
        return null;
      })}
    </ol>
  );
}

/** `finish` carries the whole answer; every other call is worth showing whole. */
function summariseArgs(name, args) {
  if (name === "finish") {
    const citations = (args?.citations || []).length;
    return `answering with ${citations} citation${citations === 1 ? "" : "s"}`;
  }
  const json = JSON.stringify(args ?? {});
  return json.length > 240 ? `${json.slice(0, 239)}…` : json;
}

function Question({ text, identity }) {
  return (
    <div className="question">
      <p>{text}</p>
      <span className="subtle mono">as {identity}</span>
    </div>
  );
}

/**
 * Paragraphs, list lines, and the two inline marks the answers actually use.
 *
 * Not a markdown parser — a parser would be more surface than this needs, and
 * the model writes plain prose with the occasional bold name or `entity_id`.
 * Everything else is left as written rather than half-interpreted.
 */
function Prose({ text }) {
  const blocks = String(text ?? "").split(/\n{2,}/);
  return (
    <>
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        const isList = lines.every((l) => /^\s*[-*•]\s+/.test(l));
        if (isList) {
          return (
            <ul className="answer-list" key={i}>
              {lines.map((line, j) => (
                <li key={j}>
                  <Inline text={line.replace(/^\s*[-*•]\s+/, "")} />
                </li>
              ))}
            </ul>
          );
        }
        return (
          <p key={i}>
            <Inline text={block} />
          </p>
        );
      })}
    </>
  );
}

/** **bold** and `code`, left alone if unmatched. */
function Inline({ text }) {
  const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
          return <strong key={i}>{part.slice(2, -2)}</strong>;
        }
        if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
          return (
            <code className="inline-code" key={i}>
              {part.slice(1, -1)}
            </code>
          );
        }
        return part;
      })}
    </>
  );
}
