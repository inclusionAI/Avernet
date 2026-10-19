import { useState } from 'react'
import type { RepairTaskSummary } from '../../../../server/contracts/repair-workbench'
import { phases, primary, RepairDialog } from './repair-view'

const pending = (task: RepairTaskSummary) => ['drafting', 'review', 'blocked', 'publishing', 'failed'].includes(task.phase)
const statusStyle: Record<string, string> = {
  review: 'bg-amber-100 text-amber-900', published: 'bg-emerald-100 text-emerald-900',
  drafting: 'bg-blue-100 text-blue-900', publishing: 'bg-blue-100 text-blue-900',
  blocked: 'bg-red-100 text-red-900', failed: 'bg-red-100 text-red-900',
}
const badge = (phase: string, label = phases[phase] ?? phase) => <span className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ${statusStyle[phase] ?? 'bg-slate-100 text-slate-700'}`}>{label}</span>
export default function RepairTaskEntry({ tasks, loading, error = false, onOpen }: {
  tasks: RepairTaskSummary[]; loading: boolean; error?: boolean; onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(false)
  const ordered = [...tasks].sort((a, b) => b.updatedAtMs - a.updatedAtMs)
  const active = ordered.filter(pending), history = ordered.filter(task => !pending(task))
  const current = active[0] ?? ordered[0]
  const title = (task: RepairTaskSummary) => task.title || `优化 ${task.itemCount} 条建议`
  const action = (task: RepairTaskSummary) => task.phase === 'review' ? '查看并审阅' : '查看任务'
  const row = (task: RepairTaskSummary) => <article key={task.taskId} aria-label={`优化任务 ${task.taskId}`} className="rounded-lg border border-input bg-card p-4 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="font-semibold">优化任务 · v{task.revision}</h4>{badge(task.phase)}</div>
    <p className="mt-2 line-clamp-2 text-muted-foreground" title={title(task)}>{title(task)}</p>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs text-muted-foreground">包含 {task.itemCount} 条建议 · {new Date(task.updatedAtMs).toLocaleString()}</p>
      <button type="button" className="font-medium text-primary underline" onClick={() => { setOpen(false); onOpen(task.taskId) }}>{action(task)}</button>
    </div>
    <details className="mt-2 text-xs text-muted-foreground"><summary>任务编号</summary><p className="break-all font-mono">{task.taskId}</p></details>
  </article>
  return <section aria-label="问题与优化任务入口" className="rounded-xl border border-input bg-card p-4">
    <div className="flex flex-wrap items-center gap-3 text-sm">
      <h3 className="font-semibold text-foreground">优化任务</h3>
      {Object.entries(phases).map(([phase, label]) => {
        const count = tasks.filter(task => task.phase === phase).length
        return count ? <span key={phase}>{badge(phase, `${count} 个${label}`)}</span> : null
      })}
      {!!tasks.length && <button type="button" className="ml-auto font-medium text-primary underline" onClick={() => setOpen(true)}>查看全部任务（{tasks.length}）</button>}
    </div>
    {!tasks.length && <p className="mt-2 text-xs text-muted-foreground">{error ? '任务数量暂不可用' : loading ? '正在读取优化任务…' : '暂无优化任务，选择建议后可生成草稿。'}</p>}
    {current && <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 text-sm">
      <div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><p className="font-medium text-foreground">{pending(current) ? '最近需跟进任务' : '最近任务'} · v{current.revision}</p>{badge(current.phase)}<span className="text-xs text-muted-foreground">包含 {current.itemCount} 条建议</span></div>
        <p className="mt-2 line-clamp-1 text-xs text-muted-foreground" title={title(current)}>任务说明：{title(current)}</p></div>
      <button type="button" className={current.phase === 'review' ? primary : 'font-medium text-primary underline'} onClick={() => onOpen(current.taskId)}>{action(current)}</button>
    </div>}
    {open && <RepairDialog title="优化任务" onClose={() => setOpen(false)}>
      <p className="mb-3 text-sm text-muted-foreground">共 {tasks.length} 个优化任务。</p>
      {!!active.length && <h4 className="mb-3 text-sm font-semibold">需跟进的任务（{active.length} 个）</h4>}
      <div className="space-y-3">{active.map(row)}</div>
      {!tasks.length && <p>{loading ? '正在读取优化任务…' : '暂无优化任务。选择问题与方案后生成草稿。'}</p>}
      {!!history.length && <details className="mt-4"><summary className="cursor-pointer text-sm">已发布与历史任务（{history.length} 个）</summary><div className="mt-3 space-y-3">{history.map(row)}</div></details>}
    </RepairDialog>}
  </section>
}
