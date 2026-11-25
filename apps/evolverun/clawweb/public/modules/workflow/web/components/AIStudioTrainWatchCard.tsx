import { useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { aiStudioTrainError, useAIStudioTrainWatch } from '../api/aistudio-train'

interface Props {
  flowId: string
  nodeId: string
  attempt: number
  nodeStatus: string
  compact?: boolean
}
const phases = { waiting: '等待查询', polling: '正在查询', resolving: '正在回写节点', resolved: '观测已结束' }
const controlClass = 'rounded-md border border-input bg-background px-2 py-1 text-xs text-foreground hover:bg-muted focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50'
const timeFormatter = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
  hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false,
})
function absoluteTime(value: string | null | undefined) {
  if (!value || !Number.isFinite(Date.parse(value))) return '—'
  return timeFormatter.format(new Date(value))
}
function safeDetailUrl(value: string | null): string | null {
  if (!value) return null
  try {
    const url = new URL(value)
    return url.protocol === 'https:' && !url.username && !url.password ? url.href : null
  } catch { return null }
}
function Field({ label, children }: { label: string; children: ReactNode }) {
  return <div><dt className="text-muted-foreground">{label}</dt><dd className="mt-0.5 break-words tabular-nums">{children}</dd></div>
}

/** Compact row and expanded detail share the same automatically refreshed server-owned watch. */
export default function AIStudioTrainWatchCard({ flowId, nodeId, attempt, nodeStatus, compact = false }: Props) {
  const { query } = useAIStudioTrainWatch(flowId, nodeId, attempt)
  const watch = query.data?.watch
  const [now, setNow] = useState(Date.now)
  const [copyState, setCopyState] = useState<'idle' | 'copied' | 'fallback'>('idle')
  const fallbackInput = useRef<HTMLInputElement>(null)
  const error = query.error ? aiStudioTrainError(query.error) : null
  const active = watch && watch.phase !== 'resolved'
  useEffect(() => {
    if (!active) return
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [active])
  useEffect(() => { setCopyState('idle') }, [flowId, nodeId, attempt, watch?.recordId])
  useEffect(() => {
    if (copyState === 'fallback') { fallbackInput.current?.focus(); fallbackInput.current?.select() }
  }, [copyState])
  const href = safeDetailUrl(watch?.detailUrl ?? null)
  const taskLink = href && <a href={href} target="_blank" rel="noopener noreferrer" className="rounded text-xs text-primary underline underline-offset-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring" aria-label="打开 AIStudio 任务">打开任务 ↗</a>
  const feedback = error && <p role="status" aria-live="polite" className="text-xs text-destructive">{error.message}</p>
  if (compact) return <div className="mt-1 space-y-1" onClick={event => event.stopPropagation()}>
    <div className="flex flex-wrap items-center gap-2">{taskLink}
      {query.isLoading && <span className="text-xs text-muted-foreground">加载训练观测…</span>}
      {query.isError && <button type="button" className={controlClass} onClick={() => void query.refetch()} disabled={query.isFetching}>重试观测</button>}
    </div>{feedback}
  </div>
  return <section aria-label="AIStudio 训练观测" className="space-y-3 rounded-lg border border-border bg-card p-3 text-sm text-card-foreground">
    <h4 className="text-sm font-semibold">AIStudio 训练观测</h4>
    <div className="flex flex-wrap gap-x-4 gap-y-1 text-xs" aria-live="polite">
      <span>节点状态：{nodeStatus}</span><span>观测状态：{watch ? phases[watch.phase] : '尚未获取'}</span><span>AIS 状态：{watch?.lastStatus ?? '尚未查询'}</span>
    </div>
    <p className="text-xs text-muted-foreground">节点等待不代表 AIS 正在运行；观测结束也不代表训练成功，训练结果以 AIS 状态与服务端节点状态为准。</p>
    {query.isLoading && <p role="status">加载训练观测…</p>}
    {watch && <>
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <span className="text-muted-foreground">recordId</span><code className="select-all break-all">{watch.recordId}</code>
        <button type="button" className={controlClass} aria-label="复制 recordId" onClick={() => {
          void (async () => {
            try { await navigator.clipboard.writeText(watch.recordId); setCopyState('copied') }
            catch { setCopyState('fallback') }
          })()
        }}>复制</button>
        {taskLink}
        <span role="status" aria-live="polite">{copyState === 'copied' ? '已复制 recordId' : copyState === 'fallback' ? '复制失败，请手动复制下方 recordId。' : ''}</span>
        {copyState === 'fallback' && <input ref={fallbackInput} aria-label="手动复制 recordId" readOnly value={watch.recordId} className="w-full rounded border border-input bg-background px-2 py-1 font-mono focus-visible:ring-2 focus-visible:ring-ring" />}
      </div>
      <p className="text-xs text-muted-foreground">模式：{watch.mode === 'submit' ? '提交并观测' : '观测已有任务'} · 第 {attempt} 次尝试 · 时间均为 Asia/Shanghai (UTC+8)</p>
      <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-xs md:grid-cols-3">
        <Field label="提交时间">{absoluteTime(watch.submittedAt)}</Field>
        <Field label="首次查询">{absoluteTime(watch.firstPollAt)}</Field>
        <Field label="查询周期">{watch.pollInterval} 秒</Field>
        <Field label="下次查询">{absoluteTime(watch.nextPollAt)}{active && watch.nextPollAt && <span className="block text-muted-foreground">{Date.parse(watch.nextPollAt) > now ? `${Math.ceil((Date.parse(watch.nextPollAt) - now) / 1000)} 秒后` : '已到计划时间，等待服务端查询'}</span>}</Field>
        <Field label="最近查询">{absoluteTime(watch.lastPolledAt)}</Field>
        <Field label="最近来源">{watch.lastPollSource === 'auto' ? '自动查询' : watch.lastPollSource === 'manual' ? '手工查询' : '—'}</Field>
        <Field label="超时时间">{absoluteTime(watch.timeoutAt)}{active && Date.parse(watch.timeoutAt) <= now && <span className="block text-muted-foreground">已到超时时间，等待服务端确认</span>}</Field>
        <Field label="连续观测异常">{watch.errorCount}</Field>
      </dl>
      {watch.lastError && <p className="break-words text-xs text-destructive">最近观测诊断：{watch.lastError}（节点状态以服务端为准）</p>}
    </>}
    {feedback}
    {query.isError && <button type="button" className={controlClass} onClick={() => void query.refetch()} disabled={query.isFetching}>重试观测</button>}
  </section>
}
