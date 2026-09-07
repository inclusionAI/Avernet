import { useMemo, useState } from 'react'
import { useRunTimeline } from '@avernet/workflow/web/api/hooks'
import type { TimelineEvent } from '@avernet/clawweb-shared/web/types'

function formatTimestamp(ts: number | null): string {
  if (!ts) return ''
  try {
    const d = new Date(ts * 1000)
    const hh = String(d.getHours()).padStart(2, '0')
    const mm = String(d.getMinutes()).padStart(2, '0')
    const ss = String(d.getSeconds()).padStart(2, '0')
    const ms = String(d.getMilliseconds()).padStart(3, '0')
    return `${hh}:${mm}:${ss}.${ms}`
  } catch {
    return String(ts)
  }
}

function formatDuration(ms: number | null): string {
  if (!ms || !Number.isFinite(ms)) return ''
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 60_000) return `${(ms / 1000).toFixed(1)}s`
  const m = Math.floor(ms / 60_000)
  const s = Math.floor((ms % 60_000) / 1000)
  return `${m}m${s}s`
}

type LogLine = {
  id: string
  ts: number | null
  relativeMs: number | null
  nodeId: string | null
  level: string
  source: string
  message: string
}

function parseLevel(payload: Record<string, unknown> | null, displayType: string): string {
  const lvl = String(payload?.level ?? displayType ?? 'info').toLowerCase()
  if (lvl === 'error' || lvl === 'err' || lvl === 'fatal' || lvl === 'critical') return 'error'
  if (lvl === 'warn' || lvl === 'warning') return 'warn'
  if (lvl === 'debug') return 'debug'
  if (lvl === 'info') return 'info'
  return 'info'
}

export default function SimpleRunLogsPanel({
  flowId,
  nodes,
}: {
  flowId: string
  nodes?: Array<{ node_id: string; node_title?: string | null }>
}) {
  const [filterNodeId, setFilterNodeId] = useState<string>('')
  const [keyword, setKeyword] = useState('')
  const [onlyErrors, setOnlyErrors] = useState(false)
  const { data: timeline, isLoading } = useRunTimeline(flowId)

  const lines: LogLine[] = useMemo(() => {
    if (!timeline?.events) return []
    return timeline.events
      .filter((e) => e.source === 'run_log')
      .map((e) => {
        const payload = e.payload as Record<string, unknown> | null
        return {
          id: e.id,
          ts: e.timestamp ?? null,
          relativeMs: e.relativeMs ?? null,
          nodeId: e.nodeId,
          level: parseLevel(payload, e.displayType),
          source: String(payload?.source ?? '-'),
          message: e.detail ?? (payload?.message ? String(payload.message) : ''),
        }
      })
      .sort((a, b) => (a.ts ?? 0) - (b.ts ?? 0))
  }, [timeline])

  const filtered = useMemo(() => {
    return lines.filter((l) => {
      if (filterNodeId && l.nodeId !== filterNodeId) return false
      if (onlyErrors && l.level !== 'error') return false
      if (keyword) {
        const k = keyword.toLowerCase()
        return (
          l.message.toLowerCase().includes(k) ||
          l.nodeId?.toLowerCase().includes(k) ||
          l.source.toLowerCase().includes(k)
        )
      }
      return true
    })
  }, [lines, filterNodeId, onlyErrors, keyword])

  if (isLoading) {
    return (
      <div className="flex items-center justify-center py-16">
        <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
      </div>
    )
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-3 rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
        <select
          value={filterNodeId}
          onChange={(e) => setFilterNodeId(e.target.value)}
          className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm text-gray-700 focus:border-blue-500 focus:outline-none"
        >
          <option value="">全部节点</option>
          {nodes?.map((n) => (
            <option key={n.node_id} value={n.node_id}>
              {n.node_title || n.node_id}
            </option>
          ))}
        </select>

        <input
          type="text"
          value={keyword}
          onChange={(e) => setKeyword(e.target.value)}
          placeholder="搜索日志关键字..."
          className="min-w-[200px] flex-1 rounded-md border border-gray-300 px-3 py-1.5 text-sm focus:border-blue-500 focus:outline-none"
        />

        <label className="flex cursor-pointer items-center gap-1.5 text-sm text-gray-700">
          <input
            type="checkbox"
            checked={onlyErrors}
            onChange={(e) => setOnlyErrors(e.target.checked)}
            className="h-4 w-4 rounded border-gray-300 text-red-600 focus:ring-red-500"
          />
          只看错误
        </label>

        <span className="ml-auto text-xs text-gray-500">共 {filtered.length} 条</span>
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6 text-center text-sm text-gray-500">
          没有匹配的日志
        </div>
      ) : (
        <div className="max-h-[600px] overflow-auto rounded-lg border border-gray-200 bg-slate-900 p-3">
          <table className="w-full border-collapse font-mono text-xs">
            <tbody>
              {filtered.map((l) => {
                const isError = l.level === 'error'
                const isWarn = l.level === 'warn'
                return (
                  <tr
                    key={l.id}
                    className={`${
                      isError ? 'bg-red-950/60 text-red-100' : isWarn ? 'text-amber-100' : 'text-slate-200'
                    } hover:opacity-90`}
                  >
                    <td className="w-24 whitespace-nowrap px-1 py-0.5 align-top opacity-70">
                      {formatTimestamp(l.ts)}
                    </td>
                    <td className="w-14 whitespace-nowrap px-1 py-0.5 align-top font-semibold">
                      {isError ? <span className="text-red-400">ERROR</span> : isWarn ? <span className="text-amber-400">WARN</span> : <span className="text-emerald-400">INFO</span>}
                    </td>
                    <td className="w-20 whitespace-nowrap px-1 py-0.5 align-top opacity-70">
                      [{l.source}]
                    </td>
                    <td className="w-28 whitespace-nowrap px-1 py-0.5 align-top text-blue-300">
                      {l.nodeId ?? '-'}
                    </td>
                    <td className="break-all px-1 py-0.5 align-top">
                      {l.message}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
