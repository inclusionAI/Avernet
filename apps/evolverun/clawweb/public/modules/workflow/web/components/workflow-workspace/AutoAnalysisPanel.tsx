import { useEffect, useRef, useState } from 'react'
import type { AutomationPolicyWrite, WorkflowAutomationPolicy, AutomationJobSummary } from '../../../server/contracts/workflow-automation'
import { useQueryClient } from '@tanstack/react-query'
import AutomationHistory, { NotificationAttention } from './AutomationHistory'
import { retryAutomationFailures, useAutomationPolicy, useAutomationStatus, useSaveAutomationPolicy } from '../../api/workflow-automation'
type Section = 'environment' | 'analysis' | 'optimization' | 'notification'
type Feedback = { section: Section; text: string; state: 'pending' | 'success' | 'error' }
export default function AutoAnalysisPanel({ workflowId }: { workflowId: string }) {
  const queryClient = useQueryClient()
  const [history, setHistory] = useState<Exclude<Section, 'environment'> | ''>('')
  const setting = useAutomationPolicy(workflowId), update = useSaveAutomationPolicy(workflowId), status = useAutomationStatus(workflowId, !!history)
  const [impact, setImpact] = useState<'medium' | 'high'>('medium')
  const [time, setTime] = useState('09:00'), [feedback, setFeedback] = useState<Feedback | null>(null), [retrying, setRetrying] = useState('')
  const retryBusy = useRef(false)
  const [recipients, setRecipients] = useState(''), [saving, setSaving] = useState('')
  const [pendingChanges, setPendingChanges] = useState<Partial<AutomationPolicyWrite>>({})
  const busy = useRef(false), previous = useRef<WorkflowAutomationPolicy | undefined>(undefined)
  useEffect(() => {
    if (feedback?.state !== 'success') return
    const timer = setTimeout(() => setFeedback(null), 2500)
    return () => clearTimeout(timer)
  }, [feedback])
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
  const save = (changes: Partial<AutomationPolicyWrite>, section: Section, action: string, done: string) => {
    if (busy.current) return
    busy.current = true
    setPendingChanges(changes)
    setSaving(action)
    setFeedback({ section, text: action, state: 'pending' })
    update.mutate({ enabled: policy.enabled, autoDraft: policy.autoDraft, dailyTime: policy.dailyTime, dailyConfigured: policy.dailyConfigured, expectedVersion: policy.version, ...changes },
      { onSuccess: saved => { if (changes.notificationRecipients !== undefined) setRecipients((saved.notificationRecipients ?? []).join(', ')); setFeedback({ section, text: done, state: 'success' }) },
        onError: error => { setFeedback({ section, text: error.message, state: 'error' }); void setting.refetch() }, onSettled: () => { busy.current = false; setSaving(''); setPendingChanges({}) } })
  }
  const showFeedback = (section: Section) => feedback?.section === section && <p role={feedback.state === 'error' ? 'alert' : 'status'} className={`text-xs ${feedback.state === 'error' ? 'text-destructive' : 'text-muted-foreground'}`}>{feedback.text}</p>
  const historyButton = (section: Exclude<Section, 'environment'>) => <button type="button" aria-expanded={history === section} aria-controls={`automation-history-${section}`}
    onClick={() => setHistory(current => current === section ? '' : section)} className="text-xs text-primary underline">
    {history === section ? '收起记录' : section === 'notification' ? '查看通知异常' : '查看记录'}</button>
  const switchStyle = 'relative inline-flex h-6 w-11 shrink-0 items-center rounded-full transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary disabled:cursor-wait disabled:opacity-60'
  const retry = { jobs: status.isError ? [] : status.data?.retryableJobs ?? [], pendingId: retrying, async onRetry(job: AutomationJobSummary) {
    if (retryBusy.current) return
    const section = job.kind === 'auto_run' ? 'analysis' : job.kind === 'auto_draft' ? 'optimization' : 'notification'
    retryBusy.current = true; setRetrying(job.id); setFeedback({ section, text: '正在提交本条重试…', state: 'pending' })
    try { await retryAutomationFailures(workflowId, job.id); setFeedback({ section, text: '本条任务已加入重试队列，请在对应记录查看进度', state: 'success' }) }
    catch (error) { setFeedback({ section, text: error instanceof Error ? error.message : '本条任务重试未提交', state: 'error' }) }
    finally {
      await Promise.allSettled([status.refetch(), queryClient.invalidateQueries({ queryKey: ['workflow-automation-records', workflowId] })])
      setRetrying(''); retryBusy.current = false
    }
  } }
  return <div className="space-y-4">
    <section aria-label="自动任务执行环境" className="flex flex-wrap items-center gap-x-3 gap-y-2 border-b border-border pb-3">
      <label className="flex items-center gap-3 text-sm font-medium">自动任务执行环境
        <select aria-label="自动任务执行环境" value={pendingChanges.executionEnvironment ?? policy.executionEnvironment ?? 'prod'} disabled={pending}
          onChange={event => save({ executionEnvironment: event.target.value as 'pre' | 'prod' }, 'environment', '执行环境保存中…', '执行环境已保存')}
          className="rounded-md border border-input bg-background px-2 py-1.5 text-foreground">
          <option value="pre">预发</option><option value="prod">生产</option>
        </select>
      </label>
      <p className="text-xs text-muted-foreground">选择后立即保存，自动分析、优化和通知由所选环境处理。</p>
      {showFeedback('environment')}
    </section>
    <section aria-label="自动分析设置" className="space-y-3 rounded-xl border border-input bg-card p-4">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div className="flex items-center gap-3"><h3 className="font-medium text-foreground">自动分析</h3>{historyButton('analysis')}</div>
      <div className="flex shrink-0 items-center gap-2 text-sm"><span>{pendingChanges.enabled !== undefined ? '保存中…' : policy.enabled ? '已开启' : '已关闭'}</span>
      <button type="button" role="switch" aria-label="自动分析" aria-checked={enabled} disabled={pending}
        aria-busy={pending} onClick={() => save({ enabled: !policy.enabled }, 'analysis', policy.enabled ? '自动分析关闭中…' : '自动分析开启中…', policy.enabled ? '自动分析已关闭；已有任务继续处理' : '自动分析已开启')}
        className={`${switchStyle} ${enabled ? 'bg-primary' : 'bg-muted-foreground/40'}`}>
        <span className={`h-4 w-4 rounded-full bg-background shadow-sm transition-transform ${enabled ? 'translate-x-6' : 'translate-x-1'}`} /></button></div>
    </div>
    <p className="text-xs text-muted-foreground">失败后分析运行问题并生成修复建议，不修改工作流。开关即时保存，已有任务继续处理。</p>
    <div className="space-y-3 border-t border-border pt-3">
    <h4 className="text-sm font-medium">每日分析计划 <span className="ml-2 text-xs font-normal text-muted-foreground">{!policy.dailyConfigured ? '尚未启用' : policy.enabled ? '已开启' : '已暂停'}</span></h4>
    <div className="flex flex-wrap items-end gap-3">
      <label className="text-sm text-foreground">每日执行时间 <span className="text-muted-foreground">Asia/Shanghai</span>
        <input type="time" aria-label="每日执行时间" value={time} disabled={!policy.enabled || pending} onChange={event => setTime(event.target.value)} className="mt-2 block rounded-md border border-input bg-background px-3 py-2 text-foreground" /></label>
      <label className="text-sm text-foreground">最低影响<select aria-label="最低影响" value={impact} onChange={event => setImpact(event.target.value as 'medium' | 'high')} disabled={!policy.enabled || pending} className="mt-2 block rounded-md border border-input bg-background px-3 py-2"><option value="medium">中等及以上</option><option value="high">高等及以上</option></select></label>
      <button type="button" disabled={!policy.enabled || pending || !time || policy.dailyConfigured && time === policy.dailyTime && impact === (policy.minimumImpact ?? 'medium') && policy.scopeVersion === 'all-qualified/v1'}
        onClick={() => save({ dailyTime: time, minimumImpact: impact, dailyConfigured: true }, 'analysis', '分析计划保存中…', '分析计划已保存，按此时间和范围执行')}
        className="rounded-md border border-input px-3 py-2 text-sm disabled:opacity-50">{saving === '分析计划保存中…' ? '保存中…' : policy.dailyConfigured ? '保存分析计划' : '保存并启用每日分析'}</button>
    </div>
    <p className="text-xs text-muted-foreground">{!policy.dailyConfigured ? '显示的时间仅为待保存设置，尚未启动每日分析；失败后自动分析由上方开关控制。' : '分析前一自然日运行及历史未处理的达标建议。'}</p>
    </div>
    {showFeedback('analysis')}
    {history === 'analysis' && <div id="automation-history-analysis"><AutomationHistory key={`${workflowId}:analysis`} kind="analysis" workflowId={workflowId} retry={retry} /></div>}
    </section>
    <section aria-label="自动优化" className="space-y-3 rounded-xl border border-input bg-card p-4">
      <div className="flex items-center gap-3"><h3 className="font-medium text-foreground">自动优化</h3>{historyButton('optimization')}</div>
      {policy.dailyConfigured ? <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" aria-label="自动生成优化草稿" checked={pendingChanges.autoDraft ?? policy.autoDraft} disabled={!policy.enabled || !policy.dailyConfigured || pending}
          onChange={event => save({ autoDraft: event.target.checked }, 'optimization', event.target.checked ? '自动优化开启中…' : '自动优化关闭中…', '自动优化设置已保存')}
          className="h-4 w-4 shrink-0 accent-primary" />
        自动生成优化草稿
      </label> : <div className="space-y-2">
        <p className="text-sm">尚未开启自动优化</p>
        {policy.enabled ? <>
          <p className="text-xs text-muted-foreground">将保存上方计划：每天 {time || '（请选择时间）'}（上海时间），{impact === 'high' ? '高等及以上' : '中等及以上'}影响；并开启自动优化。</p>
          <button type="button" disabled={pending || !time}
            onClick={() => save({ dailyTime: time, minimumImpact: impact, dailyConfigured: true, autoDraft: true }, 'optimization', '正在保存计划并开启自动优化…', '每日分析计划和自动优化已开启，按计划执行')}
            className="rounded-md bg-primary px-3 py-2 text-sm text-primary-foreground disabled:opacity-50">{feedback?.section === 'optimization' && pending ? '开启中…' : '保存计划并开启自动优化'}</button>
        </> : <button type="button" disabled={pending}
          onClick={() => save({ enabled: true }, 'optimization', '自动分析开启中…', '自动分析已开启，请确认上方每日计划后开启自动优化')}
          className="rounded-md border border-input px-3 py-2 text-sm disabled:opacity-50">开启自动分析</button>}
      </div>}
      <p className="text-xs text-muted-foreground">基于每日分析计划生成草稿，仍需审阅确认发布，不会自动部署。</p>
      {!policy.enabled && <p className="text-xs text-muted-foreground">自动分析已关闭，开启后才能按每日计划自动优化；已有任务继续处理。</p>}
      {showFeedback('optimization')}
      {history === 'optimization' && <div id="automation-history-optimization"><AutomationHistory key={`${workflowId}:optimization`} kind="optimization" workflowId={workflowId} retry={retry} /></div>}
    </section>
    <section aria-label="进化任务通知" className="space-y-3 rounded-xl border border-input bg-card p-4">
      <div className="flex items-center gap-3"><h3 className="font-medium text-foreground">进化任务通知</h3>{historyButton('notification')}</div>
      <p className="text-xs text-muted-foreground">手动和自动优化任务完成或失败时发送钉钉通知，不依赖自动分析开关。</p>
      <div className="flex flex-wrap items-end gap-3">
      <label className="block w-full max-w-sm text-sm">通知人（工号）<input aria-label="通知人" value={recipients} disabled={pending} onChange={event => setRecipients(event.target.value)}
        placeholder="留空通知 Owner；多个工号用逗号分隔" className="mt-2 block w-full rounded-md border border-input bg-background px-3 py-2" /></label>
      <button type="button" disabled={pending || recipients === (policy.notificationRecipients ?? []).join(', ')}
        onClick={() => save({ notificationRecipients: [...new Set(recipients.split(/[,，;；\s]+/).filter(Boolean))], expectedNotificationVersion: policy.notificationVersion ?? 0 }, 'notification', '正在保存通知人…', '通知人已保存')}
        className="rounded-md border border-input px-3 py-2 text-sm disabled:opacity-50">{saving === '正在保存通知人…' ? '保存中…' : '保存通知人'}</button>
      </div>
      <p className="text-xs text-muted-foreground">留空通知负责人（Owner）；填写后仅通知所填工号，最多 20 人。</p>
      {showFeedback('notification')}
      {history === 'notification' && <div id="automation-history-notification"><NotificationAttention key={workflowId} workflowId={workflowId} retry={retry} /></div>}
    </section>
  </div>
}
