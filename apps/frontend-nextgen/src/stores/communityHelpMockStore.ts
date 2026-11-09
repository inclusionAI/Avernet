import { createHelpMockState } from '@/domain/communityHelp/mock';
import type { HelpBoard, HelpTask } from '@/domain/communityHelp/types';
import { create } from 'zustand';

interface CommunityHelpMockState {
  boards: HelpBoard[];
  tasks: HelpTask[];
  selectedBoardId: string;
  selectedTaskId: string | null;
  selectBoard: (id: string) => void;
  selectTask: (id: string | null) => void;
  saveBoard: (board: HelpBoard) => void;
  addTask: (task: HelpTask) => void;
  replaceTask: (task: HelpTask) => void;
  reset: () => void;
}

/** Mock 工作台独立内存状态，不持久化、不与真实社区/任务 Store 混用。 */
export const useCommunityHelpMockStore = create<CommunityHelpMockState>((set) => ({
  ...createHelpMockState(),
  selectedTaskId: null,
  selectBoard: (selectedBoardId) => set({ selectedBoardId, selectedTaskId: null }),
  selectTask: (selectedTaskId) => set({ selectedTaskId }),
  saveBoard: (board) =>
    set((state) => ({
      boards: state.boards.some((item) => item.id === board.id)
        ? state.boards.map((item) => (item.id === board.id ? board : item))
        : [...state.boards, board],
      selectedBoardId: board.id,
      selectedTaskId: null,
    })),
  addTask: (task) => set((state) => ({ tasks: [task, ...state.tasks], selectedTaskId: task.id })),
  replaceTask: (task) => set((state) => ({ tasks: state.tasks.map((item) => (item.id === task.id ? task : item)) })),
  reset: () => set({ ...createHelpMockState(), selectedTaskId: null }),
}));
