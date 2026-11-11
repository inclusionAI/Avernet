import type { TodoStatus } from './models';

export const TODO_STATUS_OPTIONS: Array<{ value: 'all' | TodoStatus; label: string }> = [
  { value: 'all', label: '全部' },
  { value: 'draft', label: '草稿' },
  { value: 'in_progress', label: '执行中' },
  { value: 'pending_acceptance', label: '待验收' },
  { value: 'completed', label: '已完成' },
];

export const TODO_STATUS_LABEL: Record<TodoStatus, string> = {
  draft: '草稿',
  in_progress: '执行中',
  pending_acceptance: '待验收',
  completed: '已完成',
};

export const TODO_STATUS_CLASS: Record<TodoStatus, string> = {
  draft: 'border-border bg-muted text-muted-foreground',
  in_progress: 'border-primary/30 bg-primary/10 text-primary',
  pending_acceptance: 'border-warning/30 bg-warning/10 text-warning-foreground',
  completed: 'border-success/30 bg-success/10 text-success',
};

export function canInteractWithTodo(status: TodoStatus): boolean {
  return status === 'pending_acceptance';
}
