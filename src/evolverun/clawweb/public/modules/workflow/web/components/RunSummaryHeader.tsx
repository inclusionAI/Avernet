import { useCallback, useState } from 'react'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import StatusBadge from '@avernet/workflow/web/components/StatusBadge'
import { useDeleteFlowRun, useRerunFlowRun } from '@avernet/clawweb-shared/web/api/hooks'

interface RunSummaryHeaderProps {
  run: FlowRun
  nodeCount: number
  succeededCount?: number
  failedCount?: number
  onAutoHeal?: (run: FlowRun) => void
}

import { formatTime, formatDuration } from '@avernet/workflow/web/utils/time'

export default function RunSummaryHeader({ run, nodeCount, succeededCount, failedCount, onAutoHeal }: RunSummaryHeaderProps) {
  const succeeded = succeededCount ?? run.succeeded_count
  const failed = failedCount ?? run.failed_count
  const succeededPct = nodeCount > 0 ? Math.round((succeeded / nodeCount) * 100) : 0
  const failedPct = nodeCount > 0 ? Math.round((failed / nodeCount) * 100) : 0

  const deleteMutation = useDeleteFlowRun()
  const rerunMutation = useRerunFlowRun()
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
    onAutoHeal?.(run)
  }, [onAutoHeal, run])

  const isAutoHealable = ['failed', 'blocked', 'waiting'].includes(run.status)
  const canRerun = !!run.origin_bot_id

  return (
    <section className="rounded-xl border border-slate-200 bg-white p-4" aria-label="运行摘要">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="truncate text-lg font-semibold text-slate-950">
            {run.workflow_title || run.workflow_id}
          </h1>
          <p className="mt-1 font-mono text-gray-400 text-xs">{run.flow_id}</p>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={run.status} className="text-sm" />
          {canRerun && (
            <button
              type="button"
              onClick={handleRerun}
              disabled={rerunMutation.isPending}
              className="inline-flex items-center gap-0.5 rounded-md border border-green-200 bg-white px-2 py-1 text-xs text-green-600 transition-colors hover:border-green-400 hover:bg-green-50 hover:text-green-700 disabled:cursor-not-allowed disabled:opacity-50"
              title="重跑：重新发送原始命令到 Bot"
            >
              {rerunMutation.isPending ? '⏳' : '🔄 重跑'}
            </button>
          )}
          {isAutoHealable && (
            <button
              type="button"
              onClick={handleAutoHeal}
              className="inline-flex items-center gap-0.5 rounded-md border border-blue-200 bg-white px-2 py-1 text-xs text-blue-600 transition-colors hover:border-blue-400 hover:bg-blue-50 hover:text-blue-700"
              title="AI 自动诊断与修复"
            >
              🩹 中止
            </button>
          )}
          {confirming ? (
            <span className="inline-flex items-center gap-1">
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className="rounded-md bg-red-600 px-2 py-1 text-xs font-medium text-white transition-colors hover:bg-red-700 disabled:opacity-50"
              >
                {deleteMutation.isPending ? '删除中…' : '确认删除'}
              </button>
              <button
                type="button"
                onClick={handleCancelDelete}
                className="rounded-md border border-gray-300 bg-white px-2 py-1 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
              >
                取消
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={handleDelete}
              className="inline-flex items-center rounded-md border border-gray-200 bg-white px-2 py-1 text-xs text-gray-500 transition-colors hover:border-red-300 hover:bg-red-50 hover:text-red-600"
              title="删除此运行实例"
            >
              🗑 删除
            </button>
          )}
        </div>
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
