import { runStatusLabel } from '@/services/botScheduledTasks/results';
import { cn } from '@/utils/cn';

// 状态徽章族：颜色只表达调度/执行状态，不占用品牌蓝；
// 「状态未知」刻意用警示色，提醒用户刷新确认而不是静默蒙混。
const badgeBase = 'inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium';

const enableTones = {
  on: 'bg-success/10 text-success',
  off: 'bg-muted text-muted-foreground',
  unknown: 'bg-warning/10 text-warning',
} as const;

export function EnableBadge({ enabled, className }: { enabled?: boolean | null; className?: string }) {
  if (enabled !== true && enabled !== false) {
    return (
      <span className={cn(badgeBase, enableTones.unknown, className)}>
        <span aria-hidden className="size-1.5 rounded-full bg-warning" />
        状态未知
      </span>
    );
  }
  const on = enabled === true;
  return (
    <span className={cn(badgeBase, on ? enableTones.on : enableTones.off, className)}>
      <span aria-hidden className={cn('size-1.5 rounded-full', on ? 'bg-success' : 'bg-muted-foreground/60')} />
      {on ? '已启用' : '已停用'}
    </span>
  );
}

const runTones: Record<string, string> = {
  ok: 'bg-success/10 text-success',
  error: 'bg-destructive/10 text-destructive',
  skipped: 'bg-warning/10 text-warning',
};

export function RunStatusBadge({ status, className }: { status: string; className?: string }) {
  const tone = runTones[status] || 'bg-muted text-muted-foreground';
  const color = status === 'ok' ? 'bg-success' : status === 'error' ? 'bg-destructive' : status === 'skipped' ? 'bg-warning' : 'bg-muted-foreground/60';
  return (
    <span className={cn(badgeBase, tone, className)}>
      <span aria-hidden className={cn('size-1.5 rounded-full', color)} />
      {runStatusLabel(status)}
    </span>
  );
}
