// @asset-migrated: teamclaw 自研资产
/**
 * PRD 表现层状态/边语义/节点内执行流程的纯派生模块(spec §3.3)。
 * 契约保持不变决策下,PRD 词表(rework/merged/split_work…)全部从现有 DTO 派生:
 * 后端契约将来若直发这些语义,只需替换本模块实现,调用方不动。
 * 无 I/O、无 React;派生规则全部配 jest 单测(test/assets/TaskPanel/statusDerivation.test.ts)。
 */
import type { NodeActionEventDto, TaskRelationDto } from './contract';
import type { NodeStatus } from './types';

/**
 * PRD 节点展示态:pending/running/rework/done/merged。
 * failed/hung/cancelled 不在 PRD 五词表内,直通透传给渲染层
 * (tokens NODE_STATUS_TONES 已有对应中文 label,不新造词)。
 */
export type PresentNodeStatus = 'pending' | 'running' | 'rework' | 'done' | 'merged' | 'failed' | 'hung' | 'cancelled';

export type PresentEdgeSemantics = 'split_work' | 'race' | 'fallback' | 'rework' | 'none';

export interface NodePresentation {
  present: PresentNodeStatus;
  /** 返工次数 = action_log[].attempt 最大值;无日志为 0 */
  attemptCount: number;
  /** 节点级 HITL 徽章 */
  needsHitl: boolean;
}

export interface PresentEdge {
  from: string;
  to: string;
  semantics: PresentEdgeSemantics;
  reason?: string;
}

export interface ExecDagStep {
  id: string;
  label: string;
}

export interface ExecDagEdge {
  from: string;
  to: string;
  semantics: PresentEdgeSemantics;
}

/** action_log[].attempt 缺省口径:事件未带 attempt 视为第 1 次 */
const DEFAULT_ATTEMPT = 1;

/** 截断上限:label 中 payload 摘要最长 20 字(Unicode 感知,与 text.ts truncateText 同思路) */
const LABEL_SUMMARY_MAX = 20;

function truncate(text: string, max: number): string {
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function eventAttempt(e: NodeActionEventDto): number {
  return typeof e.attempt === 'number' && e.attempt > 0 ? e.attempt : DEFAULT_ATTEMPT;
}

/**
 * 节点展示态派生(spec §3.3 规则逐条):
 * - pending ← runtime PENDING/PLANNING(mapper 已归一为小写 pending)
 * - running ← runtime RUNNING 且无返工征象
 * - rework ← 执行中 && attemptCount>1 && 最近一次 acceptance verdict 非 DONE
 * - merged ← DONE 且(输出已被下游消费 || child_task_id 指向任务 DONE)
 * - done ← 其余 DONE
 * - failed/hung/cancelled ← 直通透传
 */
export function deriveNodePresentation(input: {
  runtimeStatus: NodeStatus;
  actionLog?: NodeActionEventDto[] | null;
  acceptanceVerdict?: 'DONE' | 'FAILED' | null;
  hasConsumedDownstream: boolean;
  childTaskDone: boolean | null;
  needsTaskAttention: boolean;
}): NodePresentation {
  const { runtimeStatus, actionLog, acceptanceVerdict, hasConsumedDownstream, childTaskDone, needsTaskAttention } = input;
  const log = actionLog ?? [];
  const attemptCount = log.length ? Math.max(...log.map(eventAttempt)) : 0;

  let present: PresentNodeStatus;
  // verdict 非 DONE 且非空 = 'FAILED'(联合类型收敛),rework/needsHitl 据此判定,避免宽松等号
  const verdictFailed = acceptanceVerdict === 'FAILED';
  switch (runtimeStatus) {
    case 'running':
      present = attemptCount > 1 && verdictFailed ? 'rework' : 'running';
      break;
    case 'done':
      present = hasConsumedDownstream || childTaskDone === true ? 'merged' : 'done';
      break;
    default:
      // pending / failed / hung / cancelled 直通透传
      present = runtimeStatus;
  }

  const hasVerdict = acceptanceVerdict === 'DONE' || acceptanceVerdict === 'FAILED';
  const needsHitl = needsTaskAttention && runtimeStatus === 'running' && hasVerdict;

  return { present, attemptCount, needsHitl };
}

/** 返工边 reason:目标节点 attempt>1 的最近事件描述(payload.description 优先) */
function reworkReason(log: NodeActionEventDto[] | null | undefined): string | undefined {
  const retryEvents = (log ?? []).filter((e) => eventAttempt(e) > DEFAULT_ATTEMPT);
  if (!retryEvents.length) return undefined;
  const last = retryEvents[retryEvents.length - 1];
  const desc = last.payload?.['description'];
  if (typeof desc === 'string' && desc) return desc;
  const transition =
    last.status_from || last.status_to
      ? `${last.status_from ?? '—'} → ${last.status_to ?? '—'}`
      : typeof last.payload?.['node_id'] === 'string'
        ? String(last.payload['node_id'])
        : '';
  return transition ? `${last.action}: ${transition}` : last.action;
}

/**
 * 顶层 DAG 边语义派生:relations.type 恒 'DEPENDENCY' 基线为 none;
 * - to 节点 attemptCount>1 → rework(返工事实优先于并行形态)
 * - from 节点出边数>1 → split_work(fan-out 视为拆分)
 * race/fallback 无契约数据源,预留词表不产出现阶段。
 */
export function derivePresentEdges(input: {
  relations: TaskRelationDto[];
  nodes: Array<{ id: string; presentation: NodePresentation; actionLog?: NodeActionEventDto[] | null }>;
  outDegree: Record<string, number>;
}): PresentEdge[] {
  const byId = new Map(input.nodes.map((n) => [n.id, n]));
  return input.relations.map((rel) => {
    const to = byId.get(rel.dst_id);
    const from = byId.get(rel.src_id);
    if (to && to.presentation.attemptCount > DEFAULT_ATTEMPT) {
      return { from: rel.src_id, to: rel.dst_id, semantics: 'rework' as const, reason: reworkReason(to.actionLog) };
    }
    if (from && (input.outDegree[rel.src_id] ?? 0) > 1) {
      return { from: rel.src_id, to: rel.dst_id, semantics: 'split_work' as const };
    }
    return { from: rel.src_id, to: rel.dst_id, semantics: 'none' as const };
  });
}

/**
 * 节点内执行流程(execDag)由 action_log 派生(spec §4-3 决策,候选 b):
 * 按 attempt 分组、组内按 seq 排成线性链;attempt N+1 组首节点从前一组尾连 rework 边。
 * 只产 id/label,坐标由渲染层分层布局。后端将来下发结构化 execution_graph 时替换本实现。
 */
export function deriveNodeExecDag(actionLog: NodeActionEventDto[] | null | undefined): {
  nodes: ExecDagStep[];
  edges: ExecDagEdge[];
} {
  const log = [...(actionLog ?? [])].sort((a, b) => a.seq - b.seq);
  if (!log.length) return { nodes: [], edges: [] };

  const nodes: ExecDagStep[] = [];
  const edges: ExecDagEdge[] = [];
  let prevId: string | null = null;
  let prevAttempt: number | null = null;

  for (const e of log) {
    const id = `ev-${e.seq}`;
    const summary = typeof e.payload?.['description'] === 'string' ? (e.payload['description'] as string) : '';
    nodes.push({ id, label: summary ? `${e.action} ${truncate(summary, LABEL_SUMMARY_MAX)}` : e.action });

    if (prevId) {
      const attempt = eventAttempt(e);
      const crossAttempt = prevAttempt !== null && attempt > prevAttempt;
      edges.push({ from: prevId, to: id, semantics: crossAttempt ? 'rework' : 'none' });
      prevAttempt = attempt;
    } else {
      prevAttempt = eventAttempt(e);
    }
    prevId = id;
  }
  return { nodes, edges };
}