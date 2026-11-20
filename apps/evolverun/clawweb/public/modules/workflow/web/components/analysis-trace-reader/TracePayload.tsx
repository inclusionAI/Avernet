import { useState } from 'react'
import type { ReactNode } from 'react'

export const focusStyle = 'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring'
export const controlStyle = `min-h-8 rounded-md border border-input bg-background px-2 py-1 text-sm hover:bg-muted disabled:opacity-50 ${focusStyle}`
export const summaryStyle = `cursor-pointer rounded px-2 py-1 text-sm font-medium hover:bg-muted ${focusStyle}`
const payloadStyle = 'min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere] rounded-md bg-muted px-3 py-2.5 text-sm leading-relaxed text-foreground'

// Bound DOM highlights, not the text or the number of navigable matches.
const PASSIVE_HIGHLIGHT_LIMIT = 240

type Payload = { kind: 'text' | 'json'; text: string; encoded?: string }
export type FieldMatches = { offset: number; ranges: Array<{ start: number; end: number }> }

export function readPayload(value: unknown): Payload | null {
  if (value == null) return null
  if (typeof value === 'string') {
    if (value.trimStart().startsWith('"')) {
      try {
        const decoded: unknown = JSON.parse(value)
        if (typeof decoded === 'string') return { kind: 'text', text: decoded, encoded: value }
      } catch {
        // Invalid JSON remains literal text, including any backslash sequences.
      }
    }
    return { kind: 'text', text: value }
  }
  return { kind: 'json', text: JSON.stringify(value, null, 2) ?? String(value) }
}

export function findPayloadMatches<T extends { input: Payload | null; output: Payload | null }>(rows: T[], query: string) {
  let count = 0
  const term = query.trim()
  const literal = term ? new RegExp(term.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'giu') : null
  function find(payload: Payload | null): FieldMatches | undefined {
    if (!literal || !payload) return
    const ranges = Array.from(payload.text.matchAll(literal), ({ index, 0: value }) => ({ start: index, end: index + value.length }))
    const field = { offset: count, ranges }
    count += ranges.length
    return field
  }
  const fields = rows.map(({ input, output }) => ({ input: find(input), output: find(output) }))
  return { fields, count }
}

function CopyPayload({ label, text }: { label: string; text: string }) {
  const [feedback, setFeedback] = useState('')

  async function copy() {
    setFeedback('')
    try {
      await navigator.clipboard.writeText(text)
      setFeedback('已复制全文')
    } catch {
      setFeedback('复制失败，请手动选中正文复制')
    }
  }

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-2">
      <button type="button" className={controlStyle} aria-label={`复制 ${label}`} onClick={copy}>复制</button>
      <span role="status" className="text-xs text-muted-foreground">{feedback}</span>
    </div>
  )
}

function HighlightedText({ text, matches, active }: { text: string; matches?: FieldMatches; active?: number }) {
  if (!matches?.ranges.length) return text
  const { ranges, offset } = matches
  const visible = Math.min(ranges.length, Math.max(PASSIVE_HIGHLIGHT_LIMIT - offset, 0))
  const selected = active == null ? -1 : active - offset
  const showSelected = selected >= visible && selected < ranges.length
  if (!visible && !showSelected) return text

  const parts: ReactNode[] = []
  let cursor = 0
  function mark(i: number) {
    const { start, end } = ranges[i]
    const index = offset + i
    parts.push(text.slice(cursor, start), (
      <mark
        key={start}
        data-trace-match={index}
        className={index === active ? 'bg-primary text-primary-foreground' : 'bg-warning/30 text-foreground'}
      >{text.slice(start, end)}</mark>
    ))
    cursor = end
  }
  for (let i = 0; i < visible; i++) mark(i)
  if (showSelected) mark(selected)
  parts.push(text.slice(cursor))
  return parts
}

export function TracePayload({ label, payload, matches, active }: {
  label: string
  payload: Payload | null
  matches?: FieldMatches
  active?: number
}) {
  if (!payload) return null
  const { kind, text, encoded } = payload
  const body = <pre className={`${payloadStyle} ${kind === 'json' ? 'font-mono' : 'font-sans'}`}><HighlightedText text={text} matches={matches} active={active} /></pre>

  return (
    <div className="min-w-0 space-y-1.5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-baseline gap-2 text-sm font-medium">
          {label}<span className="text-xs font-normal text-muted-foreground">{kind === 'json' ? 'JSON' : '纯文本'}</span>
        </div>
        <CopyPayload label={label} text={text} />
      </div>
      {kind === 'json' ? (
        <details className="rounded-md border border-border">
          <summary className={summaryStyle}>JSON 正文</summary>
          {body}
        </details>
      ) : text === '' ? <p className="text-sm text-muted-foreground">（空字符串）</p> : body}
      {encoded !== undefined && (
        <details className="rounded-md border border-border">
          <summary className={summaryStyle}>原始编码</summary>
          <div className="space-y-1.5 p-2">
            <CopyPayload label={`原始 ${label}`} text={encoded} />
            <pre className={`${payloadStyle} font-mono`}>{encoded}</pre>
          </div>
        </details>
      )}
    </div>
  )
}
