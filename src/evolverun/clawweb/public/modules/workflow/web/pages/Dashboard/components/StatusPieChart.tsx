import ReactECharts from 'echarts-for-react'
import { STATUS_COLORS, STATUS_LABELS } from '../constants'

interface StatusPieChartProps {
  distribution: Record<string, number>
  isLoading: boolean
  onSliceClick: (status: string) => void
}

export function StatusPieChart({ distribution, isLoading, onSliceClick }: StatusPieChartProps) {
  const entries = Object.entries(distribution).filter(([, v]) => v > 0)

  const option = {
    tooltip: {
      trigger: 'item' as const,
      formatter: (params: { name: string; value: number; percent: number }) =>
        `${params.name}: ${params.value} (${params.percent.toFixed(1)}%)`,
    },
    legend: {
      orient: 'vertical' as const,
      right: 10,
      top: 'center',
      itemWidth: 10,
      itemHeight: 10,
      textStyle: { fontSize: 12, color: '#6B7280' },
    },
    series: [{
      type: 'pie',
      radius: ['40%', '70%'],
      center: ['35%', '50%'],
      avoidLabelOverlap: false,
      itemStyle: { borderRadius: 4, borderColor: '#fff', borderWidth: 2 },
      label: { show: false },
      emphasis: {
        label: { show: true, fontSize: 13, fontWeight: 'bold' },
        itemStyle: { shadowBlur: 10, shadowOffsetX: 0, shadowColor: 'rgba(0,0,0,0.15)' },
      },
      data: entries.map(([status, value]) => ({
        name: STATUS_LABELS[status] ?? status,
        value,
        itemStyle: { color: STATUS_COLORS[status] ?? '#9CA3AF' },
        _status: status,
      })),
    }],
  }

  if (isLoading) {
    return <ChartSkeleton title="实例状态分布" />
  }

  return (
    <div className="rounded-xl bg-white p-5 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold text-gray-900">实例状态分布</h3>
      <p className="mb-2 text-xs text-gray-400">点击扇区查看对应状态实例</p>
      {entries.length === 0 ? (
        <EmptyChart />
      ) : (
        <ReactECharts
          option={option}
          style={{ height: 260 }}
          opts={{ renderer: 'svg' }}
          onEvents={{
            click: (params: { data?: { _status?: string } }) => {
              if (params.data?._status) onSliceClick(params.data._status)
            },
          }}
        />
      )}
    </div>
  )
}

function ChartSkeleton({ title }: { title: string }) {
  return (
    <div className="rounded-xl bg-white p-5 shadow-sm">
      <h3 className="mb-3 text-sm font-semibold text-gray-900">{title}</h3>
      <div className="h-[260px] animate-pulse rounded-lg bg-gray-50" />
    </div>
  )
}

function EmptyChart() {
  return (
    <div className="flex h-[260px] items-center justify-center text-gray-400">
      <div className="text-center">
        <div className="text-3xl">📊</div>
        <div className="mt-2 text-sm">暂无数据</div>
      </div>
    </div>
  )
}

export { ChartSkeleton, EmptyChart }