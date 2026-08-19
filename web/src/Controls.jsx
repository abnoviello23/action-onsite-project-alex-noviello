/**
 * Filters, legend and counts.
 *
 * The filter lists are built from /graph/meta rather than from a copy of the
 * node-type registry: the registry is Python, it grows a type every time a
 * connector learns one, and a hardcoded list here would be wrong within a week.
 */

// 0 is the API's sentinel for "every matching node", and the default.
const LIMITS = [
  { value: 0, label: "All" },
  { value: 200, label: "200" },
  { value: 600, label: "600" },
  { value: 1500, label: "1500" },
  { value: 5000, label: "5000" },
];

export default function Controls({
  meta,
  filters,
  setFilters,
  stats,
  counts,
  colors,
  loading,
  error,
  onRefresh,
}) {
  const toggle = (key, value) => {
    const current = new Set(filters[key]);
    if (current.has(value)) current.delete(value);
    else current.add(value);
    setFilters({ ...filters, [key]: [...current] });
  };

  return (
    <aside className="panel controls">
      <header className="panel-head">
        <div>
          <h1>Graph topology</h1>
          <p className="subtle">
            {meta
              ? `${meta.total_nodes} nodes · ${meta.total_edges} relation edges · ${meta.total_grants} grants`
              : "connecting…"}
          </p>
        </div>
      </header>

      {error && <div className="error">{error}</div>}

      <section>
        <label className="field">
          <span>Search</span>
          <input
            type="search"
            placeholder="body, entity id, payload…"
            value={filters.q}
            onChange={(e) => setFilters({ ...filters, q: e.target.value })}
          />
        </label>

        <label className="field">
          <span>Node limit</span>
          <select
            value={filters.limit}
            onChange={(e) =>
              setFilters({ ...filters, limit: Number(e.target.value) })
            }
          >
            {LIMITS.map(({ value, label }) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>

        <label className="check">
          <input
            type="checkbox"
            checked={filters.includeDeleted}
            onChange={(e) =>
              setFilters({ ...filters, includeDeleted: e.target.checked })
            }
          />
          <span>Include deleted (tombstoned)</span>
        </label>
      </section>

      <section>
        <h2>Sources</h2>
        <ul className="filter-list">
          {(meta?.sources ?? []).map(({ key, count }) => (
            <li key={key}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={
                    filters.sources.length === 0 || filters.sources.includes(key)
                  }
                  onChange={() => toggle("sources", key)}
                />
                <Mark color={colors[key] ?? colors.unknown} />
                <span className="grow">{key}</span>
                {/* Drawn against total. The limit takes the most recently
                    updated nodes, so a whole source can be missing from the
                    canvas while still having hundreds of rows — worth saying
                    outright rather than leaving someone hunting for it. */}
                <span className="count">
                  {stats ? `${stats.by_source?.[key] ?? 0} / ` : ""}
                  {count}
                </span>
              </label>
            </li>
          ))}
        </ul>
        {filters.sources.length > 0 && (
          <button
            className="link"
            onClick={() => setFilters({ ...filters, sources: [] })}
          >
            clear source filter
          </button>
        )}
      </section>

      <section>
        <h2>Node types</h2>
        <ul className="filter-list">
          {(meta?.node_types ?? []).map(({ key, count }) => (
            <li key={key}>
              <label className="check">
                <input
                  type="checkbox"
                  checked={
                    filters.nodeTypes.length === 0 ||
                    filters.nodeTypes.includes(key)
                  }
                  onChange={() => toggle("nodeTypes", key)}
                />
                <span className="grow mono">{key}</span>
                <span className="count">{count}</span>
              </label>
            </li>
          ))}
        </ul>
        {filters.nodeTypes.length > 0 && (
          <button
            className="link"
            onClick={() => setFilters({ ...filters, nodeTypes: [] })}
          >
            clear type filter
          </button>
        )}
      </section>

      <section>
        <h2>Legend</h2>
        <ul className="legend">
          <li>
            <LegendLine kind="permission" /> permission parent — the tree the
            layout is built from, and the one access inherits along
          </li>
          <li>
            <LegendLine kind="relation" /> relation edge, labelled with its
            relation; drawn but never allowed to move anything
          </li>
          <li>
            <span className="legend-pill">n ⚿</span> access grants on that node
            itself, not inherited
          </li>
          <li>
            <span className="legend-pill">–</span> collapse a subtree, and{" "}
            <span className="legend-pill">+n</span> to bring it back
          </li>
        </ul>
        <p className="subtle">
          One tree per source, parents above the children they contain, fully
          expanded. Blocks carry their node type as text, so identity never
          rests on colour; zoomed out they reduce to marks so the shape of the
          forest still reads.
        </p>
      </section>

      {stats && (
        <section>
          <h2>Drawn</h2>
          <dl className="stats">
            <div>
              <dt>nodes</dt>
              <dd>
                {counts?.drawn ?? stats.node_count}
                {counts?.hiddenCount ? (
                  <span className="subtle"> +{counts.hiddenCount} collapsed</span>
                ) : null}
              </dd>
            </div>
            <div>
              <dt>edges</dt>
              <dd>{stats.edge_count}</dd>
            </div>
            <div>
              <dt>matched</dt>
              <dd>{stats.matched}</dd>
            </div>
            <div>
              <dt>ancestors added</dt>
              <dd>{stats.ancestors_added}</dd>
            </div>
          </dl>
          {stats.truncated && (
            <p className="warn">
              {stats.matched} nodes matched and the limit drew fewer. Raise the
              limit or narrow the filters — this is a partial view.
            </p>
          )}
        </section>
      )}

      <footer className="panel-foot">
        <button onClick={onRefresh} disabled={loading}>
          {loading ? "Loading…" : "Refresh"}
        </button>
      </footer>
    </aside>
  );
}

function Mark({ color }) {
  return <span className="mark" style={{ background: color }} />;
}

function LegendLine({ kind }) {
  return <span className={`legend-line legend-${kind}`} />;
}
