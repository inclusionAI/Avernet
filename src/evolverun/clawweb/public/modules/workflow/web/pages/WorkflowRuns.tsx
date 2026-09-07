import { useMemo, useState, useCallback } from 'react'
import { useParams, useNavigate, useSearchParams, Link } from 'react-router-dom'
import { useFlowRuns, useDeleteFlowRun, useRerunFlowRun, useAnalyzeRun } from '@avernet/workflow/web/api/hooks'
import { useDebounce } from '../hooks/useDebounce'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import StatusBadge from '@avernet/workflow/web/components/StatusBadge'
import StatusFilter from '../components/StatusFilter'
import SearchInput from '@avernet/workflow/web/components/SearchInput'
import TimeRangeFilter, { toTimeRange } from '../components/TimeRangeFilter'
import EmptyState from '@avernet/workflow/web/components/EmptyState'
import ErrorState from '@avernet/workflow/web/components/ErrorState'
import AutoHealPanel from '../components/AutoHealPanel'
import AnalyzeRunBotModal from '../components/AnalyzeRunBotModal'
import { HealthScoreCard } from '../components/HealthScoreCard'
import { SuccessTrendCard } from '@avernet/workflow/web/components/SuccessTrendCard'
import { useWorkflowHealth } from '@avernet/workflow/web/api/hooks'
import { NodeAnalysisPanel } from '@avernet/workflow/web/components/NodeAnalysisPanel'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'

import { formatTimeShort, formatDuration } from '@avernet/workflow/web/utils/time'

const PAGE_SIZE = 100

type ViewTab = 'runs' | 'nodes'

const ANALYSIS_STATUS_LABEL: Record<string, { label: string; cls: string }> = {
  analyzing: { label: '分析中', cls: 'bg-amber-50 text-amber-700' },
  completed: { label: '已分析', cls: 'bg-emerald-50 text-emerald-700' },
  failed: { label: '分析失败', cls: 'bg-red-50 text-red-700' },
  none: { label: '未分析', cls: 'bg-gray-100 text-gray-500' },
}

function AnalysisStatusBadge({ status }: { status?: string | null }) {
  const cfg = ANALYSIS_STATUS_LABEL[status ?? 'none'] ?? ANALYSIS_STATUS_LABEL.none
  return <span className={`rounded-full px-2 py-0.5 text-[10px] font-medium ${cfg.cls}`}>{cfg.label}</span>
}

function NodeProgress({ run }: { run: FlowRun }) {
  const { node_count, succeeded_count, failed_count } = run
  if (node_count === 0) return <span className="text-gray-400">—</span>

  const parts: string[] = []
  if (succeeded_count > 0) parts.push(`${succeeded_count} 成功`)
  if (failed_count > 0) parts.push(`${failed_count} 失败`)
  const other = node_count - succeeded_count - failed_count
  if (other > 0) parts.push(`${other} 其他`)

  return <span className="text-gray-600 text-xs">{parts.join(' / ')}</span>
}


interface WorkflowStats {
  totalRuns: number
  succeededRuns: number
  failedRuns: number
  runningRuns: number
  otherRuns: number
  totalNodes: number
  succeededNodes: number
  failedNodes: number
  avgDurationMs: number | null
  totalTokens: number | null
}

/**
 * Parse server-side statusCounts and merge with page-level node/duration stats.
 *
 * Previously `computeStats` iterated only the current page of runs for status counts,
 * then replaced `totalRuns` with the server total — causing a numerator/denominator
 * mismatch when runs spanned multiple pages.  Now status counts come from the server
 * (`statusCounts`), so the success rate is always accurate regardless of pagination.
 */
function computeStats(runs: FlowRun[], statusCounts?: Record<string, number>): WorkflowStats {
  // Server-side counts (accurate across all pages)
  const succeededRuns = statusCounts?.['succeeded'] ?? 0
  const failedRuns = statusCounts?.['failed'] ?? 0
  const runningRuns = statusCounts?.['running'] ?? 0
  const totalRuns = statusCounts ? Object.values(statusCounts).reduce((a, b) => a + b, 0) : runs.length

  // When no server counts, fall back to page-level computation
  if (!statusCounts) {
    let sSucceeded = 0, sFailed = 0, sRunning = 0
    for (const r of runs) {
      if (r.status === 'succeeded') sSucceeded++
      else if (r.status === 'failed') sFailed++
      else if (r.status === 'running') sRunning++
    }
    return computeStatsFallback(runs, sSucceeded, sFailed, sRunning)
  }

  // Node-level & duration stats still from current page (reasonable approximation)
  let totalNodes = 0, succeededNodes = 0, failedNodes = 0
  let durationSum = 0, durationCount = 0, tokenSum = 0, hasTokens = false
  for (const r of runs) {
    totalNodes += r.node_count
    succeededNodes += r.succeeded_count
    failedNodes += r.failed_count
    if (r.total_duration_ms != null && r.total_duration_ms > 0) {
      durationSum += r.total_duration_ms
      durationCount++
    }
    if (r.total_token_usage != null) {
      tokenSum += r.total_token_usage
      hasTokens = true
    }
  }

  return {
    totalRuns,
    succeededRuns,
    failedRuns,
    runningRuns,
    otherRuns: totalRuns - succeededRuns - failedRuns - runningRuns,
    totalNodes,
    succeededNodes,
    failedNodes,
    avgDurationMs: durationCount > 0 ? Math.round(durationSum / durationCount) : null,
    totalTokens: hasTokens ? tokenSum : null,
  }
}

/** Fallback when server doesn't provide statusCounts (e.g. status filter is active) */
function computeStatsFallback(runs: FlowRun[], succeededRuns: number, failedRuns: number, runningRuns: number): WorkflowStats {
  let totalNodes = 0, succeededNodes = 0, failedNodes = 0
  let durationSum = 0, durationCount = 0, tokenSum = 0, hasTokens = false
  for (const r of runs) {
    totalNodes += r.node_count
    succeededNodes += r.succeeded_count
    failedNodes += r.failed_count
    if (r.total_duration_ms != null && r.total_duration_ms > 0) {
      durationSum += r.total_duration_ms
      durationCount++
    }
    if (r.total_token_usage != null) {
      tokenSum += r.total_token_usage
      hasTokens = true
    }
  }
  return {
    totalRuns: runs.length,
    succeededRuns,
    failedRuns,
    runningRuns,
    otherRuns: runs.length - succeededRuns - failedRuns - runningRuns,
    totalNodes,
    succeededNodes,
    failedNodes,
    avgDurationMs: durationCount > 0 ? Math.round(durationSum / durationCount) : null,
    totalTokens: hasTokens ? tokenSum : null,
  }
}

export default function WorkflowRuns() {
  const { workflowId } = useParams<{ workflowId: string }>()
  const [searchParams, setSearchParams] = useSearchParams()
  const [statusFilter, setStatusFilter] = useState('')
  const [search, setSearch] = useState('')
  const [timeRange, setTimeRange] = useState('7d')
  const [inputQuery, setInputQuery] = useState(searchParams.get('inputQuery') ?? '')
  const [page, setPage] = useState(0)
  const [autoHealRun, setAutoHealRun] = useState<FlowRun | null>(null)
  const [analyzeRun, setAnalyzeRun] = useState<FlowRun | null>(null)
  const [activeTab, setActiveTab] = useState<ViewTab>('runs')
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null)
  const analyzeMutation = useAnalyzeRun()
  const debouncedSearch = useDebounce(search, 300)
  const debouncedInputQuery = useDebounce(inputQuery, 300)
  const user = getClientUser()

  // Health data for Token stats (from node_executions aggregation, more reliable than flow_runs.total_token_usage)
  const { data: healthData } = useWorkflowHealth(workflowId ?? null)

  const timeParams = useMemo(() => toTimeRange(timeRange), [timeRange])

  // Reset page when filters change
  const filterKey = `${statusFilter}|${timeRange}|${debouncedInputQuery}`
  useMemo(() => { setPage(0) }, [filterKey])

  // Sync inputQuery to URL search params so it can be bookmarked/shared
  useMemo(() => {
    const next = new URLSearchParams(searchParams)
    if (debouncedInputQuery) {
      next.set('inputQuery', debouncedInputQuery)
    } else {
      next.delete('inputQuery')
    }
    setSearchParams(next, { replace: true })
  }, [debouncedInputQuery]) // eslint-disable-line react-hooks/exhaustive-deps

  const { data, isLoading, isError, error, refetch, isFetching } = useFlowRuns({
    workflowId: workflowId ?? undefined,
    status: statusFilter || undefined,
    ...timeParams,
    limit: PAGE_SIZE,
    offset: page * PAGE_SIZE,
    // Admin users bypass view permission — don't send botOwnerId so server returns all runs
    botOwnerId: user?.isAdmin ? undefined : user?.userId,
    // Input query: fuzzy-match against input_json in the database
    inputQuery: debouncedInputQuery || undefined,
  })

  const runs = data?.runs ?? []
  const totalCount = data?.total ?? 0
  const hasMore = (page + 1) * PAGE_SIZE < totalCount

  const filteredRuns = useMemo(() => {
    if (!debouncedSearch) return runs
    const q = debouncedSearch.toLowerCase()
    return runs.filter(
      (r) =>
        r.flow_id.toLowerCase().includes(q) ||
        (r.user_id ?? '').toLowerCase().includes(q) ||
        (r.triggered_by ?? '').toLowerCase().includes(q) ||
        (r.origin_bot_id ?? '').toLowerCase().includes(q),
    )
  }, [runs, debouncedSearch])

  const hasRunning = useMemo(
    () => runs.some((r) => r.status === 'running'),
    [runs],
  )

  const stats = useMemo(() => {
    const s = computeStats(runs, data?.statusCounts)
    // When no server-side statusCounts (e.g. status filter active), fall back to server total
    if (!data?.statusCounts && totalCount > s.totalRuns) s.totalRuns = totalCount
    return s
  }, [runs, totalCount, data?.statusCounts])

  const workflowTitle = runs.length > 0
    ? runs[0].workflow_title || workflowId
    : workflowId

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 sm:px-6 lg:px-8">
      <Link
        to="/workflows"
        className="mb-4 inline-flex items-center gap-1 text-sm text-gray-500 transition-colors hover:text-gray-700"
      >
        <svg className="h-4 w-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
        </svg>
        Back to workflows
      </Link>

      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">{workflowTitle}</h1>
          <p className="mt-1 text-gray-400 text-xs font-mono">{workflowId}</p>
          <p className="mt-1 text-sm text-gray-500">
            {totalCount} run{totalCount !== 1 ? 's' : ''}
            {hasRunning && (
              <span className="ml-2 inline-flex items-center">
                <span className="mr-1 h-2 w-2 animate-pulse rounded-full bg-status-running" />
                Live
              </span>
            )}
          </p>
        </div>
        <button
          onClick={() => void refetch()}
          disabled={isFetching}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <svg
            className={`h-4 w-4 ${isFetching ? 'animate-spin' : ''}`}
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
          {isFetching ? '刷新中…' : '刷新'}
        </button>
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <StatusFilter value={statusFilter} onChange={setStatusFilter} />
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="搜索 Run ID / 创建者 / Bot / Plugin…"
        />
        <SearchInput
          value={inputQuery}
          onChange={setInputQuery}
          placeholder="搜索输入内容 (input_json)…"
        />
        <TimeRangeFilter value={timeRange} onChange={setTimeRange} />
        {(statusFilter || search || timeRange !== '7d' || inputQuery) && (
          <button
            onClick={() => {
              setStatusFilter('')
              setSearch('')
              setTimeRange('7d')
              setInputQuery('')
            }}
            className="text-sm text-gray-400 transition-colors hover:text-gray-600"
          >
            清除筛选
          </button>
        )}
      </div>

      {runs.length > 0 && (
        <>
        {/* 成功率趋势 — 单独一行 */}
        {workflowId && (
          <div className="mb-4">
            <SuccessTrendCard
              workflowId={workflowId}
              currentSuccessRate={(() => {
                const terminal = stats.succeededRuns + stats.failedRuns
                return terminal > 0 ? `${Math.round((stats.succeededRuns / terminal) * 100)}%` : '—'
              })()}
              currentDetail={`${stats.succeededRuns}/${stats.succeededRuns + stats.failedRuns} runs`}
            />
          </div>
        )}

        {/* 其他统计卡片 — 一行 5 列 */}
        <div className="mb-6 grid grid-cols-2 gap-4 sm:grid-cols-3 lg:grid-cols-5">
          <StatCard
            label="Failed Runs"
            value={String(stats.failedRuns)}
            detail={stats.failedRuns > 0 ? '需要关注' : '一切正常'}
            color={stats.failedRuns > 0 ? 'red' : 'green'}
          />
          <StatCard
            label="Running"
            value={String(stats.runningRuns)}
            detail={stats.runningRuns > 0 ? '进行中' : '空闲'}
            color={stats.runningRuns > 0 ? 'blue' : 'gray'}
          />
          <StatCard
            label="Failed Nodes"
            value={String(stats.failedNodes)}
            detail={`${stats.succeededNodes} 成功 / ${stats.totalNodes} 总计`}
            color={stats.failedNodes > 0 ? 'red' : 'green'}
          />
          <StatCard
            label="Avg Duration"
            value={formatDuration(stats.avgDurationMs)}
            detail="已完成运行"
            color="gray"
          />
          <StatCard
            label="Token Usage"
            value={healthData?.totalTokens != null ? healthData.totalTokens.toLocaleString() : (stats.totalTokens != null ? stats.totalTokens.toLocaleString() : '—')}
            detail="总 Token (节点聚合)"
            color="gray"
          />
        </div>
        </>
      )}

      {/* 健康度卡片 */}
      {workflowId && <div className="mb-6"><HealthScoreCard workflowId={workflowId} onNodeClick={(nodeId) => { setActiveTab('nodes'); setHighlightNodeId(nodeId); }} /></div>}

      {/* Tab 切换 */}
      <div className="mb-4 flex items-center gap-1 border-b border-gray-200">
        <button
          onClick={() => setActiveTab('runs')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 ${activeTab === 'runs' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
        >
          运行列表
        </button>
        <button
          onClick={() => setActiveTab('nodes')}
          className={`px-4 py-2 text-sm font-medium transition-colors border-b-2 ${activeTab === 'nodes' ? 'border-blue-600 text-blue-600' : 'border-transparent text-gray-500 hover:text-gray-700'}`}
        >
          节点分析
        </button>
      </div>

      {/* 节点分析 Tab */}
      {activeTab === 'nodes' && workflowId && (
        <NodeAnalysisPanel workflowId={workflowId} highlightNodeId={highlightNodeId} />
      )}

      {/* 运行列表 Tab */}
      {activeTab === 'runs' && (isError ? (
        <ErrorState
          message={error instanceof Error ? error.message : 'Failed to load runs'}
          onRetry={() => void refetch()}
        />
      ) : isLoading ? (
        <div className="flex items-center justify-center py-16">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        </div>
      ) : filteredRuns.length === 0 ? (
        <EmptyState
          title="No runs found"
          description={runs.length === 0 ? '该工作流尚未执行过。' : '没有匹配筛选条件的运行记录。'}
        />
      ) : (
        <>
          <div className="overflow-x-auto rounded-lg border border-gray-200">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Run ID
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Status
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Nodes
                  </th>
                  <th className="whitespace-nowrap px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    进化分析
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    创建者
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    发起 Bot
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    环境
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    工作流版本
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Started
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    Duration
                  </th>
                  <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                    操作
                  </th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {filteredRuns.map((run) => (
                  <RunRow key={run.flow_id} run={run} onAutoHeal={setAutoHealRun} onAnalyze={setAnalyzeRun} analyzeMutation={analyzeMutation} />
                ))}
              </tbody>
            </table>
          </div>
          {/* Pagination */}
          {totalCount > PAGE_SIZE && (
            <div className="mt-4 flex items-center justify-between">
              <p className="text-sm text-gray-500">
                显示 {page * PAGE_SIZE + 1}–{Math.min((page + 1) * PAGE_SIZE, totalCount)} / 共 {totalCount} 条
              </p>
              <div className="flex items-center gap-2">
                <button
                  onClick={() => setPage((p) => Math.max(0, p - 1))}
                  disabled={page === 0}
                  className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  上一页
                </button>
                <span className="text-sm text-gray-500">
                  第 {page + 1} / {Math.max(1, Math.ceil(totalCount / PAGE_SIZE))} 页
                </span>
                <button
                  onClick={() => setPage((p) => p + 1)}
                  disabled={!hasMore}
                  className="rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
                >
                  下一页
                </button>
              </div>
            </div>
          )}
        </>
      )

      )}

      {/* Auto-Heal Panel */}
      {autoHealRun && (
        <AutoHealPanel
          run={autoHealRun}
          onClose={() => setAutoHealRun(null)}
          onRerunComplete={() => void refetch()}
        />
      )}

      {analyzeRun && (
        <AnalyzeRunBotModal
          workflowId={analyzeRun.workflow_id}
          flowId={analyzeRun.flow_id}
          originBotId={analyzeRun.origin_bot_id ? String(analyzeRun.origin_bot_id).split(":")[0].trim() : null}
          analyzeMutation={analyzeMutation}
          isOpen={!!analyzeRun}
          onClose={() => setAnalyzeRun(null)}
        />
      )}
    </div>
  )
}

function RunRow({ run, onAutoHeal, onAnalyze, analyzeMutation }: { run: FlowRun; onAutoHeal: (run: FlowRun) => void; onAnalyze: (run: FlowRun) => void; analyzeMutation: ReturnType<typeof useAnalyzeRun> }) {
  const navigate = useNavigate()
  const deleteMutation = useDeleteFlowRun()
  const rerunMutation = useRerunFlowRun()
  const isAnalyzingThisRun = analyzeMutation.isPending && analyzeMutation.variables?.flowId === run.flow_id
  const [confirming, setConfirming] = useState(false)

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
    <>
      <tr
        onClick={() => navigate(`/runs/${run.flow_id}`)}
      className="cursor-pointer transition-colors hover:bg-blue-50"
    >
      <td className="whitespace-nowrap px-4 py-3">
        <div className="font-mono text-sm text-gray-900">{run.flow_id}</div>
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        <StatusBadge status={run.status} />
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        <NodeProgress run={run} />
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        <div className="flex items-center gap-2">
          <AnalysisStatusBadge status={run.evolution_analysis_status} />
          <button
            onClick={handleAnalyze}
            disabled={run.evolution_analysis_status === 'analyzing' || isAnalyzingThisRun}
            className="inline-flex items-center gap-0.5 rounded-md border border-purple-200 bg-white px-2 py-1 text-xs text-purple-600 transition-colors hover:border-purple-400 hover:bg-purple-50 hover:text-purple-700 disabled:cursor-not-allowed disabled:opacity-50"
            title={run.evolution_analysis_status === 'completed' || run.evolution_analysis_status === 'failed' ? '重新分析' : '分析'}
          >
            {run.evolution_analysis_status === 'analyzing' ? '⏳ 分析中' : (run.evolution_analysis_status === 'completed' || run.evolution_analysis_status === 'failed' ? '🔍 重新分析' : '🔍 分析')}
          </button>
        </div>
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
        {run.user_id ? (
          <span className="font-mono text-xs">{run.user_id}</span>
        ) : run.triggered_by ? (
          <span className="font-mono text-xs">{run.triggered_by}</span>
        ) : (
          <span className="text-gray-300">&mdash;</span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
        {run.origin_bot_id && run.plugin_version ? (
          <span className="font-mono text-xs">{run.origin_bot_id}/{run.plugin_version}</span>
        ) : run.origin_bot_id ? (
          <span className="font-mono text-xs">{run.origin_bot_id}</span>
        ) : run.plugin_version ? (
          <span className="font-mono text-xs">{run.plugin_version}</span>
        ) : (
          <span className="text-gray-300">&mdash;</span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
        {run.engine ? (
          <span className="font-mono text-xs">{run.engine}</span>
        ) : (
          <span className="text-gray-300">&mdash;</span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
        {run.workflow_version != null ? (
          <span className="font-mono text-xs">{run.workflow_version}</span>
        ) : run.workflow_deploy_number != null ? (
          <span className="font-mono text-xs text-gray-400">#{run.workflow_deploy_number}</span>
        ) : (
          <span className="text-gray-300">&mdash;</span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
        {formatTimeShort(run.started_at)}
      </td>
      <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
        {formatDuration(run.total_duration_ms)}
      </td>
      <td className="whitespace-nowrap px-4 py-3">
        {confirming ? (
          <div className="flex items-center gap-1">
            <button
              onClick={handleDelete}
              disabled={deleteMutation.isPending}
              className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
            >
              {deleteMutation.isPending ? '删除中…' : '确认'}
            </button>
            <button
              onClick={handleCancelDelete}
              className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
            >
              取消
            </button>
          </div>
        ) : (
          <div className="flex items-center gap-1.5">
            {canRerun && (
              <button
                onClick={handleRerun}
                disabled={rerunMutation.isPending}
                className="inline-flex items-center gap-0.5 rounded-md border border-green-200 bg-white px-1.5 py-1 text-xs text-green-600 transition-colors hover:border-green-400 hover:bg-green-50 hover:text-green-700 disabled:cursor-not-allowed disabled:opacity-50"
                title="重跑：重新发送原始命令到 Bot"
              >
                {rerunMutation.isPending ? '⏳' : '🔄'}
              </button>
            )}
            {isAutoHealable && (
              <button
                onClick={handleAutoHeal}
                className="inline-flex items-center gap-0.5 rounded-md border border-blue-200 bg-white px-1.5 py-1 text-xs text-blue-600 transition-colors hover:border-blue-400 hover:bg-blue-50 hover:text-blue-700"
                title="AI 自动诊断与修复"
              >
                🩹
              </button>
            )}
            <button
              onClick={handleDelete}
              className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-500 transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-600"
              title="删除此运行实例"
            >
              删除
            </button>
          </div>
        )}
      </td>
      </tr>

    </>
  )
}

const CARD_COLORS = {
  green: {
    bg: 'bg-green-50',
    border: 'border-green-200',
    value: 'text-green-700',
    icon: 'text-green-500',
  },
  red: {
    bg: 'bg-red-50',
    border: 'border-red-200',
    value: 'text-red-700',
    icon: 'text-red-500',
  },
  yellow: {
    bg: 'bg-yellow-50',
    border: 'border-yellow-200',
    value: 'text-yellow-700',
    icon: 'text-yellow-500',
  },
  blue: {
    bg: 'bg-blue-50',
    border: 'border-blue-200',
    value: 'text-blue-700',
    icon: 'text-blue-500',
  },
  gray: {
    bg: 'bg-gray-50',
    border: 'border-gray-200',
    value: 'text-gray-700',
    icon: 'text-gray-400',
  },
} as const

type CardColor = keyof typeof CARD_COLORS

function StatCard({ label, value, detail, color }: { label: string; value: string; detail: string; color: CardColor }) {
  const c = CARD_COLORS[color]
  return (
    <div className={`rounded-lg border ${c.border} ${c.bg} px-4 py-3`}>
      <p className="text-xs font-medium uppercase tracking-wider text-gray-500">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${c.value}`}>{value}</p>
      <p className="mt-0.5 text-xs text-gray-500">{detail}</p>
    </div>
  )
}
