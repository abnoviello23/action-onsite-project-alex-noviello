/**
 * Edges whose geometry comes from node rectangles, not from handle DOM.
 *
 * React Flow's built-in edges anchor to `<Handle>` elements, whose positions it
 * discovers by measuring the DOM. That measurement is asynchronous and, for a
 * graph laid out entirely up front, buys nothing: every block is a known size
 * at a known position, so both endpoints are computable the moment the layout
 * is. Deriving them here also means an edge never waits on a browser
 * observation to appear.
 *
 * The two kinds attach differently, which is most of what makes the picture
 * readable:
 *
 *   tree     — a branch to a container child: straight down out of the parent,
 *              across a shared horizontal bus, straight down into the child.
 *              Every sibling turns at the same height, so a family reads as one
 *              fork rather than as a handful of unrelated curves.
 *   trunk    — a branch to a stacked leaf: one vertical running down the
 *              parent's left, with a short elbow into each leaf. The vertical
 *              is shared by the whole stack, which is what makes a folder and
 *              its files read as one thing.
 *   relation — nearest vertical sides, so an annotation crossing between trees
 *              leaves and arrives horizontally instead of cutting through the
 *              blocks it passes.
 */

import {
  BaseEdge,
  EdgeLabelRenderer,
  Position,
  getSmoothStepPath,
  useInternalNode,
} from "@xyflow/react";

// Where the horizontal bus sits between the two generations: just above the
// child, so the vertical drop belongs visibly to the child it feeds.
const BUS_OFFSET = 26;
const CORNER = 9;

function rect(node) {
  const { x, y } = node.internals.positionAbsolute;
  const width = node.measured?.width ?? node.width ?? 0;
  const height = node.measured?.height ?? node.height ?? 0;
  return { x, y, width, height, cx: x + width / 2, cy: y + height / 2 };
}

function TreeEdge({ id, source, target, style }) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;

  const s = rect(sourceNode);
  const t = rect(targetNode);

  const startY = s.y + s.height;
  const endY = t.y;
  const busY = endY - BUS_OFFSET;

  // Directly beneath the parent: one straight drop, no corners to round.
  if (Math.abs(t.cx - s.cx) < 1) {
    return <BaseEdge id={id} path={`M ${s.cx} ${startY} L ${t.cx} ${endY}`} style={style} />;
  }

  const right = t.cx > s.cx;
  const r = Math.min(CORNER, Math.abs(t.cx - s.cx) / 2, Math.abs(busY - startY) / 2);
  const sweepIn = right ? r : -r;

  const path = [
    `M ${s.cx} ${startY}`,
    `L ${s.cx} ${busY - r}`,
    `Q ${s.cx} ${busY} ${s.cx + sweepIn} ${busY}`,
    `L ${t.cx - sweepIn} ${busY}`,
    `Q ${t.cx} ${busY} ${t.cx} ${busY + r}`,
    `L ${t.cx} ${endY}`,
  ].join(" ");

  return <BaseEdge id={id} path={path} style={style} />;
}

function LinkEdge({ id, source, target, style, label, labelStyle, data }) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;

  const s = rect(sourceNode);
  const t = rect(targetNode);
  // Leave from whichever side faces the target, so an edge never starts by
  // doubling back across its own block.
  const rightward = t.cx >= s.cx;

  const [path, labelX, labelY] = getSmoothStepPath({
    sourceX: rightward ? s.x + s.width : s.x,
    sourceY: s.cy,
    sourcePosition: rightward ? Position.Right : Position.Left,
    targetX: rightward ? t.x : t.x + t.width,
    targetY: t.cy,
    targetPosition: rightward ? Position.Left : Position.Right,
    borderRadius: 14,
  });

  return (
    <>
      <BaseEdge id={id} path={path} style={style} markerEnd={data?.markerEnd} />
      {label && (
        <EdgeLabelRenderer>
          <div
            className="edge-label"
            style={{
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              opacity: style?.opacity,
              ...labelStyle,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}

/** Parent to a leaf stacked beneath it. */
function TrunkEdge({ id, source, target, style }) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;

  const s = rect(sourceNode);
  const t = rect(targetNode);

  // Down out of the parent, a short jog across to the trunk that serves this
  // leaf's column, then down it. A stack that wrapped into a second column gets
  // its own trunk, and the jog is what feeds it.
  const busY = s.y + s.height + 16;
  const trunk = t.x - 13;
  const endY = t.cy;
  const jog = Math.min(CORNER, Math.abs(trunk - s.cx) / 2);
  const drop = Math.min(CORNER, Math.abs(endY - busY) / 2);
  const dir = trunk >= s.cx ? 1 : -1;

  const path = [
    `M ${s.cx} ${s.y + s.height}`,
    `L ${s.cx} ${busY - jog}`,
    `Q ${s.cx} ${busY} ${s.cx + dir * jog} ${busY}`,
    `L ${trunk - dir * jog} ${busY}`,
    `Q ${trunk} ${busY} ${trunk} ${busY + drop}`,
    `L ${trunk} ${endY - drop}`,
    `Q ${trunk} ${endY} ${trunk + drop} ${endY}`,
    `L ${t.x} ${endY}`,
  ].join(" ");

  return <BaseEdge id={id} path={path} style={style} />;
}

export const edgeTypes = { tree: TreeEdge, trunk: TrunkEdge, link: LinkEdge };
