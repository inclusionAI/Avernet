import { useState, useEffect } from 'react'

type TrendPoint = {
  date: string
  totalRuns: number
  succeededRuns: number
  failedRuns: number
  successRate: number
}

/**
 * 运行实例趋势卡片 — 复用 GET /api/workflows/:workflowId/success-trend API，
 * 用每日 totalRuns 字段绘制运行实例数折线图。
 */
export function RunCountTrendCard({
  workflowId,
  currentTotalRuns,
  days = 7,
  embedded = false,
}: {
  workflowId: string
  currentTotalRuns: string
  days?: 1 | 'yesterday' | 7 | 30
  embedded?: boolean
}) {
  const [data, setData] = useState<TrendPoint[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(false)
  const [requestSeq, setRequestSeq] = useState(0)

  useEffect(() => {
    let cancelled = false
    const fetchTrend = async () => {
      setLoading(true)
      setError(false)
      try {
        const apiDays = days === 'yesterday' ? 1 : days
        const res = await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/success-trend?days=${apiDays}`)
        if (!res.ok) throw new Error(`HTTP ${res.status}`)
        const json = await res.json()
        if (!cancelled) setData(json.data ?? [])
      } catch {
        if (!cancelled) {
          setData([])
          setError(true)
        }
      }
      finally { if (!cancelled) setLoading(false) }
    }
    fetchTrend()
    return () => { cancelled = true }
  }, [workflowId, days, requestSeq])

  const hasTrend = data.length >= 2
  const latest = hasTrend ? data[data.length - 1] : null
  const previous = hasTrend ? data[Math.max(0, data.length - 2)] : null
  const change = latest && previous
    ? latest.totalRuns - previous.totalRuns
    : null
  const peakRuns = data.length > 0 ? Math.max(...data.map((d) => d.totalRuns)) : 0

  return (
    <div className={`${embedded ? '' : 'rounded-xl border border-slate-200'} bg-white px-4 py-3`}>
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-baseline gap-2">
          <span className="text-sm font-semibold text-slate-900">运行实例趋势</span>
        </div>
        <span className="text-[10px] text-slate-400">近 {days} 天 · 每日实例数</span>
      </div>

      {loading ? (
        <div className="h-[60px] flex items-center justify-center">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-200 border-t-blue-500" />
        </div>
      ) : error ? (
        <div className="flex h-[60px] items-center justify-center gap-2 text-xs text-rose-500">
          <span>趋势加载失败</span>
          <button type="button" onClick={() => setRequestSeq((value) => value + 1)} className="font-medium text-blue-600 hover:text-blue-700">重试</button>
        </div>
      ) : data.length > 0 ? (
        <div className="relative">
          {(() => {
            const maxVal = Math.max(peakRuns, 1)
            // Use ceiling to next "nice" number for Y axis
            const niceMax = maxVal <= 5 ? maxVal : Math.ceil(maxVal / 5) * 5
            const range = niceMax || 1
            const W = 600
            const H = 80
            const padX = 10
            const padY = 8
            const plotW = W - padX * 2
            const plotH = H - padY * 2 - 12 // extra 12 for labels
            const points = data.map((d, i) => {
              const x = data.length === 1 ? W / 2 : padX + (i / (data.length - 1)) * plotW
              const y = padY + plotH - (d.totalRuns / range) * plotH
              return { x, y, totalRuns: d.totalRuns, date: d.date }
            })

            const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x},${p.y}`).join(' ')
            const areaPath = `${linePath} L ${points[points.length - 1].x},${padY + plotH} L ${points[0].x},${padY + plotH} Z`
            const lineColor = '#3b82f6'

            // Y-axis grid lines at 0, 50%, 100% of range
            const gridLines = [0, Math.round(niceMax / 2), niceMax].map((v) => {
              const y = padY + plotH - (v / range) * plotH
              return { y, label: String(v) }
            })

            return (
              <>
                <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="block">
                  <defs>
                    <linearGradient id="runCountGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={lineColor} stopOpacity="0.12" />
                      <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  {/* grid lines */}
                  {gridLines.map((g, i) => (
                    <g key={i}>
                      <line x1={padX} y1={g.y} x2={W - padX} y2={g.y} stroke="#f0f0f0" strokeWidth="1" />
                      <text x={padX} y={g.y - 2} fill="#d1d5db" style={{ fontSize: '8px' }}>{g.label}</text>
                    </g>
                  ))}
                  {/* area */}
                  <path d={areaPath} fill="url(#runCountGradient)" />
                  {/* line */}
                  <path d={linePath} fill="none" stroke={lineColor} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
                  {/* points */}
                  {points.map((p, i) => (
                    <circle key={i} cx={p.x} cy={p.y} r="2.5" fill={lineColor} stroke="white" strokeWidth="1" />
                  ))}
                  {/* X-axis labels */}
                  {[0, Math.floor(points.length / 2), points.length - 1].filter((v, i, a) => a.indexOf(v) === i).map((idx) => {
                    const p = points[idx]
                    if (!p) return null
                    return (
                      <text key={idx} x={p.x} y={H - 1} textAnchor={idx === 0 ? 'start' : idx === points.length - 1 ? 'end' : 'middle'} fill="#d1d5db" style={{ fontSize: '8px' }}>
                        {p.date.slice(5)}
                      </text>
                    )
                  })}
                </svg>
                {/* tooltip — latest point */}
                {latest && (
                  <div className="absolute top-0 right-0 text-[10px] text-gray-400">
                    {latest.date.slice(5)}: {latest.totalRuns} 个实例
                  </div>
                )}
              </>
            )
          })()}
        </div>
      ) : (
        <div className="h-[60px] flex items-center justify-center text-xs text-gray-300">暂无趋势数据</div>
      )}

      {/* 底部信息 */}
      <div className="mt-1 flex items-center justify-between text-xs text-gray-400">
        <span>{hasTrend ? `峰值 ${peakRuns} 个 / 日` : '等待趋势数据'}</span>
        {change !== null && change !== 0 && (
          <span className={change > 0 ? 'text-green-500' : 'text-red-500'}>
            {change > 0 ? '↑' : '↓'} {Math.abs(change)} 比前日
          </span>
        )}
      </div>
    </div>
  )
}
