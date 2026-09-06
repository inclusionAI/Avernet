import ReactECharts from 'echarts-for-react'
import type { IDailyTrendPoint } from '../../../types/dashboard'
import { ChartSkeleton, EmptyChart } from './StatusPieChart'
import { WidgetRangeSelector } from './WidgetRangeSelector'
import type { WidgetRange } from './widget-range'

interface LoopyTrendChartProps {
  data: IDailyTrendPoint[]
  isLoading: boolean
  isError?: boolean
  range: WidgetRange
  globalRangeLabel: string
  onRangeChange: (value: WidgetRange) => void
}

/** 管理员护航主趋势：同一终态口径的运行成功率与异常结束数。 */
export function LoopyTrendChart({ data, isLoading, isError, range, globalRangeLabel, onRangeChange }: LoopyTrendChartProps) {
  const completion = data.map((d) => {
    const rate = d.successRate
    return rate != null ? +(rate * 100).toFixed(1) : null
  })
  const failures = data.map((d) => d.failedCount)

  const option = {
    tooltip: {
      trigger: 'axis' as const,
      formatter: (params: Array<{ name: string; value: number | null; seriesName: string }>) => {
        const rate = params.find((p) => p.seriesName === '运行成功率')?.value
        const failed = params.find((p) => p.seriesName === '异常结束数')?.value
        return `${params[0]?.name ?? ''}<br/>运行成功率: ${rate ?? '—'}%<br/>异常结束数: ${failed ?? '—'}`
      },
    },
    legend: { show: false },
    grid: { left: 48, right: 54, top: 18, bottom: 36 },
    xAxis: {
      type: 'category' as const,
      data: data.map((d) => d.date.slice(5)),
      axisLabel: { fontSize: 10, color: '#9CA3AF' },
      axisLine: { lineStyle: { color: '#E5E7EB' } },
    },
    yAxis: [
      {
        type: 'value' as const,
        name: '%',
        nameTextStyle: { fontSize: 10, color: '#9CA3AF' },
        axisLabel: { fontSize: 10, color: '#9CA3AF', formatter: '{value}%' },
        splitLine: { lineStyle: { color: '#F3F4F6' } },
        min: (v: { min: number }) => Math.max(0, Math.floor(v.min - 5)),
      },
      {
        type: 'value' as const,
        name: '',
        axisLabel: { fontSize: 10, color: '#9CA3AF' },
        splitLine: { show: false },
        minInterval: 1,
      },
    ],
    series: [
      {
        name: '运行成功率',
        type: 'line',
        data: completion,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#10B981', width: 2.5 },
        itemStyle: { color: '#10B981' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
          { offset: 0, color: 'rgba(16,185,129,0.16)' }, { offset: 1, color: 'rgba(16,185,129,0.02)' },
        ] } },
      },
      {
        name: '异常结束数',
        type: 'bar',
        yAxisIndex: 1,
        data: failures,
        barMaxWidth: 18,
        itemStyle: { color: '#FCA5A5', borderRadius: [4, 4, 0, 0] },
      },
    ],
  }

  if (isLoading) return <ChartSkeleton title="运行质量趋势" />

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="mb-1 flex items-center justify-between">
        <div>
          <h3 className="text-sm font-semibold text-slate-950">运行质量趋势</h3>
          <p className="mt-1 text-xs text-slate-400">成功率上升且异常结束数下降，才代表运行质量改善</p>
        </div>
        <WidgetRangeSelector ariaLabel="运行质量趋势时间范围" value={range} globalLabel={globalRangeLabel} onChange={onRangeChange} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-4 text-[11px] text-slate-500">
        <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-full bg-emerald-500" />运行成功率</span>
        <span className="inline-flex items-center gap-1.5"><i className="h-2 w-2 rounded-sm bg-rose-300" />异常结束数</span>
        <span className="ml-auto text-slate-400">同一终态样本口径</span>
      </div>
      {isError ? (
        <p className="py-24 text-center text-xs text-rose-500">趋势数据加载失败，请稍后重试</p>
      ) : data.length === 0 ? (
        <EmptyChart />
      ) : (
        <ReactECharts option={option} style={{ height: 280 }} opts={{ renderer: 'svg' }} />
      )}
    </div>
  )
}
