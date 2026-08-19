/**
 * The two node kinds React Flow renders: a source label, and a block.
 *
 * Both are plain DOM rather than canvas drawing. That is the point — a block
 * can show its type, its name, its counts and a collapse control as ordinary
 * elements, with real text rendering and real hit targets, instead of a dot
 * with a caption floating next to it.
 *
 * How much of that a block shows depends on the zoom, and it is CSS that
 * decides: the wrapper carries a level-of-detail class and the rules hide the
 * parts that have stopped being legible. Doing it in CSS rather than in props
 * means zooming does not re-render four hundred components.
 */

/** A source's name, sitting above its trees. No container, no band. */
export function ClusterLabel({ data }) {
  return (
    <div className="cluster-label" style={{ "--accent": data.color }}>
      <span className="cluster-mark" />
      <span className="cluster-name">{data.source}</span>
      <span className="cluster-count">{data.count}</span>
    </div>
  );
}

/** One graph node. */
export function BlockNode({ data, selected }) {
  const { node, color, collapsed, hiddenChildren, onToggleCollapse, focus } =
    data;

  const classes = [
    "block",
    selected && "is-selected",
    // focus is null when nothing is selected, so an unselected canvas carries
    // none of these and every block renders in its plain state.
    focus === "selected" && "is-focus-primary",
    focus === "related" && "is-focus-near",
    focus === "dim" && "is-dimmed",
    node.deleted && "is-deleted",
    !node.materialized && "is-unmaterialized",
    node.child_count > 0 && "is-container",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes} style={{ "--accent": color }}>
      <div className="block-head">
        <span className="block-type">{node.node_type ?? "unmaterialized"}</span>
        {node.grant_count > 0 && (
          <span
            className="block-grants"
            title={`${node.grant_count} access grants on this node`}
          >
            {node.grant_count} ⚿
          </span>
        )}
      </div>

      <div className="block-label" title={node.label}>
        {node.label}
      </div>

      {node.child_count > 0 && (
        <button
          className="block-toggle"
          title={
            collapsed
              ? `Expand ${node.child_count} children`
              : `Collapse ${node.child_count} children`
          }
          onClick={(event) => {
            // Stops React Flow from also treating this as a node selection.
            event.stopPropagation();
            onToggleCollapse(node.id);
          }}
        >
          {collapsed ? `+${hiddenChildren}` : "–"}
        </button>
      )}
    </div>
  );
}

export const nodeTypes = { cluster: ClusterLabel, block: BlockNode };
