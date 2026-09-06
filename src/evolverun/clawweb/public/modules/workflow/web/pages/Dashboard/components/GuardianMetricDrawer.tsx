import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import ReactECharts from 'echarts-for-react'
import { useNavigate } from 'react-router-dom'
import { api } from '@avernet/clawweb-shared/web/api/client'
import type { IDailyTrendPoint, IDashboardOverview, IWorkflowHealthRow } from '../../../types/dashboard'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import { formatDuration } from '../utils'

export type GuardianMetricKind = 'runs' | 'successRate' | 'incomplete' | 'duration' | 'running' | 'waiting' | 'blocked' | 'queued'

interface GuardianMetricDrawerProps {
  kind: GuardianMetricKind
  overview: IDashboardOverview
  trend: IDailyTrendPoint[]
  workflows: IWorkflowHealthRow[]
  from: number
  to: number
  rangeLabel: string
  onClose: () => void
}

const INCOMPLETE_STATUSES = ['failed', 'aborted', 'cancelled', 'canceled']
const LIVE_STATUS_BY_KIND: Partial<Record<GuardianMetricKind, string>> = {
  running: 'running',
  waiting: 'waiting',
  blocked: 'blocked',
  queued: 'queued',
}
const STATUS_META: Record<string, { label: string; color: string }> = {
  succeeded: { label: '成功', color: '#10B981' },
  failed: { label: '失败', color: '#F43F5E' },
  aborted: { label: '已终止', color: '#F97316' },
  cancelled: { label: '已取消', color: '#A855F7' },
  canceled: { label: '已取消(兼容)', color: '#C084FC' },
  running: { label: '运行中', color: '#3B82F6' },
  waiting: { label: '等待中', color: '#64748B' },
  blocked: { label: '阻塞', color: '#0EA5E9' },
  queued: { label: '排队中', color: '#8B5CF6' },
}

const KIND_META: Record<GuardianMetricKind, { title: string; formula: string; trendTitle: string }> = {
  runs: {
    title: '运行数明细',
    formula: '统计 started_at 落在当前页面周期内的全部运行实例，不区分状态。',
    trendTitle: '每日运行趋势',
  },
  successRate: {
    title: '运行成功率明细',
    formula: '成功运行 ÷ 全部终态运行；运行中、等待中、阻塞和排队不进入分母。',
    trendTitle: '每日成功率趋势',
  },
  incomplete: {
    title: '异常结束明细',
    formula: 'failed + aborted + cancelled + canceled；取消运行单独展示，但属于终态未完成。',
    trendTitle: '每日异常结束趋势',
  },
  duration: {
    title: '成功运行耗时明细',
    formula: '仅统计成功运行的端到端 total_duration_ms；包含人工等待，并修正历史 1000 倍异常值。',
    trendTitle: '每日耗时 P50 趋势',
  },
  running: { title: '运行中明细', formula: '当前周期 started_at 落窗且状态为 running 的运行。', trendTitle: '运行中存量' },
  waiting: { title: '等待中明细', formula: '当前周期 started_at 落窗且状态为 waiting 的运行；等待不等于失败。', trendTitle: '等待中存量' },
  blocked: { title: '阻塞明细', formula: '当前周期 started_at 落窗且状态为 blocked 的运行；需要结合阻塞原因判断是否异常。', trendTitle: '阻塞存量' },
  queued: { title: '排队中明细', formula: '当前周期 started_at 落窗且状态为 queued 的运行；排队不进入成功率分母。', trendTitle: '排队中存量' },
}

function formatRate(value: number | null | undefined): string {
  return value == null ? '—' : `${(value * 100).toFixed(1)}%`
}

function SummaryItem({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 shadow-[0_1px_2px_rgba(15,23,42,0.03)]">
      <p className="text-[11px] font-medium text-slate-500">{label}</p>
      <p data-audit-value className="mt-1 text-xl font-semibold tabular-nums text-slate-950">{value}</p>
      {note && <p className="mt-1 text-[10px] leading-4 text-slate-400">{note}</p>}
    </div>
  )
}

function formatStartedAt(value: string | number): string {
  const timestamp = Number(value)
  if (!Number.isFinite(timestamp)) return '—'
  return new Date(timestamp * 1000).toLocaleString('zh-CN', { hour12: false })
}

export function GuardianMetricDrawer({
  kind,
  overview,
  trend,
  workflows,
  from,
  to,
  rangeLabel,
  onClose,
}: GuardianMetricDrawerProps) {
  const navigate = useNavigate()
  const [page, setPage] = useState(0)
  const pageSize = 10
  const meta = KIND_META[kind]
  const liveStatus = LIVE_STATUS_BY_KIND[kind]
  const showRuns = kind === 'runs' || kind === 'incomplete' || liveStatus !== undefined
  const statuses = kind === 'incomplete' ? INCOMPLETE_STATUSES : liveStatus ? [liveStatus] : undefined
  const showHistoricalTrend = liveStatus === undefined

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [onClose])

  const runsQ = useQuery({
    enabled: showRuns,
    queryKey: ['guardian-metric-runs', kind, from, to, page],
    queryFn: () => api.runs.list({
      statuses,
      from: String(from),
      to: String(to),
      limit: pageSize,
      offset: page * pageSize,
    }),
  })

  const statusRows = useMemo(() => Object.entries(overview.statusDistribution)
    .map(([status, count]) => ({ status, count, ...(STATUS_META[status] ?? { label: status, color: '#94A3B8' }) }))
    .sort((a, b) => b.count - a.count), [overview.statusDistribution])
  const maxStatus = Math.max(...statusRows.map((row) => row.count), 1)

  const summary = liveStatus
    ? [
        { label: STATUS_META[liveStatus]?.label ?? liveStatus, value: (overview.statusDistribution[liveStatus] ?? 0).toLocaleString(), note: '当前状态存量' },
        { label: '全部非终态', value: overview.nonTerminalCount.toLocaleString(), note: '运行、等待、阻塞、排队等' },
        { label: '全部运行', value: overview.totalRuns.toLocaleString(), note: `${rangeLabel} started_at 口径` },
      ]
    : kind === 'runs'
    ? [
        { label: '全部运行', value: overview.totalRuns.toLocaleString(), note: `${rangeLabel} started_at 口径` },
        { label: '终态样本', value: overview.terminalCount.toLocaleString(), note: '成功 + 未完成' },
        { label: '非终态', value: overview.nonTerminalCount.toLocaleString(), note: '运行、等待、阻塞、排队等' },
      ]
    : kind === 'successRate'
      ? [
          { label: '成功率', value: formatRate(overview.successRate), note: '成功 ÷ 终态' },
          { label: '成功运行', value: overview.succeededCount.toLocaleString(), note: 'succeeded' },
          { label: '终态样本', value: overview.terminalCount.toLocaleString(), note: '不含非终态' },
          { label: '长程成功率', value: formatRate(overview.completionSuccessRate), note: '节点 ≥8 或端到端 ≥5min' },
        ]
      : kind === 'incomplete'
        ? [
            { label: '异常结束', value: overview.failedCount.toLocaleString(), note: '失败、终止、取消' },
            { label: '异常结束率', value: overview.terminalCount > 0 ? formatRate(overview.failedCount / overview.terminalCount) : '—', note: '异常结束 ÷ 终态' },
            { label: '终态样本', value: overview.terminalCount.toLocaleString(), note: '与成功率同一分母' },
          ]
        : [
            { label: '端到端 P50', value: overview.machineDurationP50 == null ? '—' : formatDuration(overview.machineDurationP50), note: '成功运行中位数' },
            { label: '端到端 P95', value: overview.machineDurationP95 == null ? '—' : formatDuration(overview.machineDurationP95), note: '成功运行长尾' },
            { label: '有效样本', value: (overview.durationSampleCount ?? 0).toLocaleString(), note: '有耗时的成功运行' },
            { label: '环比 P50', value: overview.prevMachineDurationP50 == null ? '—' : formatDuration(overview.prevMachineDurationP50), note: '上一等长周期' },
          ]

  const chartValues = showHistoricalTrend
    ? trend.map((point) => {
        if (kind === 'runs') return point.runCount
        if (kind === 'successRate') return point.successRate == null ? null : +(point.successRate * 100).toFixed(1)
        if (kind === 'incomplete') return point.failedCount
        return point.machineDurationP50 == null ? null : +(point.machineDurationP50 / 1000).toFixed(1)
      })
    : []
  const isPercent = kind === 'successRate'
  const isDuration = kind === 'duration'
  const chartOption = {
    tooltip: { trigger: 'axis' as const },
    grid: { left: 48, right: 24, top: 20, bottom: 34 },
    xAxis: {
      type: 'category' as const,
      data: trend.map((point) => point.date.slice(5)),
      axisLabel: { fontSize: 10, color: '#94A3B8' },
      axisLine: { lineStyle: { color: '#E2E8F0' } },
    },
    yAxis: {
      type: 'value' as const,
      axisLabel: { fontSize: 10, color: '#94A3B8', formatter: isPercent ? '{value}%' : isDuration ? '{value}s' : '{value}' },
      splitLine: { lineStyle: { color: '#F1F5F9' } },
      minInterval: isPercent || isDuration ? undefined : 1,
      min: isPercent ? 0 : undefined,
      max: isPercent ? 100 : undefined,
    },
    series: [{
      name: meta.trendTitle,
      type: kind === 'runs' || kind === 'incomplete' ? 'bar' : 'line',
      data: chartValues,
      smooth: true,
      symbolSize: 5,
      barMaxWidth: 22,
      lineStyle: { color: kind === 'duration' ? '#8B5CF6' : '#10B981', width: 2.5 },
      itemStyle: { color: kind === 'incomplete' ? '#FB7185' : kind === 'duration' ? '#8B5CF6' : kind === 'runs' ? '#60A5FA' : '#10B981', borderRadius: [4, 4, 0, 0] },
    }],
  }

  const workflowRows = workflows.slice().sort((a, b) => kind === 'duration'
    ? (b.machineDurationP50 ?? -1) - (a.machineDurationP50 ?? -1)
    : (a.completionSuccessRate ?? 1) - (b.completionSuccessRate ?? 1)).slice(0, 8)
  const runTotal = runsQ.data?.total ?? 0
  const totalPages = Math.max(Math.ceil(runTotal / pageSize), 1)

  return (
    <div className="fixed inset-0 z-50 bg-slate-950/25 backdrop-blur-[1px]" onClick={onClose}>
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={meta.title}
        className="ml-auto flex h-full w-full max-w-3xl flex-col bg-slate-50 shadow-[-20px_0_60px_rgba(15,23,42,0.16)]"
        onClick={(event) => event.stopPropagation()}
      >
        <header className="flex items-start justify-between border-b border-slate-200 bg-white px-6 py-5">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-[0.18em] text-slate-400">任务护航 · 指标审计</p>
            <h2 className="mt-1 text-lg font-semibold text-slate-950">{meta.title}</h2>
            <p className="mt-1 text-xs text-slate-500">{rangeLabel} · 管理员全量视角 · started_at 落窗</p>
          </div>
          <button onClick={onClose} className="rounded-lg border border-slate-200 px-2.5 py-1.5 text-xs text-slate-500 hover:bg-slate-50" aria-label="关闭">关闭</button>
        </header>

        <div className="flex-1 space-y-5 overflow-y-auto px-6 py-5">
          <section className={`grid gap-3 ${summary.length === 4 ? 'sm:grid-cols-4' : 'sm:grid-cols-3'}`}>
            {summary.map((item) => <SummaryItem key={item.label} {...item} />)}
          </section>

          <section className="rounded-xl border border-blue-100 bg-blue-50/70 px-4 py-3">
            <p className="text-[11px] font-semibold text-blue-900">计算口径</p>
            <p className="mt-1 text-xs leading-5 text-blue-800/80">{meta.formula}</p>
          </section>

          {showHistoricalTrend && <section className="rounded-xl border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-center justify-between">
              <h3 className="text-sm font-semibold text-slate-900">状态分布</h3>
              <span className="text-[11px] text-slate-400">合计 {overview.totalRuns.toLocaleString()}</span>
            </div>
            <div className="grid gap-x-6 gap-y-2.5 sm:grid-cols-2">
              {statusRows.map((row) => (
                <div key={row.status}>
                  <div className="mb-1 flex items-center justify-between text-xs">
                    <span className="flex items-center gap-2 text-slate-600"><i className="h-2 w-2 rounded-full" style={{ backgroundColor: row.color }} />{row.label}</span>
                    <span className="font-medium tabular-nums text-slate-900">{row.count.toLocaleString()}</span>
                  </div>
                  <div className="h-1.5 overflow-hidden rounded-full bg-slate-100"><div className="h-full rounded-full" style={{ width: `${(row.count / maxStatus) * 100}%`, backgroundColor: row.color }} /></div>
                </div>
              ))}
            </div>
          </section>}

          {showHistoricalTrend && <section className="rounded-xl border border-slate-200 bg-white p-4">
            <h3 className="text-sm font-semibold text-slate-900">{meta.trendTitle}</h3>
            <p className="mt-1 text-[11px] text-slate-400">与当前页面周期和主卡使用同一批运行样本</p>
            {trend.length === 0 ? <p className="py-12 text-center text-xs text-slate-400">当前周期暂无趋势数据</p> : <ReactECharts option={chartOption} style={{ height: 240 }} opts={{ renderer: 'svg' }} />}
          </section>}

          {showRuns ? (
            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="flex items-center justify-between border-b border-slate-100 px-4 py-3">
                <div><h3 className="text-sm font-semibold text-slate-900">运行明细</h3><p className="mt-0.5 text-[11px] text-slate-400">共 {runTotal.toLocaleString()} 条，可逐条对账</p></div>
                <span className="text-[11px] text-slate-400">第 {page + 1}/{totalPages} 页</span>
              </div>
              {runsQ.isLoading ? <div className="h-40 animate-pulse bg-slate-50" /> : runsQ.data?.runs.length ? (
                <div className="divide-y divide-slate-100">
                  {runsQ.data.runs.map((run: FlowRun) => (
                    <button key={run.flow_id} onClick={() => navigate(`/runs/${run.flow_id}`)} className="grid w-full grid-cols-[minmax(0,1fr)_90px_110px] items-center gap-3 px-4 py-3 text-left hover:bg-slate-50">
                      <span className="min-w-0"><span className="block truncate text-xs font-medium text-slate-800">{run.workflow_title || run.workflow_id}</span><span className="mt-0.5 block truncate font-mono text-[10px] text-slate-400">{run.flow_id}</span></span>
                      <span className="text-xs text-slate-600">{STATUS_META[run.status]?.label ?? run.status}</span>
                      <span className="text-right text-[11px] text-slate-400">{formatStartedAt(run.started_at)}</span>
                    </button>
                  ))}
                </div>
              ) : <p className="py-10 text-center text-xs text-slate-400">没有匹配的运行</p>}
              <div className="flex justify-end gap-2 border-t border-slate-100 px-4 py-3">
                <button disabled={page === 0} onClick={() => setPage((value) => Math.max(value - 1, 0))} className="rounded-md border border-slate-200 px-3 py-1 text-xs text-slate-600 disabled:opacity-40">上一页</button>
                <button disabled={page + 1 >= totalPages} onClick={() => setPage((value) => value + 1)} className="rounded-md border border-slate-200 px-3 py-1 text-xs text-slate-600 disabled:opacity-40">下一页</button>
              </div>
            </section>
          ) : (
            <section className="overflow-hidden rounded-xl border border-slate-200 bg-white">
              <div className="border-b border-slate-100 px-4 py-3"><h3 className="text-sm font-semibold text-slate-900">{kind === 'duration' ? '耗时最高的工作流' : '成功率较低的工作流'}</h3><p className="mt-0.5 text-[11px] text-slate-400">用于定位主要贡献者，不改变主指标口径</p></div>
              <div className="divide-y divide-slate-100">
                {workflowRows.map((row) => (
                  <button key={row.workflowId} onClick={() => navigate(`/workflow/${encodeURIComponent(row.workflowId)}/metrics`)} className="grid w-full grid-cols-[minmax(0,1fr)_90px_90px] items-center gap-3 px-4 py-3 text-left hover:bg-slate-50">
                    <span className="truncate text-xs font-medium text-slate-800">{row.workflowTitle}</span>
                    <span className="text-right text-xs tabular-nums text-slate-600">{formatRate(row.completionSuccessRate)}</span>
                    <span className="text-right text-xs tabular-nums text-slate-600">{row.machineDurationP50 == null ? '—' : formatDuration(row.machineDurationP50)}</span>
                  </button>
                ))}
              </div>
            </section>
          )}
        </div>
      </aside>
    </div>
  )
}
