import { create } from 'zustand';

export interface ScheduledTasksState {
  keyword: string;
  setKeyword: (keyword: string) => void;
  reset: () => void;
}

// scheduledTasks Store 只保存同步页面状态，不做 async 或接口调用。
export const useScheduledTasksStore = create<ScheduledTasksState>((set) => ({
  keyword: '',
  setKeyword: (keyword) => set({ keyword }),
  reset: () => set({ keyword: '' }),
}));
