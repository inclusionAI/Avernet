import type { Step, Task } from '@/domain/evolution/types';
import { timestampMs } from '@/domain/evolution/types';

const names: Record<string, string> = {
  skill_init: '执行准备',
  diagnose: 'Bot 诊断',
  plan: '目标规划',
  optimize: '优化',
  bench: '评估验证',
  baseline: '基线评估',
  pack: '创建 Pack',
  apply: '变更应用',
  pack_restore: '恢复 Pack',
  runtime_cleanup: '会话清理',
};
export const stepName = (type: string) => names[type] ?? type;
export const finishedStep = (step: Step) => ['succeeded', 'completed'].includes(step.status);
export function progressStages(steps: Step[]) {
  const stages = new Map<string, Step>();
  for (const step of steps) stages.set(step.stepType, step);
  return [...stages.values()];
}
export function displayValue(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value))
    return new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 }).format(value);
  return typeof value === 'string' ? value : '—';
}

export function taskTimes(task: Task) {
  const starts = (task.steps ?? [])
    .map((s) => timestampMs(s.startedAt))
    .filter((v): v is number => typeof v === 'number' && Number.isFinite(v));
  const lastEnd = timestampMs(task.steps?.at(-1)?.completedAt);
  const terminal = ['completed', 'succeeded', 'failed', 'canceled', 'cancelled'].includes(task.status);
  return {
    start: timestampMs(task.started_at) ?? (starts.length ? Math.min(...starts) : null),
    end:
      timestampMs(task.completed_at) ??
      (terminal && typeof lastEnd === 'number' && Number.isFinite(lastEnd) ? lastEnd : null),
  };
}
export function elapsed(start?: number | null, end?: number | null) {
  if (start === null || start === undefined || end === null || end === undefined || end < start) return '—';
  const seconds = Math.floor((end - start) / 1000);
  return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
}
