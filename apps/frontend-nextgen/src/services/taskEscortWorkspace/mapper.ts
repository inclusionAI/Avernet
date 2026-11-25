import type { DomainRunDetail, Run, RunStatus, Workflow } from '@/domain/taskEscortWorkspace';
import { immutableCopy } from '@/domain/taskEscortWorkspace/immutable';
import type { TaskEscortRawNodeExecution, TaskEscortRawRun, TaskEscortRawWorkflow } from './types';

const text = (value: unknown): string | null => (typeof value === 'string' ? value : null);
const nonnegative = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null;
function assertRecord(value: unknown, label: string): asserts value is Record<string, unknown> {
  if (value === null || typeof value !== 'object' || Array.isArray(value)) {
    throw new Error(`后端返回了非预期的数据结构: ${label}必须为对象`);
  }
}
function requiredIdentity(value: unknown, field: string): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error(`后端返回了非预期的数据结构: ${field}必须为非空字符串`);
  }
  return value;
}
function runIdentity(row: unknown): { id: string; workflowId: string } {
  assertRecord(row, '运行记录');
  return { id: requiredIdentity(row.flow_id, 'flow_id'), workflowId: requiredIdentity(row.workflow_id, 'workflow_id') };
}
function firstNumber(...values: unknown[]): number | null {
  for (const value of values) {
    const number = nonnegative(value);
    if (number !== null) return number;
  }
  return null;
}
function firstText(...values: unknown[]): string | null {
  for (const value of values) if (typeof value === 'string' && value.trim()) return value;
  return null;
}
/** Backend epochs are milliseconds, matching the existing controller's timestamp contract. */
export function mapTimestamp(value: unknown): string | null {
  if (value === null || value === undefined || value === '') return null;
  if (typeof value !== 'number' && typeof value !== 'string') return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? null : date.toISOString();
}
export function mapRunStatus(value: unknown): RunStatus {
  switch (value) {
    case 'succeeded':
    case 'success':
      return 'success';
    case 'failed':
      return 'failed';
    case 'running':
    case 'postActionsRunning':
      return 'running';
    case 'waiting':
    case 'pending':
      return 'waiting';
    case 'cancelled':
    case 'canceled':
      return 'cancelled';
    default:
      return 'unknown';
  }
}
export function mapRun(row: TaskEscortRawRun, workflows: Workflow[] = []): Run {
  const { id, workflowId } = runIdentity(row);
  const durationMs = firstNumber(row.total_duration_ms, row.duration_ms);
  const workflow = workflows.find((item) => item.id === workflowId);
  return immutableCopy({
    id,
    workflowId,
    name: firstText(row.workflow_title) ?? workflow?.name ?? workflowId,
    // The read contract supplies a creator, not a Bot identity.
    bot: '未知',
    status: mapRunStatus(row.status),
    rawStatus: text(row.status),
    duration: durationMs === null ? null : durationMs / 1000,
    startedAt: mapTimestamp(row.started_at),
    trigger: firstText(row.triggered_by, row.trigger) ?? '未知',
    node: '未知',
    succeededCount: firstNumber(row.succeeded_count, row.succeeded_nodes),
    failedCount: firstNumber(row.failed_count, row.failed_nodes),
    totalTokens: firstNumber(row.total_token_usage, row.total_tokens),
    sessionId: firstText(row.origin_session_id, row.session_id),
  });
}
export function mapWorkflow(row: TaskEscortRawWorkflow, runSample: TaskEscortRawRun[]): Workflow {
  assertRecord(row, '工作流记录');
  const id = requiredIdentity(row.workflowId, 'workflowId');
  const sampleCount = runSample.filter((run) => runIdentity(run).workflowId === id).length;
  return immutableCopy({
    id,
    name: firstText(row.title) ?? id,
    description: '只读工作流；运行数量仅为本次返回样本中的匹配数，并非全量',
    bot: '未知',
    version: '未知',
    successRate: null,
    runCount: sampleCount,
    sampleCount,
    status: 'unknown',
    updatedAt: mapTimestamp(row.updatedAt),
    category: '未分类',
  });
}
export function mapRunDetail(
  detail: { run: TaskEscortRawRun; nodes?: TaskEscortRawNodeExecution[] },
  workflows: Workflow[],
): DomainRunDetail {
  assertRecord(detail, '运行详情');
  const run = mapRun(detail.run, workflows);
  if (detail.nodes !== undefined && detail.nodes !== null && !Array.isArray(detail.nodes)) {
    throw new Error('运行详情返回了非预期的数据结构');
  }
  return immutableCopy({
    run,
    nodes: (detail.nodes ?? []).map((node) => {
      assertRecord(node, '节点执行记录');
      const id = requiredIdentity(node.node_id, 'node_id');
      const durationMs = nonnegative(node.duration_ms);
      return {
        id,
        title: firstText(node.node_title) ?? id,
        status: mapRunStatus(node.status),
        duration: durationMs === null ? null : durationMs / 1000,
        error: text(node.error_text),
        input: text(node.input_json),
        output: text(node.output_json),
      };
    }),
  });
}
