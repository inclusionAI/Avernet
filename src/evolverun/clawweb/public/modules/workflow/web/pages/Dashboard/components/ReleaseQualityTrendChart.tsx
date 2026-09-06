import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { dashboardApi } from '../api'
import type { TrendGranularity } from '../../../types/dashboard'
import { WidgetRangeSelector } from './WidgetRangeSelector'
import { resolveWidgetRange, type WidgetRange } from './widget-range'

interface ReleaseQualityTrendChartProps {
  from: number
  to: number
  globalRangeLabel: string
}

const GRANS: Array<{ key: TrendGranularity; label: string }> = [
  { key: 'day', label: '按天' },
  { key: 'week', label: '按周' },
  { key: 'month', label: '按月' },
]

function fmtBucket(b: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(b)) return b.slice(5)
  if (/^\d{4}-W\d{2}$/.test(b)) return b.slice(5)
  if (/^\d{4}-\d{2}$/.test(b)) return b.slice(5)
  return b
}

/**
 * 发布质量趋势:发布次数(柱)+ 发布成功率/回滚率(线)随时间变化,
 * 给"发布效能"区块看迭代效果 —— 发布质量有没有随版本变好。
 * MySQL only;SQLite 库显示"暂不可算"。
 */
export function ReleaseQualityTrendChart({ from, to, globalRangeLabel }: ReleaseQualityTrendChartProps) {
  const [gran, setGran] = useState<TrendGranularity>('week')
  const [range, setRange] = useState<WidgetRange>('global')
  const window = resolveWidgetRange(range, { from, to, label: globalRangeLabel })

  const q = useQuery({
    queryKey: ['dashboard', 'release-quality-trend', gran, window.from, window.to],
    queryFn: () => dashboardApi.releaseQualityTrend(gran, window.from, window.to),
  })

  const points = q.data?.points ?? []

  const option = {
    tooltip: {
      trigger: 'axis' as const,
      formatter: (params: Array<{ name: string; value: number | null; seriesName: string }>) => {
        const head = params[0]?.name ?? ''
        const rc = params.find((p) => p.seriesName === '发布次数')?.value
        const sr = params.find((p) => p.seriesName === '发布成功率')?.value
        const rr = params.find((p) => p.seriesName === '回滚率')?.value
        return `${head}<br/>发布次数: ${rc ?? 0}<br/>发布成功率: ${sr != null ? `${(sr * 100).toFixed(0)}%` : '—'}<br/>回滚率: ${rr != null ? `${(rr * 100).toFixed(0)}%` : '—'}`
      },
    },
    legend: {
      data: ['发布次数', '发布成功率', '回滚率'],
      top: 0,
      right: 0,
      textStyle: { fontSize: 11, color: '#6B7280' },
      itemWidth: 12,
      itemHeight: 8,
    },
    grid: { left: 44, right: 48, top: 36, bottom: 36 },
    xAxis: {
      type: 'category' as const,
      data: points.map((p) => fmtBucket(p.bucket)),
      axisLabel: { fontSize: 10, color: '#9CA3AF' },
      axisLine: { lineStyle: { color: '#E5E7EB' } },
    },
    yAxis: [
      {
        type: 'value' as const,
        name: '次数',
        nameTextStyle: { fontSize: 10, color: '#9CA3AF' },
        axisLabel: { fontSize: 10, color: '#9CA3AF' },
        splitLine: { lineStyle: { color: '#F3F4F6' } },
      },
      {
        type: 'value' as const,
        name: '%',
        nameTextStyle: { fontSize: 10, color: '#9CA3AF' },
        axisLabel: { fontSize: 10, color: '#9CA3AF', formatter: '{value}%' },
        splitLine: { show: false },
        min: 0,
        max: 100,
      },
    ],
    series: [
      {
        name: '发布次数',
        type: 'bar',
        barMaxWidth: 28,
        data: points.map((p) => p.releaseCount),
        itemStyle: { color: '#C7D2FE' },
      },
      {
        name: '发布成功率',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        data: points.map((p) => (p.successRate != null ? +(p.successRate * 100).toFixed(1) : null)),
        lineStyle: { color: '#10B981', width: 2.5 },
        itemStyle: { color: '#10B981' },
      },
      {
        name: '回滚率',
        type: 'line',
        yAxisIndex: 1,
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        data: points.map((p) => (p.rollbackRate != null ? +(p.rollbackRate * 100).toFixed(1) : null)),
        lineStyle: { color: '#F59E0B', width: 2.5 },
        itemStyle: { color: '#F59E0B' },
      },
    ],
  }

  return (
    <div className="rounded-xl bg-white p-5 shadow-sm">
      <div className="mb-1 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-gray-900">发布质量趋势</h3>
          <p className="text-[11px] text-gray-400">发布次数(柱)+ 发布成功率/回滚率(线)· 看迭代是否变好</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <WidgetRangeSelector ariaLabel="发布质量趋势时间范围" value={range} globalLabel={globalRangeLabel} onChange={setRange} />
          <div className="inline-flex rounded-md border border-gray-200 p-0.5">
            {GRANS.map((g) => (
              <button
                key={g.key}
                onClick={() => setGran(g.key)}
                className={`rounded px-2 py-0.5 text-[11px] transition ${gran === g.key ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'}`}
              >
                {g.label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {q.isLoading ? (
        <div className="h-56 animate-pulse rounded-lg bg-gray-50" />
      ) : q.isError ? (
        <div className="py-10 text-center text-sm text-rose-500">发布趋势加载失败，请稍后重试</div>
      ) : q.data?.available === false ? (
        <div className="py-10 text-center text-sm text-gray-400">
          当前库无 <code className="rounded bg-gray-100 px-1 text-gray-600">workflow_deploy_history</code> 表(MySQL only),发布质量趋势暂不可算。
        </div>
      ) : points.length === 0 ? (
        <div className="py-10 text-center text-sm text-gray-400">窗口内无发布</div>
      ) : (
        <ReactECharts option={option} style={{ height: 280 }} opts={{ renderer: 'svg' }} />
      )}
    </div>
  )
}
