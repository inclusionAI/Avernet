import { useEffect, useMemo, useState, type ReactNode } from 'react'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import { insightApi } from '../../api/insight'
import type { InsightGovernanceEvent, InsightMetricCounts, InsightOverview as InsightOverviewData, InsightScopeParams, InsightTrend, InsightTrendPoint } from '../../types/insight'
import { InsightIcon, EmptyPanel, ErrorPanel, LoadingPanel } from './InsightUi'
import { failureClassText, formatCompactDate, formatDateTime, formatRate } from './utils'

type BotOption = { botId: string; botName: string }
type ChartLineSeries = {
  name: string
  type: 'line'
  smooth?: boolean
  showSymbol?: boolean
  data: Array<number | null>
  lineStyle?: { color: string; width: number; type?: 'dashed' }
  itemStyle?: { color: string }
  areaStyle?: { color: string }
  markLine?: { silent: boolean; symbol: ['none', 'none']; data: Array<Record<string, unknown>> }
  markArea?: { silent: boolean; data: Array<Array<Record<string, unknown>>> }
  tooltip?: { show: boolean }
}

type TrendGranularity = 'day' | 'week'
type AggregatedTrendPoint = InsightTrendPoint & {
  periodStart: string
  periodEnd: string
  completionRateStd: number | null
  capabilityCompletionRateStd: number | null
  autoCompletionRateStd: number | null
  autoClosureRateStd: number | null
}
type GovernancePeriod = { start: string; end: string }
type VarianceApi = {
  value: (dimension: number) => number | string | null
  coord: (value: [string | number, number]) => [number, number]
}
type ChartVarianceSeries = {
  name: string
  type: 'custom'
  data: Array<[string, number, number, number]>
  renderItem: (_params: unknown, api: VarianceApi) => Record<string, unknown> | null
  encode: { x: number; y: number[] }
  silent: boolean
  z: number
  tooltip: { show: boolean }
}

function normalizeTrendDate(value: string): string | null {
  return eventDateKey(value)
}

function dateFromTrendKey(value: string): Date | null {
  if (!/^\d{8}$/.test(value)) return null
  const date = new Date(Date.UTC(Number(value.slice(0, 4)), Number(value.slice(4, 6)) - 1, Number(value.slice(6, 8))))
  return Number.isNaN(date.getTime()) ? null : date
}

function trendDateKey(value: Date): string {
  return `${value.getUTCFullYear()}${String(value.getUTCMonth() + 1).padStart(2, '0')}${String(value.getUTCDate()).padStart(2, '0')}`
}

function weekPeriodForTrendDate(value: string): GovernancePeriod | null {
  const date = dateFromTrendKey(value)
  if (!date) return null
  const day = date.getUTCDay()
  date.setUTCDate(date.getUTCDate() + (day === 0 ? -6 : 1 - day))
  const end = new Date(date)
  end.setUTCDate(end.getUTCDate() + 6)
  return { start: trendDateKey(date), end: trendDateKey(end) }
}

function averageTrendRate(values: Array<number | null | undefined>): number | null {
  const valid = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  return valid.length ? valid.reduce((sum, value) => sum + value, 0) / valid.length : null
}

function trendRateStd(values: Array<number | null | undefined>): number | null {
  const valid = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value))
  if (!valid.length) return null
  const average = valid.reduce((sum, value) => sum + value, 0) / valid.length
  return Math.sqrt(valid.reduce((sum, value) => sum + (value - average) ** 2, 0) / valid.length)
}

function sumTrendCount(points: InsightTrendPoint[], field: keyof InsightMetricCounts): number {
  return Math.round(points.reduce((sum, point) => sum + Number(point[field] ?? 0), 0) + 1e-9)
}

function optionalTrendCount(points: InsightTrendPoint[], field: 'overallTaskCount' | 'repairBotCapabilityFailureTaskCount'): number | undefined {
  if (!points.some((point) => point[field] != null)) return undefined
  return Math.round(points.reduce((sum, point) => sum + Number(point[field] ?? 0), 0) + 1e-9)
}

function trendPeriodLabel(point: AggregatedTrendPoint): string {
  if (point.periodStart === point.periodEnd) return formatCompactDate(point.periodStart)
  return `${formatCompactDate(point.periodStart)}–${formatCompactDate(point.periodEnd)}`
}

function periodForTrendDate(points: AggregatedTrendPoint[], date: string | null): AggregatedTrendPoint | null {
  if (!date) return null
  return points.find((point) => date >= point.periodStart && date <= point.periodEnd) ?? null
}

function aggregateTrendPoints(points: InsightTrendPoint[], granularity: TrendGranularity): AggregatedTrendPoint[] {
  const normalized = points.map((point) => ({ ...point, date: normalizeTrendDate(point.date) ?? point.date }))
  if (granularity === 'day') {
    return normalized.map((point) => ({
      ...point,
      periodStart: point.date,
      periodEnd: point.date,
      completionRateStd: null,
      capabilityCompletionRateStd: null,
      autoCompletionRateStd: null,
      autoClosureRateStd: null,
    }))
  }

  const buckets = new Map<string, { period: GovernancePeriod; points: InsightTrendPoint[] }>()
  for (const point of normalized) {
    const period = weekPeriodForTrendDate(point.date)
    if (!period) continue
    const bucket = buckets.get(period.start) ?? { period, points: [] }
    bucket.points.push(point)
    buckets.set(period.start, bucket)
  }

  return [...buckets.values()].sort((left, right) => left.period.start.localeCompare(right.period.start)).map(({ period, points: bucketPoints }) => ({
    date: period.start,
    periodStart: period.start,
    periodEnd: period.end,
    totalTaskCount: sumTrendCount(bucketPoints, 'totalTaskCount'),
    validTaskCount: sumTrendCount(bucketPoints, 'validTaskCount'),
    completeTaskCount: sumTrendCount(bucketPoints, 'completeTaskCount'),
    capabilityTaskCount: sumTrendCount(bucketPoints, 'capabilityTaskCount'),
    capabilityCompleteTaskCount: sumTrendCount(bucketPoints, 'capabilityCompleteTaskCount'),
    autoCompleteTaskCount: sumTrendCount(bucketPoints, 'autoCompleteTaskCount'),
    completionRate: averageTrendRate(bucketPoints.map((point) => point.completionRate)),
    capabilityCompletionRate: averageTrendRate(bucketPoints.map((point) => point.capabilityCompletionRate)),
    autoCompletionRate: averageTrendRate(bucketPoints.map((point) => point.autoCompletionRate)),
    autoClosureRate: averageTrendRate(bucketPoints.map((point) => point.autoClosureRate)),
    completionRateStd: trendRateStd(bucketPoints.map((point) => point.completionRate)),
    capabilityCompletionRateStd: trendRateStd(bucketPoints.map((point) => point.capabilityCompletionRate)),
    autoCompletionRateStd: trendRateStd(bucketPoints.map((point) => point.autoCompletionRate)),
    autoClosureRateStd: trendRateStd(bucketPoints.map((point) => point.autoClosureRate)),
    ...(optionalTrendCount(bucketPoints, 'overallTaskCount') != null ? { overallTaskCount: optionalTrendCount(bucketPoints, 'overallTaskCount') } : {}),
    ...(optionalTrendCount(bucketPoints, 'repairBotCapabilityFailureTaskCount') != null ? { repairBotCapabilityFailureTaskCount: optionalTrendCount(bucketPoints, 'repairBotCapabilityFailureTaskCount') } : {}),
  }))
}

function buildVarianceSeries(
  name: string,
  color: string,
  points: AggregatedTrendPoint[],
  rateField: 'completionRate' | 'capabilityCompletionRate' | 'autoCompletionRate' | 'autoClosureRate',
  stdField: 'completionRateStd' | 'capabilityCompletionRateStd' | 'autoCompletionRateStd' | 'autoClosureRateStd',
): ChartVarianceSeries | null {
  const data = points.flatMap((point) => {
    const average = point[rateField]
    const std = point[stdField]
    if (average == null || std == null || !Number.isFinite(average) || !Number.isFinite(std) || std <= 0) return []
    return [[point.periodStart, average * 100, Math.max(0, (average - std) * 100), Math.min(100, (average + std) * 100)] as [string, number, number, number]]
  })
  if (!data.length) return null
  return {
    name,
    type: 'custom',
    data,
    renderItem: (_params, api) => {
      const category = String(api.value(0) ?? '')
      const average = Number(api.value(1))
      const lower = Number(api.value(2))
      const upper = Number(api.value(3))
      if (!category || ![average, lower, upper].every(Number.isFinite)) return null
      const x = api.coord([category, average])[0]
      const lowY = api.coord([category, lower])[1]
      const highY = api.coord([category, upper])[1]
      const cap = 5
      return {
        type: 'group',
        children: [
          { type: 'line', shape: { x1: x, y1: lowY, x2: x, y2: highY }, style: { stroke: color, lineWidth: 2, opacity: 0.55 } },
          { type: 'line', shape: { x1: x - cap, y1: lowY, x2: x + cap, y2: lowY }, style: { stroke: color, lineWidth: 2, opacity: 0.55 } },
          { type: 'line', shape: { x1: x - cap, y1: highY, x2: x + cap, y2: highY }, style: { stroke: color, lineWidth: 2, opacity: 0.55 } },
        ],
      }
    },
    encode: { x: 0, y: [1, 2, 3] },
    silent: true,
    z: 1,
    tooltip: { show: false },
  }
}


const failureColors = ['#f97316', '#ef4444', '#8b5cf6', '#64748b', '#eab308']
const EMPTY_GOVERNANCE_EVENTS: InsightGovernanceEvent[] = []

type Props = {
  isAdmin?: boolean
  scope: InsightScopeParams
  botOptions: BotOption[]
  onScopeChange: (patch: InsightScopeParams) => void
  onFailureDrilldown: (failureClass?: string) => void
}

function MetricCard({ label, value, detail, tone = 'blue' }: {
  label: ReactNode
  value: string
  detail: string
  tone?: 'blue' | 'violet' | 'emerald' | 'red' | 'gray'
}) {
  const tones = {
    blue: 'bg-blue-50 text-blue-600',
    violet: 'bg-violet-50 text-violet-600',
    emerald: 'bg-emerald-50 text-emerald-600',
    red: 'bg-red-50 text-red-600',
    gray: 'bg-gray-100 text-gray-600',
  }
  return (
    <div className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="flex items-start justify-between gap-3">
        <div>
          <p className="text-xs font-medium text-gray-500">{label}</p>
          <p className="mt-2 text-3xl font-semibold tracking-tight text-gray-950">{value}</p>
          <p className="mt-2 text-xs text-gray-400">{detail}</p>
        </div>
        <span className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-xl ${tones[tone]}`}>
          <InsightIcon name={tone === 'gray' ? 'database' : tone === 'emerald' ? 'check' : 'chart'} />
        </span>
      </div>
    </div>
  )
}

function InfoHint({ text }: { text: string }) {
  return <span title={text} aria-label={text} className="ml-1 inline-flex cursor-help text-[11px] font-medium text-gray-400">ⓘ</span>
}

function eventDateKey(value: string | number | null | undefined): string | null {
  if (value == null || value === '') return null
  if (typeof value === 'number') {
    const date = new Date(value < 10_000_000_000 ? value * 1000 : value)
    if (Number.isNaN(date.getTime())) return null
    return date.toISOString().slice(0, 10).replaceAll('-', '')
  }
  const match = value.match(/^(\d{4})-?(\d{2})-?(\d{2})/)
  return match ? `${match[1]}${match[2]}${match[3]}` : null
}

function governanceEventMeta(event: InsightGovernanceEvent) {
  if (event.verificationStatus === 'STILL_PRESENT') {
    return { label: '仍有问题', color: '#dc2626', badge: 'bg-red-50 text-red-700', lineType: 'dashed' as const }
  }
  if (event.verificationStatus === 'VERIFIED' || event.status.toUpperCase() === 'RESOLVED') {
    return { label: '已验证生效', color: '#059669', badge: 'bg-emerald-50 text-emerald-700', lineType: 'solid' as const }
  }
  if (event.verificationStatus === 'PENDING') {
    return { label: '验收中', color: '#d97706', badge: 'bg-amber-50 text-amber-700', lineType: 'dashed' as const }
  }
  if (event.verificationStatus === 'INSUFFICIENT_DATA') {
    return { label: '数据不足', color: '#6b7280', badge: 'bg-gray-100 text-gray-600', lineType: 'dashed' as const }
  }
  return { label: '已生效', color: '#2563eb', badge: 'bg-blue-50 text-blue-700', lineType: 'solid' as const }
}

function governanceActionText(event: InsightGovernanceEvent): string {
  if (event.actionType === 'DIRECT_EVOLUTION') return '自动修复'
  if (event.actionType === 'ASSIGN_OWNER') return '手动修复'
  return '改进处理'
}

export default function InsightOverview({ isAdmin = false, scope, botOptions, onScopeChange, onFailureDrilldown }: Props) {
  const navigate = useNavigate()
  const { ownerUserId, botId, from, to, isCron } = scope
  const [overview, setOverview] = useState<InsightOverviewData | null>(null)
  const [trend, setTrend] = useState<InsightTrend | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [reloadKey, setReloadKey] = useState(0)
  const [selectedGovernancePeriod, setSelectedGovernancePeriod] = useState<GovernancePeriod | null>(null)
  const [trendGranularity, setTrendGranularity] = useState<TrendGranularity>('day')

  useEffect(() => {
    let active = true
    queueMicrotask(() => {
      if (!active) return
      setLoading(true)
      setError('')
      const requestScope = { ownerUserId, botId, from, to, isCron }
      Promise.all([insightApi.overview(requestScope), insightApi.trend(requestScope)])
        .then(([overviewResult, trendResult]) => {
          if (!active) return
          setOverview(overviewResult)
          setTrend(trendResult)
        })
        .catch((reason) => {
          if (!active) return
          setError(reason instanceof Error ? reason.message : '效果指标加载失败')
        })
        .finally(() => {
          if (active) setLoading(false)
        })
    })
    return () => { active = false }
  }, [ownerUserId, botId, from, to, isCron, reloadKey])

  const governanceEvents = trend?.governanceEvents ?? EMPTY_GOVERNANCE_EVENTS
  const botScopedTrend = Boolean(botId && ownerUserId !== '*')
  const displayTrendPoints = useMemo(
    () => aggregateTrendPoints(trend?.points ?? [], trendGranularity),
    [trend, trendGranularity],
  )
  const visibleGovernanceEvents = selectedGovernancePeriod
    ? governanceEvents.filter((event) => {
        const date = eventDateKey(event.effectiveAt)
        return Boolean(date && date >= selectedGovernancePeriod.start && date <= selectedGovernancePeriod.end)
      })
    : governanceEvents

  const hasAutoClosureData = Boolean(displayTrendPoints.some((point) => point.autoClosureRate != null))

  const openImprovement = (event: InsightGovernanceEvent) => {
    const params = new URLSearchParams({ tab: 'todo', improvementId: String(event.improvementId), botId: event.botId })
    if (ownerUserId) params.set('ownerUserId', ownerUserId === '*' ? event.ownerUserId : ownerUserId)
    navigate(`/insight?${params.toString()}`)
  }

  const trendOption = useMemo(() => {
    const trendDates = displayTrendPoints.map((point) => point.periodStart)
    const effectiveEvents = botScopedTrend
      ? governanceEvents.flatMap((event) => {
          const date = eventDateKey(event.effectiveAt)
          const period = periodForTrendDate(displayTrendPoints, date)
          return date && period ? [{ event, date, period }] : []
        })
      : []
    const markerData = effectiveEvents.map(({ event, period }) => {
      const meta = governanceEventMeta(event)
      return {
        xAxis: period.periodStart,
        periodStart: period.periodStart,
        periodEnd: period.periodEnd,
        improvementId: event.improvementId,
        lineStyle: { color: meta.color, type: meta.lineType, width: 1.5 },
        label: { show: false },
      }
    })
    const areaData = effectiveEvents.map(({ event, period }) => {
      const meta = governanceEventMeta(event)
      const endDate = eventDateKey(event.observationEndAt) ?? period.periodEnd
      const endPeriod = periodForTrendDate(displayTrendPoints, endDate) ?? displayTrendPoints[displayTrendPoints.length - 1] ?? period
      return [
        { xAxis: period.periodStart, itemStyle: { color: `${meta.color}12` } },
        { xAxis: endPeriod.periodStart },
      ]
    })
    const markerSeries: ChartLineSeries = {
      name: '改进项修复时间',
      type: 'line',
      data: trendDates.map(() => null),
      showSymbol: false,
      lineStyle: { color: '#94a3b8', width: 1, type: 'dashed' },
      itemStyle: { color: '#94a3b8' },
      markArea: { silent: true, data: areaData },
      markLine: { silent: false, symbol: ['none', 'none'], data: markerData },
      tooltip: { show: false },
    }
    const tooltip = (params: Array<{ axisValue?: string }>) => {
      const axisValue = String(params[0]?.axisValue ?? '')
      const point = displayTrendPoints.find((item) => item.periodStart === axisValue)
      if (!point) return ''
      const rows: Array<[string, number | null, number | null]> = [
        ['完成率', point.completionRate, point.completionRateStd],
        ['Bot 能力完成率', point.capabilityCompletionRate, point.capabilityCompletionRateStd],
        ['自动完成率', point.autoCompletionRate, point.autoCompletionRateStd],
      ]
      if (hasAutoClosureData) rows.push(['自动闭环解决率', point.autoClosureRate ?? null, point.autoClosureRateStd])
      return [trendPeriodLabel(point), ...rows
        .filter(([, value]) => value != null)
        .map(([name, value, std]) => `${name}：${((value ?? 0) * 100).toFixed(1)}%${trendGranularity === 'week' && std != null ? `（STD ±${(std * 100).toFixed(1)} 个百分点）` : ''}`)]
        .join('<br/>')
    }
    const lineSeries: ChartLineSeries[] = [
      {
        name: '完成率', type: 'line', smooth: true, showSymbol: true,
        data: displayTrendPoints.map((point) => point.completionRate == null ? null : Number((point.completionRate * 100).toFixed(1))),
        lineStyle: { color: '#2563eb', width: 3 }, itemStyle: { color: '#2563eb' }, areaStyle: { color: 'rgba(37,99,235,.06)' },
      },
      {
        name: 'Bot 能力完成率', type: 'line', smooth: true, showSymbol: true,
        data: displayTrendPoints.map((point) => point.capabilityCompletionRate == null ? null : Number((point.capabilityCompletionRate * 100).toFixed(1))),
        lineStyle: { color: '#7c3aed', width: 2 }, itemStyle: { color: '#7c3aed' },
      },
      {
        name: '自动完成率', type: 'line', smooth: true, showSymbol: true,
        data: displayTrendPoints.map((point) => point.autoCompletionRate == null ? null : Number((point.autoCompletionRate * 100).toFixed(1))),
        lineStyle: { color: '#059669', width: 2 }, itemStyle: { color: '#059669' },
      },
      ...(hasAutoClosureData ? [{
        name: '自动闭环解决率', type: 'line' as const, smooth: true, showSymbol: true,
        data: displayTrendPoints.map((point) => point.autoClosureRate == null ? null : Number((point.autoClosureRate * 100).toFixed(1))),
        lineStyle: { color: '#0f766e', width: 2 }, itemStyle: { color: '#0f766e' },
      }] : []),
    ]
    const varianceSeries = trendGranularity === 'week'
      ? [
          buildVarianceSeries('完成率波动范围', '#2563eb', displayTrendPoints, 'completionRate', 'completionRateStd'),
          buildVarianceSeries('Bot 能力完成率波动范围', '#7c3aed', displayTrendPoints, 'capabilityCompletionRate', 'capabilityCompletionRateStd'),
          buildVarianceSeries('自动完成率波动范围', '#059669', displayTrendPoints, 'autoCompletionRate', 'autoCompletionRateStd'),
          ...(hasAutoClosureData ? [buildVarianceSeries('自动闭环解决率波动范围', '#0f766e', displayTrendPoints, 'autoClosureRate', 'autoClosureRateStd')] : []),
        ].filter((series): series is ChartVarianceSeries => Boolean(series))
      : []
    const labels = Object.fromEntries(displayTrendPoints.map((point) => [point.periodStart, trendPeriodLabel(point)]))
    return ({
      tooltip: { trigger: 'axis' as const, formatter: tooltip },
      legend: {
        top: 0,
        data: [...lineSeries.map((series) => series.name), ...varianceSeries.map((series) => series.name), ...(markerData.length ? ['改进项修复时间'] : [])],
        selected: { '改进项修复时间': true },
        textStyle: { color: '#6b7280', fontSize: 12 },
      },
      grid: { left: 48, right: 28, top: 42, bottom: 38 },
      xAxis: {
        type: 'category' as const,
        boundaryGap: false,
        data: trendDates,
        axisLine: { lineStyle: { color: '#e5e7eb' } },
        axisLabel: { formatter: (value: string) => labels[value] ?? formatCompactDate(value), color: '#9ca3af', fontSize: 11 },
      },
      yAxis: {
        type: 'value' as const,
        min: 0,
        max: 100,
        axisLabel: { formatter: '{value}%', color: '#9ca3af', fontSize: 11 },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      series: [...lineSeries, ...varianceSeries, ...(markerData.length ? [markerSeries] : [])],
    })
  }, [botScopedTrend, displayTrendPoints, governanceEvents, hasAutoClosureData, trendGranularity])

  const governanceMarkerClick = (params: { componentType?: string; data?: { improvementId?: number; xAxis?: string; periodStart?: string; periodEnd?: string } }) => {
    if (params.componentType !== 'markLine') return
    const start = eventDateKey(params.data?.periodStart ?? params.data?.xAxis)
    if (!start) return
    const period = periodForTrendDate(displayTrendPoints, start)
    const end = eventDateKey(params.data?.periodEnd ?? params.data?.xAxis) ?? period?.periodEnd ?? start
    setSelectedGovernancePeriod({ start: period?.periodStart ?? start, end: period?.periodEnd ?? end })
  }

  const trendClickHandlers = { click: governanceMarkerClick }

  const failureTrendOption = useMemo(() => {
    const points = displayTrendPoints
    const trendDates = points.map((point) => point.periodStart)
    const markerData = governanceEvents
      .flatMap((event) => {
        const date = eventDateKey(event.effectiveAt)
        const period = periodForTrendDate(points, date)
        return date && period ? [{ event, date, period }] : []
      })
      .map(({ event, period }) => {
        const meta = governanceEventMeta(event)
        return {
          xAxis: period.periodStart,
          periodStart: period.periodStart,
          periodEnd: period.periodEnd,
          improvementId: event.improvementId,
          lineStyle: { color: meta.color, type: meta.lineType, width: 1.5 },
          label: { show: false },
        }
      })
    const series: ChartLineSeries[] = [
      {
        name: '失败任务数量',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: points.map((point) => Math.max(0, Math.round(point.totalTaskCount - point.completeTaskCount))),
        lineStyle: { color: '#dc2626', width: 3 },
        itemStyle: { color: '#dc2626' },
        areaStyle: { color: 'rgba(220,38,38,.06)' },
      },
      {
        name: '能力失败数量',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: points.map((point) => Math.max(0, Math.round(point.capabilityTaskCount - point.capabilityCompleteTaskCount))),
        lineStyle: { color: '#f97316', width: 2 },
        itemStyle: { color: '#f97316' },
      },
    ]
    if (isAdmin) {
      series.push({
        name: '修复 Bot 能力错误数量',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: points.map((point) => Math.max(0, Math.round(point.repairBotCapabilityFailureTaskCount ?? 0))),
        lineStyle: { color: '#7c3aed', width: 2 },
        itemStyle: { color: '#7c3aed' },
      })
      series.push({
        name: '整体任务数量',
        type: 'line',
        smooth: true,
        showSymbol: false,
        data: points.map((point) => Math.max(0, Math.round(point.overallTaskCount ?? 0))),
        lineStyle: { color: '#6b7280', width: 1.5, type: 'dashed' },
        itemStyle: { color: '#6b7280' },
      })
    }
    if (markerData.length > 0) {
      series.push({
        name: '改进项修复时间',
        type: 'line',
        data: points.map(() => null),
        showSymbol: false,
        lineStyle: { color: '#94a3b8', width: 1, type: 'dashed' },
        itemStyle: { color: '#94a3b8' },
        markLine: { silent: false, symbol: ['none', 'none'], data: markerData },
        tooltip: { show: false },
      })
    }
    const labels = Object.fromEntries(points.map((point) => [point.periodStart, trendPeriodLabel(point)]))
    return {
      tooltip: {
        trigger: 'axis' as const,
        formatter: (params: Array<{ axisValue?: string; seriesName?: string; value?: number }>) => {
          const axisValue = String(params[0]?.axisValue ?? '')
          const point = points.find((item) => item.periodStart === axisValue)
          if (!point) return ''
          return [trendPeriodLabel(point), ...params
            .filter((item) => item.seriesName && item.seriesName !== '改进项修复时间' && item.value != null)
            .map((item) => `${item.seriesName}：${Number(item.value).toLocaleString()} 个`)]
            .join('<br/>')
        },
      },
      legend: {
        top: 0,
        data: series.map((item) => item.name),
        selected: { '整体任务数量': false, '改进项修复时间': true },
        textStyle: { color: '#6b7280', fontSize: 12 },
      },
      grid: { left: 52, right: 28, top: 42, bottom: 38 },
      xAxis: {
        type: 'category' as const,
        boundaryGap: false,
        data: trendDates,
        axisLine: { lineStyle: { color: '#e5e7eb' } },
        axisLabel: { formatter: (value: string) => labels[value] ?? formatCompactDate(value), color: '#9ca3af', fontSize: 11 },
      },
      yAxis: {
        type: 'value' as const,
        min: 0,
        minInterval: 1,
        axisLabel: { color: '#9ca3af', fontSize: 11 },
        splitLine: { lineStyle: { color: '#f3f4f6' } },
      },
      series,
    }
  }, [displayTrendPoints, governanceEvents, isAdmin])

  const failureCounts = useMemo(() => {
    if (!overview) return { failureTaskCount: 0, capabilityFailureTaskCount: 0, capabilityFailureRate: null as number | null }
    const failureTaskCount = Math.max(0, overview.counts.totalTaskCount - overview.counts.completeTaskCount)
    const capabilityFailureTaskCount = Math.max(0, overview.counts.capabilityTaskCount - overview.counts.capabilityCompleteTaskCount)
    const capabilityFailureRate = overview.counts.capabilityTaskCount > 0
      ? capabilityFailureTaskCount / overview.counts.capabilityTaskCount
      : null
    return { failureTaskCount, capabilityFailureTaskCount, capabilityFailureRate }
  }, [overview])

  const failureData = useMemo(() => overview?.failureDistribution.map((item, index) => ({
    name: failureClassText[item.failureClass] ?? item.failureClass,
    value: item.taskCount,
    failureClass: item.failureClass,
    color: failureColors[index % failureColors.length],
  })) ?? [], [overview])

  const failureOption = useMemo(() => ({
    tooltip: {
      trigger: 'item' as const,
      formatter: (params: { name: string; value: number; percent: number }) => `${params.name}<br/>${params.value} 个任务 · ${params.percent.toFixed(1)}%`,
    },
    legend: { show: false },
    series: [{
      type: 'pie', radius: ['50%', '76%'], center: ['50%', '50%'],
      itemStyle: { borderColor: '#fff', borderWidth: 3, borderRadius: 5 },
      label: { show: false },
      data: failureData.map((item) => ({
        name: item.name,
        value: item.value,
        failureClass: item.failureClass,
        itemStyle: { color: item.color },
      })),
    }],
  }), [failureData])

  return (
    <div className="space-y-5">
      <section className="rounded-2xl border border-gray-200 bg-white p-4 shadow-sm">
        <div className="flex flex-wrap items-end gap-4">
          <label className="min-w-64 flex-1">
            <span className="mb-1.5 block text-xs font-medium text-gray-500">Bot</span>
            <select
              value={scope.botId ?? ''}
              onChange={(event) => onScopeChange({ botId: event.target.value || undefined })}
              className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500 focus:ring-2 focus:ring-blue-500/10"
            >
              <option value="">{ownerUserId === '*' ? '全部用户的全部 Bot' : ownerUserId ? `${ownerUserId} 的全部 Bot` : '我的全部 Bot'}</option>
              {botOptions.map((bot) => <option key={bot.botId} value={bot.botId}>{bot.botName} · {bot.botId}</option>)}
            </select>
          </label>
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-500">开始日期</span>
            <input type="date" value={scope.from ?? ''} onChange={(event) => onScopeChange({ from: event.target.value || undefined })} className="rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500" />
          </label>
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-500">结束日期</span>
            <input type="date" value={scope.to ?? ''} onChange={(event) => onScopeChange({ to: event.target.value || undefined })} className="rounded-lg border border-gray-200 px-3 py-2 text-sm outline-none focus:border-blue-500" />
          </label>
          <label>
            <span className="mb-1.5 block text-xs font-medium text-gray-500">任务来源</span>
            <select value={scope.isCron == null ? '' : String(scope.isCron)} onChange={(event) => onScopeChange({ isCron: event.target.value === '' ? undefined : event.target.value === 'true' })} className="rounded-lg border border-gray-200 bg-white px-3 py-2.5 text-sm outline-none focus:border-blue-500">
              <option value="">全部任务</option><option value="false">用户发起</option><option value="true">定时任务</option>
            </select>
          </label>
          <button onClick={() => setReloadKey((value) => value + 1)} className="inline-flex items-center gap-2 rounded-lg border border-gray-200 px-3 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50"><InsightIcon name="refresh" />刷新</button>
        </div>
      </section>

      {loading ? <div className="rounded-2xl border border-gray-200 bg-white"><LoadingPanel text="正在计算效果指标…" /></div> : error ? <div className="rounded-2xl border border-red-100 bg-white"><ErrorPanel message={error} onRetry={() => setReloadKey((value) => value + 1)} /></div> : overview && trend ? <>
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <MetricCard label="任务完成率" value={formatRate(overview.rates.completionRate)} detail={`${overview.counts.completeTaskCount} / ${overview.counts.totalTaskCount} 个任务完成`} />
          <MetricCard label={<span>Bot 能力完成率<InfoHint text="能力任务指真正考验 Bot 自身执行能力的任务，包括工具、工作流、配置、权限/网络、数据、参数、输出和能力边界。" /></span>} value={formatRate(overview.rates.capabilityCompletionRate)} detail={`${overview.counts.capabilityCompleteTaskCount} / ${overview.counts.capabilityTaskCount} 个能力范围任务完成`} tone="violet" />
          <MetricCard label={<span>能力失败率<InfoHint text="能力失败率 = 能力失败任务数 ÷ 能力任务总数；等待用户、主动中断等非能力问题不计入此口径。" /></span>} value={formatRate(failureCounts.capabilityFailureRate)} detail={`${failureCounts.capabilityFailureTaskCount} / ${overview.counts.capabilityTaskCount} 个能力任务失败`} tone="red" />
          <MetricCard label="自动完成率" value={formatRate(overview.rates.autoCompletionRate)} detail={`${overview.counts.autoCompleteTaskCount} 个任务无需人工介入`} tone="emerald" />
          <MetricCard label="失败任务数量" value={failureCounts.failureTaskCount.toLocaleString()} detail={`总任务 ${overview.counts.totalTaskCount.toLocaleString()} · 完成 ${overview.counts.completeTaskCount.toLocaleString()}`} tone="gray" />
          <MetricCard label={<span>能力失败数量<InfoHint text="能力失败数量 = 能力任务总数 − 能力完成任务数。" /></span>} value={failureCounts.capabilityFailureTaskCount.toLocaleString()} detail={`能力任务总数 ${overview.counts.capabilityTaskCount.toLocaleString()}`} tone="red" />
        </div>

        <section className="rounded-2xl border border-gray-200 bg-white px-5 py-3.5 shadow-sm">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-900">趋势分析</h2>
              <p className="mt-1 text-xs text-gray-400">统计粒度同时作用于完成率趋势和错误任务趋势；数量按周期求和，百分比按周期内每日均值计算。</p>
            </div>
            <div className="flex items-center gap-2">
              <span className="text-xs font-medium text-gray-500">趋势粒度</span>
              <div className="inline-flex items-center gap-1 rounded-lg border border-gray-200 bg-gray-50 p-1 text-xs font-medium">
                <button type="button" aria-pressed={trendGranularity === 'day'} onClick={() => { setTrendGranularity('day'); setSelectedGovernancePeriod(null) }} className={trendGranularity === 'day' ? 'rounded-md bg-white px-3 py-1.5 text-blue-700 shadow-sm' : 'rounded-md px-3 py-1.5 text-gray-500 hover:text-gray-700'}>按天</button>
                <button type="button" aria-pressed={trendGranularity === 'week'} onClick={() => { setTrendGranularity('week'); setSelectedGovernancePeriod(null) }} className={trendGranularity === 'week' ? 'rounded-md bg-white px-3 py-1.5 text-blue-700 shadow-sm' : 'rounded-md px-3 py-1.5 text-gray-500 hover:text-gray-700'}>按周</button>
              </div>
            </div>
          </div>
        </section>

        <div className="grid gap-5 xl:grid-cols-[minmax(0,1.7fr)_minmax(360px,1fr)]">
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div><h2 className="text-sm font-semibold text-gray-900">完成率趋势</h2><p className="mt-1 text-xs text-gray-400">对比整体完成、能力范围完成和无需人工介入的完成情况{hasAutoClosureData ? '；自动闭环解决率按验收完成日期统计，未完成观察或数据不足不计入' : '；自动闭环解决率将在产生自动修复验收结论后显示'}{trendGranularity === 'week' ? '；周粒度显示每日完成率平均值，误差线表示日间 STD' : ''}{botScopedTrend ? '；竖线为治理生效时间，浅色区间为验证观察窗' : '；当前为汇总视角，不将单个 Bot 的治理事件解释为全站曲线变化'}</p></div>
              <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[11px] text-gray-500">{displayTrendPoints.length} 个周期 · 按{trendGranularity === 'day' ? '天' : '周'}</span>
            </div>
            {displayTrendPoints.length ? <ReactECharts option={trendOption} style={{ height: 310 }} opts={{ renderer: 'svg' }} onEvents={trendClickHandlers} /> : <EmptyPanel title="暂无趋势数据" />}
          </section>
          <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
            <div className="flex items-start justify-between gap-3">
              <div><h2 className="text-sm font-semibold text-gray-900">未完成原因</h2><p className="mt-1 text-xs text-gray-400">点击分类下钻到对应失败任务</p></div>
              <button onClick={() => onFailureDrilldown()} className="text-xs font-medium text-blue-600 hover:text-blue-700">查看全部</button>
            </div>
            {failureData.length ? <div className="mt-4 grid min-w-0 gap-4 md:grid-cols-[minmax(160px,0.8fr)_minmax(0,1.2fr)] 2xl:grid-cols-[minmax(180px,0.85fr)_minmax(0,1.15fr)]">
              <div className="min-w-0 rounded-xl bg-gray-50/60">
                <ReactECharts
                  option={failureOption}
                  style={{ height: 252, width: '100%' }}
                  opts={{ renderer: 'svg' }}
                  onEvents={{ click: (params: { data?: { failureClass?: string } }) => onFailureDrilldown(params.data?.failureClass) }}
                />
              </div>
              <div className="min-w-0">
                <p className="text-[11px] font-medium text-gray-400">分类明细</p>
                <div className="mt-2 grid max-h-[252px] gap-0.5 overflow-y-auto pr-1 sm:grid-cols-2 xl:grid-cols-1 2xl:grid-cols-2">
                  {failureData.map((item) => <button
                    key={item.failureClass}
                    type="button"
                    title={item.name}
                    onClick={() => onFailureDrilldown(item.failureClass)}
                    className="flex min-w-0 items-center gap-2 rounded-lg px-2.5 py-1 text-left hover:bg-gray-50"
                  >
                    <span className="h-2.5 w-2.5 shrink-0 rounded-[3px]" style={{ backgroundColor: item.color }} />
                    <span className="min-w-0 flex-1 truncate text-xs text-gray-600">{item.name}</span>
                    <span className="shrink-0 rounded-full bg-gray-100 px-2 py-0.5 text-[10px] font-medium text-gray-500">{item.value}</span>
                  </button>)}
                </div>
              </div>
            </div> : <EmptyPanel title="当前范围没有失败任务" description="可尝试扩大日期范围或切换 Bot。" />}
          </section>
        </div>

        <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-900">错误任务趋势</h2>
              <p className="mt-1 text-xs text-gray-400">当前按{trendGranularity === 'day' ? '天' : '周'}展示当前查看范围的失败任务数量和能力失败数量；周粒度按周期内每日数量求和。普通用户只看自己的指标，管理员切换范围后可查看对应用户、Bot 或全站指标。点击图例可独立显示或隐藏曲线，点击“改进项修复时间”可筛选对应周期治理动态。</p>
            </div>
            <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[11px] text-gray-500">{displayTrendPoints.length} 个周期 · 按{trendGranularity === 'day' ? '天' : '周'}</span>
          </div>
          {displayTrendPoints.length ? <ReactECharts option={failureTrendOption} style={{ height: 280 }} opts={{ renderer: 'svg' }} onEvents={{ click: governanceMarkerClick }} /> : <EmptyPanel title="暂无错误趋势数据" />}
        </section>

        {governanceEvents.length > 0 && <section className="rounded-2xl border border-gray-200 bg-white p-5 shadow-sm">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <h2 className="text-sm font-semibold text-gray-900">{botScopedTrend ? '治理事件与验证观察' : '治理动态'}</h2>
              <p className="mt-1 text-xs leading-5 text-gray-400">{selectedGovernancePeriod ? `当前仅显示 ${formatCompactDate(selectedGovernancePeriod.start)}${selectedGovernancePeriod.start === selectedGovernancePeriod.end ? '' : `–${formatCompactDate(selectedGovernancePeriod.end)}`} 的治理动态；点击“查看”可跳转到对应改进项。` : botScopedTrend ? '修复真正生效后才进入时间轴；点击图中的改进项修复时间可筛选对应周期治理动态。' : '当前是多 Bot 汇总；点击图中的改进项修复时间可筛选对应周期治理动态。'}</p>
            </div>
            <div className="flex items-center gap-2">
              {selectedGovernancePeriod && <button type="button" onClick={() => setSelectedGovernancePeriod(null)} className="text-xs font-medium text-blue-600 hover:text-blue-700">显示全部</button>}
              <span className="rounded-full bg-gray-100 px-2.5 py-1 text-[11px] text-gray-500">{visibleGovernanceEvents.length} 个事件</span>
            </div>
          </div>
          {visibleGovernanceEvents.length ? <div className="mt-4 grid gap-2 lg:grid-cols-2">
            {visibleGovernanceEvents.slice(-20).map((event) => {
              const meta = governanceEventMeta(event)
              return <button key={`${event.improvementId}:${event.effectiveAt}`} type="button" onClick={() => openImprovement(event)} className="flex min-w-0 items-start gap-3 rounded-xl border border-gray-100 px-3.5 py-3 text-left transition hover:border-blue-200 hover:bg-blue-50/40">
                <span className="mt-1 h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: meta.color }} />
                <span className="min-w-0 flex-1">
                  <span className="flex flex-wrap items-center gap-2"><span className="truncate text-xs font-semibold text-gray-800">{event.title}</span><span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${meta.badge}`}>{meta.label}</span></span>
                  <span className="mt-1 block text-[11px] text-gray-500">{governanceActionText(event)} · 生效 {formatDateTime(event.effectiveAt)}{!botScopedTrend && ` · ${event.ownerUserId} / ${event.botId}`}</span>
                  <span className="mt-1 block text-[11px] text-gray-400">观察至 {formatDateTime(event.observationEndAt)} · 改进项 #{event.improvementId}</span>
                </span>
                <span className="shrink-0 text-xs font-medium text-blue-600">查看</span>
              </button>
            })}
          </div> : <div className="mt-4"><EmptyPanel title={trendGranularity === 'week' ? '该周期没有可展示的治理动态' : '当天没有可展示的治理动态'} description="请选择其他改进项修复时间，或点击“显示全部”。" /></div>}
        </section>}

        <section className="overflow-hidden rounded-2xl border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-100 px-5 py-4">
            <div><h2 className="text-sm font-semibold text-gray-900">Bot 效果对比</h2><p className="mt-1 text-xs text-gray-400">从个人空间下钻到具体 Bot</p></div>
            <span className="text-xs text-gray-400">数据批次 {overview.sourceBatchId}</span>
          </div>
          {overview.botComparison.length ? <div className="overflow-x-auto">
            <table className="w-full min-w-[820px] text-left text-sm">
              <thead className="bg-gray-50/80 text-xs text-gray-500"><tr><th className="px-5 py-3">Bot</th><th className="px-4 py-3">任务数</th><th className="px-4 py-3">完成率</th><th className="px-4 py-3">能力完成率</th><th className="px-4 py-3">自动完成率</th><th className="px-5 py-3 text-right">操作</th></tr></thead>
              <tbody className="divide-y divide-gray-100">{overview.botComparison.map((bot) => <tr key={`${bot.ownerUserId ?? overview.scope.userId}:${bot.botId}`} className="hover:bg-gray-50/70"><td className="px-5 py-4"><p className="font-medium text-gray-900">{bot.botName}</p><p className="mt-1 font-mono text-[11px] text-gray-400">{bot.botId}</p>{ownerUserId === '*' && bot.ownerUserId && <p className="mt-1 text-[10px] font-medium text-amber-700">归属 {bot.ownerUserId}</p>}</td><td className="px-4 py-4 text-gray-600">{bot.totalTaskCount}</td><td className="px-4 py-4 font-medium text-blue-700">{formatRate(bot.completionRate)}</td><td className="px-4 py-4 text-violet-700">{formatRate(bot.capabilityCompletionRate)}</td><td className="px-4 py-4 text-emerald-700">{formatRate(bot.autoCompletionRate)}</td><td className="px-5 py-4 text-right"><button onClick={() => onScopeChange({ ownerUserId: ownerUserId === '*' ? bot.ownerUserId : ownerUserId, botId: bot.botId })} className="font-medium text-blue-600 hover:text-blue-700">查看该 Bot</button></td></tr>)}</tbody>
            </table>
          </div> : <EmptyPanel title="当前范围没有 Bot 指标" />}
        </section>
      </> : null}
    </div>
  )
}
