import { useState } from 'react'
import type { AutomationRecordQuery } from '../../../server/contracts/workflow-automation'
import { useAutomationRecords } from '../../api/workflow-automation'
import JobRecord, { type AutomationRetryActions } from './AutomationJobRecord'
export default function AutomationHistory({ workflowId, kind, retry }: { workflowId: string; kind: 'analysis' | 'optimization'; retry?: AutomationRetryActions }) {
  const [period, setPeriod] = useState<AutomationRecordQuery['period']>('all')
  const [status, setStatus] = useState<AutomationRecordQuery['status']>('all')
  const [cursors, setCursors] = useState<string[]>([''])
  const records = useAutomationRecords(workflowId, { kind, period, status, limit: 20, ...(cursors.at(-1) ? { cursor: cursors.at(-1) } : {}) })
  return <section aria-label={kind === 'analysis' ? '自动分析记录' : '自动优化记录'} className="space-y-2 border-t border-border pt-3 text-sm" aria-busy={records.isFetching}>
    <div className="flex flex-wrap items-center gap-3 text-xs">
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
      <button type="button" disabled={records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => void records.refetch()}>刷新记录</button>
    </div>
    <p className="text-xs text-muted-foreground">{kind === 'optimization' ? '仅含自动生成草稿的任务。' : ''}按创建时间倒序，上海时间。</p>
    {records.isPending && <p role="status" className="text-muted-foreground">正在读取执行记录…</p>}
    {records.isError && <p role="alert" className="text-destructive">执行记录读取失败。<button type="button" className="ml-2 underline" onClick={() => void records.refetch()}>重试读取记录</button></p>}
    {!records.isError && records.data && <>
      {records.data.jobs.length ? <ul className="max-h-96 divide-y divide-border overflow-y-auto">{records.data.jobs.map(job => <li key={job.id} className="py-2"><JobRecord job={job} workflowId={workflowId} retry={retry} /></li>)}</ul>
        : <p className="text-muted-foreground">{period !== 'all' || status !== 'all' ? '没有符合筛选条件的记录。' : kind === 'analysis' ? '暂无自动分析记录。' : '暂无自动优化记录。'}</p>}
      <nav aria-label="执行记录分页" className="flex flex-wrap items-center justify-between gap-2">
        <button type="button" disabled={cursors.length === 1 || records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => setCursors(current => current.slice(0, -1))}>上一页</button>
        <span className="text-xs text-muted-foreground">第 {cursors.length} 页 · 每页最多 20 条</span>
        <button type="button" disabled={!records.data.nextCursor || records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => { if (records.data.nextCursor) setCursors(current => [...current, records.data.nextCursor!]) }}>下一页</button>
      </nav>
    </>}
  </section>
}

export function NotificationAttention({ workflowId, retry }: { workflowId: string; retry?: AutomationRetryActions }) {
  const [cursors, setCursors] = useState<string[]>([''])
  const records = useAutomationRecords(workflowId, { kind: 'notification', status: 'attention', limit: 8, ...(cursors.at(-1) ? { cursor: cursors.at(-1) } : {}) })
  if (records.isError) return <p className="mt-4 text-xs text-muted-foreground">通知异常记录读取失败。<button type="button" className="ml-2 text-primary underline" onClick={() => void records.refetch()}>重试读取通知异常</button></p>
  return <section aria-label="通知发送异常" className="space-y-2 border-t border-border pt-3 text-sm" aria-busy={records.isFetching}>
    <p className="text-xs text-muted-foreground">仅展示未送达或结果待核对的通知。</p>
    {records.isPending && <p role="status" className="text-muted-foreground">正在读取通知异常…</p>}
    <button type="button" disabled={records.isFetching} className="mt-2 text-primary underline disabled:opacity-50" onClick={() => void records.refetch()}>{records.isFetching ? '读取中…' : '刷新通知异常'}</button>
    <ul className="max-h-96 divide-y divide-border overflow-y-auto">{records.data?.jobs.map(job => <li key={job.id} className="py-2"><JobRecord job={job} workflowId={workflowId} retry={retry} /></li>)}</ul>
    {records.data && !records.data.jobs.length && <p className="text-muted-foreground">暂无通知发送异常。</p>}
    {(cursors.length > 1 || records.data?.nextCursor) && <nav aria-label="通知异常分页" className="mt-3 flex items-center justify-between gap-3">
      <button type="button" disabled={cursors.length === 1 || records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => setCursors(value => value.slice(0, -1))}>上一页</button>
      <span className="text-xs text-muted-foreground">第 {cursors.length} 页 · 每页最多 8 条</span>
      <button type="button" disabled={!records.data?.nextCursor || records.isFetching} className="text-primary underline disabled:opacity-50" onClick={() => { if (records.data?.nextCursor) setCursors(value => [...value, records.data.nextCursor!]) }}>下一页</button>
    </nav>}
  </section>
}
