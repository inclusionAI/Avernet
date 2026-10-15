import { useEffect, useState } from 'react'
import { retryAutomationFailures, useAutomationPolicy, useAutomationStatus, useSaveAutomationPolicy } from '../../api/workflow-automation'
const reasons: Record<string, string> = {
  OWNER_MISSING: '当前工作流没有有效负责人，保留进度等待恢复', OWNER_PERMISSION_DENIED: '负责人权限已变化，尚未派发',
  OWNER_OR_POLICY_CHANGED: '负责人或策略已变化，请人工核对原任务', AUTOMATION_DISABLED: '自动分析已关闭；已派发任务继续核对',
  ANALYSIS_FAILED: '分析未完成，可重试已明确失败的部分', PARTIAL_ANALYSIS: '分析包含未知项，未视为完整覆盖',
  ANALYSIS_OUTCOME_UNKNOWN: '原分析请求结果未知，先核对原请求', DRAFT_REQUEST_REQUIRES_LOOKUP: '正在核对原草稿请求',
  NOTIFICATION_OUTCOME_UNKNOWN: '通知结果未知，请管理员核对；不会自动重发', NOTIFICATION_REJECTED: '通知未送达，只重试通知',
  DRAFT_REQUIRES_MANUAL_REVIEW: '原候选需要人工处理，不重复生成同一方案', CANDIDATE_SUPERSEDED: '候选已变化，旧通知已停用',
}
const kinds: Record<string, string> = { auto_cycle: '每日扫描', auto_run: '运行分析', auto_draft: '优化草稿', auto_notice: 'Owner 通知' }
const statuses: Record<string, string> = { pending: '待处理', dispatching: '正在提交', waiting: '核对中', retryable: '等待重试', uncertain: '待核对', done: '已完成', blocked: '需要处理' }
export default function AutoAnalysisPanel({ workflowId }: { workflowId: string }) {
  const setting = useAutomationPolicy(workflowId), update = useSaveAutomationPolicy(workflowId), status = useAutomationStatus(workflowId)
  const [time, setTime] = useState('09:00'), [message, setMessage] = useState(''), [retrying, setRetrying] = useState(false)
  useEffect(() => { if (setting.data) setTime(setting.data.dailyTime) }, [setting.data?.version, workflowId])
  if (setting.isLoading) return <p className="py-3 text-sm text-muted-foreground">正在读取自动分析配置…</p>
  if (setting.isError || !setting.data) return <div role="alert" className="text-sm text-destructive">配置加载失败。<button className="ml-2 underline" onClick={() => void setting.refetch()}>重试</button></div>
  const policy = setting.data
  const save = (changes: Partial<{ enabled: boolean; autoDraft: boolean; dailyTime: string }>) => {
    setMessage('')
    update.mutate({ enabled: policy.enabled, autoDraft: policy.autoDraft, dailyTime: time, expectedVersion: policy.version, ...changes },
      { onSuccess: () => setMessage('策略已保存'), onError: error => { setMessage(error.message); void setting.refetch() } })
  }
  return <div className="space-y-5 rounded-xl border border-input bg-card p-5">
    <div className="flex items-start justify-between gap-5">
      <div><h3 className="font-medium text-foreground">自动分析</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">每日增量分析新运行，合并历史待处理问题；失败事件使用同一开关。手动分析与手动选择始终可用。</p></div>
      <button type="button" role="switch" aria-label="自动分析" aria-checked={policy.enabled} disabled={update.isPending}
        onClick={() => save({ enabled: !policy.enabled })} className={`shrink-0 rounded-full border px-4 py-2 text-sm ${policy.enabled ? 'border-primary bg-primary text-primary-foreground' : 'border-input bg-background text-muted-foreground'}`}>
        {policy.enabled ? '已开启' : '已关闭'}</button>
    </div>
    {policy.enabled && !policy.dailyConfigured && <p className="text-sm text-muted-foreground">原失败事件设置继续有效。请保存每日时间，明确启用每日增量调度。</p>}
    <div className="flex flex-wrap items-end gap-3 border-t border-border pt-4">
      <label className="text-sm text-foreground">每日执行时间 <span className="text-muted-foreground">Asia/Shanghai</span>
        <input type="time" aria-label="每日执行时间" value={time} disabled={!policy.enabled || update.isPending} onChange={event => setTime(event.target.value)} className="mt-2 block rounded-md border border-input bg-background px-3 py-2 text-foreground" /></label>
      <button type="button" disabled={!policy.enabled || update.isPending || !time} onClick={() => save({ dailyTime: time })} className="rounded-md border border-input px-3 py-2 text-sm">保存时间</button>
    </div>
    <label className="flex items-start gap-3 rounded-lg bg-muted p-4 text-sm text-foreground">
      <input type="checkbox" aria-label="自动生成优化草稿" checked={policy.autoDraft} disabled={!policy.enabled || update.isPending} onChange={event => save({ autoDraft: event.target.checked })} className="mt-1 accent-primary" />
      <span>自动生成优化草稿<span className="mt-1 block leading-6 text-muted-foreground">只选择有证据且适用于当前版本的范围。无法安全取舍的冲突方案留给你；草稿仍需审阅并确认发布，不会自动部署。</span></span>
    </label>
    <p className="text-xs leading-5 text-muted-foreground">关闭开关立即停止新的自动派发。已派发任务保留并继续核对，不自动取消或回滚。</p>
    {message && <p role="status" className="text-sm text-foreground">{message}</p>}
    <section aria-label="自动分析执行状态" className="border-t border-border pt-4 text-sm">
      <h4 className="font-medium text-foreground">最近执行</h4>
      {status.data?.pauseReason && <p className="mt-2 text-muted-foreground">{reasons[status.data.pauseReason] ?? '请核对策略和当前负责人'}</p>}
      {status.isError ? <p className="mt-2 text-muted-foreground">{status.error.message}</p> : <>
        <p className="mt-2 text-muted-foreground">{status.data?.lastScannedAtMs ? `扫描进度：${new Date(status.data.lastScannedAtMs).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}` : '尚未执行每日扫描'}</p>
        {status.data?.coverage && <p className="mt-2 text-xs leading-5 text-muted-foreground">分析记录 {status.data.coverage.total}；待完成 {status.data.coverage.pending}，明确失败 {status.data.coverage.failed}，部分覆盖 {status.data.coverage.partial}，结果未知 {status.data.coverage.unknown}。扫描完成不代表分析覆盖完整。</p>}
        <ul className="mt-3 space-y-2">{status.data?.jobs.filter(job => job.kind !== 'auto_cycle').slice(0, 8).map(job => <li key={job.id} className="rounded-md border border-border p-3">
          <span>{kinds[job.kind] ?? '自动任务'} · {statuses[job.status] ?? job.status}</span>
          {job.reason && <p className="mt-1 text-xs text-muted-foreground">{reasons[job.reason] ?? '请核对当前策略、负责人和原任务状态'}</p>}
          {job.taskId && <a className="mt-1 inline-block text-primary underline" href={`/workflows/workspace?workflowId=${encodeURIComponent(workflowId)}&tab=evolution&repairTaskId=${encodeURIComponent(job.taskId)}`}>查看原优化任务</a>}
        </li>)}</ul>
      </>}
      <button className="mt-3 text-primary underline disabled:opacity-50" disabled={retrying} onClick={async () => {
        setRetrying(true); try { await retryAutomationFailures(workflowId); await status.refetch(); setMessage('已恢复明确失败的任务；结果未知的请求仍需先核对') }
        catch (error) { setMessage(error instanceof Error ? error.message : '重试未完成') } finally { setRetrying(false) }
      }}>重试明确失败的部分</button>
    </section>
  </div>
}
