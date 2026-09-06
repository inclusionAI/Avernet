import { useState, useMemo, useCallback, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { useFlowRuns, useDeleteFlowRun, useWorkflowTypes } from '@avernet/workflow/web/api/hooks'
import { useDebounce } from '@avernet/workflow/web/hooks/useDebounce'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import { toTimeRange } from '@avernet/workflow/web/components/TimeRangeFilter'
import StatusFilter from '@avernet/workflow/web/components/StatusFilter'
import SearchInput from '@avernet/workflow/web/components/SearchInput'
import TimeRangeFilter from '@avernet/workflow/web/components/TimeRangeFilter'
import StatusBadge from '@avernet/workflow/web/components/StatusBadge'
import EmptyState from '@avernet/workflow/web/components/EmptyState'
import ErrorState from '@avernet/workflow/web/components/ErrorState'
import AutoHealPanel from '@avernet/workflow/web/components/AutoHealPanel'
import { formatTimeShort, formatDuration } from '@avernet/workflow/web/utils/time'
import type { FlowRun, WorkflowTypeRow } from '@avernet/clawweb-shared/web/types'

function toTs(v: number | string): number {
  const n = typeof v === 'number' ? v : Number(v)
  return Number.isNaN(n) ? new Date(v).getTime() / 1000 : n
}

export default function Dashboard() {
  const navigate = useNavigate()
  const [statusFilter, setStatusFilter] = useState('')
  const [search, setSearch] = useState('')
  const [timeRange, setTimeRange] = useState('')
  const [expandedWorkflow, setExpandedWorkflow] = useState<string | null>(null)
  const [autoHealRun, setAutoHealRun] = useState<FlowRun | null>(null)
  const debouncedSearch = useDebounce(search, 300)

  const timeParams = useMemo(() => toTimeRange(timeRange), [timeRange])

  const user = getClientUser()
  const isAdmin = user?.isAdmin === true

  // Reset expanded workflow when filters change
  const filterKey = `${statusFilter}|${timeRange}`
  useEffect(() => { setExpandedWorkflow(null) }, [filterKey])

  // ── Workflow list: admin sees all (no botOwnerId), normal user sees own ──
  const { data: workflowTypes, isLoading: typesLoading, isError: typesIsError, error: typesError, refetch: typesRefetch, isFetching: typesFetching } = useWorkflowTypes(
    isAdmin ? undefined : user?.userId,
    undefined,
    statusFilter || undefined,
  )

  // ── Runs for expanded workflow: admin sees all, normal user sees own ──
  const { data: expandedRunsData, isFetching: runsFetching } = useFlowRuns({
    workflowId: expandedWorkflow || undefined,
    ...timeParams,
    limit: 20,
    botOwnerId: isAdmin ? undefined : user?.userId,
    enabled: !!expandedWorkflow,
  })

  const expandedRuns = expandedRunsData?.runs ?? []

  const hasRunning = useMemo(
    () => (workflowTypes ?? []).some((w) => w.last_status === 'running'),
    [workflowTypes],
  )

  // Filter workflow types by search term (client-side)
  const workflows = useMemo(() => {
    let all = workflowTypes ?? []
    if (debouncedSearch) {
      const q = debouncedSearch.toLowerCase()
      all = all.filter(
        (w) =>
          w.workflow_id.toLowerCase().includes(q) ||
          (w.workflow_title ?? '').toLowerCase().includes(q),
      )
    }
    return all
  }, [workflowTypes, debouncedSearch])

  const handleAutoHealRerunComplete = useCallback(() => {
    void typesRefetch()
  }, [typesRefetch])

  return (
    <div className="mx-auto max-w-screen-2xl px-4 py-6 sm:px-6 lg:px-8">
      {/* Header */}
      <div className="mb-6 flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-gray-900">工作流</h1>
          <p className="mt-1 text-gray-400 text-sm">
            {isAdmin ? (
              <>{workflows.length} workflow{workflows.length !== 1 ? 's' : ''} (管理员视图 — 全部工作流)</>
            ) : (
              <>{workflows.length} workflow{workflows.length !== 1 ? 's' : ''}</>
            )}
            {hasRunning && (
              <span className="ml-2 inline-flex items-center">
                <span className="mr-1 h-2 w-2 animate-pulse rounded-full bg-status-running" />
                Live
              </span>
            )}
          </p>
        </div>
        <button
          onClick={() => void typesRefetch()}
          disabled={typesFetching}
          className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-sm font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
        >
          <svg
            className={`h-4 w-4 ${typesFetching ? 'animate-spin' : ''}`}
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
          {typesFetching ? '刷新中…' : '刷新'}
        </button>
      </div>

      {/* Filters */}
      <div className="mb-4 flex flex-wrap items-center gap-3">
        <StatusFilter value={statusFilter} onChange={setStatusFilter} />
        <SearchInput
          value={search}
          onChange={setSearch}
          placeholder="搜索工作流…"
        />
        <TimeRangeFilter value={timeRange} onChange={setTimeRange} />
        {(statusFilter || search || timeRange) && (
          <button
            onClick={() => {
              setStatusFilter('')
              setSearch('')
              setTimeRange('')
            }}
            className="text-sm text-gray-400 transition-colors hover:text-gray-600"
          >
            清除筛选
          </button>
        )}
      </div>

      {/* Workflow list (same structure for admin & normal user) */}
      {typesIsError ? (
        <ErrorState
          message={typesError instanceof Error ? typesError.message : 'Failed to load workflows'}
          onRetry={() => void typesRefetch()}
        />
      ) : typesLoading ? (
        <div className="flex items-center justify-center py-16">
          <div className="h-8 w-8 animate-spin rounded-full border-2 border-gray-300 border-t-blue-600" />
        </div>
      ) : workflows.length === 0 ? (
        <EmptyState
          title="No workflows found"
          description={(workflowTypes ?? []).length === 0 ? '暂无工作流。' : '没有匹配筛选条件的工作流。'}
        />
      ) : (
        <div className="overflow-x-auto rounded-lg border border-gray-200">
          <table className="min-w-full divide-y divide-gray-200">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Workflow
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Last Status
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Runs
                </th>
                <th className="px-4 py-3 text-left text-xs font-medium uppercase tracking-wider text-gray-500">
                  Last Active
                </th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100 bg-white">
              {workflows.map((wf) => {
                const isExpanded = expandedWorkflow === wf.workflow_id
                return (
                  <DashboardWorkflowRow
                    key={wf.workflow_id}
                    wf={wf}
                    isExpanded={isExpanded}
                    expandedRuns={isExpanded ? expandedRuns : []}
                    runsLoading={isExpanded && runsFetching}
                    onToggle={() => setExpandedWorkflow(isExpanded ? null : wf.workflow_id)}
                    onNavigate={() => navigate(`/workflows/${wf.workflow_id}`)}
                    onAutoHeal={(run) => setAutoHealRun(run)}
                  />
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Auto-Heal Drawer */}
      {autoHealRun && (
        <AutoHealPanel
          run={autoHealRun}
          onClose={() => setAutoHealRun(null)}
          onRerunComplete={handleAutoHealRerunComplete}
        />
      )}
    </div>
  )
}

function DashboardWorkflowRow({
  wf,
  isExpanded,
  expandedRuns,
  runsLoading,
  onToggle,
  onNavigate,
  onAutoHeal,
}: {
  wf: WorkflowTypeRow
  isExpanded: boolean
  expandedRuns: FlowRun[]
  runsLoading: boolean
  onToggle: () => void
  onNavigate: () => void
  onAutoHeal: (run: FlowRun) => void
}) {
  return (
    <>
      <tr
        className="cursor-pointer transition-colors hover:bg-blue-50"
        onClick={onNavigate}
      >
        <td className="whitespace-nowrap px-4 py-3">
          <div className="flex items-center gap-2">
            <button
              onClick={(e) => { e.stopPropagation(); onToggle() }}
              className="inline-flex h-5 w-5 items-center justify-center rounded text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600"
              title={isExpanded ? '收起实例' : '展开实例'}
            >
              <svg
                className={`h-3.5 w-3.5 transition-transform ${isExpanded ? 'rotate-90' : ''}`}
                fill="currentColor"
                viewBox="0 0 20 20"
              >
                <path fillRule="evenodd" d="M7.293 14.707a1 1 0 010-1.414L10.586 10 7.293 6.707a1 1 0 011.414-1.414l4 4a1 1 0 010 1.414l-4 4a1 1 0 01-1.414 0z" clipRule="evenodd" />
              </svg>
            </button>
            <div>
              <div className="font-medium text-gray-900 text-sm">{wf.workflow_title || wf.workflow_id}</div>
              <div className="text-gray-400 text-xs">{wf.workflow_id}</div>
            </div>
          </div>
        </td>
        <td className="whitespace-nowrap px-4 py-3">
          <StatusBadge status={wf.last_status as FlowRun['status']} />
        </td>
        <td className="whitespace-nowrap px-4 py-3">
          <div className="flex items-center gap-2 text-sm">
            <span className="text-gray-600">{wf.run_count} 总计</span>
          </div>
        </td>
        <td className="whitespace-nowrap px-4 py-3 text-gray-500 text-sm">
          {formatTimeShort(wf.last_run_at ?? wf.updated_at)}
        </td>
      </tr>
      {isExpanded && (
        runsLoading ? (
          <tr className="bg-gray-50">
            <td colSpan={4} className="px-4 py-3 pl-12 text-gray-400 text-xs">
              加载中…
            </td>
          </tr>
        ) : (
          expandedRuns
            .sort((a, b) => toTs(b.started_at) - toTs(a.started_at))
            .map((run) => (
              <DashboardRunRow key={run.flow_id} run={run} onAutoHeal={onAutoHeal} />
            ))
        )
      )}
    </>
  )
}

function DashboardRunRow({ run, onAutoHeal }: { run: FlowRun; onAutoHeal: (run: FlowRun) => void }) {
  const navigate = useNavigate()
  const deleteMutation = useDeleteFlowRun()
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

  const handleAutoHeal = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    onAutoHeal(run)
  }, [onAutoHeal, run])

  const isFailed = ['failed', 'blocked', 'waiting'].includes(run.status)

  const { succeeded_count, failed_count } = run
  const other = run.node_count - succeeded_count - failed_count
  const parts: string[] = []
  if (succeeded_count > 0) parts.push(`${succeeded_count} 成功`)
  if (failed_count > 0) parts.push(`${failed_count} 失败`)
  if (other > 0) parts.push(`${other} 其他`)

  return (
    <tr
      onClick={() => navigate(`/runs/${run.flow_id}`)}
      className="cursor-pointer bg-gray-50 transition-colors hover:bg-blue-50"
    >
      <td className="whitespace-nowrap px-4 py-2 pl-12">
        <div className="font-mono text-xs text-gray-600">{run.flow_id}</div>
      </td>
      <td className="whitespace-nowrap px-4 py-2">
        <StatusBadge status={run.status} />
      </td>
      <td className="whitespace-nowrap px-4 py-2">
        <span className="text-gray-600 text-xs">{run.node_count === 0 ? '—' : parts.join(' / ')}</span>
      </td>
      <td className="whitespace-nowrap px-4 py-2">
        <div className="flex items-center gap-2 text-xs text-gray-500">
          <span>{formatTimeShort(run.started_at)}</span>
          <span className="text-gray-300">|</span>
          <span>{formatDuration(run.total_duration_ms)}</span>
          {/* Auto-Heal button for failed runs */}
          {isFailed && !confirming && (
            <button
              onClick={handleAutoHeal}
              className="rounded-md border border-blue-200 bg-blue-50 px-2 py-0.5 text-xs font-medium text-blue-700 transition-colors hover:bg-blue-100 hover:border-blue-300"
              title="AI 自动诊断与修复"
            >
              🩹 自动修复
            </button>
          )}
          {confirming ? (
            <>
              <button
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className="rounded-md bg-red-600 px-2 py-0.5 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
              >
                {deleteMutation.isPending ? '删除中…' : '确认'}
              </button>
              <button
                onClick={handleCancelDelete}
                className="rounded-md border border-gray-300 bg-white px-2 py-0.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
              >
                取消
              </button>
            </>
          ) : (
            <button
              onClick={handleDelete}
              className="rounded-md border border-gray-200 bg-white px-2 py-0.5 text-xs text-gray-500 transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-600"
              title="删除此运行实例"
            >
              删除
            </button>
          )}
        </div>
      </td>
    </tr>
  )
}
