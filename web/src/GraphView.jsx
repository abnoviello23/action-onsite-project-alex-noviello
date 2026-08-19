import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Background,
  BackgroundVariant,
  Controls as FlowControls,
  MiniMap,
  Panel,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  useStore,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import {
  LABEL_HEIGHT,
  LEAF_WIDTH,
  NODE_HEIGHT,
  NODE_WIDTH,
  layout,
} from "./layout.js";
import { edgeTypes } from "./edges.jsx";
import { nodeTypes } from "./nodes.jsx";

/**
 * The graph, as a set of trees.
 *
 * Everything is computed from the data plus the collapse set, and then it sits
 * still. There is no simulation, no animation, and no re-layout on hover or
 * selection — the only things that move a node are a drag and a change to the
 * data or the collapse set.
 */

// Containment is the spine and is drawn as such. The rest are annotations and
// stay visually subordinate, which is the same distinction the API makes with
// kind=permission vs kind=relation.
const EDGE_STYLES = {
  permission: (color) => ({
    type: "tree",
    style: { stroke: color, strokeWidth: 1.5 },
  }),
  relation: (color) => ({
    type: "link",
    style: { stroke: color, strokeWidth: 1.3, strokeDasharray: "5 4" },
  }),
};

// How a selected node's own edges are drawn. Thick enough to follow across the
// canvas at overview zoom, since with everything else gone there is nothing for
// them to compete with.
const FOCUS_STROKE = 4;

// Handles declared as data rather than as DOM.
//
// React Flow will not route an edge until it knows where that edge attaches,
// and normally it learns that by measuring `<Handle>` elements once the browser
// delivers a resize observation. Every block here is a fixed size laid out
// ahead of time, so the attachment points are known before anything renders —
// declaring them means edges are drawn on the first paint rather than a frame
// or two later, and never depend on an observation arriving at all.
function handlesFor(width) {
  return [
    { id: null, type: "target", position: Position.Left, x: 0, y: NODE_HEIGHT / 2 },
    {
      id: null,
      type: "source",
      position: Position.Bottom,
      x: width / 2,
      y: NODE_HEIGHT,
    },
  ];
}

const BRANCH_HANDLES = handlesFor(NODE_WIDTH);
const LEAF_HANDLES = handlesFor(LEAF_WIDTH);

// Breathing room between the graph and the edge of the stage, in screen pixels.
const PAD = 32;

// A floor on the initial framing, low enough that the whole forest still fits
// at the sizes this draws, high enough that a pathological graph does not
// vanish into a smear. Level of detail keeps the marks readable down here.
const MIN_FIT_ZOOM = 0.05;

/**
 * Level of detail.
 *
 * A whole forest on screen at once means blocks a few pixels wide, where text
 * is noise rather than information. The wrapper carries one of these classes
 * and the stylesheet decides what survives at that size — bucketed so that
 * zooming re-renders one element rather than every node.
 */
function detailLevel(zoom) {
  if (zoom < 0.28) return "far";
  if (zoom < 0.62) return "mid";
  return "near";
}

function Flow({
  graph,
  theme,
  colors,
  selectedId,
  onSelect,
  collapsed,
  onToggleCollapse,
  onCounts,
  onExpandAll,
  onCollapseAll,
}) {
  // getNodesBounds from the hook, not the bare export, so positions resolve
  // through the node lookup.
  const { setViewport, getViewport, getInternalNode, getNodesBounds } =
    useReactFlow();
  const size = useStore((s) => ({ width: s.width, height: s.height }));
  const lod = useStore((s) => detailLevel(s.transform[2]));

  const { nodes, edges, hiddenCount } = useMemo(() => {
    const { positions, clusters, visible, stacked, hiddenCount } = layout(
      graph,
      collapsed
    );
    const visibleIds = new Set(visible.map((n) => n.id));
    const byId = new Map(graph.nodes.map((n) => [n.id, n]));

    // Everything one hop from the selection. Selecting turns the canvas into a
    // view of that node's own connections: these come forward, every other edge
    // goes away entirely, and the rest of the graph stays as recessive context
    // so the shape is still there to put it in.
    const selectedNode = graph.nodes.find((n) => n.entity_id === selectedId);
    const related = selectedNode ? new Set([selectedNode.id]) : null;
    if (related) {
      if (selectedNode.parent_id) related.add(selectedNode.parent_id);
      for (const e of graph.edges) {
        if (e.from_id === selectedNode.id) related.add(e.to_id);
        if (e.to_id === selectedNode.id) related.add(e.from_id);
      }
      for (const n of graph.nodes) {
        if (n.parent_id === selectedNode.id) related.add(n.id);
      }
    }

    const labelNodes = clusters.map((cluster) => ({
      id: cluster.id,
      type: "cluster",
      position: { x: cluster.x, y: cluster.y },
      selectable: false,
      draggable: false,
      initialWidth: cluster.width,
      initialHeight: LABEL_HEIGHT,
      measured: { width: cluster.width, height: LABEL_HEIGHT },
      handles: [],
      zIndex: 0,
      data: {
        source: cluster.source,
        count: cluster.count,
        color: colors[cluster.source] ?? colors.unknown,
      },
    }));

    const blockNodes = visible.map((node) => {
      const childrenHidden = graph.nodes.filter(
        (n) => n.parent_id === node.id
      ).length;
      // A stacked leaf is inset under its parent, so it is narrower by exactly
      // that inset and the stack keeps a flush right edge.
      const isLeaf = stacked.has(node.id);
      const width = isLeaf ? LEAF_WIDTH : NODE_WIDTH;
      return {
        id: node.id,
        type: "block",
        position: positions.get(node.id),
        selected: node.entity_id === selectedId,
        initialWidth: width,
        initialHeight: NODE_HEIGHT,
        measured: { width, height: NODE_HEIGHT },
        handles: isLeaf ? LEAF_HANDLES : BRANCH_HANDLES,
        style: { width, height: NODE_HEIGHT },
        zIndex: 1,
        data: {
          node,
          color: colors[node.source] ?? colors.unknown,
          collapsed: collapsed.has(node.id),
          hiddenChildren: childrenHidden,
          // One of: nothing selected, this is the selection, this is one hop
          // from it, or it is context.
          focus: !related
            ? null
            : node.entity_id === selectedId
              ? "selected"
              : related.has(node.id)
                ? "related"
                : "dim",
          onToggleCollapse,
        },
      };
    });

    const flowEdges = [];
    const seen = new Set();

    for (const edge of graph.edges) {
      if (!visibleIds.has(edge.from_id) || !visibleIds.has(edge.to_id)) continue;
      const key = `${edge.from_id}->${edge.to_id}:${edge.relation}`;
      if (seen.has(key)) continue;
      seen.add(key);

      // With a selection, only that node's own edges are drawn. Dimming the
      // other nine hundred still leaves nine hundred lines on the canvas; the
      // point of clicking a node is to see what it is attached to.
      const incident =
        selectedNode &&
        (edge.from_id === selectedNode.id || edge.to_id === selectedNode.id);
      if (selectedNode && !incident) continue;

      const permission = edge.kind === "permission";
      // The child end decides the connector: a stacked leaf hangs off a trunk,
      // a container branches on a bus.
      const child = permission ? edge.from_id : null;
      // In focus the containment edge takes the child's own source colour
      // rather than the recessive grey it wears in the full picture.
      const color = incident
        ? colors[byId.get(child ?? edge.from_id)?.source] ?? theme.accent
        : permission
          ? theme.edgePermission
          : colors[byId.get(edge.from_id)?.source] ?? theme.edgeRelation;

      const style = EDGE_STYLES[edge.kind](color);
      if (permission && stacked.has(child)) style.type = "trunk";
      flowEdges.push({
        id: key,
        // Drawn parent -> child so the tree reads downward, even though the
        // permission edge itself is stored on the child.
        source: permission ? edge.to_id : edge.from_id,
        target: permission ? edge.from_id : edge.to_id,
        ...style,
        style: {
          ...style.style,
          strokeWidth: incident ? FOCUS_STROKE : style.style.strokeWidth,
          strokeDasharray: incident && !permission ? "10 6" : style.style.strokeDasharray,
        },
        label: permission ? undefined : edge.relation,
        labelStyle: incident
          ? { color: theme.textPrimary, background: theme.surface, fontWeight: 600 }
          : { color: theme.textMuted, background: theme.surface },
        zIndex: incident ? 4 : 2,
      });
    }

    return {
      nodes: [...labelNodes, ...blockNodes],
      edges: flowEdges,
      hiddenCount,
    };
  }, [graph, collapsed, colors, theme, selectedId, onToggleCollapse]);

  useEffect(() => {
    onCounts?.({
      drawn: nodes.filter((n) => n.type === "block").length,
      hiddenCount,
    });
  }, [nodes, hiddenCount, onCounts]);

  /**
   * Frame the graph.
   *
   * Computed and applied directly rather than through fitView(), which in v12
   * only queues the request and resolves it on the next nodes update — with a
   * layout that settles into a stable node array, that update never comes and
   * the queued fit sits there forever.
   *
   * Fits the whole forest, because the shape of the thing is the first fact
   * worth showing; the level-of-detail rules keep it legible as shapes at that
   * size, and scrolling in restores the text.
   */
  const fit = useCallback(
    (target) => {
      if (!size.width || !size.height || !target.length) return;
      const bounds = getNodesBounds(target);
      const ideal = Math.min(
        1,
        (size.width - PAD * 2) / bounds.width,
        (size.height - PAD * 2) / bounds.height
      );
      const zoom = Math.max(MIN_FIT_ZOOM, ideal);

      // When the whole thing fits, centre it. When it does not, pin the top-left
      // so the first thing on screen is the roots rather than the middle of a
      // subtree.
      const fits = ideal >= MIN_FIT_ZOOM;
      setViewport({
        zoom,
        x: fits
          ? size.width / 2 - (bounds.x + bounds.width / 2) * zoom
          : PAD - bounds.x * zoom,
        y: fits
          ? size.height / 2 - (bounds.y + bounds.height / 2) * zoom
          : PAD - bounds.y * zoom,
      });
    },
    [setViewport, getNodesBounds, size.width, size.height]
  );

  // Deliberately keyed on the data alone. Expanding a subtree does not refit:
  // having the viewport jump because you opened a folder is the behaviour this
  // is meant to avoid, and Fit is one click away.
  useEffect(() => {
    fit(nodes);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [graph, size.width, size.height]);

  /**
   * Bring the selection into view, but only when it is not already there.
   *
   * Following a link in the detail panel routinely lands on a node in another
   * tree or far off screen, and leaving the viewport where it was means the
   * panel describes something invisible. Clicking a block you can already see
   * never moves anything, and the zoom is never touched.
   */
  useEffect(() => {
    if (!selectedId || !size.width) return;
    const node = graph.nodes.find((n) => n.entity_id === selectedId);
    const internal = node && getInternalNode(node.id);
    if (!internal) return;

    const { x, y } = internal.internals.positionAbsolute;
    const vp = getViewport();
    const left = x * vp.zoom + vp.x;
    const top = y * vp.zoom + vp.y;
    const margin = 32;
    const onScreen =
      left > margin &&
      top > margin &&
      left + NODE_WIDTH * vp.zoom < size.width - margin &&
      top + NODE_HEIGHT * vp.zoom < size.height - margin;
    if (onScreen) return;

    setViewport({
      zoom: vp.zoom,
      x: size.width / 2 - (x + NODE_WIDTH / 2) * vp.zoom,
      y: size.height / 2 - (y + NODE_HEIGHT / 2) * vp.zoom,
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const onNodeClick = useCallback(
    (_event, node) => {
      if (node.type !== "block") return;
      onSelect(node.data.node.entity_id);
    },
    [onSelect]
  );

  return (
    // React Flow measures its parent, so the parent needs a definite size. The
    // stage is a grid cell whose height comes from the row, which is not a
    // definite height until layout has run — hence an absolutely positioned
    // wrapper rather than relying on percentage inheritance.
    <div className={`flow-wrap lod-${lod}`}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodeClick={onNodeClick}
        onPaneClick={() => onSelect(null)}
        minZoom={0.02}
        maxZoom={2.5}
        nodesConnectable={false}
        elevateNodesOnSelect={false}
        proOptions={{ hideAttribution: true }}
        colorMode={theme.surface === "#fcfcfb" ? "light" : "dark"}
      >
        <Panel position="top-right" className="flow-panel">
          <button className="ghost" onClick={() => fit(nodes)}>
            Fit
          </button>
          <button className="ghost" onClick={onExpandAll}>
            Expand all
          </button>
          <button className="ghost" onClick={onCollapseAll}>
            Collapse all
          </button>
        </Panel>
        <Background variant={BackgroundVariant.Dots} gap={26} size={1} />
        <FlowControls showInteractive={false} />
        <MiniMap
          pannable
          zoomable
          nodeColor={(n) =>
            n.type === "cluster" ? "transparent" : n.data.color ?? "#888"
          }
          maskColor="rgba(0,0,0,0.12)"
        />
      </ReactFlow>
    </div>
  );
}

export default function GraphView(props) {
  // Expanded is the default: the whole tree is the picture, and hiding parts of
  // it by default means the first thing anyone sees is not the graph.
  const [collapsed, setCollapsed] = useState(() => new Set());

  // Dropped when the node set changes, so collapse state never outlives the
  // nodes it referred to.
  useEffect(() => setCollapsed(new Set()), [props.graph]);

  const expandAll = useCallback(() => setCollapsed(new Set()), []);

  const collapseAll = useCallback(
    () =>
      setCollapsed(
        new Set(
          props.graph.nodes.filter((n) => n.child_count > 0).map((n) => n.id)
        )
      ),
    [props.graph]
  );

  const onToggleCollapse = useCallback((id) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  return (
    <ReactFlowProvider>
      <Flow
        {...props}
        collapsed={collapsed}
        onToggleCollapse={onToggleCollapse}
        onExpandAll={expandAll}
        onCollapseAll={collapseAll}
      />
    </ReactFlowProvider>
  );
}
