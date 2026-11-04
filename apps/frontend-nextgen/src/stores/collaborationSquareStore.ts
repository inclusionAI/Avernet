import type {
  BotSearchMode,
  PublicBot,
  PublicBotProfile,
  PublicGroup,
  PublicGroupMember,
  PublicGroupMembersDetail,
  PublicTask,
  TaskStatusFilter,
} from '@/domain/collaborationSquare/types';
import { getPublicBotTargetId } from '@/domain/collaborationSquare/types';
import { create } from 'zustand';

export interface CollaborationSquareState {
  bots: PublicBot[];
  groups: PublicGroup[];
  botProfile: PublicBotProfile | null;
  /** 画像拉取失败的原因文案；非空时由弹窗内展示（不再 toast）。随 botProfile 一同复位。 */
  botProfileError: string | null;
  groupMembers: PublicGroupMember[];
  groupOwnerUserName: string | null;
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
  setBotProfileError: (error: string | null) => void;
  setGroupMembers: (members: PublicGroupMember[]) => void;
  setGroupMembersDetail: (detail: PublicGroupMembersDetail) => void;
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

/** 按 id 去重地把分页结果追加到既有列表（bots/groups/tasks 的 append 复用）。 */
const appendUniqueById = <T extends { id: string }>(list: T[], items: T[]) => {
  const existingIds = new Set(list.map((item) => item.id));
  return [...list, ...items.filter((item) => !existingIds.has(item.id))];
};

const initialState = {
  bots: [],
  groups: [],
  botProfile: null,
  botProfileError: null,
  groupMembers: [],
  groupOwnerUserName: null,
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
  appendBots: (bots) => set((state) => ({ bots: appendUniqueById(state.bots, bots) })),
  setGroups: (groups) => set({ groups }),
  appendGroups: (groups) => set((state) => ({ groups: appendUniqueById(state.groups, groups) })),
  setBotProfile: (botProfile) => set({ botProfile, botProfileError: null }),
  setBotProfileError: (botProfileError) => set({ botProfileError }),
  setGroupMembers: (groupMembers) => set({ groupMembers }),
  setGroupMembersDetail: ({ members, ownerUserName }) =>
    set({ groupMembers: members, groupOwnerUserName: ownerUserName }),
  setLoading: (loading) => set({ loading }),
  setDetailLoading: (detailLoading) => set({ detailLoading }),
  setError: (error) => set({ error }),
  setQuery: (resource, query) => set(resource === 'bot' ? { botQuery: query } : { groupQuery: query }),
  setBotSearchMode: (botSearchMode) =>
    set((state) => (state.botSearchMode === botSearchMode ? { botSearchMode } : { botSearchMode, botQuery: '' })),
  setSelectedBotId: (selectedBotId) => set({ selectedBotId, botProfile: null, botProfileError: null }),
  setSelectedGroupId: (selectedGroupId) => set({ selectedGroupId, groupMembers: [], groupOwnerUserName: null }),
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
      botProfileError: state.selectedBotId === id ? null : state.botProfileError,
    })),
  removeGroup: (id) =>
    set((state) => ({
      groups: state.groups.filter((group) => group.id !== id),
      selectedGroupId: state.selectedGroupId === id ? null : state.selectedGroupId,
      groupMembers: state.selectedGroupId === id ? [] : state.groupMembers,
      groupOwnerUserName: state.selectedGroupId === id ? null : state.groupOwnerUserName,
    })),
  setTasks: (tasks) => set({ tasks }),
  appendTasks: (tasks) => set((state) => ({ tasks: appendUniqueById(state.tasks, tasks) })),
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
