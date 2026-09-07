import { useMemo, type ReactNode } from 'react'
import type { IconName } from './helpers'

export type { IconName } from './helpers'

export function Icon({ name, className = 'h-4 w-4' }: { name: IconName; className?: string }) {
  const paths: Record<IconName, ReactNode> = {
    spark: <><path d="m12 3-1.2 3.8a6 6 0 0 1-4 4L3 12l3.8 1.2a6 6 0 0 1 4 4L12 21l1.2-3.8a6 6 0 0 1 4-4L21 12l-3.8-1.2a6 6 0 0 1-4-4L12 3Z" /></>,
    plus: <path d="M12 5v14M5 12h14" />,
    bot: <><rect x="4" y="7" width="16" height="12" rx="3" /><path d="M9 12h.01M15 12h.01M8 16h8M12 3v4" /></>,
    arrow: <path d="m9 18 6-6-6-6" />,
    check: <path d="m5 12 4 4L19 6" />,
    clock: <><circle cx="12" cy="12" r="9" /><path d="M12 7v5l3 2" /></>,
    file: <><path d="M6 3h8l4 4v14H6V3Z" /><path d="M14 3v5h5M9 13h6M9 17h4" /></>,
    chart: <><path d="M4 19V9M10 19V5M16 19v-7M22 19H2" /></>,
    code: <><path d="m8 9-3 3 3 3M16 9l3 3-3 3M14 5l-4 14" /></>,
    send: <><path d="m22 2-7 20-4-9-9-4 20-7Z" /><path d="M22 2 11 13" /></>,
    target: <><circle cx="12" cy="12" r="9" /><circle cx="12" cy="12" r="5" /><circle cx="12" cy="12" r="1" /></>,
    package: <><path d="m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Z" /><path d="m4.5 7.5 7.5 4 7.5-4M12 11.5V21" /></>,
  }
  return <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">{paths[name]}</svg>
}

export function Status({ type, children }: { type: 'running' | 'waiting' | 'done' | 'scheduled'; children: ReactNode }) {
  const style = {
    running: 'bg-blue-50 text-blue-700',
    waiting: 'bg-violet-50 text-violet-700',
    done: 'bg-emerald-50 text-emerald-700',
    scheduled: 'bg-gray-100 text-gray-600',
  }[type]
  return <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-medium ${style}`}>{children}</span>
}

export function TaskType({ children }: { type: string; children: ReactNode }) {
  return (
    <span className="inline-flex rounded-md bg-slate-100 px-2 py-1 text-xs font-medium text-slate-700">
      {children}
    </span>
  )
}

export function PageTitle({ action }: { action?: ReactNode }) {
  return (
    <div className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight text-gray-950">进化任务</h1>
        <p className="mt-1.5 text-sm text-gray-500">向指定 Bot 发起进化，并查看任务执行结果。</p>
      </div>
      {action}
    </div>
  )
}

export function GitDiffView({ content }: { content: string }) {
  const fileNodes = useMemo(() => buildDiffFileNodes(content), [content])
  if (!content) {
    return <div className="mt-2 rounded-lg border border-gray-200 bg-white px-4 py-8 text-center text-xs text-gray-400">本轮无文本变更</div>
  }
  return <div className="mt-2 space-y-3">{fileNodes}</div>
}

function buildDiffFileNodes(content: string) {
  const normalized = content.replace(/\r\n/g, '\n')
  const starts = [...normalized.matchAll(/^diff --git /gm)].map((match) => match.index ?? 0)
  const files = starts.length > 0
    ? starts.map((start, index) => normalized.slice(start, starts[index + 1] ?? normalized.length).replace(/\n$/, ''))
    : [normalized]
  return files.map((file, index) => {
    const title = file.match(/^diff --git a\/(.+?) b\/(.+)$/m)?.[2] ?? `Diff ${index + 1}`
    return (
      <div key={`${title}-${index}`} className="overflow-hidden rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-200 bg-gray-50 px-3 py-2">
          <span className="break-all font-mono text-[11px] font-semibold text-gray-700">{title}</span>
          <span className="ml-3 shrink-0 text-[9px] text-gray-400">{index + 1} / {files.length}</span>
        </div>
        <GitDiffFile content={file} />
      </div>
    )
  })
}

function GitDiffFile({ content }: { content: string }) {
  const rows = useMemo(() => renderGitDiffRows(content), [content])
  return <div className="max-h-[28rem] overflow-auto font-mono text-[11px] leading-5">{rows}</div>
}

function renderGitDiffRows(content: string) {
  const lines = content.split('\n')
  let oldLine: number | null = null
  let newLine: number | null = null
  return lines.map((line, index) => {
    const hunk = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
    let oldNumber: number | null = null
    let newNumber: number | null = null
    let tone = 'bg-white text-gray-700'
    if (hunk) {
      oldLine = Number(hunk[1])
      newLine = Number(hunk[2])
      tone = 'bg-blue-50 text-blue-700'
    } else if (line.startsWith('diff --git ') || line.startsWith('index ')) {
      tone = 'bg-gray-100 text-gray-700 font-semibold'
    } else if (line.startsWith('--- ') || line.startsWith('+++ ')) {
      tone = 'bg-gray-50 text-gray-600 font-semibold'
    } else if (line.startsWith('+')) {
      newNumber = newLine
      if (newLine != null) newLine += 1
      tone = 'bg-emerald-50 text-emerald-900'
    } else if (line.startsWith('-')) {
      oldNumber = oldLine
      if (oldLine != null) oldLine += 1
      tone = 'bg-red-50 text-red-900'
    } else if (!line.startsWith('\\ No newline')) {
      oldNumber = oldLine
      newNumber = newLine
      if (oldLine != null) oldLine += 1
      if (newLine != null) newLine += 1
    }
    const marker = line.startsWith('+') ? '+' : line.startsWith('-') ? '−' : hunk ? '•' : ' '
    return (
      <div key={index} className={`flex min-w-max border-b border-gray-100 last:border-b-0 ${tone}`}>
        <span className="sticky left-0 w-11 shrink-0 select-none border-r border-gray-200 bg-inherit px-2 text-right text-gray-400">{oldNumber ?? ''}</span>
        <span className="sticky left-11 w-11 shrink-0 select-none border-r border-gray-200 bg-inherit px-2 text-right text-gray-400">{newNumber ?? ''}</span>
        <span className="w-7 shrink-0 select-none text-center font-semibold opacity-70">{marker}</span>
        <span className="whitespace-pre pr-4">{line.startsWith('+') || line.startsWith('-') ? line.slice(1) : line || ' '}</span>
      </div>
    )
  })
}
