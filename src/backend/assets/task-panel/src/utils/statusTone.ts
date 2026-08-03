/**
 * Task workflow panel — status → tone + edge-state rules.
 * Ported from bcsPanel.StateMachineRunView (getStatusTone:1748, getEdgeState:1967),
 * adapted to the unified task state machine (state_machine.py / spec §2/§3.3):
 *   task status (GraphStatus): drafting/defined/running/human_required/bbs_active/reviewing/done/cancelled/failed
 *   node:       pending/running/done/failed/skipped/hung
 */

export interface StatusTone {
  bg: string;
  border: string;
  text: string;
  stroke: string;
  fill: string;
}

export type EdgeState = 'executed' | 'pending' | 'blocked' | 'skipped';

export function normalizeStatus(status: string | undefined | null): string {
  if (!status) return 'pending';
  return String(status).trim().toLowerCase();
}

// --- task status tone (task-level, unified GraphStatus) ------------------

export function getTaskStatusTone(phase: string | undefined): StatusTone {
  const s = normalizeStatus(phase);
  if (s === 'drafting' || s === 'defined') {
    return { bg: '#eef2ff', border: '#c7d2fe', text: '#4338ca', stroke: '#4f46e5', fill: '#e0e7ff' };
  }
  if (s === 'running') {
    return { bg: '#eff6ff', border: '#bfdbfe', text: '#1d4ed8', stroke: '#2563eb', fill: '#dbeafe' };
  }
  if (s === 'human_required') {
    return { bg: '#f5f3ff', border: '#ddd6fe', text: '#6d28d9', stroke: '#7c3aed', fill: '#ede9fe' };
  }
  if (s === 'bbs_active') {
    return { bg: '#fff7ed', border: '#fed7aa', text: '#c2410c', stroke: '#ea580c', fill: '#ffedd5' };
  }
  if (s === 'reviewing') {
    return { bg: '#fffbeb', border: '#fde68a', text: '#b45309', stroke: '#d97706', fill: '#fef3c7' };
  }
  if (s === 'done') {
    return { bg: '#ecfdf5', border: '#bbf7d0', text: '#047857', stroke: '#16a34a', fill: '#dcfce7' };
  }
  if (s === 'failed') {
    return { bg: '#fef2f2', border: '#fecaca', text: '#b91c1c', stroke: '#dc2626', fill: '#fee2e2' };
  }
  if (s === 'cancelled') {
    return { bg: '#f8fafc', border: '#cbd5e1', text: '#64748b', stroke: '#94a3b8', fill: '#e2e8f0' };
  }
  return { bg: '#f8fafc', border: '#cbd5e1', text: '#475569', stroke: '#94a3b8', fill: '#f1f5f9' };
}

// --- node status tone -----------------------------------------------------

export function getNodeStatusTone(status: string | undefined): StatusTone {
  const s = normalizeStatus(status);
  if (s === 'running') {
    return { bg: '#eff6ff', border: '#bfdbfe', text: '#1d4ed8', stroke: '#2563eb', fill: '#dbeafe' };
  }
  if (s === 'done') {
    return { bg: '#ecfdf5', border: '#bbf7d0', text: '#047857', stroke: '#16a34a', fill: '#dcfce7' };
  }
  if (s === 'failed') {
    return { bg: '#fef2f2', border: '#fecaca', text: '#b91c1c', stroke: '#dc2626', fill: '#fee2e2' };
  }
  if (s === 'skipped') {
    return { bg: '#f8fafc', border: '#cbd5e1', text: '#64748b', stroke: '#94a3b8', fill: '#e2e8f0' };
  }
  if (s === 'hung') {
    return { bg: '#f5f3ff', border: '#ddd6fe', text: '#6d28d9', stroke: '#7c3aed', fill: '#ede9fe' };
  }
  return { bg: '#f8fafc', border: '#cbd5e1', text: '#475569', stroke: '#94a3b8', fill: '#f1f5f9' };
}

export function getNodeStatusLabel(status: string | undefined): string {
  const map: Record<string, string> = {
    pending: '待执行',
    running: '执行中',
    done: '已完成',
    failed: '失败',
    skipped: '已跳过',
    hung: '已挂起',
  };
  return map[normalizeStatus(status)] ?? '待执行';
}

export function getTaskStatusLabel(phase: string | undefined): string {
  const map: Record<string, string> = {
    drafting: '草稿中',
    defined: '已就绪',
    running: '执行中',
    human_required: '待人工',
    bbs_active: 'BBS 广场中',
    reviewing: '验收中',
    done: '已完成',
    cancelled: '已取消',
    failed: '已失败',
  };
  return map[normalizeStatus(phase)] ?? '草稿中';
}

// --- edge state (ported from getEdgeState:1967) ---------------------------

function isFailed(s: string | undefined): boolean {
  return normalizeStatus(s) === 'failed';
}
function isSkipped(s: string | undefined): boolean {
  return normalizeStatus(s) === 'skipped';
}
function isCompleted(s: string | undefined): boolean {
  return normalizeStatus(s) === 'done';
}

export function getEdgeState(
  sourceStatus: string | undefined,
  targetStatus: string | undefined,
): EdgeState {
  if (isFailed(sourceStatus)) return 'blocked';
  if (isSkipped(sourceStatus) || isSkipped(targetStatus)) return 'skipped';
  if (
    isCompleted(targetStatus) ||
    isFailed(targetStatus) ||
    normalizeStatus(targetStatus) === 'running' ||
    normalizeStatus(targetStatus) === 'hung'
  ) {
    return 'executed';
  }
  return 'pending';
}

export interface EdgeStroke {
  color: string;
  dasharray: string;
  width: number;
}

export function getEdgeStroke(
  state: EdgeState,
  kind: string | undefined,
): EdgeStroke {
  const isBranch =
    kind === 'conditional' || kind === 'fallback' || kind === 'parallel_sync';
  switch (state) {
    case 'executed':
      return { color: '#2563eb', dasharray: isBranch ? '6 4' : '', width: 1.6 };
    case 'blocked':
      return { color: '#dc2626', dasharray: '6 4', width: 1.6 };
    case 'skipped':
      return { color: '#cbd5e1', dasharray: '2 4', width: 1.2 };
    case 'pending':
    default:
      return { color: '#94a3b8', dasharray: '6 4', width: 1.2 };
  }
}
