import { useFlowApprovals } from '@avernet/clawweb-shared/web/api/hooks'
import type { ApprovalCardSummary } from '@avernet/clawweb-shared/web/api/client'
import { Link } from 'react-router-dom'

const STATUS_CFG: Record<string, { label: string; cls: string; dot: string }> = {
  pending: { label: '待审批', cls: 'bg-amber-50 text-amber-700 border-amber-200', dot: 'bg-amber-400' },
  approved: { label: '已通过', cls: 'bg-emerald-50 text-emerald-700 border-emerald-200', dot: 'bg-emerald-500' },
  rejected: { label: '已拒绝', cls: 'bg-red-50 text-red-700 border-red-200', dot: 'bg-red-500' },
}

const TYPE_LABEL: Record<string, string> = {
  HUMAN_CONFIRM: '人工确认',
  BUDGET_APPROVE: '预算审批',
  SUPPLEMENT_COMPLETE: '补充完成',
}

function formatTime(ts: number | null): string {
  if (!ts) return '—'
  const ms = ts > 1e12 ? ts : ts * 1000
  const d = new Date(ms)
  return `${d.getMonth() + 1}/${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

function ApprovalCard({ card }: { card: ApprovalCardSummary }) {
  const cfg = STATUS_CFG[card.status] ?? STATUS_CFG.pending
  const approverNames = card.approverNames.length > 0 ? card.approverNames : card.approverIds

  return (
    <div className="rounded-xl border border-slate-200 bg-white shadow-sm overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <span className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-0.5 text-xs font-medium ${cfg.cls}`}>
            <span className={`h-1.5 w-1.5 rounded-full ${cfg.dot}`} />
            {cfg.label}
          </span>
          <span className="text-xs text-slate-400">
            {TYPE_LABEL[card.approvalType ?? ''] ?? card.approvalType ?? '审批'}
          </span>
        </div>
        <Link
          to={`/approval/${card.id}`}
          className="text-xs font-medium text-blue-600 hover:text-blue-800 transition-colors"
        >
          前往审批 →
        </Link>
      </div>

      {/* Body */}
      <div className="px-4 py-3 space-y-2">
        {/* Approvers */}
        <div className="flex items-center gap-2 pt-1">
          <span className="text-xs text-slate-400">审批人:</span>
          <div className="flex flex-wrap gap-1">
            {approverNames.map((name, i) => {
              const isApproved = card.approvedBy.includes(card.approverIds[i] ?? name)
              const isRejected = card.rejectedBy.includes(card.approverIds[i] ?? name)
              return (
                <span
                  key={i}
                  className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs ${
                    isApproved
                      ? 'bg-emerald-50 text-emerald-700'
                      : isRejected
                        ? 'bg-red-50 text-red-700'
                        : 'bg-slate-100 text-slate-600'
                  }`}
                >
                  {isApproved && '✓'}
                  {isRejected && '✕'}
                  {name}
                </span>
              )
            })}
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between pt-1 text-xs text-slate-400">
          <span>节点: {card.nodeId}</span>
          <span>{formatTime(card.createdAt)}</span>
        </div>
      </div>
    </div>
  )
}

export default function ApprovalPanel({ flowId }: { flowId: string }) {
  const { data, isLoading, isError } = useFlowApprovals(flowId)
  const items = data?.items ?? []
  const pendingCount = items.filter((c) => c.status === 'pending').length

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-slate-400">
        <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-200 border-t-blue-500" />
        加载审批信息...
      </div>
    )
  }

  if (isError) {
    return null
  }

  if (items.length === 0) {
    return null
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <h3 className="text-sm font-semibold text-slate-700">审批</h3>
        {pendingCount > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full bg-amber-50 px-2 py-0.5 text-xs font-medium text-amber-700 border border-amber-200">
            <span className="h-1.5 w-1.5 rounded-full bg-amber-400 animate-pulse" />
            {pendingCount} 项待处理
          </span>
        )}
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {items.map((card) => (
          <ApprovalCard key={card.id} card={card} />
        ))}
      </div>
    </div>
  )
}
