import type { FailedTarget, OperationResult, RoutineOperationBatch, RoutineRunDto, RoutineTarget } from './types';
const reasonNames: Record<string, string> = {
  instances_query_timeout: '实例查询超时',
  instances_query_failed: '实例查询失败',
  caller_unavailable: 'Caller 实例不存在或未就绪',
  instances_not_found: '未找到运行实例',
  binding_not_active: '运行环境未就绪',
  device_unavailable: '运行实例不可用',
  cron_api_failed: '任务查询失败',
};
export const failureReason = (reason?: string | null) => reasonNames[reason || ''] || '目标暂不可用，请刷新核实';
export const targetLabel = (target: RoutineTarget) =>
  [
    target.runtime_stage || '运行态未报告',
    target.device_uuid || '实例未报告',
    target.operator_id ? `Caller：${target.operator_id}` : '',
  ]
    .filter(Boolean)
    .join(' · ');
export const describeFailure = (target: FailedTarget) => `${targetLabel(target)}：${failureReason(target.reason)}`;
export function isBatch(value: unknown): value is RoutineOperationBatch {
  return Boolean(value && typeof value === 'object' && Array.isArray((value as RoutineOperationBatch).results));
}
export function runOutcome(run: RoutineRunDto): OperationResult {
  if (run.status === 'completed') return { level: 'success', message: '触发已确认，请查看执行历史核实结果' };
  if (run.status === 'failed') return { level: 'error', message: '引擎未确认触发成功，请刷新执行历史核实' };
  return { level: 'warning', message: '触发状态未确认，请查询执行历史；不要重复提交' };
}
export function batchOutcome(batch: RoutineOperationBatch, operation: 'run' | 'update'): OperationResult {
  const uncertain = operation === 'run' && batch.results.some((row) => row.success && row.run?.status !== 'completed');
  const failed = batch.failed > 0 || batch.failed_targets.length > 0 || batch.results.some((row) => !row.success);
  return {
    level: failed || uncertain ? 'warning' : 'success',
    batch,
    message: `${operation === 'run' ? '触发请求' : '更新请求'}：${batch.succeeded} 个目标已响应，${
      batch.failed
    } 个失败${uncertain ? '，部分触发状态待确认' : ''}。请查看逐目标结果并刷新核实`,
  };
}
export const runStatusLabel = (status: string) =>
  ({ ok: '执行完成', error: '执行失败或超时', skipped: '已触发但未运行' }[status] || status || '未知');
