import { useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useFlowRuns, useWorkflowHealth } from '../../api/hooks'
import { HealthScoreCard } from '../HealthScoreCard'
import { SuccessTrendCard } from '../SuccessTrendCard'
import StatusBadge from '../StatusBadge'
import EmptyState from '../EmptyState'
import ErrorState from '../ErrorState'
import { formatTimeShort, formatDuration } from '../../utils/time'
import type { FlowRun, WorkflowTypeRow, NodeStatus } from '../../types'

function StatCard({
  label,
  value,
  detail,
  tone = 'default',
}: {
  label: string
  value: string
  detail: string
  tone?: 'default' | 'danger' | 'warning' | 'success'
}) {
  const toneClasses = {
    default: 'border-gray-200 bg-white',
    danger: 'border-red-200 bg-red-50',
    warning: 'border-yellow-200 bg-yellow-50',
    success: 'border-green-200 bg-green-50',
  }
  const valueClasses = {
    default: 'text-gray-900',
    danger: 'text-red-700',
    warning: 'text-yellow-700',
    success: 'text-green-700',
  }
  return (
    <div className={`rounded-lg border p-4 shadow-sm ${toneClasses[tone]}`}>
      <p className="text-xs font-medium uppercase tracking-wider text-gray-500">{label}</p>
      <p className={`mt-1 text-2xl font-bold ${valueClasses[tone]}`}>{value}</p>
      <p className="mt-0.5 text-xs text-gray-500">{detail}</p>
    </div>
  )
}

function RunRow({ run }: { run: FlowRun }) {
  const navigate = useNavigate()
  const { node_count, succeeded_count, failed_count } = run
  const other = Math.max(0, node_count - succeeded_count - failed_count)
  const parts: string[] = []
  if (succeeded_count > 0) parts.push(`${succeeded_count} 成功`)
  if (failed_count > 0) parts.push(`${failed_count} 失败`)
  if (other > 0) parts.push(`${other} 其他`)

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
        {run.user_id ? (
          <span className="font-mono">{run.user_id}</span>
        ) : run.triggered_by ? (
          <span className="font-mono">{run.triggered_by}</span>
        ) : (
          <span className="text-gray-300">—</span>
        )}
      </td>
      <td className="whitespace-nowrap px-4 py-2 text-xs text-gray-500">
        {run.origin_bot_id ? (
          <span className="font-mono">
            {run.plugin_version ? `${run.origin_bot_id}/${run.plugin_version}` : run.origin_bot_id}
          </span>
        ) : (
          <span className="text-gray-300">—</span>
        )}
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
    </tr>
  )
}

interface OverviewTabProps {
  workflow: WorkflowTypeRow
}

export default function OverviewTab({ workflow }: OverviewTabProps) {
  const workflowId = workflow.workflow_id
  const { data: health } = useWorkflowHealth(workflowId)
  const {
    data,
    isLoading,
    isError,
    error,
    refetch,
    isFetching,
  } = useFlowRuns({ workflowId, limit: 20 })

  const runs = data?.runs ?? []
  const totalCount = data?.total ?? 0

  const stats = useMemo(() => {
    let failedRuns = 0
    let runningRuns = 0
    let durationSum = 0
    let durationCount = 0
    for (const r of runs) {
      if (r.status === 'failed') failedRuns++
      if (r.status === 'running') runningRuns++
      if ((r.total_duration_ms ?? 0) > 0) {
        durationSum += r.total_duration_ms!
        durationCount++
      }
    }
    const successRate = health
      ? Math.round(health.successRate)
      : totalCount > 0
        ? Math.round(((totalCount - failedRuns) / totalCount) * 100)
        : 0
    return {
      failedRuns,
      runningRuns,
      successRate,
      avgDurationMs: durationCount > 0 ? Math.round(durationSum / durationCount) : null,
    }
  }, [runs, totalCount, health])

  const currentSuccessRate = health ? `${Math.round(health.successRate)}%` : `${stats.successRate}%`
  const currentDetail = health
    ? `${Math.round(health.successRate * totalCount / 100)} / ${totalCount} runs`
    : `${stats.successRate}% 基于最近 ${runs.length} 条`

  return (
    <div className="space-y-4">
      {workflowId && (
        <SuccessTrendCard
          workflowId={workflowId}
          currentSuccessRate={currentSuccessRate}
          currentDetail={currentDetail}
        />
      )}

      {workflowId && <HealthScoreCard workflowId={workflowId} />}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          label="成功率"
          value={`${stats.successRate}%`}
          detail="过去 7 天"
          tone={stats.successRate < 50 && totalCount > 0 ? 'danger' : 'default'}
        />
        <StatCard
          label="失败运行"
          value={`${stats.failedRuns}`}
          detail="需要关注"
          tone={stats.failedRuns > 0 ? 'danger' : 'default'}
        />
        <StatCard
          label="运行中"
          value={`${stats.runningRuns}`}
          detail="当前活跃"
          tone={stats.runningRuns > 0 ? 'warning' : 'default'}
        />
        <StatCard
          label="平均耗时"
          value={formatDuration(stats.avgDurationMs)}
          detail="已完成运行"
        />
      </div>

      <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="flex items-center justify-between border-b border-gray-200 px-4 py-3">
          <div>
            <h3 className="text-sm font-semibold text-gray-900">运行记录</h3>
            <p className="text-xs text-gray-500">最近运行，点击可查看详情</p>
          </div>
          <button
            onClick={() => void refetch()}
            disabled={isFetching}
            className="inline-flex items-center gap-1.5 rounded-md border border-gray-300 bg-white px-3 py-1.5 text-xs font-medium text-gray-700 shadow-sm transition-colors hover:bg-gray-50 disabled:cursor-not-allowed disabled:opacity-50"
          >
            <svg
              className={`h-3.5 w-3.5 ${isFetching ? 'animate-spin' : ''}`}
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

        {isError ? (
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
            <EmptyState title="暂无运行" description="该工作流尚未执行过" />
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">Run ID</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">状态</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">节点</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">创建者</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">发起 Bot</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">环境</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">开始时间</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">耗时</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 bg-white">
                {runs.map((run) => (
                  <RunRow key={run.flow_id} run={run} />
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  )
}
