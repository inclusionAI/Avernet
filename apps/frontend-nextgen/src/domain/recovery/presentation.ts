import type { RecoveryEffect, RecoveryListItem, RecoveryStatus, RecoveryTask } from './types';

export const recoveryStatusLabels: Record<RecoveryStatus, string> = {
  running: '执行中',
  waiting_owner: '等待用户处理',
  waiting_effect: '等待效果核验',
  completed: '已完成',
  failed: '失败',
  canceled: '已停止',
};

export const recoveryStatusTones = {
  running: 'primary',
  waiting_owner: 'warning',
  waiting_effect: 'purple',
  completed: 'success',
  failed: 'error',
  canceled: 'neutral',
} as const;

export const recoveryEffectLabels: Record<RecoveryEffect, string> = {
  recovered: '已恢复',
  not_recovered: '未恢复',
  unknown: '尚未确认',
  not_intervened: '未干预',
};

export const recoveryEffectTones = {
  recovered: 'success',
  not_recovered: 'error',
  unknown: 'warning',
  not_intervened: 'neutral',
} as const;

export function recoveryListStatus(task: RecoveryListItem) {
  if (task.completionReason === 'user_continued') return { label: '已让路', tone: 'purple' as const };
  return { label: recoveryStatusLabels[task.status], tone: recoveryStatusTones[task.status] };
}

export function recoveryStatus(task: RecoveryTask) {
  if (task.simulation) return { label: '离线分析', tone: 'neutral' as const };
  if (task.completionReason === 'user_continued') return { label: '已让路', tone: 'purple' as const };
  if (task.completionReason === 'no_recovery_needed') return { label: '无需自愈', tone: 'neutral' as const };
  if (task.status === 'waiting_owner') return { label: '待你处理', tone: 'warning' as const };
  if (task.status === 'canceled') return { label: '已停止', tone: 'neutral' as const };
  if (task.judgment?.effect === 'recovered') return { label: '已恢复', tone: 'success' as const };
  if (task.status === 'waiting_effect') return { label: '等待核验', tone: 'purple' as const };
  if (task.status === 'running') return { label: '处理中', tone: 'primary' as const };
  if (task.status === 'failed') return { label: '自愈未完成', tone: 'error' as const };
  return { label: '结果待确认', tone: 'neutral' as const };
}

const beijingTime = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  second: '2-digit',
  hourCycle: 'h23',
});

export function recoveryTime(value?: string | number | null) {
  if (value == null || value === '') return '—';
  const time = new Date(value);
  return Number.isNaN(time.getTime()) ? '—' : beijingTime.format(time);
}
