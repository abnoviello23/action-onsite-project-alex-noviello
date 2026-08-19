import { useState } from "react";

/**
 * Everything stored on one node.
 *
 * The payload is the part worth reading and it gets rendered as fields rather
 * than as a JSON blob — the blob is still there behind a toggle, because for a
 * Notion property map or a Slack thread pointer the raw shape is sometimes
 * exactly what you are checking.
 *
 * The grants shown are the ones on this node alone. Effective access is
 * whatever the walk up the permission chain accumulates, and this panel does
 * not compute it — a half-resolved answer would be worse than the raw fact plus
 * a link to the parent to climb.
 */

export default function DetailPanel({ detail, loading, onSelect, onClose }) {
  const [showRaw, setShowRaw] = useState(false);

  if (!detail && !loading) {
    return (
      <aside className="panel detail detail-empty">
        <p className="subtle">Select a node to see everything stored on it.</p>
      </aside>
    );
  }

  return (
    <aside className="panel detail">
      <header className="panel-head">
        <div className="grow">
          <span className="block-type standalone">
            {detail?.node.node_type ?? "…"}
          </span>
          <h2 className="detail-title">{detail?.node.label ?? "Loading…"}</h2>
        </div>
        <button className="ghost" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </header>

      {detail && (
        <>
          <dl className="stats wide">
            <div>
              <dt>entity id</dt>
              <dd className="mono wrap">{detail.node.entity_id}</dd>
            </div>
            <div>
              <dt>updated</dt>
              <dd>{formatTime(detail.node.updated_at)}</dd>
            </div>
            <div>
              <dt>created</dt>
              <dd>{formatTime(detail.node.created_at)}</dd>
            </div>
            <div>
              <dt>children</dt>
              <dd>{detail.node.child_count}</dd>
            </div>
          </dl>

          {detail.node.deleted && (
            <p className="warn">Tombstoned. Inbound edges stay valid.</p>
          )}

          <section>
            <div className="section-head">
              <h3>Payload</h3>
              <button className="link" onClick={() => setShowRaw(!showRaw)}>
                {showRaw ? "fields" : "raw json"}
              </button>
            </div>
            {showRaw ? (
              <pre className="payload">
                {JSON.stringify(detail.payload, null, 2)}
              </pre>
            ) : (
              <Fields payload={detail.payload} />
            )}
          </section>

          {detail.body && (
            <section>
              <h3>Body</h3>
              <p className="body">{detail.body}</p>
            </section>
          )}

          <section>
            <h3>Permission parent</h3>
            {detail.parent_entity_id ? (
              <button
                className="link mono wrap"
                onClick={() => onSelect(detail.parent_entity_id)}
              >
                {detail.parent_entity_id}
              </button>
            ) : (
              <p className="subtle">
                None — this is a root, or nothing grants access to it.
              </p>
            )}
          </section>

          <section>
            <h3>Grants on this node ({detail.grants.length})</h3>
            {detail.grants.length ? (
              <ul className="grants">
                {detail.grants.map((g) => (
                  <li key={`${g.identity_id}:${g.level}`}>
                    <span className="grow">
                      <span className="mono">
                        {g.display_name || g.identity_id}
                      </span>
                      {g.display_name && (
                        <span className="subtle block-sub">{g.identity_id}</span>
                      )}
                    </span>
                    <span className="pill">{g.level}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="subtle">
                None directly. Access, if any, is inherited from the parent.
              </p>
            )}
          </section>

          <section>
            <h3>Edges ({detail.neighbors.length})</h3>
            {detail.neighbors.length ? (
              <ul className="neighbors">
                {detail.neighbors.map((n) => (
                  <li key={`${n.direction}:${n.relation}:${n.entity_id}`}>
                    <span className="pill">
                      {n.direction === "out" ? "→" : "←"} {n.relation}
                    </span>
                    <button
                      className="link grow"
                      onClick={() => onSelect(n.entity_id)}
                      title={n.entity_id}
                    >
                      {n.label}
                    </button>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="subtle">None.</p>
            )}
          </section>
        </>
      )}
    </aside>
  );
}

/** Payload as key/value rows, one level deep. */
function Fields({ payload }) {
  const entries = Object.entries(payload ?? {});
  if (!entries.length) return <p className="subtle">Empty.</p>;

  return (
    <dl className="fields">
      {entries.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{renderValue(value)}</dd>
        </div>
      ))}
    </dl>
  );
}

function renderValue(value) {
  if (value === null || value === undefined) return <span className="subtle">—</span>;
  if (typeof value === "boolean") return <span className="pill">{String(value)}</span>;
  if (Array.isArray(value)) {
    if (!value.length) return <span className="subtle">empty</span>;
    return (
      <span className="chips">
        {value.map((v, i) => (
          <span className="pill" key={i}>
            {typeof v === "object" ? JSON.stringify(v) : String(v)}
          </span>
        ))}
      </span>
    );
  }
  // Nested objects — a Notion property map, a Drive parent ref. One level of
  // nesting rendered inline; anything deeper is what the raw toggle is for.
  if (typeof value === "object") {
    return (
      <span className="chips">
        {Object.entries(value).map(([k, v]) => (
          <span className="pill" key={k}>
            {k}: {typeof v === "object" ? JSON.stringify(v) : String(v)}
          </span>
        ))}
      </span>
    );
  }
  return <span className="wrap">{String(value)}</span>;
}

function formatTime(value) {
  if (!value) return "—";
  // Source semantics, both of them — when the thing was created and last edited
  // in the source, never when it was ingested.
  return new Date(value).toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}
