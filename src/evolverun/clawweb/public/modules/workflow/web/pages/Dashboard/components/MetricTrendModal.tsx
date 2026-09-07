import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import ReactECharts from 'echarts-for-react'
import { dashboardApi } from '../api'
import type { MetricKey, TrendGranularity } from '../../../types/dashboard'

/**
 * KPI 点钻统一弹窗:点任何 KPI 卡 = 看趋势(按天/周/月切换,窗口随粒度自适应)。
 * - metric != null:拉真趋势。
 * - metric == null:该指标暂不可算 → 占位说明(naReason)+ 列表入口。
 * - 质量类指标底部带"查看工作流列表 →"(l2),回答"谁拖的"。
 */
interface MetricTrendModalProps {
  open: boolean
  metric: MetricKey | null
  label: string
  naReason?: string           // metric=null 时展示的缺失原因
  l2Link?: string             // 覆盖默认 L2 链接(分轨卡用 ?track=released 等)
  defaultGranularity?: TrendGranularity
  onClose: () => void
}

const GRANS: Array<{ key: TrendGranularity; label: string }> = [
  { key: 'day', label: '按天' },
  { key: 'week', label: '按周' },
  { key: 'month', label: '按月' },
]

// 粒度自适应窗口:30 天按月只有 1 个桶没意义,窗口跟着粒度走
const GRAN_WINDOW: Record<TrendGranularity, { days: number; desc: string }> = {
  day: { days: 30, desc: '近 30 天 · 每日' },
  week: { days: 84, desc: '近 12 周 · 每周' },
  month: { days: 365, desc: '近 12 个月 · 每月' },
}

type ValueKind = 'count' | 'percent' | 'seconds' | 'hours'

const METRIC_META: Record<MetricKey, { kind: ValueKind; unitDesc: string; l2?: string }> = {
  runs: { kind: 'count', unitDesc: '运行实例数' },
  activeWorkflows: { kind: 'count', unitDesc: '跑过的工作流数' },
  dau: { kind: 'count', unitDesc: '活跃用户数' },
  releases: { kind: 'count', unitDesc: '上线工作流数' },
  deploys: { kind: 'count', unitDesc: '发布次数' },
  completionRate: { kind: 'percent', unitDesc: '长程任务完成率', l2: '/workflow-health?sort=completionAsc' },
  successRate: { kind: 'percent', unitDesc: '总成功率(终态口径)', l2: '/workflow-health?sort=completionDesc' },
  machineP50: { kind: 'seconds', unitDesc: '完成耗时 P50', l2: '/workflow-health?sort=machineAsc' },
  releaseSuccessRate: { kind: 'percent', unitDesc: '发布成功率' },
  rollbackRate: { kind: 'percent', unitDesc: '回滚率' },
  deliveryLagHours: { kind: 'hours', unitDesc: '平均交付拖期' },
  selfHealRate: { kind: 'percent', unitDesc: '自愈成功率', l2: '/workflow-health?sort=healDesc' },
  onlineCompletionRate: { kind: 'percent', unitDesc: '线上完成成功率', l2: '/workflow-health?track=released' },
  onlineRuns: { kind: 'count', unitDesc: '线上运行次数', l2: '/workflow-health?track=released' },
  testRuns: { kind: 'count', unitDesc: '测试运行次数', l2: '/workflow-health?track=draft' },
}

/** 桶标签瘦身:天/月去年份、周保留 W##。 */
function fmtBucket(b: string): string {
  if (/^\d{4}-\d{2}-\d{2}$/.test(b)) return b.slice(5)
  if (/^\d{4}-W\d{2}$/.test(b)) return b.slice(5)
  if (/^\d{4}-\d{2}$/.test(b)) return b.slice(5)
  return b
}

export function MetricTrendModal({
  open,
  metric,
  label,
  naReason,
  l2Link,
  defaultGranularity = 'week',
  onClose,
}: MetricTrendModalProps) {
  const [gran, setGran] = useState<TrendGranularity>(defaultGranularity)
  const navigate = useNavigate()
  const win = GRAN_WINDOW[gran]
  const to = Math.floor(Date.now() / 1000)
  const from = to - win.days * 86400

  const meta = metric ? METRIC_META[metric] : null
  const resolvedL2 = l2Link ?? meta?.l2

  const q = useQuery({
    enabled: open && metric !== null,
    queryKey: ['dashboard', 'metric-trend', metric, gran],
    queryFn: () => dashboardApi.metricTrend(metric as MetricKey, gran, from, to),
  })

  if (!open) return null

  // 值 → 图值:percent ×100;seconds ms→s;count/hours 原值
  const toChart = (v: number | null): number | null => {
    if (v == null || !meta) return null
    if (meta.kind === 'percent') return +(v * 100).toFixed(1)
    if (meta.kind === 'seconds') return +(v / 1000).toFixed(1)
    if (meta.kind === 'hours') return +v.toFixed(1)
    return v
  }
  const fmtVal = (v: number | null): string => {
    if (v == null || !meta) return '—'
    if (meta.kind === 'percent') return `${(v * 100).toFixed(1)}%`
    if (meta.kind === 'seconds') return `${(v / 1000).toFixed(1)}s`
    if (meta.kind === 'hours') return `${v.toFixed(1)}h`
    return v.toLocaleString()
  }

  const points = q.data?.points ?? []
  const isPercent = meta?.kind === 'percent'

  const option = {
    tooltip: {
      trigger: 'axis' as const,
      formatter: (params: Array<{ name: string; dataIndex: number }>) => {
        const p = points[params[0]?.dataIndex ?? 0]
        return `${params[0]?.name ?? ''}: ${p ? fmtVal(p.value) : '—'}`
      },
    },
    grid: { left: 48, right: 24, top: 24, bottom: 36 },
    xAxis: {
      type: 'category' as const,
      data: points.map((p) => fmtBucket(p.bucket)),
      axisLabel: { fontSize: 10, color: '#9CA3AF' },
      axisLine: { lineStyle: { color: '#E5E7EB' } },
    },
    yAxis: {
      type: 'value' as const,
      // 百分比轴不下钻到 0,围绕数据波动(否则 90% 附近的线在 0-100 轴上贴顶成直线)
      min: isPercent ? (v: { min: number }) => Math.max(0, Math.floor(v.min - 5)) : undefined,
      max: isPercent ? 100 : undefined,
      axisLabel: { fontSize: 10, color: '#9CA3AF', formatter: isPercent ? '{value}%' : '{value}' },
      splitLine: { lineStyle: { color: '#F3F4F6' } },
    },
    series: [
      {
        type: 'line',
        data: points.map((p) => toChart(p.value)),
        smooth: true,
        symbol: 'circle',
        symbolSize: 5,
        connectNulls: false,
        lineStyle: { color: '#3B82F6', width: 2.5 },
        itemStyle: { color: '#3B82F6' },
        areaStyle: { color: { type: 'linear', x: 0, y: 0, x2: 0, y2: 1, colorStops: [
          { offset: 0, color: 'rgba(59,130,246,0.18)' }, { offset: 1, color: 'rgba(59,130,246,0.02)' },
        ] } },
      },
    ],
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-2xl rounded-xl bg-white p-5 shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-3 flex items-center justify-between">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">{label}</h3>
            <p className="text-[11px] text-gray-400">
              {metric ? `${win.desc}${meta?.unitDesc ?? ''}` : '暂不可算'}
            </p>
          </div>
          <button onClick={onClose} className="rounded p-1 text-gray-400 hover:bg-gray-100 hover:text-gray-700" aria-label="关闭">✕</button>
        </div>

        {metric !== null && (
          <div className="mb-3 inline-flex rounded-lg border border-gray-200 p-0.5">
            {GRANS.map((g) => (
              <button
                key={g.key}
                onClick={() => setGran(g.key)}
                className={`rounded-md px-3 py-1 text-xs transition ${
                  gran === g.key ? 'bg-blue-50 text-blue-700' : 'text-gray-500 hover:bg-gray-50'
                }`}
              >
                {g.label}
              </button>
            ))}
          </div>
        )}

        {metric === null ? (
          <div className="py-10 text-center text-sm text-gray-400">
            {naReason ?? '该指标暂不可算'}
          </div>
        ) : q.isLoading ? (
          <div className="h-64 animate-pulse rounded-lg bg-gray-50" />
        ) : q.data?.available === false ? (
          <div className="py-10 text-center text-sm text-gray-400">
            {naReason ?? (
              <>当前库暂不可算该指标趋势(需 MySQL <code className="rounded bg-gray-100 px-1 text-gray-600">workflow_deploy_history</code>)。</>
            )}
          </div>
        ) : points.length === 0 ? (
          <div className="py-10 text-center text-sm text-gray-400">所选周期内无数据</div>
        ) : (
          <ReactECharts option={option} style={{ height: 300 }} opts={{ renderer: 'svg' }} />
        )}

        {resolvedL2 && (
          <div className="mt-3 border-t border-gray-100 pt-3 text-xs text-gray-500">
            谁拖低了它?
            <button
              onClick={() => { onClose(); navigate(resolvedL2) }}
              className="ml-1 text-blue-600 hover:underline"
            >
              查看工作流列表 →
            </button>
          </div>
        )}
      </div>
    </div>
  )
}