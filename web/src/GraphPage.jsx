import { useCallback, useEffect, useMemo, useState } from "react";
import Controls from "./Controls.jsx";
import DetailPanel from "./DetailPanel.jsx";
import GraphView from "./GraphView.jsx";
import { fetchGraph, fetchMeta, fetchNode } from "./api.js";
import { sourceColors, tokens } from "./theme.js";

// Typing in the search box should not fire a query per keystroke against a
// payload::text ILIKE.
const SEARCH_DEBOUNCE_MS = 300;

const INITIAL_FILTERS = {
  // Empty means "everything", which is also what the API means by an absent
  // filter — so the default view needs no special case at either end.
  sources: [],
  nodeTypes: [],
  q: "",
  // Everything, by default. 0 is the API's "no limit" — the whole graph is the
  // point of this view, and a cap silently omits whole sources whenever one of
  // them has not been written to lately.
  limit: 0,
  includeDeleted: false,
};

export default function GraphPage({ mode }) {
  const [meta, setMeta] = useState(null);
  const [graph, setGraph] = useState(null);
  const [filters, setFilters] = useState(INITIAL_FILTERS);
  const [debouncedQ, setDebouncedQ] = useState("");
  const [selectedId, setSelectedId] = useState(null);
  const [detail, setDetail] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [reloadToken, setReloadToken] = useState(0);

  const [counts, setCounts] = useState(null);

  // A citation in the chat links here as /?node=<entity_id>. Read once on
  // mount: after that the selection belongs to the canvas, and re-reading the
  // URL would fight the user's clicks.
  useEffect(() => {
    const wanted = new URLSearchParams(window.location.search).get("node");
    if (wanted) setSelectedId(wanted);
  }, []);

  useEffect(() => {
    const id = setTimeout(() => setDebouncedQ(filters.q), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [filters.q]);

  useEffect(() => {
    fetchMeta().then(setMeta).catch((e) => setError(String(e.message ?? e)));
  }, [reloadToken]);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    fetchGraph({
      sources: filters.sources,
      nodeTypes: filters.nodeTypes,
      q: debouncedQ,
      limit: filters.limit,
      includeDeleted: filters.includeDeleted,
    })
      .then((data) => {
        if (cancelled) return;
        setGraph(data);
        setError(null);
      })
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [
    filters.sources,
    filters.nodeTypes,
    filters.limit,
    filters.includeDeleted,
    debouncedQ,
    reloadToken,
  ]);

  useEffect(() => {
    if (!selectedId) {
      setDetail(null);
      return;
    }
    let cancelled = false;
    setDetailLoading(true);
    fetchNode(selectedId)
      .then((d) => !cancelled && setDetail(d))
      .catch((e) => !cancelled && setError(String(e.message ?? e)))
      .finally(() => !cancelled && setDetailLoading(false));
    return () => {
      cancelled = true;
    };
  }, [selectedId]);

  // Colour and shape are assigned from the *catalogue* of sources, not from
  // whatever survived the current filter, so hiding one source cannot repaint
  // the others.
  const allSources = useMemo(
    () => (meta?.sources ?? []).map((s) => s.key),
    [meta]
  );
  const colors = useMemo(() => sourceColors(allSources, mode), [allSources, mode]);
  const theme = useMemo(() => tokens(mode), [mode]);

  const onCounts = useCallback((c) => setCounts(c), []);

  const onSelect = useCallback((entityId) => setSelectedId(entityId), []);

  const empty = graph && graph.nodes.length === 0;

  return (
    <>
      <Controls
        meta={meta}
        filters={filters}
        setFilters={setFilters}
        stats={graph?.stats}
        counts={counts}
        colors={colors}
        loading={loading}
        error={error}
        onRefresh={() => setReloadToken((n) => n + 1)}
      />

      <main className="stage">
        {graph && !empty && (
          <GraphView
            graph={graph}
            theme={theme}
            colors={colors}
            selectedId={selectedId}
            onSelect={onSelect}
            onCounts={onCounts}
          />
        )}

        {empty && (
          <div className="empty">
            <h2>Nothing to draw</h2>
            {meta?.total_nodes === 0 ? (
              <p>
                The <code>node</code> table is empty — nothing has been ingested
                yet. This view draws whatever the pipeline writes, so it fills in
                on its own once the worker starts persisting.
              </p>
            ) : (
              <p>
                {meta?.total_nodes} nodes exist, but none match these filters.
              </p>
            )}
          </div>
        )}

        {!graph && loading && <div className="empty">Loading graph…</div>}

        <div className="hint">
          drag to pan · scroll to zoom · click a block for its data · – under a
          block collapses its subtree
        </div>
      </main>

      <DetailPanel
        detail={detail}
        loading={detailLoading}
        onSelect={onSelect}
        onClose={() => setSelectedId(null)}
      />
    </>
  );
}
