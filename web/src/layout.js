/**
 * Tree layout: containers branch sideways, leaves hang beneath their parent.
 *
 * The graph has a spine and it has annotations, and only the spine decides
 * where anything sits. The spine is `permission_parent_id` — the tree access
 * inherits along, which is also how every source organizes itself (drive >
 * folder > file, channel > message, database > data source > page).
 * Annotations — `next` between consecutive messages, a mention across
 * sources — are drawn but never allowed to move anything, because a link
 * between two leaves pulling on the layout is what turns a tree into a
 * hairball.
 *
 * Why the split between containers and leaves. A strict tidy tree gives every
 * leaf its own column, and these trees are overwhelmingly leaves: two hundred
 * nodes came out forty-eight thousand pixels wide, which is a picture of dashes
 * at any zoom that fits it. But the *branching* — which folder holds which
 * folder, which database holds which data source — is the part worth seeing as
 * a shape, and there are only ever a few dozen of those.
 *
 * So containers fan out horizontally, each claiming its own column of space,
 * and a container's leaf children stack vertically underneath it off a trunk.
 * Width then scales with the number of containers rather than the number of
 * files, and the branching structure stays visible while the contents stay
 * attached to what contains them.
 *
 * Nothing here runs on a frame loop. Positions are computed when the data or
 * the collapse set changes, and then they hold.
 */

export const NODE_WIDTH = 208;
export const NODE_HEIGHT = 58;

// A stacked leaf is inset under its parent and shares its right edge, so the
// stack reads as belonging to the block above it.
const LEAF_INDENT = 26;
export const LEAF_WIDTH = NODE_WIDTH - LEAF_INDENT;

// Pitch down a leaf stack, and the drop from one generation to the next.
const LEAF_STEP = NODE_HEIGHT + 12;
const V_STEP = NODE_HEIGHT + 62;

// A stack longer than this wraps into another column. One data source with
// forty pages under it is otherwise a single ribbon three thousand pixels tall,
// which drags the whole forest out of shape for one node's contents.
const STACK_MAX = 12;
const STACK_GAP = 18;

// Between one container subtree and the next.
const SIBLING_GAP = 34;
const ROOT_GAP = 64;
const SOURCE_GAP = 150;

// Room above a cluster for its source label. Generous, because at overview zoom
// that label is counter-scaled to stay readable and needs somewhere to grow.
export const LABEL_HEIGHT = 72;

/**
 * Stable, meaningful sibling order: containers first, then oldest first, then
 * by name. Deterministic, so the same data always draws the same way.
 */
function compareNodes(a, b) {
  if ((b.child_count > 0) !== (a.child_count > 0)) {
    return b.child_count - a.child_count;
  }
  const at = a.created_at ?? a.updated_at ?? "";
  const bt = b.created_at ?? b.updated_at ?? "";
  if (at !== bt) return at < bt ? -1 : 1;
  return a.label.localeCompare(b.label);
}

/**
 * @param graph      the /graph response
 * @param collapsed  Set of node ids whose children are hidden
 */
export function layout(graph, collapsed = new Set()) {
  const byId = new Map(graph.nodes.map((n) => [n.id, n]));

  const childrenOf = new Map();
  const roots = [];
  for (const node of graph.nodes) {
    // A node whose parent was not returned — filtered out, or past the limit —
    // is a root as far as this view is concerned. Dropping it instead would
    // silently lose whole subtrees.
    if (node.parent_id && byId.has(node.parent_id)) {
      if (!childrenOf.has(node.parent_id)) childrenOf.set(node.parent_id, []);
      childrenOf.get(node.parent_id).push(node);
    } else {
      roots.push(node);
    }
  }
  for (const list of childrenOf.values()) list.sort(compareNodes);
  roots.sort(compareNodes);

  const bySource = new Map();
  for (const root of roots) {
    if (!bySource.has(root.source)) bySource.set(root.source, []);
    bySource.get(root.source).push(root);
  }
  const sources = [...bySource.keys()].sort();

  const positions = new Map();
  const stacked = new Set();
  const visible = [];
  let hiddenCount = 0;

  const clusters = [];
  let clusterTop = 0;

  for (const source of sources) {
    // --- lay each root tree out in its own space ------------------------
    const trees = bySource.get(source).map((root) => {
      const local = new Map();
      let bottom = 0;
      let right = 0;

      const place = (node, depth, left) => {
        visible.push(node);
        const y = depth * V_STEP;
        local.set(node.id, { x: left, y });
        bottom = Math.max(bottom, y + NODE_HEIGHT);
        right = Math.max(right, left + NODE_WIDTH);

        if (collapsed.has(node.id)) {
          hiddenCount += countSubtree(node, childrenOf);
          return NODE_WIDTH;
        }

        const children = childrenOf.get(node.id) ?? [];
        const containers = children.filter(
          (c) => (childrenOf.get(c.id) ?? []).length
        );
        const leaves = children.filter(
          (c) => !(childrenOf.get(c.id) ?? []).length
        );

        // Leaves stack under the parent, wrapping into further columns once a
        // column is full.
        leaves.forEach((leaf, i) => {
          visible.push(leaf);
          stacked.add(leaf.id);
          const column = Math.floor(i / STACK_MAX);
          const row = i % STACK_MAX;
          const ly = y + V_STEP + row * LEAF_STEP;
          local.set(leaf.id, {
            x: left + LEAF_INDENT + column * (LEAF_WIDTH + STACK_GAP),
            y: ly,
          });
          bottom = Math.max(bottom, ly + NODE_HEIGHT);
        });

        const stackColumns = Math.ceil(leaves.length / STACK_MAX);
        const ownWidth = Math.max(
          NODE_WIDTH,
          stackColumns
            ? LEAF_INDENT +
              stackColumns * LEAF_WIDTH +
              (stackColumns - 1) * STACK_GAP
            : 0
        );
        right = Math.max(right, left + ownWidth);

        // Containers branch to the right of that column, each claiming its own.
        let childLeft = left + ownWidth + (containers.length ? SIBLING_GAP : 0);
        for (const container of containers) {
          childLeft += place(container, depth + 1, childLeft) + SIBLING_GAP;
        }

        const claimed = containers.length
          ? childLeft - SIBLING_GAP - left
          : ownWidth;
        return Math.max(ownWidth, claimed);
      };

      const width = place(root, 0, 0);
      return { local, width: Math.max(width, right), height: bottom };
    });

    // --- pack those trees into rows -------------------------------------
    //
    // Laid end to end, a source's trees run tens of thousands of pixels wide
    // and a few thousand tall, which frames as a thin band with the whole
    // viewport wasted above and below it. Wrapping at a target width close to
    // the forest's own square root keeps the group roughly square, so fitting
    // it actually fills the screen.
    const area = trees.reduce((sum, t) => sum + t.width * t.height, 0);
    const target = Math.max(Math.sqrt(area) * 1.6, NODE_WIDTH * 4);

    let rowX = 0;
    let rowTop = clusterTop + LABEL_HEIGHT;
    let rowHeight = 0;
    let clusterRight = 0;

    for (const tree of trees) {
      if (rowX > 0 && rowX + tree.width > target) {
        rowX = 0;
        rowTop += rowHeight + ROOT_GAP;
        rowHeight = 0;
      }
      for (const [id, p] of tree.local) {
        positions.set(id, { x: rowX + p.x, y: rowTop + p.y });
      }
      rowX += tree.width + ROOT_GAP;
      rowHeight = Math.max(rowHeight, tree.height);
      clusterRight = Math.max(clusterRight, rowX - ROOT_GAP);
    }

    const bottom = rowTop + rowHeight;
    clusters.push({
      id: `cluster:${source}`,
      source,
      x: 0,
      y: clusterTop,
      width: Math.max(clusterRight, NODE_WIDTH),
      height: bottom - clusterTop,
      count: visible.filter((n) => n.source === source).length,
    });

    // Sources stack down the page rather than side by side: a row of tall
    // columns is the shape that made this look like strips.
    clusterTop = bottom + SOURCE_GAP;
  }

  return { positions, clusters, visible, stacked, hiddenCount };
}

function countSubtree(node, childrenOf) {
  const children = childrenOf.get(node.id) ?? [];
  let total = children.length;
  for (const child of children) total += countSubtree(child, childrenOf);
  return total;
}
