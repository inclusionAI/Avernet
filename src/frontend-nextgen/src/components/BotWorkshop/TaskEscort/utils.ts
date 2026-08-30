export function formatDuration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return '—';
  if (ms < 1000) return `${ms}ms`;
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`;
  const mins = Math.floor(ms / 60000);
  const secs = Math.round((ms % 60000) / 1000);
  return `${mins}m ${secs}s`;
}

export function formatTime(ts: number | null | undefined): string {
  if (ts === null || ts === undefined) return '—';
  const d = new Date(ts);
  return d.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    second: '2-digit',
  });
}

export function formatTimeShort(ts: number | null | undefined): string {
  if (ts === null || ts === undefined) return '—';
  const d = new Date(ts);
  const now = new Date();
  const diffMs = now.getTime() - d.getTime();
  if (diffMs < 60000) return '刚刚';
  if (diffMs < 3600000) return `${Math.floor(diffMs / 60000)} 分钟前`;
  if (diffMs < 86400000) return `${Math.floor(diffMs / 3600000)} 小时前`;
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
}

export const STATUS_TONE: Record<string, 'success' | 'error' | 'warning' | 'neutral'> = {
  succeeded: 'success',
  failed: 'error',
  running: 'warning',
  waiting: 'warning',
  pending: 'neutral',
  blocked: 'error',
  skipped: 'neutral',
};

export const STATUS_LABEL: Record<string, string> = {
  succeeded: '成功',
  failed: '失败',
  running: '运行中',
  waiting: '等待中',
  pending: '待执行',
  blocked: '阻塞',
  skipped: '跳过',
};
