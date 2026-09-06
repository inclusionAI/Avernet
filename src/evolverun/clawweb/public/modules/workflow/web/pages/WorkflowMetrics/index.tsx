import { useParams, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { dashboardApi, isDemoMode } from '@avernet/workflow/web/pages/Dashboard/api'
import { formatDuration } from '@avernet/workflow/web/pages/Dashboard/utils'
import { LoopyTrendChart } from '@avernet/workflow/web/pages/Dashboard/components/LoopyTrendChart'
import { FailureHotspotChart } from '@avernet/workflow/web/pages/Dashboard/components/FailureHotspotChart'

function MiniStat({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-gray-100 bg-gray-50/60 p-3">
      <div className="text-xs text-gray-500">{label}</div>
      <div className="mt-1 text-xl font-semibold tabular-nums text-gray-900">{value}</div>
      {sub && <div className="mt-0.5 text-[11px] text-gray-400">{sub}</div>}
    </div>
  )
}

function Rate({ r }: { r: number | null }) {
  if (r === null) return <span className="text-gray-400">—</span>
  const pct = (r * 100).toFixed(1)
  const cls = r >= 0.9 ? 'text-emerald-600' : r >= 0.7 ? 'text-amber-600' : 'text-red-600'
  return <span className={`tabular-nums font-medium ${cls}`}>{pct}%</span>
}

export default function WorkflowMetricsPage() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const navigate = useNavigate()
  const DEMO = isDemoMode()
  const nowSec = Math.floor(Date.now() / 1000)
  const from = nowSec - 30 * 86400
  const to = nowSec
  const id = decodeURIComponent(workflowId ?? '')

  const q = useQuery({
    enabled: !!id,
    queryKey: ['workflow-metrics', id, from, to],
    queryFn: () => dashboardApi.workflowMetrics(id, from, to),
  })

  const m = q.data

  // L3 暂未真实化(stub 路由返 available:false)→ 占位
  if (!q.isLoading && m?.available === false) {
    return (
      <div className="mx-auto max-w-screen-2xl px-4 py-6 sm:px-6 lg:px-8">
        <button onClick={() => navigate('/workflow-health')} className="mb-1 text-xs text-gray-500 hover:text-gray-800">← 返回工作流列表</button>
        <h1 className="text-2xl font-bold text-gray-900">{id}</h1>
        <div className="mt-10 rounded-xl border border-dashed border-gray-200 bg-gray-50/60 px-6 py-10 text-center text-sm text-gray-500">
          单工作流详情(L3)即将上线 · 大盘 L1 指标已真实化,L2/L3 下钻下一版接真数据。
        </div>
      </div>
    )
  }

  return (
    <div className="mx-auto max-w-screen-2xl px-4 py-6 sm:px-6 lg:px-8">
      <div className="mb-4">
        <button onClick={() => navigate('/workflow-health')} className="mb-1 text-xs text-gray-500 hover:text-gray-800">← 返回工作流列表</button>
        <div className="flex items-center gap-3">
          <h1 className="text-2xl font-bold text-gray-900">{m?.workflowTitle ?? id}</h1>
          {m?.released ? (
            <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-medium text-emerald-700 ring-1 ring-emerald-200">线上</span>
          ) : (
            <span className="rounded-full bg-gray-50 px-2 py-0.5 text-[11px] font-medium text-gray-500 ring-1 ring-gray-200">测试</span>
          )}
          {DEMO && <span className="rounded-md bg-violet-50 px-2 py-1 text-xs font-medium text-violet-700 ring-1 ring-violet-200">示例数据</span>}
        </div>
        <p className="mt-1 text-sm text-gray-500">
          {m ? `运行数:${m.runCount.toLocaleString()} · ` : ''}单工作流三主线趋势与节点级下钻
        </p>
      </div>

      {/* 头部四指标 */}
      <div className="mb-6 grid grid-cols-2 gap-3 lg:grid-cols-4">
        <MiniStat label="完成成功率" value={m?.completionSuccessRate != null ? `${(m.completionSuccessRate * 100).toFixed(1)}%` : '—'} sub="长程任务口径" />
        <MiniStat label="自愈成功率" value={m?.selfHealSuccessRate != null ? `${(m.selfHealSuccessRate * 100).toFixed(0)}%` : '—'} sub={`本周触发 ${m?.selfHealTriggeredRuns ?? 0} 次`} />
        <MiniStat label="完成耗时 P50" value={m?.machineDurationP50 != null ? `${(m.machineDurationP50 / 1000).toFixed(0)}s` : '—'} sub="端到端" />
        <MiniStat label="运行数" value={(m?.runCount ?? 0).toLocaleString()} sub="窗口内" />
      </div>

      {/* 运行质量演进趋势(该工作流) */}
      <div className="mb-6">
        <LoopyTrendChart data={m?.trend ?? []} isLoading={q.isLoading} range="global" globalRangeLabel="近 30 天" onRangeChange={() => {}} />
      </div>

      {/* 节点级成功率/重试表 + 失败归因 */}
      <div className="mb-6 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="rounded-xl bg-white p-5 shadow-sm">
          <h3 className="mb-3 text-sm font-semibold text-gray-900">节点级健康度</h3>
          {q.isLoading ? (
            <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <div key={i} className="h-8 animate-pulse rounded bg-gray-50" />)}</div>
          ) : (m?.nodeHealth ?? []).length === 0 ? (
            <div className="py-6 text-center text-sm text-gray-400">无节点数据</div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-left text-xs">
                <thead className="text-gray-400">
                  <tr className="border-b border-gray-100">
                    <th className="py-2 pr-3 font-medium">节点</th>
                    <th className="py-2 pr-3 font-medium">类型</th>
                    <th className="py-2 pr-3 text-right font-medium">运行</th>
                    <th className="py-2 pr-3 text-right font-medium">成功率</th>
                    <th className="py-2 pr-3 text-right font-medium">重试</th>
                    <th className="py-2 pr-3 text-right font-medium">自愈成功率</th>
                    <th className="py-2 text-right font-medium">平均耗时</th>
                  </tr>
                </thead>
                <tbody>
                  {(m?.nodeHealth ?? []).map((n) => (
                    <tr key={n.nodeId} className={n.topError ? 'border-b border-gray-50 bg-red-50/30' : 'border-b border-gray-50'}>
                      <td className="py-2 pr-3">
                        <div className="font-medium text-gray-800">{n.nodeTitle}</div>
                        {n.topError && <div className="text-[11px] text-red-500">⚠ {n.topError}</div>}
                      </td>
                      <td className="py-2 pr-3 text-gray-500">{n.executorType}</td>
                      <td className="py-2 pr-3 text-right tabular-nums text-gray-600">{n.runCount}</td>
                      <td className="py-2 pr-3 text-right"><Rate r={n.successRate} /></td>
                      <td className="py-2 pr-3 text-right tabular-nums text-gray-600">{n.retryCount}</td>
                      <td className="py-2 pr-3 text-right">{n.retryCount > 0 ? <Rate r={n.healSuccessRate} /> : <span className="text-gray-400">—</span>}</td>
                      <td className="py-2 text-right tabular-nums text-gray-600">{formatDuration(n.avgDurationMs)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <FailureHotspotChart
          data={m ? { from, to, hotspots: m.failureHotspots } : undefined}
          isLoading={q.isLoading}
          range="global"
          globalRangeLabel="近 30 天"
          onRangeChange={() => {}}
        />
      </div>

      <div className="text-xs text-gray-400">
        看该工作流的运行列表:<button onClick={() => navigate(`/workflows/${encodeURIComponent(id)}`)} className="text-blue-500 hover:underline">→ 前往运行列表</button>
      </div>
    </div>
  )
}
