import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import JobRecord, { reasons } from './AutomationJobRecord'
import AutomationHistory from './AutomationHistory'
import { retryAutomationFailures, useAutomationPolicy, useAutomationStatus, useSaveAutomationPolicy } from '../../api/workflow-automation'
export default function AutoAnalysisPanel({ workflowId }: { workflowId: string }) {
  const queryClient = useQueryClient()
  const setting = useAutomationPolicy(workflowId), update = useSaveAutomationPolicy(workflowId), status = useAutomationStatus(workflowId)
  const [impact, setImpact] = useState<'medium' | 'high'>('medium')
  const [time, setTime] = useState('09:00'), [message, setMessage] = useState(''), [retrying, setRetrying] = useState(false)
  useEffect(() => { if (setting.data) { setTime(setting.data.dailyTime); setImpact(setting.data.minimumImpact ?? 'medium') } }, [setting.data?.version, workflowId])
  if (setting.isLoading) return <p className="py-3 text-sm text-muted-foreground">正在读取自动分析配置…</p>
  if (setting.isError || !setting.data) return <div role="alert" className="text-sm text-destructive">配置加载失败。<button className="ml-2 underline" onClick={() => void setting.refetch()}>重试</button></div>
  const policy = setting.data
  const save = (changes: Partial<{ enabled: boolean; autoDraft: boolean; dailyTime: string; minimumImpact: 'medium' | 'high' }>) => {
    setMessage('')
    update.mutate({ enabled: policy.enabled, autoDraft: policy.autoDraft, dailyTime: time, expectedVersion: policy.version, ...changes },
      { onSuccess: () => setMessage('设置已保存'), onError: error => { setMessage(error.message); void setting.refetch() } })
  }
  return <div className="space-y-5 rounded-xl border border-input bg-card p-5">
    <div className="flex items-start justify-between gap-5">
      <div><h3 className="font-medium text-foreground">自动分析</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">失败后自动分析与每日定时分析共用此开关；每日定时是否配置见下方。手动分析与手动选择始终可用。</p></div>
      <button type="button" role="switch" aria-label="自动分析" aria-checked={policy.enabled} disabled={update.isPending}
        onClick={() => save({ enabled: !policy.enabled })} className={`shrink-0 rounded-full border px-4 py-2 text-sm ${policy.enabled ? 'border-primary bg-primary text-primary-foreground' : 'border-input bg-background text-muted-foreground'}`}>
        {policy.enabled ? '自动分析已开启' : '自动分析已关闭'}</button>
    </div>
    <p className="text-sm text-muted-foreground">失败后自动分析：{policy.enabled ? '已开启' : '已关闭'}；每日定时分析：{!policy.dailyConfigured ? '未配置' : policy.enabled ? '已开启' : '已暂停'}。</p>
    {!policy.dailyConfigured && <p className="text-sm text-muted-foreground">保存设置后启用每日定时分析；已有失败事件分析记录不受影响。</p>}
    <p className="text-xs text-muted-foreground">{policy.scopeVersion === 'all-qualified/v1' ? '按最低影响等级处理前一自然日及历史未处理的建议。' : '当前保留原有建议范围；保存设置后，按所选最低影响等级纳入前一自然日及历史未处理的建议。'}</p>
    <div className="flex flex-wrap items-end gap-3 border-t border-border pt-4">
      <label className="text-sm text-foreground">每日执行时间 <span className="text-muted-foreground">Asia/Shanghai</span>
        <input type="time" aria-label="每日执行时间" value={time} disabled={!policy.enabled || update.isPending} onChange={event => setTime(event.target.value)} className="mt-2 block rounded-md border border-input bg-background px-3 py-2 text-foreground" /></label>
      <label className="text-sm text-foreground">最低影响<select aria-label="最低影响" value={impact} onChange={event => setImpact(event.target.value as 'medium' | 'high')} disabled={!policy.enabled || update.isPending} className="mt-2 block rounded-md border border-input bg-background px-3 py-2"><option value="medium">中等及以上</option><option value="high">高等及以上</option></select></label>
      <button type="button" disabled={!policy.enabled || update.isPending || !time} onClick={() => save({ dailyTime: time, minimumImpact: impact })} className="rounded-md border border-input px-3 py-2 text-sm">保存设置</button>
    </div>
    <label className="flex items-start gap-3 rounded-lg bg-muted p-4 text-sm text-foreground">
      <input type="checkbox" aria-label="自动生成优化草稿" checked={policy.autoDraft} disabled={!policy.enabled || update.isPending} onChange={event => save({ autoDraft: event.target.checked })} className="mt-1 accent-primary" />
      <span>自动生成优化草稿<span className="mt-1 block leading-6 text-muted-foreground">只选择有证据且适用于当前版本的范围。无法安全取舍的冲突方案留给你；草稿仍需审阅并确认发布，不会自动部署。</span></span>
    </label>
    <p className="text-xs leading-5 text-muted-foreground">关闭开关立即停止新的自动派发。已派发任务保留并继续核对，不自动取消或回滚。</p>
    {message && <p role="status" className="text-sm text-foreground">{message}</p>}
    <section aria-label="自动分析执行状态" className="border-t border-border pt-4 text-sm">
      <h4 className="font-medium text-foreground">执行记录</h4>
      {status.data?.round && <p className="mt-2 text-muted-foreground">本轮纳入 {status.data.round.total} 项；已完成 {status.data.round.completedBatches}/{status.data.round.batches} 批。保留待处理 {Object.values(status.data.round.deferred).reduce((a, b) => a + b, 0)} 项。<span className="block text-xs">按运行发生时间纳入前一自然日及历史未处理项；证据截至 {new Date(status.data.round.evidenceCutoffMs).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}。</span></p>}
      {status.data?.pauseReason && <p className="mt-2 text-muted-foreground">{reasons[status.data.pauseReason] ?? '请核对策略和当前负责人'}</p>}
      {status.isError ? <p className="mt-2 text-muted-foreground">{status.error.message}</p> : <>
        <p className="mt-2 text-muted-foreground">{status.data?.lastScannedAtMs ? `扫描进度：${new Date(status.data.lastScannedAtMs).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}` : '每日扫描尚无记录；失败事件触发的分析单独记录在下方。'}</p>
        {status.data?.coverage && <p className="mt-2 text-xs leading-5 text-muted-foreground">分析记录 {status.data.coverage.total}；待完成 {status.data.coverage.pending}，明确失败 {status.data.coverage.failed}，部分覆盖 {status.data.coverage.partial}，结果未知 {status.data.coverage.unknown}。扫描完成不代表分析覆盖完整。</p>}
      </>}
      <AutomationHistory key={workflowId} workflowId={workflowId} />
      {!status.isError && status.data && <section aria-label="失败任务重试" className="mt-5 border-t border-border pt-4">
        <h4 className="mb-3 font-medium">失败任务重试</h4>
        {status.data.retryableJobs?.length ? <>
          <p className="text-muted-foreground">将重试以下 {status.data.retryableJobs.length} 个失败任务；分析重试仅针对列出的运行，草稿重试沿用原优化任务，通知重试只重新发送通知。</p>
          <ul aria-label="将重试的失败任务" className="mt-2 space-y-2">{status.data.retryableJobs.map(job => <li key={job.id} className="rounded-md border border-border p-3"><JobRecord job={job} workflowId={workflowId} /></li>)}</ul>
          <p className="mt-2 text-xs text-muted-foreground">运行中或结果未知的请求继续核对原请求。执行前会重新检查状态和权限。</p>
          <button className="mt-3 text-primary underline disabled:opacity-50" disabled={retrying} onClick={async () => {
            setRetrying(true); try { await retryAutomationFailures(workflowId); await Promise.all([status.refetch(), queryClient.invalidateQueries({ queryKey: ['workflow-automation-records', workflowId] })]); setMessage('已提交失败任务重试；实际进度请查看执行记录') }
            catch (error) { setMessage(error instanceof Error ? error.message : '重试未完成') } finally { setRetrying(false) }
          }}>{retrying ? '提交中…' : '重试失败任务'}</button>
        </> : <p className="text-xs text-muted-foreground">{status.data.retryableJobs ? '当前没有可重试的失败任务；运行中或结果未知的请求继续核对原请求。' : '暂未获取可重试对象，请刷新后核对。'}</p>}
      </section>}
    </section>
  </div>
}
