import { useEffect, useRef, useState } from 'react'
import type { AutomationPolicyWrite, WorkflowAutomationPolicy } from '../../../server/contracts/workflow-automation'
import { useQueryClient } from '@tanstack/react-query'
import JobRecord, { reasons } from './AutomationJobRecord'
import AutomationHistory from './AutomationHistory'
import { retryAutomationFailures, useAutomationPolicy, useAutomationStatus, useSaveAutomationPolicy } from '../../api/workflow-automation'
export default function AutoAnalysisPanel({ workflowId }: { workflowId: string }) {
  const queryClient = useQueryClient()
  const setting = useAutomationPolicy(workflowId), update = useSaveAutomationPolicy(workflowId), status = useAutomationStatus(workflowId)
  const [impact, setImpact] = useState<'medium' | 'high'>('medium')
  const [time, setTime] = useState('09:00'), [message, setMessage] = useState(''), [retrying, setRetrying] = useState(false)
  const [recipients, setRecipients] = useState(''), [saving, setSaving] = useState('')
  const [pendingChanges, setPendingChanges] = useState<Partial<AutomationPolicyWrite>>({})
  const busy = useRef(false), previous = useRef<WorkflowAutomationPolicy | undefined>(undefined)
  useEffect(() => {
    const next = setting.data
    if (!next) return
    const prior = previous.current, changedWorkflow = prior?.workflowId !== workflowId
    setTime(value => changedWorkflow || value === prior?.dailyTime ? next.dailyTime : value)
    setImpact(value => changedWorkflow || value === (prior?.minimumImpact ?? 'medium') ? next.minimumImpact ?? 'medium' : value)
    setRecipients(value => changedWorkflow || value === (prior?.notificationRecipients ?? []).join(', ') ? (next.notificationRecipients ?? []).join(', ') : value)
    previous.current = next
  }, [setting.data?.version, setting.data?.notificationVersion, workflowId])
  if (setting.isLoading) return <p className="py-3 text-sm text-muted-foreground">正在读取自动分析配置…</p>
  if (!setting.data) return <div role="alert" className="text-sm text-destructive">配置加载失败。<button className="ml-2 underline" onClick={() => void setting.refetch()}>重试</button></div>
  const policy = setting.data
  const pending = !!saving || update.isPending
  const enabled = pendingChanges.enabled ?? policy.enabled
  const save = (changes: Partial<AutomationPolicyWrite>, action: string, done: string) => {
    if (busy.current) return
    busy.current = true
    setPendingChanges(changes)
    setSaving(action)
    setMessage('')
    update.mutate({ enabled: policy.enabled, autoDraft: policy.autoDraft, dailyTime: policy.dailyTime, dailyConfigured: policy.dailyConfigured, expectedVersion: policy.version, ...changes },
      { onSuccess: saved => { if (changes.notificationRecipients !== undefined) setRecipients((saved.notificationRecipients ?? []).join(', ')); setMessage(done) },
        onError: error => { setMessage(error.message); void setting.refetch() }, onSettled: () => { busy.current = false; setSaving(''); setPendingChanges({}) } })
  }
  const switchStyle = 'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-wait disabled:opacity-60'
  return <div className="space-y-5">
    <section aria-label="自动分析设置" className="space-y-4 rounded-xl border border-input bg-card p-5">
    <div className="flex items-start justify-between gap-5">
      <div><h3 className="font-medium text-foreground">1. 自动分析</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">自动分析运行问题，产出诊断和修复建议，不修改工作流。</p></div>
      <div className="flex shrink-0 items-center gap-2 text-sm"><span>{pendingChanges.enabled !== undefined ? '保存中…' : policy.enabled ? '已开启' : '已关闭'}</span>
      <button type="button" role="switch" aria-label="自动分析" aria-checked={enabled} disabled={pending}
        aria-busy={pending} onClick={() => save({ enabled: !policy.enabled }, policy.enabled ? '自动分析关闭中…' : '自动分析开启中…', policy.enabled ? '自动分析已关闭；已有任务继续处理' : '自动分析已开启')}
        className={`${switchStyle} ${enabled ? 'bg-primary' : 'bg-muted-foreground/40'}`}>
        <span className={`h-4 w-4 rounded-full bg-background shadow-sm transition-transform ${enabled ? 'translate-x-6' : 'translate-x-1'}`} /></button></div>
    </div>
    <p className="text-sm text-muted-foreground">失败后自动分析：{policy.enabled ? '已开启' : '已关闭'}；每日定时分析：{!policy.dailyConfigured ? '未配置' : policy.enabled ? '已开启' : '已暂停'}。</p>
    <div className="space-y-3 border-t border-border pt-4">
    <h4 className="text-sm font-medium">每日分析计划</h4>
    <p className="text-xs leading-5 text-muted-foreground">保存下方时间和最低影响等级后启用每日计划。按上海时间分析前一自然日运行，并纳入历史未处理的达标建议。</p>
    <div className="flex flex-wrap items-end gap-3">
      <label className="text-sm text-foreground">每日执行时间 <span className="text-muted-foreground">Asia/Shanghai</span>
        <input type="time" aria-label="每日执行时间" value={time} disabled={!policy.enabled || pending} onChange={event => setTime(event.target.value)} className="mt-2 block rounded-md border border-input bg-background px-3 py-2 text-foreground" /></label>
      <label className="text-sm text-foreground">最低影响<select aria-label="最低影响" value={impact} onChange={event => setImpact(event.target.value as 'medium' | 'high')} disabled={!policy.enabled || pending} className="mt-2 block rounded-md border border-input bg-background px-3 py-2"><option value="medium">中等及以上</option><option value="high">高等及以上</option></select></label>
      <button type="button" disabled={!policy.enabled || pending || !time || policy.dailyConfigured && time === policy.dailyTime && impact === (policy.minimumImpact ?? 'medium') && policy.scopeVersion === 'all-qualified/v1'}
        onClick={() => save({ dailyTime: time, minimumImpact: impact, dailyConfigured: true }, '分析计划保存中…', '分析计划已保存，按此时间和范围执行')}
        className="rounded-md border border-input px-3 py-2 text-sm disabled:opacity-50">{saving === '分析计划保存中…' ? '保存中…' : '保存分析计划'}</button>
    </div>
    </div>
    <p className="text-xs leading-5 text-muted-foreground">开关即时保存。关闭后停止新的自动派发，已有任务继续处理；手动分析与手动优化不受影响。</p>
    </section>
    <section aria-label="自动优化" className="space-y-3 rounded-xl border border-input bg-card p-5">
      <h3 className="font-medium text-foreground">2. 自动优化</h3>
      <p className="text-sm leading-6 text-muted-foreground">在每日分析计划的基础上，根据修复建议调用 AIS，自动生成优化草稿供你审阅。</p>
      <label className="flex items-center justify-between gap-4 rounded-lg bg-muted p-4 text-sm">
        <span>自动生成优化草稿<span className="mt-1 block text-xs leading-5 text-muted-foreground">草稿仍需你审阅并确认发布，不会自动部署。</span></span>
        <input type="checkbox" aria-label="自动生成优化草稿" checked={pendingChanges.autoDraft ?? policy.autoDraft} disabled={!policy.enabled || !policy.dailyConfigured || pending}
          onChange={event => save({ autoDraft: event.target.checked }, event.target.checked ? '自动优化开启中…' : '自动优化关闭中…', '自动优化设置已保存')}
          className="h-4 w-4 shrink-0 accent-primary" />
      </label>
      {(!policy.enabled || !policy.dailyConfigured) && <p className="text-xs text-muted-foreground">请先开启自动分析并保存每日分析计划，再开启自动优化。</p>}
    </section>
    <section aria-label="进化任务通知" className="space-y-3 rounded-xl border border-input bg-card p-5">
      <h3 className="font-medium text-foreground">3. 进化任务通知</h3>
      <p className="text-sm leading-6 text-muted-foreground">手动和自动生成的 AIS 优化任务完成后，发送钉钉通知并附任务链接；失败也会通知。此设置不依赖自动分析开关。</p>
      <label className="block text-sm">通知人<input aria-label="通知人" value={recipients} disabled={pending} onChange={event => setRecipients(event.target.value)}
        placeholder="留空默认通知 Owner；多个账号 ID 用逗号分隔" className="mt-2 block w-full max-w-xl rounded-md border border-input bg-background px-3 py-2" /></label>
      <p className="text-xs text-muted-foreground">留空通知工作流负责人（Owner）；填写后仅通知所填账号，最多 20 人。</p>
      <button type="button" disabled={pending || recipients === (policy.notificationRecipients ?? []).join(', ')}
        onClick={() => save({ notificationRecipients: [...new Set(recipients.split(/[,，;；\s]+/).filter(Boolean))], expectedNotificationVersion: policy.notificationVersion ?? 0 }, '正在保存通知人…', '通知人已保存')}
        className="rounded-md border border-input px-3 py-2 text-sm disabled:opacity-50">{saving === '正在保存通知人…' ? '保存中…' : '保存通知人'}</button>
    </section>
    {(saving || message) && <p role="status" aria-live="polite" className="sticky bottom-3 z-10 rounded-lg border border-border bg-background px-4 py-3 text-sm shadow-sm">{saving || message}</p>}
    <section aria-label="自动分析执行状态" className="rounded-xl border border-input bg-card p-5 text-sm">
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
