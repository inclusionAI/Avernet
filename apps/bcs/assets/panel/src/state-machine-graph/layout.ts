import type { StateMachineNode, StateMachineEdge } from '../StateMachineRunView';

export interface LayoutNode {
  node: StateMachineNode;
  x: number;
  y: number;
  width: number;
  height: number;
}

export interface GraphLayout {
  groups?: LoopGroupLayout[];
  nodes: LayoutNode[];
  edges: Array<{
    edge: StateMachineEdge;
    source: LayoutNode;
    target: LayoutNode;
    evidence?: StateMachineEdge;
    stateOverride?: 'pending' | 'skipped';
    returnX?: number;
    label?: string;
  }>;
  width: number;
  height: number;
}

export interface LoopGroupLayout {
  loopId: string;
  title: string;
  maxIterations: number;
  iteration: number;
  x: number;
  y: number;
  width: number;
  height: number;
}

export const NODE_WIDTH = 188;
export const NODE_HEIGHT = 58;
export const LEVEL_GAP = 56;
export const COLUMN_GAP = 18;
export const PADDING = 28;

export function buildGraphLayout(
  nodes: StateMachineNode[],
  edges: StateMachineEdge[],
  initialNodes: string[] = [],
): GraphLayout {
  const nodeWidth = nodes.some((node) => node.execution) ? 240 : NODE_WIDTH;
  const nodeById = new Map(nodes.map((node) => [node.node_id, node]));
  const incomingCount = new Map(nodes.map((node) => [node.node_id, 0]));

  edges.forEach((edge) => {
    incomingCount.set(edge.target, (incomingCount.get(edge.target) || 0) + 1);
  });

  const rootIds = initialNodes.filter((nodeId) => nodeById.has(nodeId));
  const fallbackRoots = nodes
    .filter((node) => (incomingCount.get(node.node_id) || 0) === 0)
    .map((node) => node.node_id);
  const startIds = rootIds.length > 0 ? rootIds : fallbackRoots;
  const levels = new Map(nodes.map((node) => [node.node_id, 0]));

  startIds.forEach((nodeId) => levels.set(nodeId, 0));

  for (let index = 0; index < nodes.length + edges.length; index += 1) {
    edges.forEach((edge) => {
      const sourceLevel = levels.get(edge.source);
      const targetLevel = levels.get(edge.target);

      if (sourceLevel === undefined || targetLevel === undefined) {
        return;
      }

      levels.set(edge.target, Math.max(targetLevel, sourceLevel + 1));
    });
  }

  const groups = new Map<number, StateMachineNode[]>();

  nodes.forEach((node) => {
    const level = levels.get(node.node_id) || 0;
    const group = groups.get(level) || [];

    group.push(node);
    groups.set(level, group);
  });

  const sortedGroups = Array.from(groups.entries()).sort(([a], [b]) => a - b);
  const maxColumns = Math.max(
    1,
    ...sortedGroups.map(([, group]) => group.length),
  );
  const width =
    PADDING * 2 + (nodes.some((node) => node.execution) ? 320 : 0) +
    maxColumns * nodeWidth +
    Math.max(0, maxColumns - 1) * COLUMN_GAP;
  const height =
    PADDING * 2 +
    sortedGroups.length * NODE_HEIGHT +
    Math.max(0, sortedGroups.length - 1) * LEVEL_GAP;
  const layoutNodes: LayoutNode[] = [];

  sortedGroups.forEach(([, group], levelIndex) => {
    const rowWidth =
      group.length * nodeWidth + Math.max(0, group.length - 1) * COLUMN_GAP;
    const rowOffset = Math.max(0, (width - PADDING * 2 - rowWidth) / 2);
    const y = PADDING + levelIndex * (NODE_HEIGHT + LEVEL_GAP);

    group.forEach((node, row) => {
      layoutNodes.push({
        node,
        x: PADDING + rowOffset + row * (nodeWidth + COLUMN_GAP),
        y,
        width: nodeWidth,
        height: NODE_HEIGHT,
      });
    });
  });

  const layoutById = new Map(
    layoutNodes.map((layoutNode) => [layoutNode.node.node_id, layoutNode]),
  );
  const layoutEdges = edges
    .map((edge) => {
      const source = layoutById.get(edge.source);
      const target = layoutById.get(edge.target);

      if (!source || !target) {
        return null;
      }

      return { edge, source, target };
    })
    .filter((edge): edge is NonNullable<typeof edge> => Boolean(edge));

  return {
    nodes: layoutNodes,
    edges: layoutEdges,
    width,
    height,
  };
}
