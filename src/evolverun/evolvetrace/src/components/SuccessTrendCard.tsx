import { useState, useEffect } from 'react'

type TrendPoint = {
  date: string
  totalRuns: number
  succeededRuns: number
  failedRuns: number
  successRate: number
}

export function SuccessTrendCard({ workflowId, currentSuccessRate, currentDetail }: {
  workflowId: string
  currentSuccessRate: string
  currentDetail: string
}) {
  const [days, setDays] = useState<7 | 30>(7)
  const [data, setData] = useState<TrendPoint[]>([])
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    let cancelled = false
    const fetchTrend = async () => {
      setLoading(true)
      try {
        const res = await fetch(`/api/workflows/${encodeURIComponent(workflowId)}/success-trend?days=${days}`)
        if (res.ok) {
          const json = await res.json()
          if (!cancelled) setData(json.data ?? [])
        }
      } catch { /* ignore */ }
      finally { if (!cancelled) setLoading(false) }
    }
    fetchTrend()
    return () => { cancelled = true }
  }, [workflowId, days])

  const hasTrend = data.length >= 2
  const latest = hasTrend ? data[data.length - 1] : null
  const previous = hasTrend ? data[Math.max(0, data.length - 2)] : null
  const change = latest && previous
    ? latest.successRate - previous.successRate
    : null

  return (
    <div className="rounded-lg border border-gray-200 bg-white px-4 py-3 shadow-sm">
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-medium uppercase tracking-wider text-gray-500">Success Rate</span>
          <span className={`text-2xl font-bold ${currentSuccessRate === '—' ? 'text-gray-300' : Number(currentSuccessRate.replace('%', '')) >= 80 ? 'text-green-600' : Number(currentSuccessRate.replace('%', '')) >= 50 ? 'text-orange-500' : 'text-red-600'}`}>
            {currentSuccessRate}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {[7, 30].map((d) => (
            <button
              key={d}
              onClick={() => setDays(d as 7 | 30)}
              className={`rounded-md px-2 py-0.5 text-[10px] transition-all ${days === d ? 'bg-blue-600 text-white' : 'bg-gray-100 text-gray-400 hover:bg-gray-200'}`}
            >
              {d}天
            </button>
          ))}
        </div>
      </div>

      {loading ? (
        <div className="flex h-[60px] items-center justify-center">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-200 border-t-blue-500" />
        </div>
      ) : data.length > 0 ? (
        <div className="relative">
          {(() => {
            const rates = data.map((d) => d.successRate)
            const maxRate = 100
            const minRate = 0
            const range = maxRate - minRate || 1
            const W = 600
            const H = 80
            const padX = 10
            const padY = 8
            const plotW = W - padX * 2
            const plotH = H - padY * 2 - 12
            const points = data.map((d, i) => {
              const x = data.length === 1 ? W / 2 : padX + (i / (data.length - 1)) * plotW
              const y = padY + plotH - ((d.successRate - minRate) / range) * plotH
              return { x, y, rate: d.successRate, date: d.date }
            })

            const linePath = points.map((p, i) => `${i === 0 ? 'M' : 'L'} ${p.x},${p.y}`).join(' ')
            const areaPath = `${linePath} L ${points[points.length - 1].x},${padY + plotH} L ${points[0].x},${padY + plotH} Z`
            const lineColor = latest && latest.successRate >= 50 ? '#22c55e' : '#ef4444'

            const gridLines = [0, 50, 100].map((v) => {
              const y = padY + plotH - ((v - minRate) / range) * plotH
              return { y, label: `${v}%` }
            })

            return (
              <>
                <svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="block">
                  <defs>
                    <linearGradient id="successGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={lineColor} stopOpacity="0.12" />
                      <stop offset="100%" stopColor={lineColor} stopOpacity="0" />
                    </linearGradient>
                  </defs>
                  {gridLines.map((g, i) => (
                    <g key={i}>
                      <line x1={padX} y1={g.y} x2={W - padX} y2={g.y} stroke="#f0f0f0" strokeWidth="1" />
                      <text x={padX} y={g.y - 2} fill="#d1d5db" style={{ fontSize: '8px' }}>{g.label}</text>
                    </g>
                  ))}
                  <path d={areaPath} fill="url(#successGradient)" />
                  <path d={linePath} fill="none" stroke={lineColor} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
                  {points.map((p, i) => (
                    <circle key={i} cx={p.x} cy={p.y} r="2.5" fill={p.rate >= 50 ? '#22c55e' : '#ef4444'} stroke="white" strokeWidth="1" />
                  ))}
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
                {latest && (
                  <div className="absolute right-0 top-0 text-[10px] text-gray-400">
                    {latest.date.slice(5)}: {latest.successRate}% ({latest.succeededRuns}/{latest.totalRuns})
                  </div>
                )}
              </>
            )
          })()}
        </div>
      ) : (
        <div className="flex h-[60px] items-center justify-center text-xs text-gray-300">暂无趋势数据</div>
      )}

      <div className="mt-1 flex items-center justify-between text-xs text-gray-400">
        <span>{currentDetail}</span>
        {change !== null && change !== 0 && (
          <span className={change > 0 ? 'text-green-500' : 'text-red-500'}>
            {change > 0 ? '↑' : '↓'} {Math.abs(change).toFixed(1)}% 比前日
          </span>
        )}
      </div>
    </div>
  )
}
