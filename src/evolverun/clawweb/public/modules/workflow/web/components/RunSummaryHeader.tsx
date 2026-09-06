import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import StatusBadge from '@avernet/workflow/web/components/StatusBadge'

interface RunSummaryHeaderProps {
  run: FlowRun
  nodeCount: number
  succeededCount?: number
  failedCount?: number
}

import { formatTime, formatDuration } from '@avernet/workflow/web/utils/time'

export default function RunSummaryHeader({ run, nodeCount, succeededCount, failedCount }: RunSummaryHeaderProps) {
  const succeeded = succeededCount ?? run.succeeded_count
  const failed = failedCount ?? run.failed_count
  const succeededPct = nodeCount > 0 ? Math.round((succeeded / nodeCount) * 100) : 0
  const failedPct = nodeCount > 0 ? Math.round((failed / nodeCount) * 100) : 0

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="运行摘要">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold text-slate-950">
            {run.workflow_title || run.workflow_id}
          </h1>
          <p className="mt-1 font-mono text-gray-400 text-xs">{run.flow_id}</p>
        </div>
        <StatusBadge status={run.status} className="text-sm" />
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3 border-t border-slate-100 pt-3 sm:grid-cols-3 lg:grid-cols-6">
        <Stat label="工作流" value={run.workflow_id} mono />
        <Stat label="创建者" value={run.user_id || run.triggered_by || '—'} mono={!!(run.user_id || run.triggered_by)} />
        <Stat label="发起 Bot" value={(run.origin_bot_id || '—') + (run.plugin_version ? ` / ${run.plugin_version}` : '')} mono={!!(run.origin_bot_id || run.plugin_version)} />
        <Stat label="运行引擎" value={run.engine || '—'} mono={!!run.engine} />
        <Stat label="耗时" value={formatDuration(run.total_duration_ms)} />
        <Stat label="开始时间" value={formatTime(run.started_at)} />
      </dl>

      {/* Node progress bar */}
      <div className="mt-4">
        <div className="mb-1 flex items-center justify-between text-xs text-gray-500">
          <span>节点进度</span>
          <span>
            {succeeded} 成功 / {failed} 失败 / {nodeCount} 总计
          </span>
        </div>
        <div className="flex h-2 overflow-hidden rounded-full bg-gray-100">
          {nodeCount > 0 && (
            <>
              <div
                className="bg-status-succeeded transition-all"
                style={{ width: `${succeededPct}%` }}
              />
              <div
                className="bg-status-failed transition-all"
                style={{ width: `${failedPct}%` }}
              />
            </>
          )}
        </div>
      </div>

      {run.total_token_usage !== null && run.total_token_usage > 0 && (
        <div className="mt-3 text-xs text-gray-400">
          Token 用量: {run.total_token_usage.toLocaleString()}
        </div>
      )}
    </section>
  )
}

function Stat({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <dt className="text-gray-400 text-xs">{label}</dt>
      <dd className={`mt-0.5 text-sm font-medium text-gray-900 ${mono ? 'font-mono' : ''}`}>
        {value}
      </dd>
    </div>
  )
}
