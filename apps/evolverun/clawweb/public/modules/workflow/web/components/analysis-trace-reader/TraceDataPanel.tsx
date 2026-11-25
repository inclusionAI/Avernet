import { memo, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { LangfuseObservation, LangfuseTrace, LangfuseTracesResponse } from '@avernet/clawweb-shared/web/types'
import { formatTime, formatTimeShort } from '@avernet/workflow/web/utils/time'
import { controlStyle, findPayloadMatches, focusStyle, readPayload, summaryStyle, TracePayload } from './TracePayload'

const wrapStyle = '[overflow-wrap:anywhere]'

function scrollBelowToolbar(target: HTMLElement, toolbar: HTMLDivElement | null) {
  // Measure at navigation time because the sticky controls can wrap.
  target.style.scrollMarginTop = `${(toolbar?.offsetHeight ?? 0) + 12}px`
  target.scrollIntoView?.({ block: 'start', inline: 'nearest' })
}

function latency(ms: number) {
  return ms < 1000 ? `${ms.toFixed(0)}ms` : `${(ms / 1000).toFixed(1)}s`
}

export function TraceDataPanel({ data }: { data: LangfuseTracesResponse }) {
  const [turnId, setTurnId] = useState<string | null>(null)
  const [query, setQuery] = useState('')
  const traces = data.data ?? []
  const turn = traces.find(({ id }) => id === turnId) ?? traces[0]
  if (!turn) return <p className="text-sm text-muted-foreground">未找到追踪数据</p>

  return (
    <div className="min-w-0 space-y-2 text-foreground">
      <div role="group" aria-label="选择 Turn" className="flex min-w-0 flex-wrap gap-1.5">
        {traces.map((trace) => (
          <button
            key={trace.id}
            type="button"
            aria-pressed={trace.id === turn.id}
            className={`${controlStyle} min-w-0 max-w-full text-left font-medium ${wrapStyle} aria-pressed:border-primary aria-pressed:bg-primary/10`}
            onClick={() => setTurnId(trace.id)}
          >
            {trace.name || trace.id}
            {trace.latencyMs != null && <span className="ml-1.5 text-xs font-normal text-muted-foreground">{latency(trace.latencyMs)}</span>}
          </button>
        ))}
      </div>
      {data.meta?.totalItems != null && <p className="text-xs text-muted-foreground">共 {data.meta.totalItems} 条追踪</p>}
      <TurnReader key={turn.id} trace={turn} query={query} onQueryChange={setQuery} />
    </div>
  )
}

function TurnReader({ trace, query, onQueryChange }: { trace: LangfuseTrace; query: string; onQueryChange: (query: string) => void }) {
  const listRef = useRef<HTMLDivElement>(null)
  const toolbarRef = useRef<HTMLDivElement>(null)
  const [jumpId, setJumpId] = useState('')
  const observations = useMemo(() => (trace.observations ?? []).map((observation, index) => ({
    observation,
    title: `${String(index + 1).padStart(2, '0')} · ${observation.name || observation.id}`,
    input: readPayload(observation.input),
    output: readPayload(observation.output),
  })), [trace.observations])
  const search = useMemo(() => findPayloadMatches(observations, query), [observations, query])
  const [selection, setSelection] = useState<{ search: typeof search; index: number } | null>(null)
  const active = selection?.search === search ? selection.index : -1
  const jumpIndex = observations.findIndex(({ observation }) => observation.id === jumpId)

  useLayoutEffect(() => {
    if (active < 0) return
    const target = listRef.current?.querySelector<HTMLElement>(`[data-trace-match="${active}"]`)
    if (!target) return
    const disclosure = target.closest('details')
    if (disclosure) disclosure.open = true
    scrollBelowToolbar(target, toolbarRef.current)
  }, [active, selection])

  function stepMatch(direction: 1 | -1) {
    const { count } = search
    if (!count) return
    const index = active < 0 ? (direction === 1 ? 0 : count - 1) : (active + direction + count) % count
    setSelection({ search, index })
  }

  function jumpToObservation() {
    const article = listRef.current?.querySelector<HTMLElement>(`[data-observation-index="${jumpIndex}"]`)
    if (!article) return
    article.focus({ preventScroll: true })
    scrollBelowToolbar(article, toolbarRef.current)
  }

  return (
    <>
      <TurnDetails trace={trace} />
      <div ref={toolbarRef} className="sticky top-0 z-10 min-w-0 space-y-2 border-b border-border bg-background py-2">
        <div className="flex min-w-0 flex-wrap items-center justify-between gap-2">
          <h4 className="text-sm font-semibold">Observations <span className="font-normal text-muted-foreground">({observations.length})</span></h4>
          <div className="flex min-w-0 max-w-full items-center gap-1.5">
            <select
              aria-label="选择要跳转的 Observation"
              className={`${controlStyle} min-w-0 max-w-full`}
              value={jumpIndex < 0 ? '' : jumpId}
              onChange={(event) => setJumpId(event.target.value)}
              disabled={!observations.length}
            >
              <option value="">选择调用…</option>
              {observations.map(({ observation, title }) => <option key={observation.id} value={observation.id}>{title}</option>)}
            </select>
            <button type="button" className={`${controlStyle} shrink-0`} disabled={jumpIndex < 0} onClick={jumpToObservation}>跳到</button>
          </div>
        </div>
        <div role="search" aria-label="在当前 Turn 的 Observations 中搜索" className="flex min-w-0 flex-wrap items-center gap-1.5">
          <input
            type="search"
            aria-label="搜索 Observations 的 Input / Output"
            placeholder="在 Observations 中搜索"
            autoComplete="off"
            spellCheck={false}
            className={`${controlStyle} min-w-0 flex-[1_1_10rem]`}
            value={query}
            onChange={(event) => { setSelection(null); onQueryChange(event.target.value) }}
            onKeyDown={(event) => {
              if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
              event.preventDefault()
              stepMatch(event.shiftKey ? -1 : 1)
            }}
          />
          <div className="ml-auto flex items-center gap-1.5 text-xs">
            <span role="status" className="tabular-nums text-muted-foreground">
              {query.trim() ? search.count ? (active < 0 ? `${search.count} 处匹配` : `${active + 1} / ${search.count}`) : '无匹配' : ''}
            </span>
            <button type="button" className={controlStyle} aria-label="上一处匹配" title="上一处匹配 (Shift+Enter)" disabled={!search.count} onClick={() => stepMatch(-1)}>↑</button>
            <button type="button" className={controlStyle} aria-label="下一处匹配" title="下一处匹配 (Enter)" disabled={!search.count} onClick={() => stepMatch(1)}>↓</button>
          </div>
        </div>
      </div>
      <div ref={listRef} className="min-w-0 space-y-3 pt-1">
        {observations.map(({ observation, title, input, output }, index) => (
          <ObservationCard key={observation.id} observation={observation} title={title} index={index}>
            <TracePayload label="Input" payload={input} matches={search.fields[index].input} active={active} />
            <TracePayload label="Output" payload={output} matches={search.fields[index].output} active={active} />
          </ObservationCard>
        ))}
        {!observations.length && <p className="text-sm text-muted-foreground">暂无 Observations</p>}
      </div>
    </>
  )
}

const TurnDetails = memo(function TurnDetails({ trace }: { trace: LangfuseTrace }) {
  const scores = trace.scores ?? []
  return (
    <details className="min-w-0 rounded-md border border-border">
      <summary className={summaryStyle}>Turn 详情 <span className="text-xs font-normal text-muted-foreground">Metadata / Input / Output / Scores</span></summary>
      <div className="space-y-3 border-t border-border p-3">
        <Metadata rows={[
          ['ID', trace.id], ['Session ID', trace.sessionId], ['开始', formatTime(trace.startTime)],
          ['结束', trace.endTime && formatTime(trace.endTime)], ['耗时', trace.latencyMs == null ? null : latency(trace.latencyMs)],
        ]} />
        <TracePayload label="Metadata" payload={readPayload(trace.metadata)} />
        <TracePayload label="Input" payload={readPayload(trace.input)} />
        <TracePayload label="Output" payload={readPayload(trace.output)} />
        {scores.length > 0 && (
          <div className={`text-sm ${wrapStyle}`}>
            <p className="font-medium">Scores</p>
            <ul className="space-y-1 text-muted-foreground">
              {scores.map((score) => <li key={score.id}>{score.name}: {score.value}{score.comment && ` · ${score.comment}`}</li>)}
            </ul>
          </div>
        )}
      </div>
    </details>
  )
})

function ObservationCard({ observation: obs, title, index, children }: {
  observation: LangfuseObservation; title: string; index: number; children: ReactNode
}) {
  const titleId = useId()
  return (
    <article
      aria-labelledby={titleId}
      tabIndex={-1}
      data-observation-index={index}
      className={`min-w-0 rounded-lg border border-border bg-card text-card-foreground ${focusStyle}`}
    >
      <header className={`flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-1 border-b border-border px-3 py-2 ${wrapStyle}`}>
        <h5 id={titleId} className="min-w-0 text-sm font-semibold">{title}</h5>
        <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs font-medium uppercase">{obs.type}</span>
        <div className="ml-auto flex min-w-0 flex-wrap gap-x-2 text-xs text-muted-foreground">
          <time dateTime={obs.startTime}>{formatTimeShort(obs.startTime)}</time>
          {obs.latencyMs != null && <span>{latency(obs.latencyMs)}</span>}
          {obs.model && <span className="min-w-0 font-mono">{obs.model}</span>}
          {obs.totalTokens != null && <span>{obs.totalTokens} tokens</span>}
        </div>
      </header>
      <div className="min-w-0 space-y-3 p-3">
        {children}
        <details className="rounded-md border border-border">
          <summary className={summaryStyle}>调用信息</summary>
          <div className="space-y-2 px-3 pb-2">
            <Metadata rows={[
              ['ID', obs.id], ['开始', formatTime(obs.startTime)], ['结束', obs.endTime && formatTime(obs.endTime)],
              ['耗时', obs.latencyMs == null ? null : latency(obs.latencyMs)], ['模型', obs.model],
              ['输入 Token', obs.promptTokens], ['输出 Token', obs.completionTokens], ['总 Token', obs.totalTokens],
            ]} />
            {obs.statusMessage && <p className={`whitespace-pre-wrap text-sm text-destructive ${wrapStyle}`}>状态消息: {obs.statusMessage}</p>}
          </div>
        </details>
      </div>
    </article>
  )
}

function Metadata({ rows }: { rows: Array<[string, string | number | null]> }) {
  return (
    <dl className={`grid min-w-0 grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-1 text-xs ${wrapStyle}`}>
      {rows.map(([label, value]) => value != null && (
        <div key={label} className="contents"><dt className="text-muted-foreground">{label}</dt><dd>{value}</dd></div>
      ))}
    </dl>
  )
}
