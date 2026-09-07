import { useWorkflowHealth, useWorkflowHealthTrend } from '@avernet/workflow/web/api/hooks'

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

function scoreColor(score: number) {
  if (score >= 80) return { text: 'text-green-600', ring: 'stroke-green-500', label: 'text-green-600 bg-green-100', desc: '健康' }
  if (score >= 60) return { text: 'text-orange-500', ring: 'stroke-orange-400', label: 'text-orange-600 bg-orange-100', desc: '需关注' }
  return { text: 'text-red-600', ring: 'stroke-red-500', label: 'text-red-600 bg-red-100', desc: '需修复' }
}

export function HealthScoreCard({ workflowId, onNodeClick }: { workflowId: string; onNodeClick?: (nodeId: string) => void }) {
  const { data: health, isLoading, error } = useWorkflowHealth(workflowId)
  const { data: trend } = useWorkflowHealthTrend(workflowId, 7)

  if (isLoading) {
    return (
      <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
        <div className="flex items-center gap-2 text-gray-400">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-gray-200 border-t-blue-500" />
          <span className="text-sm">计算健康度…</span>
        </div>
      </div>
    )
  }

  if (error || !health) return null

  const s = scoreColor(health.overallScore)
  const circumference = 2 * Math.PI * 32
  const dashOffset = circumference - (health.overallScore / 100) * circumference

  const metricCard = (label: string, value: string, color = 'text-gray-700') => (
    <div className="rounded-lg bg-gray-50 p-2.5">
      <div className="text-[10px] text-gray-400 mb-0.5">{label}</div>
      <div className={`text-sm font-bold ${color}`}>{value}</div>
    </div>
  )

  return (
    <div className={`rounded-xl border ${health.overallScore >= 80 ? 'border-green-200' : health.overallScore >= 60 ? 'border-orange-200' : 'border-red-200'} bg-white p-5 shadow-sm`}>
      <div className="flex items-center gap-5">
        {/* 评分圆环 */}
        <div className="relative flex-shrink-0">
          <svg width="80" height="80" viewBox="0 0 80 80">
            <circle cx="40" cy="40" r="32" fill="none" stroke="#e5e7eb" strokeWidth="6" />
            <circle
              cx="40" cy="40" r="32" fill="none"
              className={s.ring} strokeWidth="6" strokeLinecap="round"
              strokeDasharray={circumference} strokeDashoffset={dashOffset}
              transform="rotate(-90 40 40)"
            />
          </svg>
          <div className="absolute inset-0 flex flex-col items-center justify-center">
            <span className={`text-2xl font-bold ${s.text}`}>{health.overallScore}</span>
            <span className="text-[10px] text-gray-400">/100</span>
          </div>
        </div>

        {/* 指标列 */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-sm font-semibold text-gray-700">工作流健康度</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded-full font-medium ${s.label}`}>{s.desc}</span>
          </div>
          <div className="grid grid-cols-2 gap-3">
            {metricCard('成功率', `${health.successRate}%`, health.successRate >= 80 ? 'text-green-600' : health.successRate >= 60 ? 'text-orange-500' : 'text-red-600')}
            {metricCard('节点失败率', `${health.nodeFailureRate}%`)}
            {metricCard('P95 耗时', formatDuration(health.p95DurationMs))}
            {metricCard('平均重试', `${health.retryRate}`)}
          </div>
        </div>
      </div>

      {/* 趋势折线图 */}
      {trend && trend.length > 1 && (
        <div className="mt-3 pt-3 border-t border-gray-100">
          <div className="flex items-center justify-between mb-1">
            <span className="text-[10px] text-gray-400">过去 {trend.length} 天评分走势</span>
            <span className={`text-[10px] font-medium ${trend[trend.length - 1].overall_score >= trend[0].overall_score ? 'text-green-500' : 'text-red-500'}`}>
              {trend[trend.length - 1].overall_score >= trend[0].overall_score ? '↑' : '↓'} {Math.abs(trend[trend.length - 1].overall_score - trend[0].overall_score)} 分
            </span>
          </div>
          <svg width="100%" height="30" viewBox="0 0 100 30" preserveAspectRatio="none">
            {(() => {
              const scores = trend.map((t) => t.overall_score)
              const min = Math.min(...scores, 0)
              const max = Math.max(...scores, 100)
              const range = max - min || 1
              const points = scores.map((s, i) => {
                const x = (i / (scores.length - 1)) * 100
                const y = 30 - ((s - min) / range) * 28 - 1
                return `${x},${y}`
              }).join(' ')
              const color = health && health.overallScore >= 80 ? '#22c55e' : health && health.overallScore >= 60 ? '#f97316' : '#ef4444'
              return (
                <>
                  <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinejoin="round" strokeLinecap="round" />
                  {scores.map((s, i) => {
                    const x = (i / (scores.length - 1)) * 100
                    const y = 30 - ((s - min) / range) * 28 - 1
                    return <circle key={i} cx={x} cy={y} r="1.5" fill={color} />
                  })}
                </>
              )
            })()}
          </svg>
        </div>
      )}

      {/* 瓶颈/脆弱节点 + 建议 */}
      <div className="mt-4 pt-3 border-t border-gray-100 space-y-1.5">
        {(health.bottleneckNode || health.fragileNode) && (
          <div className="flex items-center gap-3 text-xs">
            {health.bottleneckNode && (
              <button
                onClick={() => onNodeClick?.(health.bottleneckNode!)}
                className="inline-flex items-center gap-1 text-purple-600 hover:text-purple-700 hover:underline cursor-pointer"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-purple-400" />
                瓶颈: {health.bottleneckNode}
              </button>
            )}
            {health.fragileNode && (
              <button
                onClick={() => onNodeClick?.(health.fragileNode!)}
                className="inline-flex items-center gap-1 text-red-500 hover:text-red-600 hover:underline cursor-pointer"
              >
                <span className="h-1.5 w-1.5 rounded-full bg-red-400" />
                脆弱: {health.fragileNode}
              </button>
            )}
          </div>
        )}
        <p className="text-xs text-gray-400 leading-relaxed">{health.recommendation}</p>
      </div>
    </div>
  )
}