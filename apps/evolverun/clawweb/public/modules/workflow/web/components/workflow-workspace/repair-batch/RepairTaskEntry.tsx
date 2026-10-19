import { useState } from 'react'
import type { RepairTaskSummary } from '../../../../server/contracts/repair-workbench'
import { phases, RepairDialog } from './repair-view'

const pending = (task: RepairTaskSummary) => ['drafting', 'review', 'blocked', 'publishing', 'failed'].includes(task.phase)
const statusStyle: Record<string, string> = {
  review: 'bg-warning/10 text-warning-foreground', published: 'bg-success/10 text-success',
  drafting: 'bg-primary/10 text-primary', publishing: 'bg-primary/10 text-primary',
  blocked: 'bg-destructive/10 text-destructive', failed: 'bg-destructive/10 text-destructive',
}
const badge = (phase: string, label = phases[phase] ?? phase) => <span className={`inline-flex rounded-md px-2 py-1 text-xs font-semibold ${statusStyle[phase] ?? 'bg-muted text-muted-foreground'}`}>{label}</span>
export default function RepairTaskEntry({ tasks, loading, error = false, onOpen }: {
  tasks: RepairTaskSummary[]; loading: boolean; error?: boolean; onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(false)
  const ordered = [...tasks].sort((a, b) => b.updatedAtMs - a.updatedAtMs)
  const active = ordered.filter(pending), history = ordered.filter(task => !pending(task))
  const origin = (task: RepairTaskSummary) => <span className="rounded border border-input px-2 py-1 text-xs text-muted-foreground">{task.source === 'automatic' ? '自动任务' : task.source === 'manual' ? '手动任务' : '来源未知'}</span>
  const title = (task: RepairTaskSummary) => task.title || `优化 ${task.itemCount} 条建议`
  const row = (task: RepairTaskSummary) => <article key={task.taskId} aria-label={`优化任务 ${task.taskId}`} className="rounded-lg border border-input bg-card p-4 text-sm">
    <div className="flex flex-wrap items-center justify-between gap-2"><h4 className="min-w-0 flex-1 break-words font-semibold">{title(task)}</h4>{origin(task)}{badge(task.phase)}</div>
    <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
      <p className="text-xs text-muted-foreground">v{task.revision} · 包含 {task.itemCount} 条建议 · {new Date(task.updatedAtMs).toLocaleString()}</p>
      <button type="button" className="font-medium text-primary underline" onClick={() => { setOpen(false); onOpen(task.taskId) }}>查看任务</button>
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
    {!!active.length && <div className="mt-3 space-y-3 border-t border-border pt-3">{active.map(row)}</div>}
    {open && <RepairDialog title="优化任务" onClose={() => setOpen(false)}>
      <p className="mb-3 text-sm text-muted-foreground">共 {tasks.length} 个优化任务。</p>
      {!!active.length && <h4 className="mb-3 text-sm font-semibold">需跟进的任务（{active.length} 个）</h4>}
      <div className="space-y-3">{active.map(row)}</div>
      {!tasks.length && <p>{loading ? '正在读取优化任务…' : '暂无优化任务。选择问题与方案后生成草稿。'}</p>}
      {!!history.length && <details className="mt-4"><summary className="cursor-pointer text-sm">已发布与历史任务（{history.length} 个）</summary><div className="mt-3 space-y-3">{history.map(row)}</div></details>}
    </RepairDialog>}
  </section>
}
