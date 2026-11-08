import type {
  CollaborationDefinitionGraphEdge,
  CollaborationDefinitionGraphNode,
  CollaborationDefinitionGraphPreview,
  CollaborationDefinitionLoopRoute,
} from '@/domain/collaboration/graphTypes';
import type { ParticipantDefinition } from '@/services/workspace/collaborationDefinitionService';

export const COLLABORATION_FLOW_NODE_WIDTH = 210;
export const COLLABORATION_FLOW_NODE_HEIGHT = 84;
const HORIZONTAL_GAP = 56;
const VERTICAL_GAP = 88;

export const COLLABORATION_NODE_KIND_LABELS: Record<CollaborationDefinitionGraphNode['kind'], string> = {
  bot_task: 'Bot 任务',
  group_chat: '群聊',
  human_input: '人工输入',
  tool_action: '工具操作',
  sub_state_machine: '子流程',
};

export type CollaborationNodeTone = 'blue' | 'green' | 'neutral';

export class CollaborationGraphLayoutError extends Error {
  constructor(public readonly code: 'unsupported_mode' | 'invalid_graph', message: string) {
    super(message);
    this.name = 'CollaborationGraphLayoutError';
  }
}

export interface CollaborationBindingView {
  roleName: string;
  botId?: string;
  botName?: string;
}

export interface CollaborationGraphNodeData extends Record<string, unknown> {
  definition: CollaborationDefinitionGraphNode;
  title: string;
  assigneeLabel: string;
  assigneeBinding?: string;
  assigneeBotId?: string;
  assigneeBotName?: string;
  isInitial: boolean;
  logicalLoopView?: boolean;
}

export interface CollaborationGraphLayoutNode {
  parentId?: string;
  id: string;
  position: { x: number; y: number };
  data: CollaborationGraphNodeData;
}

export interface CollaborationGraphLayoutEdge {
  outcome: string;
  sourceHandle?: string;
  targetHandle?: string;
  id: string;
  source: string;
  target: string;
  label?: string;
  data?: {
    loopRoute: CollaborationDefinitionLoopRoute;
    outcome: string;
    labelLane: number;
    logicalView?: boolean;
    returnEdge?: boolean;
    returnX?: number;
  };
}

export interface CollaborationGraphLayout {
  nodes: CollaborationGraphLayoutNode[];
  edges: CollaborationGraphLayoutEdge[];
}

export function getCollaborationNodeTone(kind: CollaborationDefinitionGraphNode['kind']): CollaborationNodeTone {
  if (kind === 'bot_task') return 'blue';
  if (kind === 'human_input') return 'green';
  return 'neutral';
}

export function buildCollaborationBindingViews(
  participants: ParticipantDefinition[],
  bindings: Record<string, string[]>,
  resolveBotName: (botId: string) => string | undefined,
): Record<string, CollaborationBindingView> {
  return Object.fromEntries(
    participants.map((participant) => {
      const botId = bindings[participant.key]?.[0];
      return [
        participant.key,
        {
          roleName: participant.displayName?.trim() || participant.key,
          botId,
          botName: botId ? resolveBotName(botId) : undefined,
        },
      ];
    }),
  );
}

function formatAssignee(
  node: CollaborationDefinitionGraphNode,
  bindingViews: Record<string, CollaborationBindingView>,
): Pick<CollaborationGraphNodeData, 'assigneeLabel' | 'assigneeBinding' | 'assigneeBotId' | 'assigneeBotName'> {
  if (!node.assignee) return { assigneeLabel: '无固定执行者' };
  if (node.assignee.type === 'runtime_actor') return { assigneeLabel: node.assignee.actor || '运行时角色' };
  const bindingKey = node.assignee.binding || '';
  const view = bindingViews[bindingKey];
  return {
    assigneeLabel: view?.roleName || bindingKey,
    assigneeBinding: node.assignee.binding,
    assigneeBotId: view?.botId,
    assigneeBotName: view?.botName,
  };
}

export function buildCollaborationGraphLayout(
  graph: CollaborationDefinitionGraphPreview,
  initialNodes: string[],
  bindingViews: Record<string, CollaborationBindingView> = {},
): CollaborationGraphLayout {
  const layoutMode = graph.execution_graph_mode ?? graph.graph_mode;
  if (layoutMode !== 'acyclic') {
    throw new CollaborationGraphLayoutError('unsupported_mode', `暂不支持 ${layoutMode} 模式的协作流程预览`);
  }
  if (!graph.nodes.length) throw new Error('图中没有节点');

  const nodeById = new Map<string, CollaborationDefinitionGraphNode>();
  const indegree = new Map<string, number>();
  const rank = new Map<string, number>();
  const outgoing = new Map<string, string[]>();
  graph.nodes.forEach((node) => {
    if (nodeById.has(node.node_id)) {
      throw new CollaborationGraphLayoutError('invalid_graph', `节点 ID 重复：${node.node_id}`);
    }
    nodeById.set(node.node_id, node);
    indegree.set(node.node_id, 0);
    rank.set(node.node_id, 0);
    outgoing.set(node.node_id, []);
  });

  const initialSet = new Set(initialNodes);
  for (const id of initialSet) {
    if (!nodeById.has(id)) throw new CollaborationGraphLayoutError('invalid_graph', `入口节点不存在：${id}`);
  }
  const outcomeCount = new Map<string, Set<string>>();
  graph.edges.forEach((edge: CollaborationDefinitionGraphEdge) => {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) {
      throw new CollaborationGraphLayoutError('invalid_graph', `边引用不存在的节点：${edge.source} -> ${edge.target}`);
    }
    outgoing.get(edge.source)?.push(edge.target);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    const outcomes = outcomeCount.get(edge.source) ?? new Set<string>();
    outcomes.add(edge.outcome);
    outcomeCount.set(edge.source, outcomes);
  });

  const ready = graph.nodes
    .map((n) => n.node_id)
    .filter((id) => (indegree.get(id) ?? 0) === 0)
    .sort();
  let visited = 0;
  while (ready.length) {
    const source = ready.shift() as string;
    visited += 1;
    for (const target of outgoing.get(source) ?? []) {
      rank.set(target, Math.max(rank.get(target) ?? 0, (rank.get(source) ?? 0) + 1));
      const next = (indegree.get(target) ?? 0) - 1;
      indegree.set(target, next);
      if (next === 0) {
        const idx = ready.findIndex((c) => target < (c as string));
        if (idx === -1) ready.push(target);
        else ready.splice(idx, 0, target);
      }
    }
  }
  if (visited !== graph.nodes.length) throw new Error('图中包含环');

  const byRank = new Map<number, CollaborationDefinitionGraphNode[]>();
  graph.nodes.forEach((node) => {
    const r = rank.get(node.node_id) ?? 0;
    const layer = byRank.get(r) ?? [];
    layer.push(node);
    byRank.set(r, layer);
  });

  const nodes = Array.from(byRank.entries())
    .sort(([a], [b]) => a - b)
    .flatMap(([r, layer]) => {
      layer.sort((a, b) => (a.node_id < b.node_id ? -1 : 1));
      const layerWidth = layer.length * COLLABORATION_FLOW_NODE_WIDTH + (layer.length - 1) * HORIZONTAL_GAP;
      return layer.map((node, i) => ({
        id: node.node_id,
        position: {
          x: i * (COLLABORATION_FLOW_NODE_WIDTH + HORIZONTAL_GAP) - layerWidth / 2,
          y: r * (COLLABORATION_FLOW_NODE_HEIGHT + VERTICAL_GAP),
        },
        data: {
          definition: node,
          title: node.display_name.trim() || node.execution?.definition_node_id || node.node_id,
          ...formatAssignee(node, bindingViews),
          isInitial: initialSet.has(node.node_id),
        },
      }));
    });

  const pairCounts = new Map<string, number>();
  const edges = graph.edges.map((edge, i) => {
    const pair = JSON.stringify([edge.source, edge.target]);
    const labelLane = pairCounts.get(pair) ?? 0;
    pairCounts.set(pair, labelLane + 1);
    return {
      id: `${edge.source}:${edge.outcome}:${edge.target}:${i}`,
      outcome: edge.outcome,
      source: edge.source,
      target: edge.target,
      ...(edge.loop_route ? { data: { loopRoute: edge.loop_route, outcome: edge.outcome, labelLane } } : {}),
      label: edge.display_name ?? (edge.loop_route
        ? edge.loop_route.kind === 'continue' ? 'continue' : edge.loop_route.logical_outcome
        : edge.outcome !== 'complete' || (outcomeCount.get(edge.source)?.size ?? 0) > 1 ? edge.outcome : undefined),
    };
  });

  return { nodes, edges };
}
