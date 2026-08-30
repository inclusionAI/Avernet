import { create } from 'zustand';
import type { TaskRecord } from '@/domain/tasks/models';

/**
 * 任务执行 Loop 同步 Store（仅同步 setter/reset，不含异步动作；编排由 useTaskExecution 负责）。
 * 守卫：Store ≤ 150 行。
 */
export interface TaskStoreState {
  submitting: boolean;
  lastTaskId: string | null;
  lastTaskRecord: TaskRecord | null;
  error: string | null;
  /** UI 偏好：上次选的 panel tab / progress view，可持久化。 */
  preferredTab: 'info' | 'artifacts' | 'progress';
  preferredView: 'node' | 'dag';
  setSubmitting: (v: boolean) => void;
  setLastTask: (record: TaskRecord) => void;
  setError: (msg: string | null) => void;
  setPreferredTab: (t: 'info' | 'artifacts' | 'progress') => void;
  setPreferredView: (v: 'node' | 'dag') => void;
  reset: () => void;
}

const initial = {
  submitting: false,
  lastTaskId: null,
  lastTaskRecord: null,
  error: null,
  preferredTab: 'progress' as const,
  preferredView: 'node' as const,
};

export const useTaskStore = create<TaskStoreState>((set) => ({
  ...initial,
  setSubmitting: (submitting) => set({ submitting }),
  setLastTask: (record) => set({ lastTaskRecord: record, lastTaskId: record.task_id, error: null }),
  setError: (error) => set({ error }),
  setPreferredTab: (preferredTab) => set({ preferredTab }),
  setPreferredView: (preferredView) => set({ preferredView }),
  reset: () => set(initial),
}));
