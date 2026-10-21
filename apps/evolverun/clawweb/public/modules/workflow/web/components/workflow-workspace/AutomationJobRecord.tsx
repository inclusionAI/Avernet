import type { AutomationJobSummary } from '../../../server/contracts/workflow-automation'
export const reasons: Record<string, string> = {
  AUTOMATIC_CHAIN_INCOMPLETE: '批次链有未解决项，保留完整任务等待处理',
  SOURCE_CHANGED: '证据已变化，原范围保留，需重新核对', BASELINE_REASSESSMENT_REQUIRED: '基线已变化，原建议保留待重评估', NO_ELIGIBLE_SCOPE: '当前没有可安全自动纳入的达标项，保留项可在问题页核对',
  OWNER_MISSING: '当前工作流没有有效负责人，保留进度等待恢复', OWNER_PERMISSION_DENIED: '负责人权限已变化，尚未派发',
  OWNER_OR_POLICY_CHANGED: '负责人或策略已变化，请人工核对原任务', AUTOMATION_DISABLED: '自动分析已关闭；已派发任务继续核对',
  ANALYSIS_FAILED: '分析失败，可重试该运行的分析', PARTIAL_ANALYSIS: '分析包含未知项，未视为完整覆盖',
  ANALYSIS_OUTCOME_UNKNOWN: '原分析请求结果未知，先核对原请求', DRAFT_REQUEST_REQUIRES_LOOKUP: '正在核对原草稿请求',
  NOTIFICATION_OUTCOME_UNKNOWN: '通知结果未知，请管理员核对；不会自动重发', NOTIFICATION_REJECTED: '通知未送达，只重试通知',
  DRAFT_REQUIRES_MANUAL_REVIEW: '草稿需要处理，请查看原优化任务；可重试对象列在下方', CANDIDATE_SUPERSEDED: '候选已变化，旧通知已停用',
}
const kinds: Record<string, string> = { auto_cycle: '每日扫描', auto_run: '运行分析', auto_draft: '优化草稿', auto_notice: '任务通知' }
const statuses: Record<string, string> = { pending: '待处理', dispatching: '正在提交', waiting: '等待结果', retryable: '等待重试', uncertain: '待核对', done: '已结束', blocked: '需要处理' }
const shortId = (id: string) => id.length > 16 ? `${id.slice(0, 8)}…` : id
function jobStatus(job: AutomationJobSummary) {
  if (job.kind === 'auto_run') {
    if (job.status === 'waiting') return '分析中，等待分析结果'
    if (job.status === 'done') return job.reason === 'PARTIAL_ANALYSIS' || job.complete === false ? '分析结束，证据不完整'
      : job.complete === true ? '分析结束' : '分析结束，证据完整性待核对'
  }
  return statuses[job.status] ?? job.status
}
export default function JobRecord({ job, workflowId }: { job: AutomationJobSummary; workflowId: string }) {
  const taskLink = job.taskId ? `/workflows/workspace?workflowId=${encodeURIComponent(workflowId)}&tab=evolution&repairTaskId=${encodeURIComponent(job.taskId)}` : null
  const analysisLink = job.flowId ? `/runs/${encodeURIComponent(job.flowId)}${job.analysisId ? `?analysisId=${encodeURIComponent(job.analysisId)}` : ''}` : null
  return <>
    <span>{kinds[job.kind] ?? '自动任务'} · {jobStatus(job)}</span>
    <p className="mt-1 break-all text-xs text-muted-foreground">{job.createdAtMs ? new Date(job.createdAtMs).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' }) : '时间未记录'}{job.flowId ? ` · 运行 ${shortId(job.flowId)}` : job.taskId ? ` · 优化任务 ${shortId(job.taskId)}` : ''}</p>
    {job.reason && <p className="mt-1 text-xs text-muted-foreground">{reasons[job.reason] ?? '请核对当前策略、负责人和原任务状态'}</p>}
    {analysisLink ? <a className="mt-1 inline-block text-primary underline" href={analysisLink}>查看分析</a>
      : taskLink ? <a className="mt-1 inline-block text-primary underline" href={taskLink}>查看原优化任务</a> : null}
    <details className="mt-1 text-xs text-muted-foreground"><summary className="cursor-pointer text-primary">查看详情</summary>
      <p className="mt-1 break-all">任务编号：{job.id}；尝试次数：{job.attempts}{job.analysisId ? `；分析编号：${job.analysisId}` : ''}</p>
      {job.flowId && <p className="break-all">运行编号：{job.flowId}</p>}{job.taskId && <p className="break-all">优化任务编号：{job.taskId}</p>}
      {!analysisLink && !taskLink && <p>该记录未关联可跳转的运行或优化任务。</p>}
    </details>
  </>
}
