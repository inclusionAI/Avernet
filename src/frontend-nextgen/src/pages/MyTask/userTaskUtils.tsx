import { Badge } from '@/components/ui/Badge';
import { normalizeTaskStatus } from '@/domain/tasks/status';

export const userTabOptions = [
  { label: '全部', value: 'all' },
  { label: '进行中', value: 'progress' },
  { label: '已完成', value: 'done' },
  { label: '失败', value: 'failed' },
  { label: '已取消', value: 'cancelled' },
] as const;

export type UserTabFilter = (typeof userTabOptions)[number]['value'];

const statusLabelMap: Record<string, string> = {
  DRAFTING: '待定义',
  DEFINED: '已定义',
  EXECUTING: '执行中',
  REVIEWING: '待验收',
  DONE: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
  PENDING: '待执行',
  PLANNING: '规划中',
  RUNNING: '运行中',
  HUNG: '挂起',
  SKIPPED: '已跳过',
};

const statusToneMap: Record<string, 'neutral' | 'primary' | 'success' | 'warning' | 'error'> = {
  DRAFTING: 'neutral',
  DEFINED: 'neutral',
  EXECUTING: 'primary',
  REVIEWING: 'warning',
  DONE: 'success',
  FAILED: 'error',
  CANCELLED: 'neutral',
  PENDING: 'warning',
  PLANNING: 'primary',
  RUNNING: 'primary',
  HUNG: 'warning',
  SKIPPED: 'neutral',
};

const taskTypeLabelMap: Record<string, string> = {
  yaml: 'YAML 任务',
  workflow: '工作流任务',
  dynamic: '动态任务',
};

const sourceTypeLabelMap: Record<string, string> = {
  bot: '单 Bot 对话',
  coop_group: '协作群',
  api: 'API 发起',
};

export function formatDateTime(value?: string | null): string {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hours = String(date.getHours()).padStart(2, '0');
  const minutes = String(date.getMinutes()).padStart(2, '0');
  const seconds = String(date.getSeconds()).padStart(2, '0');
  return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
}

export function formatDuration(startTime?: string | null, finishTime?: string | null): string {
  if (!startTime) return '—';
  const start = new Date(startTime).getTime();
  const end = finishTime ? new Date(finishTime).getTime() : Date.now();
  if (Number.isNaN(start) || Number.isNaN(end) || end < start) return '—';
  const minutes = Math.max(1, Math.round((end - start) / 60_000));
  if (minutes < 60) return `${minutes} 分钟`;
  return `${Math.floor(minutes / 60)} 小时 ${minutes % 60} 分钟`;
}

export function matchUserTaskScope(status: string, scope: UserTabFilter): boolean {
  const normalizedStatus = normalizeTaskStatus(status);
  if (scope === 'all') return true;
  if (scope === 'progress') return ['DRAFTING', 'DEFINED', 'EXECUTING', 'REVIEWING'].includes(normalizedStatus);
  if (scope === 'done') return normalizedStatus === 'DONE';
  if (scope === 'failed') return normalizedStatus === 'FAILED';
  if (scope === 'cancelled') return normalizedStatus === 'CANCELLED';
  return true;
}

export function taskStatusBadge(status: string) {
  const normalizedStatus = normalizeTaskStatus(status);
  return (
    <Badge tone={statusToneMap[normalizedStatus] ?? 'neutral'}>
      {statusLabelMap[normalizedStatus] ?? normalizedStatus}
    </Badge>
  );
}

export function taskTypeBadge(type: string) {
  return <Badge tone="primary">{taskTypeLabelMap[type] ?? type}</Badge>;
}

export function sourceTypeBadge(sourceType: string) {
  return <Badge tone="neutral">{sourceTypeLabelMap[sourceType] ?? sourceType}</Badge>;
}

export function getUserTaskTitle(record: any): string {
  return record.task_spec?.metadata?.title || record.task_id;
}

export function getUserTaskDescription(record: any): string {
  return record.task_spec?.metadata?.instruction || record.task_spec?.goal?.objective || '—';
}
