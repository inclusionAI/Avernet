import { estimateNextRun, type RoutineSchedule } from '@/services/botScheduledTasks/nextRun';
import { useEffect, useMemo, useState } from 'react';

export function useRoutineClock() {
  const [now, setNow] = useState(Date.now);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const tick = () => {
      clearTimeout(timer);
      const current = Date.now();
      setNow(current);
      timer = setTimeout(tick, 60000 - (current % 60000) + 25);
    };
    tick();
    window.addEventListener('focus', tick);
    return () => {
      clearTimeout(timer);
      window.removeEventListener('focus', tick);
    };
  }, []);
  return now;
}

export function RoutineNextRun({ task, now }: { task: RoutineSchedule; now: number }) {
  const { cron, timezone, enabled } = task;
  const next = useMemo(() => estimateNextRun({ cron, timezone, enabled }, now), [cron, timezone, enabled, now]);
  if (!task.enabled) return <span className="text-muted-foreground">已停用</span>;
  if (!next)
    return (
      <span className="text-muted-foreground" title="缺少有效的五段 Cron 或时区，无法估算">
        无法估算
      </span>
    );
  return (
    <time dateTime={next} title={`按 Cron 和 ${task.timezone} 估算，实际执行以运行环境为准`}>
      {new Intl.DateTimeFormat('zh-CN', {
        timeZone: task.timezone,
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      }).format(new Date(next))}
    </time>
  );
}
