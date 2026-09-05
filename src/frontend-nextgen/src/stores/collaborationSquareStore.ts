import type {
  BotSearchMode,
  PublicBot,
  PublicBotProfile,
  PublicGroup,
  PublicGroupMember,
  PublicTask,
  TaskStatusFilter,
} from '@/domain/collaborationSquare/types';
import { getPublicBotTargetId } from '@/domain/collaborationSquare/types';
import { create } from 'zustand';

export interface CollaborationSquareState {
  bots: PublicBot[];
  groups: PublicGroup[];
  botProfile: PublicBotProfile | null;
  groupMembers: PublicGroupMember[];
  loading: boolean;
  detailLoading: boolean;
  error: string | null;
  botQuery: string;
  groupQuery: string;
  botSearchMode: BotSearchMode;
  selectedBotId: string | null;
  selectedGroupId: string | null;
  busyKeys: string[];
  tasks: PublicTask[];
  taskQuery: string;
  taskStatusFilter: TaskStatusFilter;
  selectedTaskId: string | null;
  taskDetail: PublicTask | null;
  setBots: (bots: PublicBot[]) => void;
  appendBots: (bots: PublicBot[]) => void;
  setGroups: (groups: PublicGroup[]) => void;
  appendGroups: (groups: PublicGroup[]) => void;
  setBotProfile: (profile: PublicBotProfile | null) => void;
  setGroupMembers: (members: PublicGroupMember[]) => void;
  setLoading: (loading: boolean) => void;
  setDetailLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setQuery: (resource: 'bot' | 'group', query: string) => void;
  setBotSearchMode: (mode: BotSearchMode) => void;
  setSelectedBotId: (id: string | null) => void;
  setSelectedGroupId: (id: string | null) => void;
  setBusy: (key: string, busy: boolean) => void;
  updateBotRelationship: (targetId: string, status: PublicBot['relationshipStatus']) => void;
  removeBot: (id: string) => void;
  removeGroup: (id: string) => void;
  setTasks: (tasks: PublicTask[]) => void;
  appendTasks: (tasks: PublicTask[]) => void;
  setTaskQuery: (query: string) => void;
  setTaskStatusFilter: (filter: TaskStatusFilter) => void;
  setSelectedTaskId: (id: string | null) => void;
  setTaskDetail: (task: PublicTask | null) => void;
  removeTask: (id: string) => void;
  resetTaskFilters: () => void;
  reset: () => void;
}

const initialState = {
  bots: [],
  groups: [],
  botProfile: null,
  groupMembers: [],
  loading: true,
  detailLoading: false,
  error: null,
  botQuery: '',
  groupQuery: '',
  botSearchMode: 'name' as BotSearchMode,
  selectedBotId: null,
  selectedGroupId: null,
  busyKeys: [],
  tasks: [],
  taskQuery: '',
  taskStatusFilter: 'all' as TaskStatusFilter,
  selectedTaskId: null,
  taskDetail: null,
};

export const useCollaborationSquareStore = create<CollaborationSquareState>((set) => ({
  ...initialState,
  setBots: (bots) => set({ bots }),
  appendBots: (bots) =>
    set((state) => {
      const existingIds = new Set(state.bots.map((bot) => bot.id));
      return { bots: [...state.bots, ...bots.filter((bot) => !existingIds.has(bot.id))] };
    }),
  setGroups: (groups) => set({ groups }),
  appendGroups: (groups) =>
    set((state) => {
      const existingIds = new Set(state.groups.map((group) => group.id));
      return { groups: [...state.groups, ...groups.filter((group) => !existingIds.has(group.id))] };
    }),
  setBotProfile: (botProfile) => set({ botProfile }),
  setGroupMembers: (groupMembers) => set({ groupMembers }),
  setLoading: (loading) => set({ loading }),
  setDetailLoading: (detailLoading) => set({ detailLoading }),
  setError: (error) => set({ error }),
  setQuery: (resource, query) => set(resource === 'bot' ? { botQuery: query } : { groupQuery: query }),
  setBotSearchMode: (botSearchMode) =>
    set((state) =>
      state.botSearchMode === botSearchMode ? { botSearchMode } : { botSearchMode, botQuery: '' },
    ),
  setSelectedBotId: (selectedBotId) => set({ selectedBotId, botProfile: null }),
  setSelectedGroupId: (selectedGroupId) => set({ selectedGroupId, groupMembers: [] }),
  setBusy: (key, busy) =>
    set((state) => ({
      busyKeys: busy ? [...new Set([...state.busyKeys, key])] : state.busyKeys.filter((item) => item !== key),
    })),
  updateBotRelationship: (targetId, relationshipStatus) =>
    set((state) => ({
      bots: state.bots.map((bot) => (getPublicBotTargetId(bot) === targetId ? { ...bot, relationshipStatus } : bot)),
    })),
  removeBot: (id) =>
    set((state) => ({
      bots: state.bots.filter((bot) => bot.id !== id),
      selectedBotId: state.selectedBotId === id ? null : state.selectedBotId,
      botProfile: state.botProfile?.id === id ? null : state.botProfile,
    })),
  removeGroup: (id) =>
    set((state) => ({
      groups: state.groups.filter((group) => group.id !== id),
      selectedGroupId: state.selectedGroupId === id ? null : state.selectedGroupId,
      groupMembers: state.selectedGroupId === id ? [] : state.groupMembers,
    })),
  setTasks: (tasks) => set({ tasks }),
  appendTasks: (tasks) =>
    set((state) => {
      const existingIds = new Set(state.tasks.map((task) => task.id));
      return { tasks: [...state.tasks, ...tasks.filter((task) => !existingIds.has(task.id))] };
    }),
  setTaskQuery: (taskQuery) => set({ taskQuery }),
  setTaskStatusFilter: (taskStatusFilter) => set({ taskStatusFilter }),
  setSelectedTaskId: (selectedTaskId) => set({ selectedTaskId, taskDetail: null }),
  setTaskDetail: (taskDetail) => set({ taskDetail }),
  removeTask: (id) =>
    set((state) => ({
      tasks: state.tasks.filter((task) => task.id !== id),
      selectedTaskId: state.selectedTaskId === id ? null : state.selectedTaskId,
      taskDetail: state.taskDetail?.id === id ? null : state.taskDetail,
    })),
  resetTaskFilters: () => set({ taskQuery: '', taskStatusFilter: 'all' }),
  reset: () => set(initialState),
}));
