import { useCallback, useState } from 'react'
import type { FlowRun } from '@avernet/clawweb-shared/web/types'
import StatusBadge from '@avernet/workflow/web/components/StatusBadge'
import RunMarkers from './RunMarkers'
import { useDeleteFlowRun, useRerunFlowRun, useAbortFlowRun } from '@avernet/clawweb-shared/web/api/hooks'
import type { RunRuntimeMetadata } from '../../shared/run-runtime-metadata'
import type { RunLaunchMetadata } from '../../shared/run-launch-metadata'
import RunLaunchDetails from './RunLaunchDetails'
import { formatTime, formatDuration } from '@avernet/workflow/web/utils/time'

interface RunSummaryHeaderProps {
  run: FlowRun & Partial<RunRuntimeMetadata> & Partial<RunLaunchMetadata>
  nodeCount: number
  succeededCount?: number
  failedCount?: number
  onAutoHeal?: (run: FlowRun) => void
  onApproval?: () => void
  pendingApprovalCount?: number
  readOnly?: boolean
}

const actionClass = 'inline-flex min-h-8 items-center justify-center gap-1 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50'
const neutralActionClass = `${actionClass} border-input bg-card text-muted-foreground hover:bg-muted hover:text-foreground`
const dangerActionClass = `${actionClass} border-destructive/25 bg-card text-destructive hover:border-destructive/50 hover:bg-destructive/10`
const confirmActionClass = `${actionClass} border-destructive bg-destructive text-primary-foreground hover:bg-destructive/90`

export default function RunSummaryHeader(props: RunSummaryHeaderProps) {
  // Destructive confirmations belong to one run, never a cached navigation destination.
  return <RunScopedSummaryHeader key={props.run.flow_id} {...props} />
}

function RunScopedSummaryHeader({ run, nodeCount, succeededCount, failedCount, onAutoHeal, onApproval, pendingApprovalCount = 0, readOnly = false }: RunSummaryHeaderProps) {
  const succeeded = succeededCount ?? run.succeeded_count
  const failed = failedCount ?? run.failed_count
  const succeededPct = nodeCount > 0 ? Math.round((succeeded / nodeCount) * 100) : 0
  const failedPct = nodeCount > 0 ? Math.round((failed / nodeCount) * 100) : 0

  const deleteMutation = useDeleteFlowRun()
  const rerunMutation = useRerunFlowRun()
  const abortMutation = useAbortFlowRun()
  const [confirming, setConfirming] = useState(false)
  const [confirmingAbort, setConfirmingAbort] = useState(false)

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

  const handleAbort = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    if (!confirmingAbort) {
      setConfirmingAbort(true)
      return
    }
    abortMutation.mutate(run.flow_id)
    setConfirmingAbort(false)
  }, [confirmingAbort, abortMutation, run.flow_id])

  const handleCancelAbort = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    setConfirmingAbort(false)
  }, [])

  const handleAutoHeal = useCallback((e: React.MouseEvent) => {
    e.stopPropagation()
    onAutoHeal?.(run)
  }, [onAutoHeal, run])

  const isAutoHealable = ['failed', 'blocked', 'waiting'].includes(run.status)
  const isAbortable = ['running', 'waiting', 'blocked', 'pending', 'queued', 'postActionsRunning'].includes(run.status)
  const canRerun = !!run.origin_bot_id

  return (
    <section className="min-w-0 rounded-xl border border-border bg-card p-4 text-foreground sm:p-5" aria-label="运行摘要">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-3">
          <div aria-hidden="true" className="flex size-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" className="size-5">
              <rect x="9" y="2" width="6" height="6" rx="1.5" />
              <rect x="2" y="16" width="6" height="6" rx="1.5" />
              <rect x="16" y="16" width="6" height="6" rx="1.5" />
              <path d="M12 8v4M5 16v-4h14v4" />
            </svg>
          </div>
          <div className="min-w-0">
            <p aria-hidden="true" className="text-[10px] font-medium tracking-widest text-muted-foreground">WORKFLOW RUN</p>
            <h1 className="mt-0.5 break-words text-lg font-semibold tracking-tight text-foreground [overflow-wrap:anywhere] sm:text-xl">
              {run.workflow_title || run.workflow_id}
            </h1>
            <p className="mt-1 break-words font-mono text-[11px] leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{run.flow_id}</p>
          </div>
        </div>
        <StatusBadge status={run.status} className="mt-1 shrink-0 text-xs" />
      </div>
      {!readOnly && (
        <div role="group" aria-label="运行操作" className="mt-3 flex flex-wrap items-center justify-end gap-1.5">
          {!readOnly && canRerun && (
            <button
              type="button"
              onClick={handleRerun}
              disabled={rerunMutation.isPending}
              className={`${actionClass} border-success/25 bg-card text-success hover:border-success/50 hover:bg-success/10`}
              title="重跑：重新发送原始命令到 Bot"
            >
              {rerunMutation.isPending ? '⏳' : '🔄 重跑'}
            </button>
          )}
          {!readOnly && isAbortable && (
            confirmingAbort ? (
              <span className="inline-flex items-center gap-1">
                <button
                  type="button"
                  onClick={handleAbort}
                  disabled={abortMutation.isPending}
                  className={confirmActionClass}
                >
                  {abortMutation.isPending ? '中止中…' : '确认中止'}
                </button>
                <button
                  type="button"
                  onClick={handleCancelAbort}
                  className={neutralActionClass}
                >
                  取消
                </button>
              </span>
            ) : (
              <button
                type="button"
                onClick={handleAbort}
                disabled={abortMutation.isPending}
                className={dangerActionClass}
                title="中止运行中的工作流"
              >
                ⛔ 中止
              </button>
            )
          )}
          {!readOnly && isAutoHealable && (
            <button
              type="button"
              onClick={handleAutoHeal}
              className={`${actionClass} border-primary/25 bg-card text-primary hover:border-primary/50 hover:bg-primary/5`}
              title="AI 自动诊断与修复"
            >
              🩹 修复
            </button>
          )}
          {!readOnly && run.status === 'waiting' && pendingApprovalCount > 0 && onApproval && (
            <button
              type="button"
              onClick={onApproval}
              className={`${actionClass} border-warning/30 bg-warning/10 text-warning-foreground hover:border-warning/50 hover:bg-warning/20`}
              title="审批"
            >
              📋 审批
              {pendingApprovalCount > 1 && (
                <span className="ml-0.5 rounded border border-warning/40 px-1 text-[10px] leading-4">{pendingApprovalCount}</span>
              )}
            </button>
          )}
          {!readOnly && (confirming ? (
            <span className="inline-flex items-center gap-1">
              <button
                type="button"
                onClick={handleDelete}
                disabled={deleteMutation.isPending}
                className={confirmActionClass}
              >
                {deleteMutation.isPending ? '删除中…' : '确认删除'}
              </button>
              <button
                type="button"
                onClick={handleCancelDelete}
                className={neutralActionClass}
              >
                取消
              </button>
            </span>
          ) : (
            <button
              type="button"
              onClick={handleDelete}
              className={`${actionClass} border-input bg-card text-muted-foreground hover:border-destructive/30 hover:bg-destructive/10 hover:text-destructive`}
              title="删除此运行实例"
            >
              🗑 删除
            </button>
          ))}
        </div>
      )}

      <section aria-label="运行身份" className="mt-4 border-t border-border pt-3">
        <GroupHeading label="运行身份" caption="IDENTITY" />
        <dl className="mt-2.5 grid grid-cols-2 gap-x-5 gap-y-3 sm:grid-cols-4">
          <Stat label="工作流" value={run.workflow_id} mono />
          <Stat label="版本" value={run.workflow_version != null ? (run.workflow_version === -1 ? '未绑定发布版本' : String(run.workflow_version)) : '—'} mono={run.workflow_version != null && run.workflow_version !== -1} />
          <Stat label="创建者" value={run.user_id || run.triggered_by || '—'} mono={!!(run.user_id || run.triggered_by)} />
          <Stat label="发起 Bot" value={run.origin_bot_id || '—'} mono={!!(run.origin_bot_id || run.plugin_version)} note={run.plugin_version ? `plugin_version · ${run.plugin_version}` : undefined} />
        </dl>
      </section>

      <section aria-label="执行上下文" className="mt-4 border-t border-border pt-3">
        <GroupHeading label="执行上下文" caption="RUNTIME" />
        <dl className="mt-2.5 grid grid-cols-2 gap-x-5 gap-y-3 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="运行引擎" value={run.engine || '—'} mono={!!run.engine} />
          <RunMarkers run={run} variant="details" />
          <Stat label="device_id" value={run.device_id ?? ''} mono className="break-all" />
          <Stat label="BOT_TYPE" value={run.bot_type ?? ''} mono className="break-all" />
          <Stat label="STAGE" value={run.stage ?? ''} mono className="break-all" />
        </dl>
      </section>

      <RunLaunchDetails run={run} />

      <section aria-label="运行用量与进度" className="mt-4 border-t border-border pt-3">
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <dl className="flex min-w-0 flex-wrap gap-x-7 gap-y-3">
            <Stat label="耗时" value={formatDuration(run.total_duration_ms)} mono />
            <Stat label="开始时间" value={formatTime(run.started_at)} />
          </dl>
          {run.total_token_usage !== null && run.total_token_usage > 0 && (
            <dl className="flex min-w-0 items-baseline gap-2">
              <dt className="text-xs text-muted-foreground">Token 用量</dt>
              <dd className="font-mono text-base font-medium tabular-nums text-foreground">{run.total_token_usage.toLocaleString()}</dd>
            </dl>
          )}
        </div>
        <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-2 text-xs text-muted-foreground">
          <span>节点进度</span>
          <div
            role="progressbar"
            aria-label="节点进度"
            aria-valuemin={0}
            aria-valuemax={nodeCount}
            aria-valuenow={succeeded + failed}
            aria-valuetext={`${succeeded} 成功 / ${failed} 失败 / ${nodeCount} 总计`}
            className="flex h-1.5 min-w-24 flex-1 overflow-hidden rounded-full bg-muted"
          >
            {nodeCount > 0 && (
              <>
                <div className="shrink-0 bg-success transition-all motion-reduce:transition-none" style={{ width: `${succeededPct}%` }} />
                <div className="shrink-0 bg-destructive transition-all motion-reduce:transition-none" style={{ width: `${failedPct}%` }} />
              </>
            )}
          </div>
          <span className="font-mono text-[11px] tabular-nums">
            {succeeded} 成功 / {failed} 失败 / {nodeCount} 总计
          </span>
        </div>
      </section>
    </section>
  )
}

function GroupHeading({ label, caption }: { label: string; caption: string }) {
  return (
    <h2 className="flex items-baseline gap-2 text-xs font-semibold text-muted-foreground">
      {label}
      <span aria-hidden="true" className="text-[9px] font-normal tracking-widest">{caption}</span>
    </h2>
  )
}

function Stat({ label, value, mono, className, note }: { label: string; value: string; mono?: boolean; className?: string; note?: string }) {
  return (
    <div className={`min-w-0 ${className ?? ''}`}>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className={`mt-1 min-w-0 break-words text-xs font-medium leading-relaxed text-foreground [overflow-wrap:anywhere] ${mono ? 'font-mono' : ''}`}>
        {value}
        {note && <small className="mt-0.5 block text-[10px] font-normal text-muted-foreground">{note}</small>}
      </dd>
    </div>
  )
}
