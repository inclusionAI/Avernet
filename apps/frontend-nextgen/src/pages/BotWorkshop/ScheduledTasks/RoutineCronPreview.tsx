import { estimateNextRun } from '@/services/botScheduledTasks/nextRun';
import { CalendarClock } from 'lucide-react';

/** 提交前就回答“这个表达式下次什么时候触发”，而不是保存后到列表里才发现写错。 */
export function RoutineCronPreview({ cron, timezone, enabled }: { cron: string; timezone: string; enabled: boolean }) {
  const next = estimateNextRun({ cron, timezone, enabled }, Date.now());
  if (!next)
    return (
      <p className="m-0 flex items-center gap-1.5 text-xs text-warning">
        <CalendarClock aria-hidden className="size-3.5" />
        {enabled
          ? '无法解析该表达式，请检查是否为完整的五段 Cron（分钟 小时 日期 月份 星期）'
          : '任务当前处于停用状态，保存后不会自动调度；启用后才会按此频率执行'}
      </p>
    );
  const text = new Intl.DateTimeFormat('zh-CN', {
    timeZone: timezone,
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(next));
  return (
    <p className="m-0 flex items-center gap-1.5 text-xs text-muted-foreground">
      <CalendarClock aria-hidden className="size-3.5" />
      下次执行预计：
      <time dateTime={next} className="font-mono">
        {text}
      </time>
      <span>（{timezone}，实际以运行环境为准）</span>
    </p>
  );
}
