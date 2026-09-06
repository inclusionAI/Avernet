/**
 * Shared status constants for the Dashboard page.
 */

export const STATUS_LABELS: Record<string, string> = {
  succeeded: '成功',
  failed: '失败',
  aborted: '中止',
  running: '运行中',
  waiting: '等待中',
  blocked: '阻塞',
  queued: '排队中',
  skipped: '跳过',
}

export const STATUS_COLORS: Record<string, string> = {
  succeeded: '#10B981',
  failed: '#EF4444',
  aborted: '#6B7280',
  running: '#F59E0B',
  waiting: '#9CA3AF',
  blocked: '#3B82F6',
  queued: '#8B5CF6',
  skipped: '#D1D5DB',
}