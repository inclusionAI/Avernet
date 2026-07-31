/**
 * Task workflow panel — graph layout.
 * Ported from bcsPanel.StateMachineRunView buildGraphLayout:2342.
 * Topological-level layout (no dagre). Entry nodes = not present as to_node in
 * any edge (the task graph has no definition.initial_nodes, unlike SM).
 */
import {
  COLUMN_GAP,
  LEVEL_GAP,
  NODE_HEIGHT,
  NODE_WIDTH,
  PADDING,
} from '../constants';
import type { TaskEdgeView, TaskNodeView } from '../types';

export interface LayoutNode {
  node: TaskNodeView;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface LayoutEdge {
  edge: TaskEdgeView;
  source: LayoutNode;
  target: LayoutNode;
}

export interface GraphLayout {
  nodes: LayoutNode[];
  edges: LayoutEdge[];
  width: number;
  height: number;
}

export function buildGraphLayout(
  nodes: TaskNodeView[],
  edges: TaskEdgeView[],
): GraphLayout {
  const incomingCount = new Map(nodes.map((n) => [n.node_id, 0]));

  edges.forEach((e) => {
    incomingCount.set(
      e.to_node,
      (incomingCount.get(e.to_node) || 0) + 1,
    );
  });

  const startIds = nodes
    .filter((n) => (incomingCount.get(n.node_id) || 0) === 0)
    .map((n) => n.node_id);
  const levels = new Map(nodes.map((n) => [n.node_id, 0]));

  startIds.forEach((id) => levels.set(id, 0));

  // Edge-relaxation to assign topological levels (bounded iterations).
  for (let i = 0; i < nodes.length + edges.length; i += 1) {
    edges.forEach((e) => {
      const sourceLevel = levels.get(e.from_node);
      const targetLevel = levels.get(e.to_node);
      if (sourceLevel === undefined || targetLevel === undefined) return;
      levels.set(e.to_node, Math.max(targetLevel, sourceLevel + 1));
    });
  }

  const groups = new Map<number, TaskNodeView[]>();
  nodes.forEach((n) => {
    const level = levels.get(n.node_id) || 0;
    const group = groups.get(level) || [];
    group.push(n);
    groups.set(level, group);
  });

  const sortedGroups = Array.from(groups.entries()).sort(([a], [b]) => a - b);
  const maxColumns = Math.max(
    1,
    ...sortedGroups.map(([, g]) => g.length),
  );
  const width =
    PADDING * 2 +
    maxColumns * NODE_WIDTH +
    Math.max(0, maxColumns - 1) * COLUMN_GAP;
  const height =
    PADDING * 2 +
    sortedGroups.length * NODE_HEIGHT +
    Math.max(0, sortedGroups.length - 1) * LEVEL_GAP;

  const layoutNodes: LayoutNode[] = [];
  sortedGroups.forEach(([, group], levelIndex) => {
    const rowWidth =
      group.length * NODE_WIDTH + Math.max(0, group.length - 1) * COLUMN_GAP;
    const rowOffset = Math.max(0, (width - PADDING * 2 - rowWidth) / 2);
    const y = PADDING + levelIndex * (NODE_HEIGHT + LEVEL_GAP);
    group.forEach((node, row) => {
      layoutNodes.push({
        node,
        x: PADDING + rowOffset + row * (NODE_WIDTH + COLUMN_GAP),
        y,
        width: NODE_WIDTH,
        height: NODE_HEIGHT,
      });
    });
  });

  const layoutById = new Map(layoutNodes.map((ln) => [ln.node.node_id, ln]));
  const layoutEdges: LayoutEdge[] = [];
  edges.forEach((edge) => {
    const source = layoutById.get(edge.from_node);
    const target = layoutById.get(edge.to_node);
    if (source && target) {
      layoutEdges.push({ edge, source, target });
    }
  });

  return { nodes: layoutNodes, edges: layoutEdges, width, height };
}
