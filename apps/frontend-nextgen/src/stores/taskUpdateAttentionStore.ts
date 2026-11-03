import { create } from 'zustand';

/**
 * 非焦点任务进展提醒状态。
 *
 * 焦点任务机制要求其它任务进展不抢焦点，只在切换器上保留“待查看”徽标；
 * 独立成 store 可让徽标在 TaskPanel/切换条重挂后仍然存在。
 */
export interface TaskUpdateAttentionState {
  attentionByTask: Record<string, true>;
  markTaskUpdates: (taskIds: string[]) => void;
  clearTaskUpdate: (taskId: string) => void;
  resetAll: () => void;
}

const initial = {
  attentionByTask: {} as Record<string, true>,
};

export const useTaskUpdateAttentionStore = create<TaskUpdateAttentionState>((set) => ({
  ...initial,
  markTaskUpdates: (taskIds) =>
    set((state) => {
      const attentionByTask = { ...state.attentionByTask };
      for (const taskId of taskIds) attentionByTask[taskId] = true;
      return { attentionByTask };
    }),
  clearTaskUpdate: (taskId) =>
    set((state) => {
      if (!state.attentionByTask[taskId]) return state;
      const attentionByTask = { ...state.attentionByTask };
      delete attentionByTask[taskId];
      return { attentionByTask };
    }),
  resetAll: () => set(initial),
}));
