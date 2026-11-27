import type { TodoStatus } from './models';

export const TODO_STATUS_OPTIONS: Array<{ value: 'all' | TodoStatus; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'not_started', label: '未开始' },
  { value: 'in_progress', label: '进行中' },
  { value: 'interrupted', label: '已中断' },
  { value: 'pending_action', label: '待处理' },
  { value: 'completed', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
];

export const TODO_STATUS_LABEL: Record<TodoStatus, string> = {
  not_started: '未开始',
  in_progress: '进行中',
  interrupted: '已中断',
  pending_action: '待处理',
  completed: '已完成',
  cancelled: '已取消',
};

export const TODO_STATUS_CLASS: Record<TodoStatus, string> = {
  not_started: 'border-border bg-muted text-muted-foreground',
  in_progress: 'border-primary/30 bg-primary/10 text-primary',
  interrupted: 'border-destructive/20 bg-destructive/10 text-destructive',
  pending_action: 'border-warning/20 bg-warning/10 text-warning',
  completed: 'border-success/30 bg-success/10 text-success',
  cancelled: 'border-border bg-muted text-muted-foreground',
};

export function canInteractWithTodo(status: TodoStatus): boolean {
  return status === 'pending_action';
}
