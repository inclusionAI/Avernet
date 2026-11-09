import { create } from 'zustand';
import type { TaskLinkedSessionTarget } from './taskSidePanelStoreTypes';

export type { TaskLinkedSessionTarget, TaskNavTarget } from './taskSidePanelStoreTypes';

/** Host-only state. Task selection/navigation belongs exclusively to the shared controller. */
export interface TaskSidePanelState {
  /** Output-only projection of the shared snapshot; never a host task-selection command. */
  readonly activeTaskId: string | null;
  /** One-shot explicit SDK open intent; submitting a task is not evidence it has been displayed. */
  taskPanelOpenIntentTaskId: string | null;
  /** Host ChatArea rendering container, independent of the controller's task navigation. */
  linkedSession: TaskLinkedSessionTarget | null;
  markTaskPanelOpenIntent: (taskId: string) => void;
  consumeTaskPanelOpenIntent: (taskId: string) => boolean;
  openLinkedSession: (target: TaskLinkedSessionTarget) => void;
  closeLinkedSession: () => void;
  resetAll: () => void;
}

const initial = {
  activeTaskId: null,
  taskPanelOpenIntentTaskId: null,
  linkedSession: null as TaskLinkedSessionTarget | null,
};

export const useTaskSidePanelStore = create<TaskSidePanelState>((set, get) => ({
  ...initial,
  markTaskPanelOpenIntent: (taskId) => set({ taskPanelOpenIntentTaskId: taskId }),
  consumeTaskPanelOpenIntent: (taskId) => {
    const matched = get().taskPanelOpenIntentTaskId === taskId;
    if (matched) set({ taskPanelOpenIntentTaskId: null });
    return matched;
  },
  openLinkedSession: (target) => set({ linkedSession: target }),
  closeLinkedSession: () => set({ linkedSession: null }),
  resetAll: () => set(initial),
}));
