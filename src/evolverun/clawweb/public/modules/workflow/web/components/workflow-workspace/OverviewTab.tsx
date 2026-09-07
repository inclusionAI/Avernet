import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAnalysisProgress, useFlowRuns, useWorkflowHealth } from '../../api/hooks'
import { SuccessTrendCard } from '../SuccessTrendCard'
import { NodeAnalysisPanel } from '../NodeAnalysisPanel'
import StatusBadge from '../StatusBadge'
import EmptyState from '../EmptyState'
import ErrorState from '../ErrorState'
import { formatTimeShort, formatDuration } from '../../utils/time'
import type { FlowRun, WorkflowTypeRow } from '@avernet/clawweb-shared/web/types'

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

function RunRow({ run }: { run: FlowRun }) {
  const navigate = useNavigate()
  const status = run.evolution_analysis_status ?? null
  const progressQuery = useAnalysisProgress(run.flow_id, status === 'analyzing')
  const progress = progressQuery.data?.progress ?? null
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
        <span className="block font-mono text-slate-600">{run.user_id || run.triggered_by || '—'}</span>
        {run.origin_bot_id && <span className="mt-0.5 block max-w-52 truncate font-mono text-[10px] text-slate-400" title={run.origin_bot_id}>{run.plugin_version ? `${run.origin_bot_id}/${run.plugin_version}` : run.origin_bot_id}</span>}
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
        {status === 'analyzing' ? (
          <div className="min-w-56">
            <div className="flex items-center gap-2 text-blue-600">
              <span className="inline-block h-3 w-3 shrink-0 animate-spin rounded-full border border-current border-t-transparent" />
              <span className="font-medium">{progress?.message ?? '分析中'}</span>
              {progress && <span className="text-[10px] tabular-nums text-slate-400">已用时 {formatAnalysisElapsed(progress.elapsedMs)}</span>}
            </div>
            {progress?.inputSummary && <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-400">
              <span>{`证据 ${progress.inputSummary.evidenceIncluded}/${progress.inputSummary.evidenceTotal} · 节点 ${progress.inputSummary.nodeCount}（失败 ${progress.inputSummary.failedNodeCount}）· Trace ${progress.inputSummary.traceCount}`}</span>
              {progress.inputSummary.truncated && <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">输入已截断</span>}
            </div>}
            {!progress && progressQuery.isError && <p className="mt-1 text-[10px] text-slate-400">进度暂不可用，分析仍在后台运行</p>}
          </div>
        ) : status === 'completed' ? (
          <span className="text-emerald-600">已分析</span>
        ) : status === 'failed' ? (
          <span className="text-red-500">待分析</span>
        ) : (
          <span className="text-gray-300">—</span>
        )}
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
  const workflowId = workflow.workflow_id
  const [days, setDays] = useState<7 | 30>(7)
  const [page, setPage] = useState(0)
  const [windowEnd] = useState(() => Math.floor(Date.now() / 1000))
  const [activeSubTab, setActiveSubTab] = useState<'runs' | 'nodes'>('runs')
  const [highlightNodeId, setHighlightNodeId] = useState<string | null>(null)
  const {
    data: health,
    isError: isHealthError,
  } = useWorkflowHealth(workflowId, days)
  const pageSize = 20
  const windowStart = windowEnd - days * 86400
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
    from: String(windowStart),
    to: String(windowEnd),
  })

  const runs = useMemo(() => data?.runs ?? [], [data?.runs])
  const totalCount = data?.total ?? 0

  const stats = useMemo(() => {
    const counts = data?.statusCounts ?? {}
    const succeededRuns = counts.succeeded ?? 0
    const abnormalRuns = (counts.failed ?? 0) + (counts.aborted ?? 0) + (counts.cancelled ?? 0) + (counts.canceled ?? 0)
    const terminalRuns = succeededRuns + abnormalRuns
    return {
      succeededRuns,
      abnormalRuns,
      terminalRuns,
      successRate: terminalRuns > 0 ? Math.round((succeededRuns / terminalRuns) * 100) : 0,
      runningRuns: counts.running ?? 0,
      waitingRuns: counts.waiting ?? 0,
      blockedRuns: counts.blocked ?? 0,
      queuedRuns: counts.queued ?? 0,
    }
  }, [data?.statusCounts])

  const currentSuccessRate = stats.terminalRuns > 0 ? `${stats.successRate}%` : '—'
  const currentDetail = `${stats.succeededRuns} / ${stats.terminalRuns} 个终态运行`
  const totalPages = Math.max(1, Math.ceil(totalCount / pageSize))
  const currentPage = Math.min(page + 1, totalPages)

  const changeDays = (nextDays: 7 | 30) => {
    setDays(nextDays)
    setPage(0)
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end gap-1" aria-label="概览时间范围">
        {[7, 30].map((value) => (
          <button
            key={value}
            type="button"
            onClick={() => changeDays(value as 7 | 30)}
            className={`rounded-md px-3 py-1 text-xs font-medium transition ${days === value ? 'bg-blue-600 text-white' : 'bg-white text-slate-500 ring-1 ring-slate-200 hover:bg-slate-50'}`}
          >{value}天</button>
        ))}
      </div>
      <section aria-label="工作流关键指标" className="grid grid-cols-2 divide-x divide-y divide-slate-100 overflow-hidden rounded-xl border border-slate-200 bg-white lg:grid-cols-4 lg:divide-y-0">
        <MetricCell label="健康度" value={health ? String(health.overallScore) : '—'} detail={health ? (health.overallScore >= 80 ? '运行稳定' : health.overallScore >= 60 ? '需要关注' : '建议优先处理') : '等待健康数据'} emphasis={health && health.overallScore < 60 ? 'danger' : 'default'} />
        <MetricCell label="运行成功率" value={currentSuccessRate} detail={`近 ${days} 天 · 成功 / 终态`} />
        <MetricCell label="异常结束" value={String(stats.abnormalRuns)} detail="失败、终止或取消" emphasis={stats.abnormalRuns > 0 ? 'danger' : 'default'} />
        <MetricCell label="节点耗时 P95" value={health ? formatDuration(health.p95DurationMs) : '—'} detail="最慢节点 P95 口径" />
      </section>

      <section aria-label="当前运行状态" className="grid grid-cols-2 gap-2 sm:grid-cols-4">
        {[
          ['运行中', stats.runningRuns, 'bg-blue-50 text-blue-700'],
          ['等待中', stats.waitingRuns, 'bg-amber-50 text-amber-700'],
          ['阻塞', stats.blockedRuns, 'bg-sky-50 text-sky-700'],
          ['排队中', stats.queuedRuns, 'bg-violet-50 text-violet-700'],
        ].map(([label, count, cls]) => (
          <div key={String(label)} className={`rounded-lg px-3 py-2 text-xs font-medium ${cls}`}>
            {`${label} ${count}`}
          </div>
        ))}
      </section>

      {isHealthError && <div className="rounded-lg border border-rose-100 bg-rose-50 px-4 py-2 text-xs text-rose-600">健康指标加载失败；运行列表仍可继续查看。</div>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(280px,1fr)]">
        {workflowId && <SuccessTrendCard workflowId={workflowId} currentSuccessRate={currentSuccessRate} currentDetail={currentDetail} compact days={days} showRangeSelector={false} />}
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
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">发起方</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">环境</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">开始时间</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">耗时</th>
                  <th className="px-4 py-2 text-left text-xs font-medium uppercase tracking-wider text-gray-500">自进化</th>
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
