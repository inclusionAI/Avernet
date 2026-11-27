import type { TodoStatus } from '@/domain/todos/models';
import {
  TODO_STATUS_CLASS,
  TODO_STATUS_LABEL,
  TODO_STATUS_OPTIONS,
  canInteractWithTodo,
} from '@/domain/todos/status';

const statuses: TodoStatus[] = [
  'not_started',
  'in_progress',
  'interrupted',
  'pending_action',
  'completed',
  'cancelled',
];

describe('Todo status presentation contract', () => {
  it('exposes every backend status once with the agreed label', () => {
    expect(TODO_STATUS_OPTIONS).toEqual([
      { value: 'all', label: '全部' },
      { value: 'not_started', label: '未开始' },
      { value: 'in_progress', label: '进行中' },
      { value: 'interrupted', label: '已中断' },
      { value: 'pending_action', label: '待处理' },
      { value: 'completed', label: '已完成' },
      { value: 'cancelled', label: '已取消' },
    ]);
    expect(TODO_STATUS_LABEL).toEqual({
      not_started: '未开始',
      in_progress: '进行中',
      interrupted: '已中断',
      pending_action: '待处理',
      completed: '已完成',
      cancelled: '已取消',
    });
    expect(Object.keys(TODO_STATUS_CLASS)).toEqual(statuses);
    expect(statuses.every((status) => TODO_STATUS_CLASS[status].length > 0)).toBe(true);
  });

  it('allows interaction only when the backend is waiting for human action', () => {
    expect(statuses.filter(canInteractWithTodo)).toEqual(['pending_action']);
  });
});
