import ReactECharts from 'echarts-for-react'
import type { IDashboardFailureHotspots } from '../../../types/dashboard'
import { ChartSkeleton, EmptyChart } from './StatusPieChart'
import { WidgetRangeSelector } from './WidgetRangeSelector'
import type { WidgetRange } from './widget-range'

interface FailureHotspotChartProps {
  data: IDashboardFailureHotspots | undefined
  isLoading: boolean
  isError?: boolean
  range: WidgetRange
  globalRangeLabel: string
  onRangeChange: (value: WidgetRange) => void
}

/**
 * 失败归因 Top:按节点 × 工作流 × 业务线 聚类的错误热点(横向条形),
 * 悬停看示例错误与排障单。对应运行健康层的"失败归因"指标。
 */
export function FailureHotspotChart({ data, isLoading, isError, range, globalRangeLabel, onRangeChange }: FailureHotspotChartProps) {
  const hotspots = (data?.hotspots ?? []).slice().sort((a, b) => a.count - b.count) // 升序,echarts 横向条形从下往上

  const option = {
    tooltip: {
      trigger: 'item' as const,
      formatter: (params: { name: string; data?: { count?: number; sharePct?: number; exampleError?: string; ticketRef?: string; workflowTitle?: string; sceneName?: string } }) => {
        const d = params.data
        if (!d) return params.name
        return [
          `<strong>${d.workflowTitle ?? ''} · ${params.name}</strong>`,
          `${d.sceneName ?? ''}`,
          `次数: ${d.count} · 占比 ${d.sharePct}%`,
          `示例错误: ${d.exampleError ?? ''}`,
          d.ticketRef ? `排障单: ${d.ticketRef}` : '',
        ].filter(Boolean).join('<br/>')
      },
    },
    grid: { left: 150, right: 24, top: 8, bottom: 24 },
    xAxis: {
      type: 'value' as const,
      axisLabel: { fontSize: 10, color: '#9CA3AF' },
      splitLine: { lineStyle: { color: '#F3F4F6' } },
    },
    yAxis: {
      type: 'category' as const,
      data: hotspots.map((h) => h.nodeLabel),
      axisLabel: { fontSize: 11, color: '#374151' },
      axisLine: { lineStyle: { color: '#E5E7EB' } },
    },
    series: [
      {
        type: 'bar',
        barWidth: 14,
        itemStyle: {
          color: (params: { dataIndex: number }) => {
            const palette = ['#EF4444', '#F97316', '#F59E0B', '#EAB308', '#A3A3A3']
            return palette[params.dataIndex % palette.length] ?? '#A3A3A3'
          },
          borderRadius: [0, 4, 4, 0],
        },
        label: {
          show: true,
          position: 'right' as const,
          formatter: (params: { data?: { sharePct?: number } }) => `${params.data?.sharePct ?? ''}%`,
          fontSize: 10,
          color: '#6B7280',
        },
        data: hotspots.map((h) => ({
          name: h.nodeLabel,
          value: h.count,
          count: h.count,
          sharePct: h.sharePct,
          exampleError: h.exampleError,
          ticketRef: h.ticketRef,
          workflowTitle: h.workflowTitle,
          sceneName: h.sceneName,
        })),
      },
    ],
  }

  if (isLoading) return <ChartSkeleton title="异常热点" />

  return (
    <div className="rounded-2xl border border-slate-200 bg-white p-5 shadow-[0_1px_2px_rgba(15,23,42,0.04)]">
      <div className="mb-1 flex items-center justify-between">
        <div><h3 className="text-sm font-semibold text-slate-950">异常热点</h3><p className="mt-1 text-xs text-slate-400">失败运行与成功运行中的异常信号 · 按问题签名聚合</p></div>
        <WidgetRangeSelector ariaLabel="异常热点时间范围" value={range} globalLabel={globalRangeLabel} onChange={onRangeChange} />
      </div>
      {isError ? (
        <p className="py-24 text-center text-xs text-rose-500">异常热点加载失败，请稍后重试</p>
      ) : hotspots.length === 0 ? (
        <EmptyChart />
      ) : (
        <ReactECharts option={option} style={{ height: 280 }} opts={{ renderer: 'svg' }} />
      )}
    </div>
  )
}
