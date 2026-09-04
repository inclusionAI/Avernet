import type { NodeStatus } from '../types'

const STATUS_STYLES: Record<NodeStatus, string> = {
  succeeded: 'bg-status-succeeded/15 text-status-succeeded',
  failed: 'bg-status-failed/15 text-status-failed',
  running: 'bg-status-running/15 text-status-running',
  waiting: 'bg-status-waiting/15 text-status-waiting',
  pending: 'bg-status-pending/15 text-status-pending',
  skipped: 'bg-status-skipped/15 text-status-skipped',
  blocked: 'bg-status-blocked/15 text-status-blocked',
  postActionsRunning: 'bg-status-running/15 text-status-running',
}

const STATUS_LABELS: Record<NodeStatus, string> = {
  succeeded: '已成功',
  failed: '已失败',
  running: '运行中',
  waiting: '等待中',
  pending: '待执行',
  skipped: '已跳过',
  blocked: '已阻塞',
  postActionsRunning: '后置动作',
}

interface StatusBadgeProps {
  status: NodeStatus
  className?: string
}

export default function StatusBadge({ status, className = '' }: StatusBadgeProps) {
  const style = STATUS_STYLES[status] ?? 'bg-gray-100 text-gray-500'
  const label = STATUS_LABELS[status] ?? status

  return (
    <span
      className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${style} ${className}`}
    >
      {label}
    </span>
  )
}