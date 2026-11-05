import { create } from 'zustand';

/**
 * TaskPanel 会话关联状态。
 *
 * by_session 返回轻量 task ref，TaskPanel 的标题/状态仍来自 task list。
 * 这个 store 只保存当前会话的关联 task id 集合，让同会话下拉条能展示
 * 与 run_info.session_id 关联、但 execution_config.main_session_id 不同的任务。
 */
export interface TaskSessionAssociationState {
  activeSessionId: string | null;
  activeSessionTaskIds: string[];
  setActiveSession: (sessionId: string) => void;
  setActiveSessionTaskIds: (sessionId: string, taskIds: string[]) => void;
  reset: () => void;
}

const initial = {
  activeSessionId: null as string | null,
  activeSessionTaskIds: [] as string[],
};

export const useTaskSessionAssociationStore = create<TaskSessionAssociationState>((set) => ({
  ...initial,
  setActiveSession: (sessionId) =>
    set((state) =>
      state.activeSessionId === sessionId
        ? state
        : { activeSessionId: sessionId, activeSessionTaskIds: [] },
    ),
  setActiveSessionTaskIds: (sessionId, taskIds) =>
    set((state) =>
      state.activeSessionId === sessionId
        ? { activeSessionTaskIds: taskIds.filter((id) => Boolean(id?.trim())) }
        : state,
    ),
  reset: () => set(initial),
}));

export default useTaskSessionAssociationStore;
