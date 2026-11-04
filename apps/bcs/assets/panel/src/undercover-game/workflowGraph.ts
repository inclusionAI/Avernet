import type { StateMachineNode, StateMachineRunGraph, UndercoverGamePanelParams } from './types';

export type WorkflowNodeKind = 'speak' | 'vote' | 'vote-start' | 'collect' | 'tally' | 'unknown';
export type WorkflowStatusTone = 'pending' | 'ready' | 'running' | 'completed' | 'failed' | 'aborted' | 'retry' | 'skipped' | 'unknown';
export type WorkflowDegradeReason =
  | 'missing-graph'
  | 'stale-run'
  | 'empty-nodes'
  | 'missing-edges'
  | 'unknown-node'
  | 'unknown-edge'
  | 'cyclic-graph'
  | 'unknown-status'
  | 'unknown-kind';

export interface WorkflowNodeView {
  nodeId: string;
  kind: WorkflowNodeKind;
  kindLabel: string;
  actorId?: string;
  mapped: boolean;
  isHost: boolean;
  seatNumber?: number;
  displayName: string;
  status?: string;
  statusLabel: string;
  tone: WorkflowStatusTone;
  attempt: number;
  attemptLabel?: string;
  rank: number;
  row: number;
}

export interface WorkflowEdgeView {
  source: string;
  target: string;
}

export interface WorkflowBoardView {
  available: boolean;
  runId?: string;
  runStatus?: string;
  runStatusLabel: string;
  runStatusTone: WorkflowStatusTone;
  nodes: WorkflowNodeView[];
  edges: WorkflowEdgeView[];
  lanes: WorkflowNodeView[][];
  finalNodeIds: string[];
  unknownNodeIds: string[];
  unknownKindNodeIds: string[];
  degradeReasons: WorkflowDegradeReason[];
  notice?: string;
}

interface StatusInfo {
  label: string;
  tone: WorkflowStatusTone;
  known: boolean;
}

const KIND_LABELS: Record<WorkflowNodeKind, string> = {
  speak: '发言',
  vote: '投票',
  'vote-start': '预备确认',
  collect: '汇总',
  tally: '计票',
  unknown: '未知节点',
};

const STATUS_MAP: Record<string, StatusInfo> = {
  pending: { label: '待开始', tone: 'pending', known: true },
  ready: { label: '待执行', tone: 'ready', known: true },
  running: { label: '进行中', tone: 'running', known: true },
  completed: { label: '已完成', tone: 'completed', known: true },
  failed: { label: '失败', tone: 'failed', known: true },
  aborted: { label: '已中止', tone: 'aborted', known: true },
  retry_scheduled: { label: '等待重试', tone: 'retry', known: true },
  skipped: { label: '已跳过', tone: 'skipped', known: true },
};

const RUN_STATUS_LABELS: Record<string, string> = {
  pending: '待执行',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  aborted: '已中止',
};

function normalizeStatus(raw: unknown): StatusInfo {
  if (typeof raw !== 'string' || !raw.trim()) return { label: '状态未知', tone: 'unknown', known: false };
  const key = raw.trim().toLowerCase();
  return STATUS_MAP[key] ?? { label: '状态未知', tone: 'unknown', known: false };
}

export function workflowNodeKind(nodeId: string): WorkflowNodeKind {
  if (/^speak_[1-9]\d*$/.test(nodeId)) return 'speak';
  if (/^vote_[1-9]\d*$/.test(nodeId)) return 'vote';
  if (nodeId === 'vote_start') return 'vote-start';
  if (nodeId === 'collect') return 'collect';
  if (nodeId === 'tally') return 'tally';
  return 'unknown';
}

export function workflowStatusLabel(raw: unknown): string {
  return normalizeStatus(raw).label;
}

function finiteAttempt(raw: unknown): number {
  return typeof raw === 'number' && Number.isInteger(raw) && raw > 0 ? raw : 1;
}

function runInfo(status: unknown): { label: string; tone: WorkflowStatusTone; known: boolean } {
  if (typeof status !== 'string' || !status.trim()) return { label: '运行状态未知', tone: 'unknown', known: false };
  const key = status.trim().toLowerCase();
  const label = RUN_STATUS_LABELS[key];
  if (!label) return { label: '运行状态未知', tone: 'unknown', known: false };
  return { label, tone: normalizeStatus(key).tone, known: true };
}

interface WorkflowParams {
  runId: UndercoverGamePanelParams['runId'];
  nodeActorMap: UndercoverGamePanelParams['nodeActorMap'];
  players: UndercoverGamePanelParams['players'];
  host: UndercoverGamePanelParams['host'];
}

function emptyBoard(reasons: WorkflowDegradeReason[], notice: string, runId?: string): WorkflowBoardView {
  return {
    available: false,
    runId,
    runStatusLabel: '运行图未就绪',
    runStatusTone: 'unknown',
    nodes: [],
    edges: [],
    lanes: [],
    finalNodeIds: [],
    unknownNodeIds: [],
    unknownKindNodeIds: [],
    degradeReasons: reasons,
    notice,
  };
}

function rawNodes(graph: StateMachineRunGraph): StateMachineNode[] {
  if (!Array.isArray(graph.nodes)) return [];
  const seen = new Set<string>();
  const nodes: StateMachineNode[] = [];
  for (const node of graph.nodes) {
    if (!node || typeof node !== 'object' || typeof node.node_id !== 'string' || !node.node_id.trim()) continue;
    if (seen.has(node.node_id)) continue;
    seen.add(node.node_id);
    nodes.push(node);
  }
  return nodes;
}

interface RawEdge {
  source: string;
  target: string;
}

function rawEdges(graph: StateMachineRunGraph): { edges?: RawEdge[]; invalid: number; selfLoops: number } {
  if (!Array.isArray(graph.edges)) return { invalid: 0, selfLoops: 0 };
  const edges: RawEdge[] = [];
  const seen = new Set<string>();
  let invalid = 0;
  let selfLoops = 0;
  for (const edge of graph.edges) {
    if (!edge || typeof edge.source !== 'string' || typeof edge.target !== 'string' || !edge.source.trim() || !edge.target.trim()) {
      invalid += 1;
      continue;
    }
    const source = edge.source.trim();
    const target = edge.target.trim();
    if (source === target) {
      selfLoops += 1;
      continue;
    }
    const key = `${source}->${target}`;
    if (seen.has(key)) continue;
    seen.add(key);
    edges.push({ source, target });
  }
  return { edges, invalid, selfLoops };
}

function rankNodes(nodeIds: string[], edges: RawEdge[]): { ranks?: Map<string, number>; cyclic: boolean } {
  const ids = new Set(nodeIds);
  const valid = edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target) && edge.source !== edge.target);
  const incoming = new Map<string, number>();
  const outgoing = new Map<string, string[]>();
  for (const id of nodeIds) {
    incoming.set(id, 0);
    outgoing.set(id, []);
  }
  for (const edge of valid) {
    incoming.set(edge.target, (incoming.get(edge.target) ?? 0) + 1);
    outgoing.get(edge.source)!.push(edge.target);
  }
  const ranks = new Map<string, number>();
  const queue = nodeIds.filter((id) => (incoming.get(id) ?? 0) === 0);
  for (const id of queue) ranks.set(id, 0);
  const processed: string[] = [];
  while (queue.length) {
    const id = queue.shift()!;
    processed.push(id);
    for (const target of outgoing.get(id) ?? []) {
      ranks.set(target, Math.max(ranks.get(target) ?? 0, (ranks.get(id) ?? 0) + 1));
      incoming.set(target, (incoming.get(target) ?? 1) - 1);
      if ((incoming.get(target) ?? 0) === 0) queue.push(target);
    }
  }
  if (processed.length !== nodeIds.length) return { cyclic: true };
  return { ranks, cyclic: false };
}

/**
 * Build the public workflow board from the real run graph. Only structural
 * fields are read (run id/status, node id/status/attempt, edges); node
 * artifacts, instructions, errors, words and ballots are never touched.
 */
export function buildWorkflowBoard(graph: StateMachineRunGraph | undefined, params: WorkflowParams): WorkflowBoardView {
  if (!graph || typeof graph !== 'object' || !graph.run || typeof graph.run.run_id !== 'string') {
    return emptyBoard(['missing-graph'], '尚未获取本阶段运行图，等待刷新。');
  }
  const graphRunId = graph.run.run_id;
  if (graphRunId !== params.runId) {
    return emptyBoard(['stale-run'], '已隐藏上一张运行图的节点与连线；等待当前运行的最新状态。', graphRunId);
  }
  const nodes = rawNodes(graph);
  if (!nodes.length) return emptyBoard(['empty-nodes'], '运行图暂时没有节点，等待主持人推进。', graphRunId);
  const run = runInfo(graph.run.status);
  const players = new Map(params.players.map((player) => [player.actorId, player]));
  const degrade = new Set<WorkflowDegradeReason>();
  if (!run.known) degrade.add('unknown-status');
  const { edges: parsedEdges, invalid, selfLoops } = rawEdges(graph);
  if (parsedEdges === undefined) degrade.add('missing-edges');
  if (invalid > 0) degrade.add('unknown-edge');
  if (selfLoops > 0) degrade.add('cyclic-graph');

  const nodeIds = nodes.map((node) => node.node_id);
  const known = new Set(nodeIds);
  const edges: WorkflowEdgeView[] = (parsedEdges ?? []).filter((edge) => known.has(edge.source) && known.has(edge.target));
  if (parsedEdges && edges.length !== parsedEdges.length) degrade.add('unknown-edge');

  const unknownNodeIds: string[] = [];
  const unknownKindNodeIds: string[] = [];
  const base = nodes.map((node, index) => {
    const kind = workflowNodeKind(node.node_id);
    const actorId = params.nodeActorMap[node.node_id];
    const player = actorId ? players.get(actorId) : undefined;
    const isHost = Boolean(actorId && actorId === params.host.actorId);
    const attempt = finiteAttempt(node.attempt);
    const status = normalizeStatus(node.status);
    if (!status.known) degrade.add('unknown-status');
    if (kind === 'unknown') unknownKindNodeIds.push(node.node_id);
    if (!isHost && !player && kind !== 'vote-start' && kind !== 'unknown') unknownNodeIds.push(node.node_id);
    const displayName = isHost
      ? params.host.displayName
      : player
        ? player.displayName
        : kind === 'vote-start'
          ? '投票开始确认'
          : kind === 'unknown'
            ? '未映射节点'
            : '未映射节点';
    return {
      nodeId: node.node_id,
      kind,
      kindLabel: KIND_LABELS[kind],
      actorId,
      mapped: isHost || Boolean(player),
      isHost,
      seatNumber: player?.seatNumber,
      displayName,
      status: typeof node.status === 'string' ? node.status : undefined,
      statusLabel: status.label,
      tone: status.tone,
      attempt,
      attemptLabel: attempt > 1 || String(node.status).toLowerCase() === 'retry_scheduled' ? `第 ${attempt} 次尝试` : undefined,
      rank: 0,
      row: index,
    } satisfies WorkflowNodeView;
  });
  if (unknownNodeIds.length) degrade.add('unknown-node');
  if (unknownKindNodeIds.length) degrade.add('unknown-kind');

  let lanes: WorkflowNodeView[][];
  if (parsedEdges === undefined) {
    lanes = [base.map((node) => ({ ...node, rank: 0, row: base.findIndex((item) => item.nodeId === node.nodeId) }))];
  } else {
    const ranked = rankNodes(nodeIds, edges);
    if (ranked.cyclic || !ranked.ranks) {
      degrade.add('cyclic-graph');
      lanes = [base.map((node, index) => ({ ...node, rank: 0, row: index }))];
    } else {
      const colCount = Math.max(...nodeIds.map((id) => ranked.ranks!.get(id) ?? 0)) + 1;
      const columns: WorkflowNodeView[][] = Array.from({ length: colCount }, () => []);
      base.forEach((node, index) => {
        const rank = ranked.ranks!.get(node.nodeId) ?? 0;
        const view = { ...node, rank, row: index };
        columns[rank].push(view);
      });
      lanes = columns.map((column) => column.map((node, row) => ({ ...node, row })));
    }
  }
  const finalNodeIds = nodeIds.filter((id) => !edges.some((edge) => edge.source === id));
  const noticeParts: string[] = [];
  if (degrade.has('missing-edges')) noticeParts.push('运行图未提供连线，仅按节点状态展示，不推断先后顺序。');
  if (degrade.has('cyclic-graph')) noticeParts.push('运行图存在循环连线，已按节点列表降级展示。');
  if (degrade.has('unknown-node')) noticeParts.push(`有 ${unknownNodeIds.length} 个节点未映射到公开座位，已标为未映射。`);
  if (degrade.has('unknown-kind')) noticeParts.push(`有 ${unknownKindNodeIds.length} 个节点类型未识别，已按未知节点展示。`);
  if (degrade.has('unknown-edge')) noticeParts.push('部分连线指向未知节点，已忽略。');
  if (degrade.has('unknown-status')) noticeParts.push('部分运行或节点状态无法识别，已显示为状态未知。');
  return {
    available: true,
    runId: graphRunId,
    runStatus: typeof graph.run.status === 'string' ? graph.run.status : undefined,
    runStatusLabel: run.label,
    runStatusTone: run.tone,
    nodes: lanes.flat(),
    edges,
    lanes,
    finalNodeIds,
    unknownNodeIds,
    unknownKindNodeIds,
    degradeReasons: [...degrade],
    notice: noticeParts.length ? noticeParts.join(' ') : undefined,
  };
}
