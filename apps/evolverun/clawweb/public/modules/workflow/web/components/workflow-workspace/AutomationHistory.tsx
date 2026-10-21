import { useState } from 'react'
import type { AutomationRecordQuery } from '../../../server/contracts/workflow-automation'
import { useAutomationRecords } from '../../api/workflow-automation'
import JobRecord from './AutomationJobRecord'
export default function AutomationHistory({ workflowId }: { workflowId: string }) {
  const [kind, setKind] = useState<'analysis' | 'optimization'>('analysis')
  const [all, setAll] = useState(false)
  const [period, setPeriod] = useState<AutomationRecordQuery['period']>('all')
  const [status, setStatus] = useState<AutomationRecordQuery['status']>('all')
  const [cursors, setCursors] = useState<string[]>([''])
  const records = useAutomationRecords(workflowId, { kind, period: all ? period : 'all', status: all ? status : 'all',
    limit: all ? 20 : 8, ...(all && cursors.at(-1) ? { cursor: cursors.at(-1) } : {}) })
  return <section aria-label="自动任务历史记录" className="mt-4 space-y-3" aria-busy={records.isFetching}>
    <div role="tablist" aria-label="自动任务记录类型" className="flex gap-2 border-b border-border">
      {([['analysis', '自动分析'], ['optimization', '自动优化']] as const).map(([value, label]) => <button key={value} type="button" role="tab" aria-selected={kind === value}
        onClick={() => { setKind(value); setCursors(['']) }} className={`border-b-2 px-3 py-2 text-sm ${kind === value ? 'border-primary text-primary' : 'border-transparent text-muted-foreground'}`}>{label}</button>)}
    </div>
    <p className="text-xs text-muted-foreground">{kind === 'analysis' ? '失败事件和每日计划触发的自动分析。' : '根据分析建议自动生成优化草稿的任务。手动优化请进入对应进化任务查看。'}</p>
    <div className="flex flex-wrap items-center justify-between gap-2">
      <p className="font-medium">{all ? '全部执行记录' : '最近 8 条记录'}</p>
      <div className="flex gap-3">
        <button type="button" disabled={records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => void records.refetch()}>刷新记录</button>
        <button type="button" className="text-primary underline" onClick={() => { setAll(!all); setCursors(['']) }}>{all ? '收起全部记录' : '查看全部记录'}</button>
      </div>
    </div>
    <p className="text-xs text-muted-foreground">按任务创建时间从新到旧排列，时间均为上海时间。</p>
    {all && <div className="flex flex-wrap gap-3">
      <label>时间范围<select aria-label="执行记录时间范围" className="ml-2 rounded-md border border-input bg-background px-2 py-1" value={period}
        onChange={event => { setPeriod(event.target.value as AutomationRecordQuery['period']); setCursors(['']) }}>
        <option value="all">全部时间</option><option value="today">今天</option><option value="7d">近 7 天</option>
      </select></label>
      <label>状态<select aria-label="执行记录状态" className="ml-2 rounded-md border border-input bg-background px-2 py-1" value={status}
        onChange={event => { setStatus(event.target.value as AutomationRecordQuery['status']); setCursors(['']) }}>
        <option value="all">全部状态</option><option value="pending">待处理</option><option value="dispatching">正在提交</option>
        <option value="waiting">等待结果</option><option value="retryable">等待重试</option><option value="uncertain">结果待核对</option>
        <option value="done">已结束</option><option value="blocked">需要处理</option>
      </select></label>
    </div>}
    {records.isPending && <p role="status" className="text-muted-foreground">正在读取执行记录…</p>}
    {records.isError && <p role="alert" className="text-destructive">执行记录读取失败。<button type="button" className="ml-2 underline" onClick={() => void records.refetch()}>重试读取记录</button></p>}
    {!records.isError && records.data && <>
      {records.data.jobs.length ? <ul className="space-y-2">{records.data.jobs.map(job => <li key={job.id} className="rounded-md border border-border p-3"><JobRecord job={job} workflowId={workflowId} /></li>)}</ul>
        : <p className="text-muted-foreground">{all ? '没有符合筛选条件的记录。' : kind === 'analysis' ? '暂无自动分析记录。' : '暂无自动优化记录。'}</p>}
      {all && <nav aria-label="执行记录分页" className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" disabled={cursors.length === 1 || records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => setCursors(current => current.slice(0, -1))}>上一页</button>
        <span className="text-xs text-muted-foreground">第 {cursors.length} 页 · 每页最多 20 条</span>
        <button type="button" disabled={!records.data.nextCursor || records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => { if (records.data.nextCursor) setCursors(current => [...current, records.data.nextCursor!]) }}>下一页</button>
      </nav>}
    </>}
  </section>
}

export function NotificationAttention({ workflowId }: { workflowId: string }) {
  const [cursors, setCursors] = useState<string[]>([''])
  const [open, setOpen] = useState(false)
  const records = useAutomationRecords(workflowId, { kind: 'notification', status: 'attention', limit: 8, ...(cursors.at(-1) ? { cursor: cursors.at(-1) } : {}) })
  if (records.isError) return <p className="mt-4 text-xs text-muted-foreground">通知异常记录读取失败。<button type="button" className="ml-2 text-primary underline" onClick={() => void records.refetch()}>重试读取通知异常</button></p>
  if (!records.data?.jobs.length && cursors.length === 1 && !open) return null
  return <details open={open} onToggle={event => setOpen(event.currentTarget.open)} className="mt-4 rounded-lg border border-border p-3 text-sm">
    <summary className="cursor-pointer font-medium">通知发送异常，需处理</summary>
    <p className="mt-2 text-xs text-muted-foreground">仅展示未送达或结果待核对的通知；成功通知不占用任务记录。这里不会重新生成草稿或重复执行工作流。</p>
    <button type="button" disabled={records.isFetching} className="mt-2 text-primary underline disabled:opacity-50" onClick={() => void records.refetch()}>{records.isFetching ? '读取中…' : '刷新通知异常'}</button>
    <ul className="mt-3 space-y-2">{records.data?.jobs.map(job => <li key={job.id} className="rounded-md border border-border p-3"><JobRecord job={job} workflowId={workflowId} /></li>)}</ul>
    {records.data && !records.data.jobs.length && <p className="mt-2 text-muted-foreground">本页暂无通知异常。</p>}
    {(cursors.length > 1 || records.data?.nextCursor) && <nav aria-label="通知异常分页" className="mt-3 flex items-center justify-between gap-3">
      <button type="button" disabled={cursors.length === 1 || records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => setCursors(value => value.slice(0, -1))}>上一页</button>
      <span className="text-xs text-muted-foreground">第 {cursors.length} 页 · 每页最多 8 条</span>
      <button type="button" disabled={!records.data?.nextCursor || records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => { if (records.data?.nextCursor) setCursors(value => [...value, records.data.nextCursor!]) }}>下一页</button>
    </nav>}
  </details>
}
