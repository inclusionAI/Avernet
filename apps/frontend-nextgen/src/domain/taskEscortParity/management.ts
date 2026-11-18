import type { ManagementJob, ManagementVersionSource } from './managementTypes';
export const MANAGEMENT_WRITE_REASON = '未接入写入契约';
export function managementTime(value: number | null): string {
  return value === null
    ? '时间未记录'
    : new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false });
}
export function managementSourceLabel(source: ManagementVersionSource | null | undefined): string {
  if (source?.kind === 'auto_optimization') return '自动优化 · 人工确认发布';
  if (source?.kind === 'manual_optimization') return '手动优化 · 人工确认发布';
  if (source?.kind === 'rollback')
    return `回滚${source.restoredFromDeployNumber === null ? '' : `至 #${source.restoredFromDeployNumber}`}`;
  return '来源未知';
}
export const managementEvents: Record<string, string> = {
  workflow_started: '工作流启动',
  node_started: '节点开始',
  node_succeeded: '节点成功',
  node_failed: '节点失败',
  node_rejected: '节点拒绝',
  node_skipped: '节点跳过',
  workflow_succeeded: '工作流成功',
  workflow_failed: '工作流失败',
  workflow_cancelled: '工作流取消',
};
export const managementJobKinds: Record<string, string> = {
  auto_cycle: '每日扫描',
  auto_run: '运行分析',
  auto_draft: '优化草稿',
  auto_notice: '任务通知',
};
export const managementJobStatuses: Record<string, string> = {
  pending: '待处理',
  dispatching: '正在提交',
  waiting: '等待结果',
  retryable: '等待重试',
  uncertain: '结果待核对',
  done: '已结束',
  blocked: '需要处理',
  attention: '通知异常',
};
export const managementReasons: Record<string, string> = {
  AUTOMATIC_CHAIN_INCOMPLETE: '批次链有未解决项，等待处理',
  SOURCE_CHANGED: '证据已变化，需重新核对',
  BASELINE_REASSESSMENT_REQUIRED: '基线已变化，原建议待重评估',
  NO_ELIGIBLE_SCOPE: '当前没有可安全自动纳入的达标项',
  OWNER_MISSING: '工作流缺少有效负责人',
  OWNER_PERMISSION_DENIED: '负责人权限已变化，尚未派发',
  OWNER_OR_POLICY_CHANGED: '负责人或策略已变化，请核对原任务',
  AUTOMATION_DISABLED: '自动分析已关闭，已有任务继续核对',
  ANALYSIS_FAILED: '分析失败',
  PARTIAL_ANALYSIS: '分析包含未知项，证据不完整',
  ANALYSIS_OUTCOME_UNKNOWN: '原分析结果未知，先核对原请求',
  DRAFT_REQUEST_REQUIRES_LOOKUP: '正在核对原草稿请求',
  NOTIFICATION_OUTCOME_UNKNOWN: '通知结果未知，不会自动重发',
  NOTIFICATION_REJECTED: '通知未送达',
  NOTIFICATION_SETTINGS_INVALID: '通知配置格式有误，尚未发送',
  DRAFT_REQUIRES_MANUAL_REVIEW: '草稿需要人工处理',
  CANDIDATE_SUPERSEDED: '候选已变化，旧通知已停用',
};
export function managementJobStatus(job: ManagementJob): string {
  if (job.kind === 'auto_run' && job.status === 'done') {
    if (job.complete === false || job.reason === 'PARTIAL_ANALYSIS') return '分析结束，证据不完整';
    return job.complete === true ? '分析结束' : '分析结束，证据完整性待核对';
  }
  return managementJobStatuses[job.status] ?? job.status;
}
/** Actual application routes, not clawweb-only /runs or /workflows URLs. */
export function managementJobLink(workflowId: string, job: ManagementJob): string | null {
  if (!job.flowId && !job.taskId) return null;
  const params = new URLSearchParams({
    workflowId,
    view: job.flowId ? 'runs' : 'issues',
    tab: job.flowId ? 'overview' : 'evolution',
  });
  if (job.flowId) {
    params.set('runId', job.flowId);
    if (job.analysisId) params.set('analysisId', job.analysisId);
  } else if (job.taskId) {
    params.set('repairTaskId', job.taskId);
    params.set('evoTab', 'diagnosis');
  }
  return `/evolution-factory/task-escort?${params.toString()}`;
}
