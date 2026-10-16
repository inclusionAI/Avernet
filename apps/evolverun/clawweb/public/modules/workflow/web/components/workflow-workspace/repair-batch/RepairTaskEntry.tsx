import { useState } from 'react'
import type { RepairTaskSummary } from '../../../../server/contracts/repair-workbench'
import { phases, RepairDialog } from './repair-view'

const pending = (task: RepairTaskSummary) => ['drafting', 'review', 'blocked', 'publishing', 'failed'].includes(task.phase)
export default function RepairTaskEntry({ tasks, issueCount, loading, onOpen }: {
  tasks: RepairTaskSummary[]; issueCount?: number; loading: boolean; onOpen: (id: string) => void;
}) {
  const [open, setOpen] = useState(false)
  const ordered = [...tasks].sort((a, b) => b.updatedAtMs - a.updatedAtMs)
  const active = ordered.filter(pending), history = ordered.filter(task => !pending(task))
  const current = active[0] ?? ordered[0]
  const title = (task: RepairTaskSummary) => task.title || `优化 ${task.itemCount} 项问题`
  const row = (task: RepairTaskSummary) => <article key={task.taskId} className="rounded-lg border border-input bg-card p-4 text-sm">
    <button type="button" className="text-left font-medium text-primary underline" onClick={() => { setOpen(false); onOpen(task.taskId) }}>{title(task)}</button>
    <p className="mt-2 text-xs text-muted-foreground">{phases[task.phase] ?? task.phase} · v{task.revision} · {task.itemCount} 项 · {new Date(task.updatedAtMs).toLocaleString()}</p>
    <details className="mt-2 text-xs text-muted-foreground"><summary>任务编号</summary><p className="break-all font-mono">{task.taskId}</p></details>
  </article>
  return <section aria-label="问题与优化任务入口" className="rounded-xl border border-input bg-card p-4">
    <nav aria-label="问题与任务" className="flex flex-wrap items-center gap-4 text-sm">
      <span aria-current="page" className="font-semibold text-foreground">问题{issueCount == null ? '' : `（${issueCount}）`}</span>
      <button type="button" className="font-semibold text-primary underline" onClick={() => setOpen(true)}>优化任务（{loading && !tasks.length ? '读取中' : active.length} 待处理）</button>
    </nav>
    {current && <div className="mt-3 flex flex-wrap items-center justify-between gap-3 border-t border-border pt-3 text-sm">
      <div><p className="font-medium text-foreground">{pending(current) ? '当前任务' : '最近任务'}：{title(current)}</p>
        <p className="mt-1 text-xs text-muted-foreground">{phases[current.phase] ?? current.phase} · v{current.revision} · {current.itemCount} 项</p></div>
      <button type="button" className="text-primary underline" onClick={() => onOpen(current.taskId)}>查看本轮任务</button>
    </div>}
    {open && <RepairDialog title="优化任务" onClose={() => setOpen(false)}>
      <p className="mb-3 text-sm text-muted-foreground">待处理 {active.length} 项；查看任务不生成草稿、不发布。</p>
      <div className="space-y-3">{active.map(row)}</div>
      {!tasks.length && <p>{loading ? '正在读取优化任务…' : '暂无优化任务。选择问题与方案后生成草稿。'}</p>}
      {!!history.length && <details className="mt-4"><summary className="cursor-pointer text-sm">历史与观察中的任务（{history.length}）</summary><div className="mt-3 space-y-3">{history.map(row)}</div></details>}
    </RepairDialog>}
  </section>
}
