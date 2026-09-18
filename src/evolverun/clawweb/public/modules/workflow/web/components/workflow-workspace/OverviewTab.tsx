import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAnalyzeRun, useAnalysisProgress, useFlowRuns, useWorkflowHealth } from '../../api/hooks'
import { useDeleteFlowRun, useRerunFlowRun } from '@avernet/clawweb-shared/web/api/hooks'
import AnalyzeRunBotModal from '../AnalyzeRunBotModal'
import AutoHealPanel from '../AutoHealPanel'
import { SuccessTrendCard } from '../SuccessTrendCard'
import { RunCountTrendCard } from '../RunCountTrendCard'
import { NodeAnalysisPanel } from '../NodeAnalysisPanel'
import StatusBadge from '../StatusBadge'
import EmptyState from '../EmptyState'
import ErrorState from '../ErrorState'
import { formatTimeShort, formatDuration } from '../../utils/time'
import type { FlowRun, WorkflowTypeRow } from '@avernet/clawweb-shared/web/types'
import TimeRangeFilter, { toTimeRange } from '../TimeRangeFilter'

function TrendTabs({
  workflowId,
  currentSuccessRate,
  currentDetail,
  currentTotalRuns,
  days,
}: {
  workflowId: string
  currentSuccessRate: string
  currentDetail: string
  currentTotalRuns: string
  days: 1 | 'yesterday' | 7 | 30
}) {
  const [tab, setTab] = useState<'success' | 'count'>('success')
  return (
    <div className="rounded-xl border border-slate-200 bg-white">
      <div className="flex items-center gap-1 border-b border-slate-100 px-3 pt-2">
        <button
          type="button"
          onClick={() => setTab('success')}
          className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 ${tab === 'success' ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}
        >
          成功率趋势
        </button>
        <button
          type="button"
          onClick={() => setTab('count')}
          className={`px-3 py-1.5 text-xs font-medium transition-colors border-b-2 ${tab === 'count' ? 'border-blue-600 text-blue-600' : 'border-transparent text-slate-500 hover:text-slate-700'}`}
        >
          运行实例趋势
        </button>
      </div>
      <div className="px-4 py-3">
        {tab === 'success' ? (
          <SuccessTrendCard workflowId={workflowId} currentSuccessRate={currentSuccessRate} currentDetail={currentDetail} compact days={days} showRangeSelector={false} embedded />
        ) : (
          <RunCountTrendCard workflowId={workflowId} currentTotalRuns={currentTotalRuns} days={days} embedded />
        )}
      </div>
    </div>
  )
}

function MetricCell({
  label,
  value,
  detail,
  emphasis = 'default',
}: {
  label: string
  value: string
  detail: string
  emphasis?: 'default' | 'danger'
}) {
  return (
    <div className="min-w-0 px-5 py-4">
      <p className="text-xs text-slate-500">{label}</p>
      <p className={`mt-1 text-2xl font-semibold tabular-nums tracking-tight ${emphasis === 'danger' ? 'text-rose-600' : 'text-slate-950'}`}>{value}</p>
      <p className="mt-0.5 truncate text-[11px] text-slate-400">{detail}</p>
    </div>
  )
}

function RunRow({ run, onAnalyze, onAutoHeal, dispatching, busy, onAnalysisFinished }: { run: FlowRun; onAnalyze: (run: FlowRun) => void; onAutoHeal: (run: FlowRun) => void; dispatching: boolean; busy: boolean; onAnalysisFinished: () => unknown }) {
  const navigate = useNavigate()
  const status = run.evolution_analysis_status ?? null
  const progressQuery = useAnalysisProgress(run.flow_id, status === 'analyzing')
  const progress = progressQuery.data?.progress ?? null
  const deleteMutation = useDeleteFlowRun()
  const rerunMutation = useRerunFlowRun()
  const [confirming, setConfirming] = useState(false)
  useEffect(() => {
    if (status === 'analyzing' && ['completed', 'failed', 'insufficient_evidence'].includes(progressQuery.data?.status ?? '')) {
      void onAnalysisFinished()
    }
  }, [status, progressQuery.data?.status, onAnalysisFinished])
  const { node_count, succeeded_count, failed_count } = run
  const other = Math.max(0, node_count - succeeded_count - failed_count)
  const parts: string[] = []
  if (succeeded_count > 0) parts.push(`${succeeded_count} 成功`)
  if (failed_count > 0) parts.push(`${failed_count} 失败`)
  if (other > 0) parts.push(`${other} 其他`)

  const handleDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirming) {
      setConfirming(true)
      return
    }
    deleteMutation.mutate(run.flow_id)
    setConfirming(false)
  }, [confirming, deleteMutation, run.flow_id])

  const handleCancelDelete = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirming(false)
  }, [])

  const handleRerun = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    rerunMutation.mutate(run.flow_id)
  }, [rerunMutation, run.flow_id])

  const handleAutoHeal = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    onAutoHeal(run)
  }, [onAutoHeal, run])

  const handleAnalyze = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    onAnalyze(run)
  }, [onAnalyze, run])

  const isAutoHealable = ['failed', 'blocked', 'waiting'].includes(run.status)
  const canRerun = !!run.origin_bot_id

  return (
    <tr
      onClick={() => navigate(`/runs/${run.flow_id}?from=workspace`)}
      className="cursor-pointer transition-colors hover:bg-blue-50"
    >
      <td className="whitespace-nowrap px-4 py-2">
        <div className="font-mono text-xs text-gray-900">{run.flow_id}</div>
      </td>
      <td className="whitespace-nowrap px-4 py-2">
        <StatusBadge status={run.status} />
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-600">
        {node_count === 0 ? '—' : parts.join(' / ')}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">
        <span className="block font-mono text-slate-600">{run.user_id || run.triggered_by || '—'}</span>
        {run.origin_bot_id && <span className="mt-0.5 block max-w-52 truncate font-mono text-[10px] text-slate-400" title={run.origin_bot_id}>{run.plugin_version ? `${run.origin_bot_id}/${run.plugin_version}` : run.origin_bot_id}</span>}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">
        {run.workflow_version != null ? (
          <span className="font-mono text-xs">{run.workflow_version === -1 ? '未绑定发布版本' : run.workflow_version}</span>
        ) : <span className="text-gray-300">—</span>}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">
        {run.engine ? <span className="font-mono">{run.engine}</span> : <span className="text-gray-300">—</span>}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">
        {formatTimeShort(run.started_at)}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">
        {formatDuration(run.total_duration_ms)}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs">
        <div className="flex items-center gap-1.5">
          {/* 分析状态 */}
          {status === 'analyzing' ? (
            <span className="inline-flex items-center gap-1 text-blue-600" title="分析中">
              <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border border-current border-t-transparent" />
            </span>
          ) : status === 'completed' ? (
            <span className="text-emerald-600" title="已分析">✓</span>
          ) : status === 'failed' ? (
            <span className="text-red-500" title="分析失败">✗</span>
          ) : (
            <span className="text-gray-300">—</span>
          )}
          <button
            type="button"
            disabled={status === 'analyzing' || busy}
            onClick={handleAnalyze}
            className="rounded border border-blue-200 px-1.5 py-1 text-xs text-blue-600 hover:bg-blue-50 disabled:cursor-not-allowed disabled:opacity-50"
            title={status === 'completed' || status === 'failed' ? '重新分析' : '分析'}
          >
            {dispatching ? '⏳' : '🔍'}
          </button>
          {/* 重跑 */}
          {canRerun && (
            <button
              type="button"
              onClick={handleRerun}
              disabled={rerunMutation.isPending}
              className="rounded border border-green-200 px-1.5 py-1 text-xs text-green-600 hover:border-green-400 hover:bg-green-50 disabled:cursor-not-allowed disabled:opacity-50"
              title="重跑：重新发送原始命令到 Bot"
            >
              {rerunMutation.isPending ? '⏳' : '🔄'}
            </button>
          )}
          {/* 自动修复（中止） */}
          {isAutoHealable && (
            <button
              type="button"
              onClick={handleAutoHeal}
              className="rounded border border-blue-200 px-1.5 py-1 text-xs text-blue-600 hover:border-blue-400 hover:bg-blue-50"
              title="AI 自动诊断与修复"
            >
              🩹
            </button>
          )}
          {/* 删除 */}
          {confirming ? (
            <span className="inline-flex items-center gap-1">
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className="rounded bg-red-600 px-2 py-1 text-xs font-medium text-white hover:bg-red-700 disabled:opacity-50"
              >
                {deleteMutation.isPending ? '删除中…' : '确认'}
              </button>
              <button
                type="button"
                onClick={handleCancelDelete}
                className="rounded border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50"
              >
                取消
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={handleDelete}
              className="rounded border border-gray-200 px-1.5 py-1 text-xs text-gray-500 hover:border-red-300 hover:bg-red-50 hover:text-red-600"
              title="删除此运行实例"
            >
              🗑
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}

function formatAnalysisElapsed(elapsedMs: number): string {
  const seconds = Math.max(0, Math.floor(elapsedMs / 1000))
  if (seconds < 60) return `${seconds}秒`
  const minutes = Math.floor(seconds / 60)
  const remainder = seconds % 60
  return remainder > 0 ? `${minutes}分${remainder}秒` : `${minutes}分`
}

interface OverviewTabProps {
  workflow: WorkflowTypeRow
}

export default function OverviewTab({ workflow }: OverviewTabProps) {
  return <OverviewContent key={workflow.workflow_id} workflow={workflow} />
}

function readListState(workflowId: string) {
  try {
    const saved = JSON.parse(sessionStorage.getItem(`workflow-run-list:${workflowId}`) ?? 'null')
    if (saved && typeof saved.query === 'string' && typeof saved.searchInput === 'string'
      && ['', 'running', 'succeeded', 'failed', 'waiting', 'blocked', 'queued', 'cancelled', 'aborted'].includes(saved.statusFilter)
      && Number.isSafeInteger(saved.page) && saved.page >= 0) return {
        ...saved as { query: string; searchInput: string; statusFilter: string; page: number },
        timeRange: ['', '1d', '3d', '7d'].includes(saved.timeRange) ? String(saved.timeRange) : '7d',
      }
  } catch { /* Storage may be unavailable or contain an older format. */ }
  return { query: '', searchInput: '', statusFilter: '', page: 0, timeRange: '7d' }
}

function OverviewContent({ workflow }: OverviewTabProps) {
  const workflowId = workflow.workflow_id
  const [saved] = useState(() => readListState(workflowId))
  const [days, setDays] = useState<1 | 'yesterday' | 7 | 30>(7)
  const [page, setPage] = useState(saved.page)
  const [statusFilter, setStatusFilter] = useState(saved.statusFilter)
  const [searchInput, setSearchInput] = useState(saved.searchInput)
  const [query, setQuery] = useState(saved.query)
  const [timeRange, setTimeRange] = useState(saved.timeRange)
  const timeParams = useMemo(() => toTimeRange(timeRange), [timeRange])
  const [analyzeRun, setAnalyzeRun] = useState<FlowRun | null>(null)
  const [autoHealRun, setAutoHealRun] = useState<FlowRun | null>(null)
  const analyzeMutation = useAnalyzeRun()
  const dispatchError = analyzeMutation.isError
    ? analyzeMutation.error instanceof Error ? analyzeMutation.error.message : String(analyzeMutation.error)
    : null
  useEffect(() => {
    try {
      sessionStorage.setItem(`workflow-run-list:${workflowId}`, JSON.stringify({ page, statusFilter, searchInput, query, timeRange }))
    } catch { /* Filtering still works when browser storage is disabled. */ }
  }, [workflowId, page, statusFilter, searchInput, query, timeRange])
  const hasFilters = Boolean(statusFilter || query || timeRange !== '7d')
  const [nowSec] = useState(() => Math.floor(Date.now() / 1000))
  const startOfToday = useMemo(() => {
    const d = new Date(nowSec * 1000)
    d.setHours(0, 0, 0, 0)
    return Math.floor(d.getTime() / 1000)
  }, [nowSec])
  const windowEnd = days === 'yesterday' ? startOfToday - 1 : nowSec
  const [activeSubTab, setActiveSubTab] = useState<'runs' | 'nodes'>('runs')
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null)
  const {
    data: health,
    isError: isHealthError,
  } = useWorkflowHealth(workflowId, days === 'yesterday' ? 1 : days)
  const pageSize = 20
  const windowStart = days === 1
    ? startOfToday
    : days === 'yesterday'
      ? startOfToday - 86400
      : windowEnd - days * 86400
  const {
    data: metricsData,
    isPending: isMetricsPending,
    isError: isMetricsError,
    isFetching: isMetricsFetching,
    refetch: refetchMetrics,
  } = useFlowRuns({
    workflowId,
    limit: 1,
    from: String(windowStart),
    to: String(windowEnd),
  })
  const {
    data,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useFlowRuns({
    workflowId,
    limit: pageSize,
    offset: page * pageSize,
    status: statusFilter && statusFilter !== 'cancelled' ? statusFilter : undefined,
    statuses: statusFilter === 'cancelled' ? ['cancelled', 'canceled'] : undefined,
    query: query || undefined,
    ...timeParams,
  })

  const runs = useMemo(() => data?.runs ?? [], [data?.runs])
  const totalCount = data?.total ?? 0

  const stats = useMemo(() => {
    const counts = metricsData?.statusCounts ?? {}
    const succeededRuns = counts.succeeded ?? 0
    const abnormalRuns = (counts.failed ?? 0) + (counts.aborted ?? 0) + (counts.cancelled ?? 0) + (counts.canceled ?? 0)
    const terminalRuns = succeededRuns + abnormalRuns
    const runningRuns = counts.running ?? 0
    const waitingRuns = counts.waiting ?? 0
    const blockedRuns = counts.blocked ?? 0
    const queuedRuns = counts.queued ?? 0
    const totalRuns = terminalRuns + runningRuns + waitingRuns + blockedRuns + queuedRuns
    return {
      succeededRuns,
      abnormalRuns,
      terminalRuns,
      successRate: terminalRuns > 0 ? Math.round((succeededRuns / terminalRuns) * 100) : 0,
      runningRuns,
      waitingRuns,
      blockedRuns,
      queuedRuns,
      totalRuns,
    }
  }, [metricsData?.statusCounts])

  const hasMetrics = !isMetricsPending && !isMetricsError && metricsData?.statusCounts != null
  const rangeLabel = days === 'yesterday' ? '昨天' : `近 ${days} 天`
  const currentSuccessRate = hasMetrics && stats.terminalRuns > 0 ? `${stats.successRate}%` : '—'
  const currentDetail = hasMetrics
    ? `${stats.succeededRuns} / ${stats.terminalRuns} 个终态运行`
    : isMetricsError ? '运行指标加载失败' : '运行指标加载中'
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize))
  useEffect(() => {
    // Only correct saved offsets after the history request has settled successfully.
    if (!data || isLoading || isError || isFetching) return
    setPage((value) => Math.min(value, totalPages - 1))
  }, [data, isLoading, isError, isFetching, totalPages])
  const currentPage = Math.min(page + 1, totalPages)
  const isRefreshing = isFetching || isMetricsFetching

  const changeDays = (nextDays: 1 | 'yesterday' | 7 | 30) => {
    setDays(nextDays)
  }

  return (
    <div className="space-y-4">
      {dispatchError && !analyzeRun && <div role="alert" className="rounded border border-red-200 bg-red-50 p-3 text-xs text-red-600">
        分析任务派发失败：{dispatchError}
      </div>}
      {analyzeRun && <AnalyzeRunBotModal
        flowId={analyzeRun.flow_id}
        workflowId={analyzeRun.workflow_id}
        originBotId={analyzeRun.origin_bot_id?.split(':')[0].trim() || null}
        analyzeMutation={analyzeMutation}
        dispatchError={dispatchError}
        isOpen
        onClose={() => setAnalyzeRun(null)}
      />}
      {autoHealRun && (
        <AutoHealPanel
          run={autoHealRun}
          onClose={() => setAutoHealRun(null)}
          onRerunComplete={() => void refetch()}
        />
      )}
      <div className="flex items-center justify-end gap-1" aria-label="概览时间范围">
        {([1, 'yesterday', 7, 30] as const).map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => changeDays(value)}
            className={`rounded-md px-3 py-1 text-xs font-medium transition ${days === value ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50'}`}
          >{value === 1 ? '今天' : value === 'yesterday' ? '昨天' : `${value}天`}</button>
        ))}
      </div>
      <section aria-label="工作流关键指标" className="grid grid-cols-2 divide-x divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white lg:grid-cols-5 lg:divide-y-0">
        <MetricCell label="健康度" value={health ? String(health.overallScore) : '—'} detail={health ? (health.overallScore >= 80 ? '运行稳定' : health.overallScore >= 60 ? '需要关注' : '建议优先处理') : '等待健康数据'} emphasis={health && health.overallScore < 60 ? 'danger' : 'default'} />
        <MetricCell label="运行成功率" value={currentSuccessRate} detail={`${rangeLabel} · 成功 / 终态`} />
        <MetricCell label="异常结束" value={hasMetrics ? String(stats.abnormalRuns) : '—'} detail="失败、终止或取消" emphasis={hasMetrics && stats.abnormalRuns > 0 ? 'danger' : 'default'} />
        <MetricCell label="节点耗时 P95" value={health ? formatDuration(health.p95DurationMs) : '—'} detail="最慢节点 P95 口径" />
        <MetricCell label="总运行实例" value={hasMetrics ? String(stats.totalRuns) : '—'} detail={`${rangeLabel} · 全部状态`} />
      </section>

      <section aria-label="当前运行状态" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ['运行中', stats.runningRuns, 'bg-blue-50 text-blue-700'],
          ['等待中', stats.waitingRuns, 'bg-amber-50 text-amber-700'],
          ['阻塞', stats.blockedRuns, 'bg-sky-50 text-sky-700'],
          ['排队中', stats.queuedRuns, 'bg-violet-50 text-violet-700'],
        ].map(([label, count, cls]) => (
          <div key={String(label)} className={`rounded-lg px-3 py-2 text-xs font-medium ${cls}`}>
            {`${label} ${hasMetrics ? count : '—'}`}
          </div>
        ))}
      </section>

      {isMetricsError ? (
        <div role="alert" className="flex items-center justify-between rounded-lg border border-rose-100 bg-rose-50 px-4 py-2 text-xs text-rose-600">
          <span>运行指标加载失败；运行列表仍可继续查看。</span>
          <button type="button" onClick={() => void refetchMetrics()} disabled={isMetricsFetching} className="shrink-0 rounded px-2 py-1 font-medium hover:bg-rose-100 disabled:opacity-50">重试指标</button>
        </div>
      ) : isMetricsPending && <p role="status" className="px-4 text-xs text-slate-500">运行指标加载中…</p>}

      {isHealthError && <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-2 text-xs text-rose-600">健康指标加载失败；运行列表仍可继续查看。</div>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
        {workflowId && (
          <TrendTabs
            workflowId={workflowId}
            currentSuccessRate={currentSuccessRate}
            currentDetail={currentDetail}
            currentTotalRuns={hasMetrics ? String(stats.totalRuns) : '—'}
            days={days}
          />
        )}
        <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="运行风险摘要">
          <div className="flex items-center justify-between">
            <div><h3 className="text-sm font-semibold text-slate-900">运行风险</h3><p className="mt-0.5 text-[11px] text-slate-400">优先关注影响成功率与耗时的节点</p></div>
            <span className={`rounded-full px-2 py-1 text-[10px] font-medium ${health && health.overallScore < 60 ? 'bg-rose-50 text-rose-600' : 'bg-slate-100 text-slate-500'}`}>{health ? `${health.overallScore} 分` : '待计算'}</span>
          </div>
          <div className="mt-4 space-y-2">
            {[{ label: '耗时瓶颈', node: health?.bottleneckNode }, { label: '脆弱节点', node: health?.fragileNode }].map((item) => <button key={item.label} type="button" disabled={!item.node} onClick={() => { if (item.node) { setActiveSubTab('nodes'); setHighlightNodeId(item.node) } }} className="flex w-full items-center justify-between rounded-lg bg-slate-50 px-3 py-2 text-left disabled:cursor-default">
              <span className="text-xs text-slate-500">{item.label}</span><span className="font-mono text-xs font-medium text-slate-800">{item.node || '—'}</span>
            </button>)}
          </div>
          <p className="mt-3 line-clamp-2 text-xs leading-5 text-slate-500">{health?.recommendation || '暂无明确风险建议'}</p>
        </section>
      </div>

      <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
          <div className="flex items-center gap-1">
            <button
              onClick={() => setActiveSubTab('runs')}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 ${activeSubTab === 'runs' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
            >
              运行列表
            </button>
            <button
              onClick={() => setActiveSubTab('nodes')}
              className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 ${activeSubTab === 'nodes' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
            >
              节点分析
            </button>
          </div>
          <button
            onClick={() => { void refetch(); void refetchMetrics() }}
            disabled={isRefreshing}
            aria-busy={isRefreshing}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg
              className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
            刷新
          </button>
        </div>

        {activeSubTab === 'runs' && (
          <form
            aria-label="运行记录筛选"
            onSubmit={(event) => { event.preventDefault(); setQuery(searchInput.trim()); setPage(0) }}
            className="flex flex-wrap items-center gap-2 border-b border-slate-100 px-4 py-3"
          >
            <label><span className="sr-only">运行时间范围</span><TimeRangeFilter value={timeRange} onChange={(value) => { setTimeRange(value); setPage(0) }} /></label>
            <select aria-label="运行状态" value={statusFilter} onChange={(event) => { setStatusFilter(event.target.value); setPage(0) }} className="rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700">
              <option value="">全部状态</option>
              <option value="running">运行中</option>
              <option value="succeeded">成功</option>
              <option value="failed">失败</option>
              <option value="waiting">等待中</option>
              <option value="blocked">阻塞</option>
              <option value="queued">排队中</option>
              <option value="cancelled">取消</option>
              <option value="aborted">终止</option>
            </select>
            <input type="search" aria-label="搜索运行记录" placeholder="搜索输入内容 / Run ID / 发起方 / Bot ID" value={searchInput} onChange={(event) => setSearchInput(event.target.value)} className="min-w-0 flex-1 basis-64 rounded-md border border-slate-200 px-3 py-2 text-xs text-slate-700 sm:max-w-sm" />
            <button type="submit" className="rounded-md bg-blue-600 px-3 py-2 text-xs font-medium text-white hover:bg-blue-700">搜索</button>
            <button type="button" disabled={!hasFilters && !searchInput} onClick={() => { setStatusFilter(''); setSearchInput(''); setQuery(''); setTimeRange('7d'); setPage(0) }} className="rounded-md px-3 py-2 text-xs text-slate-500 hover:bg-slate-50 disabled:opacity-40">重置筛选</button>
            {!isLoading && !isError && <span className="ml-auto text-xs text-slate-400" aria-live="polite">{hasFilters ? '匹配' : '共'} {totalCount} 条</span>}
          </form>
        )}

        {activeSubTab === 'nodes' && workflowId ? (
          <div className="p-4">
            <NodeAnalysisPanel workflowId={workflowId} highlightNodeId={highlightNodeId} />
          </div>
        ) : isError ? (
          <div className="p-4">
            <ErrorState
              message={error instanceof Error ? error.message : '加载运行记录失败'}
              onRetry={() => void refetch()}
            />
          </div>
        ) : isLoading ? (
          <div className="flex items-center justify-center py-12">
            <div className="h-6 w-6 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
          </div>
        ) : runs.length === 0 ? (
          <div className="p-6">
            <EmptyState title={hasFilters ? '没有匹配的运行' : '暂无运行'} description={hasFilters ? '请调整筛选条件或重置筛选' : '该工作流尚未执行过'} />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Run ID</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">状态</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">节点</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">发起方</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">版本</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">环境</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">开始时间</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">耗时</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">操作</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {runs.map((run) => (
                  <RunRow key={run.flow_id} run={run}
                    busy={analyzeMutation.isPending}
                    onAnalysisFinished={refetch}
                    dispatching={analyzeMutation.isPending && analyzeMutation.variables?.flowId === run.flow_id}
                    onAnalyze={(selected) => { if (!analyzeMutation.isPending) { analyzeMutation.reset(); setAnalyzeRun(selected) } }}
                    onAutoHeal={(selected) => setAutoHealRun(selected)} />
                ))}
              </tbody>
            </table>
          </div>
        )}
        {activeSubTab === 'runs' && !isLoading && !isError && totalCount > pageSize && (
          <div className="flex items-center justify-end gap-2 border-t border-slate-100 px-4 py-3 text-xs text-slate-500">
            <span>第 {currentPage} / {totalPages} 页 · 共 {totalCount} 条</span>
            <button type="button" onClick={() => setPage((value) => Math.max(0, value - 1))} disabled={page === 0} className="rounded border border-slate-200 px-2.5 py-1 transition hover:bg-slate-50 disabled:opacity-40">上一页</button>
            <button type="button" onClick={() => setPage((value) => Math.min(totalPages - 1, value + 1))} disabled={page >= totalPages - 1} className="rounded border border-slate-200 px-2.5 py-1 transition hover:bg-slate-50 disabled:opacity-40">下一页</button>
          </div>
        )}
      </div>
    </div>
  )
}
