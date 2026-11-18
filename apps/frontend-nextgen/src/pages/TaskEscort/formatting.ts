export function formatDuration(seconds: number | null): string {
  if (seconds === null || !Number.isFinite(seconds)) return '—';
  const safe = Math.max(0, Math.round(seconds));
  if (safe < 60) return `${safe}秒`;
  const minutes = Math.floor(safe / 60);
  return `${minutes}分 ${String(safe % 60).padStart(2, '0')}秒`;
}

export function formatStartedAt(value: string | null): string {
  return value ? value.replace('T', ' ').slice(0, 19) : '—';
}
