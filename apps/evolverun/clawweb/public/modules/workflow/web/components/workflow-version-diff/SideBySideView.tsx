import { useMemo, useRef, useState, type KeyboardEvent, type UIEvent } from 'react'
import type { VersionDiffResult } from '@avernet/clawweb-shared/web/types'
import { compactRows, sideBySideDiff, type DiffLine, type DiffRow } from './sideBySide'

type Entry =
  | { kind: 'row'; row: DiffRow; index: number }
  | { kind: 'skip'; start: number; end: number; count: number }

interface SideBySideViewProps {
  result: VersionDiffResult
  lines: DiffLine[]
  changedFields: string[]
}

function formatTime(epochSec: number): string {
  return epochSec ? new Date(epochSec * 1000).toLocaleString() : '-'
}

export default function SideBySideView({ result, lines, changedFields }: SideBySideViewProps) {
  const rows = useMemo(() => sideBySideDiff(lines), [lines])
  const compacted = useMemo(() => compactRows(rows, 2), [rows])
  const [showFullContext, setShowFullContext] = useState(true)
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set())
  const [currentChange, setCurrentChange] = useState(-1)
  const leftRef = useRef<HTMLDivElement>(null)
  const rightRef = useRef<HTMLDivElement>(null)
  const additions = lines.filter(line => line.type === 'add').length
  const deletions = lines.filter(line => line.type === 'del').length
  const changeCount = rows.reduce((count, row) => row.changeId === null ? count : Math.max(count, row.changeId + 1), 0)

  const entries: Entry[] = showFullContext
    ? rows.map((row, index) => ({ kind: 'row', row, index }))
    : compacted.flatMap(entry => {
      if (entry.kind === 'row' || !expanded.has(`${entry.start}:${entry.end}`)) return [entry]
      return [entry, ...rows.slice(entry.start, entry.end).map((row, offset) => ({
        kind: 'row' as const, row, index: entry.start + offset,
      }))]
    })

  const syncScroll = (event: UIEvent<HTMLDivElement>, other: HTMLDivElement | null) => {
    if (other && other.scrollTop !== event.currentTarget.scrollTop) other.scrollTop = event.currentTarget.scrollTop
  }

  const navigateChange = (direction: number) => {
    if (!changeCount) return
    const next = currentChange < 0 ? (direction > 0 ? 0 : changeCount - 1) : (currentChange + direction + changeCount) % changeCount
    setCurrentChange(next)
    for (const pane of [leftRef.current, rightRef.current]) {
      const target = pane?.querySelector<HTMLElement>(`[data-change-id="${next}"]`)
      if (pane && target) {
        pane.scrollTop = Math.max(0, target.getBoundingClientRect().top - pane.getBoundingClientRect().top + pane.scrollTop - 12)
      }
    }
  }

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (!event.altKey || (event.key !== 'ArrowUp' && event.key !== 'ArrowDown')) return
    event.preventDefault()
    navigateChange(event.key === 'ArrowUp' ? -1 : 1)
  }

  const toggleFold = (key: string) => setExpanded(previous => {
    const next = new Set(previous)
    if (next.has(key)) next.delete(key)
    else next.add(key)
    return next
  })

  const renderRows = (side: 'left' | 'right') => entries.map(entry => {
    if (entry.kind === 'skip') {
      const key = `${entry.start}:${entry.end}`
      const isExpanded = expanded.has(key)
      return <div key={`skip-${key}`} className="flex h-6 items-center bg-slate-800 text-slate-300">
        <span className="w-10 shrink-0 text-center text-slate-500" aria-hidden="true">···</span>
        <button type="button" aria-expanded={isExpanded} onClick={() => toggleFold(key)}
          className="px-2 text-left text-[11px] text-blue-200 hover:underline focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
          {isExpanded ? '收起' : '展开'} {entry.count} 行未变更内容
        </button>
      </div>
    }
    const line = entry.row[side]
    const changed = line?.type === 'del' || line?.type === 'add'
    const background = line?.type === 'del' ? 'bg-red-950/60' : line?.type === 'add' ? 'bg-emerald-950/60' : line ? '' : 'bg-slate-800/70'
    const color = line?.type === 'del' ? 'text-red-300' : line?.type === 'add' ? 'text-emerald-300' : 'text-slate-300'
    const lineNumber = side === 'left' ? line?.fromLine : line?.toLine
    return <div key={`row-${entry.index}`} data-change-id={entry.row.changeId ?? undefined}
      aria-label={line ? undefined : '对齐空行'}
      className={`flex h-6 items-center ${background} ${entry.row.changeId !== null && entry.row.changeId === currentChange ? 'shadow-[inset_2px_0_0_0_var(--primary)]' : ''}`}>
      {line && <span className="sr-only">第 {lineNumber} 行，{line.type === 'del' ? '删除' : line.type === 'add' ? '新增' : '未变更'}</span>}
      <span aria-hidden="true" className="sticky left-0 z-10 w-10 shrink-0 select-none border-r border-slate-700 bg-slate-900 px-1 text-right text-slate-500">{lineNumber ?? ''}</span>
      <span aria-hidden="true" className={`w-5 shrink-0 select-none text-center ${color}`}>{changed ? (line?.type === 'del' ? '-' : '+') : ''}</span>
      <span className={`whitespace-pre pr-6 ${color}`}>{line?.text ?? ''}</span>
    </div>
  })

  const sides = [
    { side: 'left' as const, label: '旧版本', snapshot: result.from, ref: leftRef, otherRef: rightRef },
    { side: 'right' as const, label: '新版本', snapshot: result.to, ref: rightRef, otherRef: leftRef },
  ]

  return <div className="flex h-full min-h-0 flex-col" onKeyDown={onKeyDown}>
    <div className="flex flex-wrap items-center justify-between gap-2 border-b border-border bg-muted/50 px-3 py-2 text-xs">
      <div className="flex items-center gap-2 text-muted-foreground">
        <span>from <strong className="text-foreground">v{result.from.version}</strong> (deploy #{result.from.deployNumber}, {result.from.action})</span>
        <span aria-hidden="true">→</span>
        <span>to <strong className="text-foreground">v{result.to.version}</strong> (deploy #{result.to.deployNumber}, {result.to.action})</span>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-emerald-600" aria-label={`新增 ${additions} 行`}>+{additions}</span>
        <span className="text-red-600" aria-label={`删除 ${deletions} 行`}>-{deletions}</span>
        <div role="group" aria-label="内容显示方式" className="flex rounded-md bg-muted p-0.5">
          <button type="button" aria-pressed={showFullContext} onClick={() => setShowFullContext(true)} className={`rounded px-2 py-1 ${showFullContext ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground'}`}>完整内容</button>
          <button type="button" aria-pressed={!showFullContext} onClick={() => setShowFullContext(false)} className={`rounded px-2 py-1 ${!showFullContext ? 'bg-card text-foreground shadow-sm' : 'text-muted-foreground'}`}>仅看变更</button>
        </div>
        <div role="group" aria-label="变更导航" className="flex items-center gap-1 border-l border-border pl-2">
          <button type="button" aria-label="上一处变更" title="上一处变更 · Alt + ↑" disabled={!changeCount} onClick={() => navigateChange(-1)} className="rounded px-1.5 py-1 hover:bg-muted disabled:opacity-40">↑</button>
          <span aria-label="变更位置" aria-live="polite" className="min-w-10 text-center tabular-nums text-muted-foreground">{changeCount ? (currentChange < 0 ? '—' : currentChange + 1) : 0} / {changeCount}</span>
          <button type="button" aria-label="下一处变更" title="下一处变更 · Alt + ↓" disabled={!changeCount} onClick={() => navigateChange(1)} className="rounded px-1.5 py-1 hover:bg-muted disabled:opacity-40">↓</button>
        </div>
      </div>
    </div>
    {changedFields.length > 0 && <div className="border-b border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">
      <span className="mr-2 font-medium text-foreground">变更字段</span>
      {changedFields.map(field => <span key={field} className="mr-1 inline-block rounded bg-card px-1.5 py-0.5 font-mono text-[11px] text-primary">{field}</span>)}
    </div>}
    {additions === 0 && deletions === 0 && <div className="border-b border-border bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">两个版本内容完全相同</div>}
    <div className="min-h-0 flex-1 overflow-x-auto">
      <div className="grid h-full min-w-[720px] grid-cols-2">
        {sides.map(({ side, label, snapshot, ref, otherRef }) => <section key={side} className="flex min-h-0 min-w-0 flex-col border-r border-slate-700 last:border-r-0">
          <div className="flex min-h-14 items-center justify-between gap-2 border-b border-border bg-muted/40 px-3 py-2">
            <div><div className="text-xs font-semibold text-foreground">{label} · v{snapshot.version}</div>
              <div className="mt-1 text-[10px] text-muted-foreground">deploy #{snapshot.deployNumber} · {formatTime(snapshot.gmtCreate)}</div></div>
            <span className="rounded border border-border px-1.5 py-0.5 text-[10px] text-muted-foreground">{side === 'left' ? '对比基准' : '对比版本'}</span>
          </div>
          <div ref={ref} role="region" aria-label={`${label} v${snapshot.version}`} tabIndex={0}
            onScroll={event => syncScroll(event, otherRef.current)} className="min-h-0 flex-1 overflow-scroll bg-slate-900 font-mono text-xs leading-6 focus-visible:outline focus-visible:outline-2 focus-visible:outline-primary">
            <div className="w-max min-w-full py-2">{renderRows(side)}</div>
          </div>
        </section>)}
      </div>
    </div>
    <div className="flex flex-wrap items-center gap-x-4 border-t border-slate-700 bg-slate-800 px-3 py-1.5 text-[10px] text-slate-300">
      <span><span className="text-red-300">−</span> 旧版本移除</span><span><span className="text-emerald-300">+</span> 新版本新增</span>
      <span>空行用于对齐</span><span className="ml-auto">纵向同步 · 横向独立</span>
    </div>
  </div>
}
