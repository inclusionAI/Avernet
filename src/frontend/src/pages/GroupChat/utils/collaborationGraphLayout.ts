import type {
  CollaborationDefinitionGraphNode,
  CollaborationDefinitionGraphPreview,
} from '@/services/backend-api/BcnController';
import type { CollaborationParticipantDefinition } from './collaborationValidation';

export const COLLABORATION_FLOW_NODE_WIDTH = 210;
export const COLLABORATION_FLOW_NODE_HEIGHT = 84;
const HORIZONTAL_GAP = 56;
const VERTICAL_GAP = 88;

export const COLLABORATION_NODE_KIND_LABELS: Record<
  CollaborationDefinitionGraphNode['kind'],
  string
> = {
  bot_task: 'Bot 任务',
  group_chat: '群聊',
  human_input: '人工输入',
  tool_action: '工具操作',
  sub_state_machine: '子流程',
};

export type CollaborationGraphLayoutErrorCode =
  | 'unsupported_mode'
  | 'invalid_graph';

export class CollaborationGraphLayoutError extends Error {
  constructor(
    public readonly code: CollaborationGraphLayoutErrorCode,
    message: string,
  ) {
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
}

export interface CollaborationNodePresentation {
  title: string;
  kindLabel: string;
  botName: string;
  roleName: string;
}

export interface CollaborationGraphLayoutNode {
  id: string;
  position: { x: number; y: number };
  data: CollaborationGraphNodeData;
}

export interface CollaborationGraphLayoutEdge {
  id: string;
  source: string;
  target: string;
  label?: string;
}

export interface CollaborationGraphLayout {
  nodes: CollaborationGraphLayoutNode[];
  edges: CollaborationGraphLayoutEdge[];
}

export type CollaborationNodeTone = 'blue' | 'green' | 'neutral';

export function getCollaborationNodeTone(
  kind: CollaborationDefinitionGraphNode['kind'],
): CollaborationNodeTone {
  if (kind === 'bot_task') return 'blue';
  if (kind === 'human_input') return 'green';
  return 'neutral';
}

export function getCollaborationNodeInteractionState({
  nodeId,
  assigneeBinding,
  selectedNodeId,
  highlightedBinding,
}: {
  nodeId: string;
  assigneeBinding?: string;
  selectedNodeId?: string;
  highlightedBinding?: string;
}) {
  const selected = nodeId === selectedNodeId;
  return {
    selected,
    highlighted:
      !selectedNodeId &&
      !!highlightedBinding &&
      assigneeBinding === highlightedBinding,
  };
}

function invalidGraph(message: string) {
  return new CollaborationGraphLayoutError('invalid_graph', message);
}

function compareText(left: string, right: string) {
  if (left === right) return 0;
  return left < right ? -1 : 1;
}

function insertSorted(values: string[], value: string) {
  const index = values.findIndex(
    (candidate) => compareText(value, candidate) < 0,
  );
  if (index === -1) values.push(value);
  else values.splice(index, 0, value);
}

export function buildCollaborationBindingViews(
  participants: CollaborationParticipantDefinition[],
  bindings: Record<string, string[]>,
  resolveBotName: (botId: string) => string | undefined,
): Record<string, CollaborationBindingView> {
  return Object.fromEntries(
    participants.map((participant) => {
      const botId = bindings[participant.key]?.[0];
      return [
        participant.key,
        {
          roleName:
            participant.displayName?.trim() ||
            participant.name.trim() ||
            participant.key,
          botId,
          botName: botId ? resolveBotName(botId) : undefined,
        },
      ];
    }),
  );
}

export function buildCollaborationNodePresentation(
  data: CollaborationGraphNodeData,
): CollaborationNodePresentation {
  if (data.assigneeBinding) {
    return {
      title: data.title,
      kindLabel: COLLABORATION_NODE_KIND_LABELS[data.definition.kind],
      botName: data.assigneeBotId
        ? data.assigneeBotName || '已绑定 Bot'
        : '未绑定 Bot',
      roleName: data.assigneeLabel,
    };
  }

  if (data.definition.assignee?.type === 'runtime_actor') {
    return {
      title: data.title,
      kindLabel: COLLABORATION_NODE_KIND_LABELS[data.definition.kind],
      botName: data.assigneeLabel,
      roleName: '运行时角色',
    };
  }

  return {
    title: data.title,
    kindLabel: COLLABORATION_NODE_KIND_LABELS[data.definition.kind],
    botName: '未分配 Bot',
    roleName: '无固定角色',
  };
}

function formatCollaborationGraphAssignee(
  node: CollaborationDefinitionGraphNode,
  bindingViews: Record<string, CollaborationBindingView>,
): Pick<
  CollaborationGraphNodeData,
  'assigneeLabel' | 'assigneeBinding' | 'assigneeBotId' | 'assigneeBotName'
> {
  if (!node.assignee) {
    return { assigneeLabel: '无固定执行者' };
  }
  if (node.assignee.type === 'runtime_actor') {
    return { assigneeLabel: node.assignee.actor };
  }

  const bindingView = bindingViews[node.assignee.binding];
  return {
    assigneeLabel: bindingView?.roleName || node.assignee.binding,
    assigneeBinding: node.assignee.binding,
    assigneeBotId: bindingView?.botId,
    assigneeBotName: bindingView?.botName,
  };
}

export function buildCollaborationGraphLayout(
  graph: CollaborationDefinitionGraphPreview,
  initialNodes: string[],
  bindingViews: Record<string, CollaborationBindingView> = {},
): CollaborationGraphLayout {
  if (graph.graph_mode !== 'acyclic') {
    throw new CollaborationGraphLayoutError(
      'unsupported_mode',
      `暂不支持 ${graph.graph_mode} 模式的协作流程预览`,
    );
  }
  if (!graph.nodes.length) {
    throw invalidGraph('图中没有节点');
  }

  const nodeById = new Map<string, CollaborationDefinitionGraphNode>();
  const indegree = new Map<string, number>();
  const rank = new Map<string, number>();
  const outgoing = new Map<string, string[]>();
  graph.nodes.forEach((node) => {
    if (nodeById.has(node.node_id)) {
      throw invalidGraph(`节点 ID 重复：${node.node_id}`);
    }
    nodeById.set(node.node_id, node);
    indegree.set(node.node_id, 0);
    rank.set(node.node_id, 0);
    outgoing.set(node.node_id, []);
  });

  const initialNodeSet = new Set(initialNodes);
  for (const initialNode of initialNodeSet) {
    if (!nodeById.has(initialNode)) {
      throw invalidGraph(`入口节点不存在：${initialNode}`);
    }
  }

  const outcomeCountBySource = new Map<string, Set<string>>();
  graph.edges.forEach((edge) => {
    if (!nodeById.has(edge.source) || !nodeById.has(edge.target)) {
      throw invalidGraph(
        `边引用不存在的节点：${edge.source} -> ${edge.target}`,
      );
    }
    outgoing.get(edge.source)?.push(edge.target);
    indegree.set(edge.target, (indegree.get(edge.target) ?? 0) + 1);
    const outcomes = outcomeCountBySource.get(edge.source) ?? new Set<string>();
    outcomes.add(edge.outcome);
    outcomeCountBySource.set(edge.source, outcomes);
  });

  const ready = graph.nodes
    .map((node) => node.node_id)
    .filter((nodeId) => indegree.get(nodeId) === 0)
    .sort(compareText);
  let visitedCount = 0;
  while (ready.length) {
    const source = ready.shift() as string;
    visitedCount += 1;
    for (const target of outgoing.get(source) ?? []) {
      rank.set(
        target,
        Math.max(rank.get(target) ?? 0, (rank.get(source) ?? 0) + 1),
      );
      const nextIndegree = (indegree.get(target) ?? 0) - 1;
      indegree.set(target, nextIndegree);
      if (nextIndegree === 0) {
        insertSorted(ready, target);
      }
    }
  }
  if (visitedCount !== graph.nodes.length) {
    throw invalidGraph('图中包含环');
  }

  const nodesByRank = new Map<number, CollaborationDefinitionGraphNode[]>();
  graph.nodes.forEach((node) => {
    const nodeRank = rank.get(node.node_id) ?? 0;
    const layer = nodesByRank.get(nodeRank) ?? [];
    layer.push(node);
    nodesByRank.set(nodeRank, layer);
  });

  const nodes = Array.from(nodesByRank.entries())
    .sort(([left], [right]) => left - right)
    .flatMap(([nodeRank, layer]) => {
      layer.sort((left, right) => compareText(left.node_id, right.node_id));
      const layerWidth =
        layer.length * COLLABORATION_FLOW_NODE_WIDTH +
        (layer.length - 1) * HORIZONTAL_GAP;
      return layer.map((node, index) => ({
        id: node.node_id,
        position: {
          x:
            index * (COLLABORATION_FLOW_NODE_WIDTH + HORIZONTAL_GAP) -
            layerWidth / 2,
          y:
            nodeRank *
            (COLLABORATION_FLOW_NODE_HEIGHT + VERTICAL_GAP),
        },
        data: {
          definition: node,
          title: node.display_name.trim() || node.node_id,
          ...formatCollaborationGraphAssignee(node, bindingViews),
          isInitial: initialNodeSet.has(node.node_id),
        },
      }));
    });

  const edges = graph.edges.map((edge, index) => ({
    id: `${edge.source}:${edge.outcome}:${edge.target}:${index}`,
    source: edge.source,
    target: edge.target,
    label:
      edge.outcome !== 'complete' ||
      (outcomeCountBySource.get(edge.source)?.size ?? 0) > 1
        ? edge.outcome
        : undefined,
  }));

  return { nodes, edges };
}
