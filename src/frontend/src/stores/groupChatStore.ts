/**
 * GroupChat Store - 群聊状态管理
 *
 * 遵循三层架构：Store层只管理纯数据状态，不包含API调用和Toast逻辑
 *
 * 注意：driverBot 由 botNetworkStore 管理，不在此存储
 */

import type {
  GroupInfo,
  GroupMember,
  GroupMessage,
} from '@/pages/GroupChat/types';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

/** Bot 信息（用于显示成员名字） */
export interface BotInfo {
  bot_uuid: string;
  bot_name: string | null;
  summary?: string | null;
  domains?: string[];
  skills?: string[];
}

interface GroupChatState {
  // === 群组列表 ===
  groups: GroupInfo[];
  activeGroupId: string;
  isLoadingGroups: boolean;
  hasMoreGroups: boolean;
  isLoadingMoreGroups: boolean;
  groupsTotal: number;

  // === 当前群组详情 ===
  currentGroup: GroupInfo | null;
  isLoadingDetail: boolean;

  // === 消息 ===
  messages: GroupMessage[];
  isLoadingMessages: boolean;
  hasMoreMessages: boolean;
  messageCursor: string | null;

  // === 成员管理 ===
  isAddingMember: boolean;
  isRemovingMember: boolean;

  // === 群组操作 ===
  isCreatingGroup: boolean;
  isUpdatingGroup: boolean;
  isDeletingGroup: boolean;

  // === Bot 信息缓存（bot_uuid -> BotInfo） ===
  botInfoMap: Record<string, BotInfo>;

  // === Actions: 群组列表 ===
  setGroups: (groups: GroupInfo[]) => void;
  appendGroups: (groups: GroupInfo[]) => void;
  addGroup: (group: GroupInfo) => void;
  updateGroup: (groupId: string, updates: Partial<GroupInfo>) => void;
  removeGroup: (groupId: string) => void;
  setActiveGroupId: (id: string) => void;
  setHasMoreGroups: (hasMore: boolean) => void;
  setIsLoadingMoreGroups: (loading: boolean) => void;
  setGroupsTotal: (total: number) => void;

  // === Actions: 当前群组 ===
  setCurrentGroup: (group: GroupInfo | null) => void;

  // === Actions: 消息 ===
  setMessages: (messages: GroupMessage[]) => void;
  addMessage: (message: GroupMessage) => void;
  prependMessages: (messages: GroupMessage[]) => void;
  updateMessage: (messageId: string, updates: Partial<GroupMessage>) => void;
  setMessageCursor: (cursor: string | null) => void;
  setHasMoreMessages: (hasMore: boolean) => void;

  // === Actions: 成员 ===
  addMember: (groupId: string, member: GroupMember) => void;
  removeMember: (groupId: string, memberId: string) => void;

  // === Actions: Bot 信息 ===
  setBotInfo: (botUuid: string, info: BotInfo) => void;
  setBotInfoMap: (infoMap: Record<string, BotInfo>) => void;
  getBotInfo: (botUuid: string) => BotInfo | undefined;
  getBotName: (botUuid: string) => string;

  // === Loading Setters ===
  setLoadingGroups: (loading: boolean) => void;
  setLoadingDetail: (loading: boolean) => void;
  setLoadingMessages: (loading: boolean) => void;
  setAddingMember: (adding: boolean) => void;
  setRemovingMember: (removing: boolean) => void;
  setCreatingGroup: (creating: boolean) => void;
  setUpdatingGroup: (updating: boolean) => void;
  setDeletingGroup: (deleting: boolean) => void;

  // === Computed Getters ===
  getGroupById: (id: string) => GroupInfo | undefined;
  getActiveGroup: () => GroupInfo | undefined;

  // === Reset ===
  reset: () => void;
  resetMessages: () => void;
}

const initialState = {
  groups: [] as GroupInfo[],
  activeGroupId: '',
  isLoadingGroups: false,
  hasMoreGroups: false,
  isLoadingMoreGroups: false,
  groupsTotal: 0,

  currentGroup: null as GroupInfo | null,
  isLoadingDetail: false,

  messages: [] as GroupMessage[],
  isLoadingMessages: false,
  hasMoreMessages: false,
  messageCursor: null as string | null,

  isAddingMember: false,
  isRemovingMember: false,

  isCreatingGroup: false,
  isUpdatingGroup: false,
  isDeletingGroup: false,

  botInfoMap: {} as Record<string, BotInfo>,
};

export const useGroupChatStore = create<GroupChatState>()(
  devtools(
    (set, get) => ({
      ...initialState,

      // === Actions: 群组列表 ===
      setGroups: (groups) => set({ groups }, false, 'setGroups'),

      appendGroups: (newGroups) =>
        set(
          (state) => ({
            groups: [...state.groups, ...newGroups],
          }),
          false,
          'appendGroups',
        ),

      addGroup: (group) =>
        set(
          (state) => ({
            groups: [group, ...state.groups],
          }),
          false,
          'addGroup',
        ),

      updateGroup: (groupId, updates) =>
        set(
          (state) => ({
            groups: state.groups.map((group) =>
              group.id === groupId ? { ...group, ...updates } : group,
            ),
            currentGroup:
              state.currentGroup?.id === groupId
                ? { ...state.currentGroup, ...updates }
                : state.currentGroup,
          }),
          false,
          'updateGroup',
        ),

      removeGroup: (groupId) =>
        set(
          (state) => ({
            groups: state.groups.filter((group) => group.id !== groupId),
            activeGroupId:
              state.activeGroupId === groupId ? '' : state.activeGroupId,
            currentGroup:
              state.currentGroup?.id === groupId ? null : state.currentGroup,
          }),
          false,
          'removeGroup',
        ),

      setActiveGroupId: (id) =>
        set({ activeGroupId: id }, false, 'setActiveGroupId'),

      // === Actions: 当前群组 ===
      setCurrentGroup: (group) =>
        set({ currentGroup: group }, false, 'setCurrentGroup'),

      // === Actions: 消息 ===
      setMessages: (messages) => set({ messages }, false, 'setMessages'),

      addMessage: (message) =>
        set(
          (state) => ({
            messages: [...state.messages, message],
          }),
          false,
          'addMessage',
        ),

      prependMessages: (messages) =>
        set(
          (state) => ({
            messages: [...messages, ...state.messages],
          }),
          false,
          'prependMessages',
        ),

      updateMessage: (messageId, updates) =>
        set(
          (state) => ({
            messages: state.messages.map((msg) =>
              msg.id === messageId ? { ...msg, ...updates } : msg,
            ),
          }),
          false,
          'updateMessage',
        ),

      setMessageCursor: (cursor) =>
        set({ messageCursor: cursor }, false, 'setMessageCursor'),

      setHasMoreMessages: (hasMore) =>
        set({ hasMoreMessages: hasMore }, false, 'setHasMoreMessages'),

      // === Actions: 成员 ===
      addMember: (groupId, member) =>
        set(
          (state) => ({
            groups: state.groups.map((group) =>
              group.id === groupId
                ? { ...group, participants: [...group.participants, member] }
                : group,
            ),
            currentGroup:
              state.currentGroup?.id === groupId
                ? {
                    ...state.currentGroup,
                    participants: [...state.currentGroup.participants, member],
                  }
                : state.currentGroup,
          }),
          false,
          'addMember',
        ),

      removeMember: (groupId, memberId) =>
        set(
          (state) => ({
            groups: state.groups.map((group) =>
              group.id === groupId
                ? {
                    ...group,
                    participants: group.participants.filter(
                      (p) => p.id !== memberId,
                    ),
                  }
                : group,
            ),
            currentGroup:
              state.currentGroup?.id === groupId
                ? {
                    ...state.currentGroup,
                    participants: state.currentGroup.participants.filter(
                      (p) => p.id !== memberId,
                    ),
                  }
                : state.currentGroup,
          }),
          false,
          'removeMember',
        ),

      // === Actions: Bot 信息 ===
      setBotInfo: (botUuid, info) =>
        set(
          (state) => ({
            botInfoMap: { ...state.botInfoMap, [botUuid]: info },
          }),
          false,
          'setBotInfo',
        ),

      setBotInfoMap: (infoMap) =>
        set({ botInfoMap: infoMap }, false, 'setBotInfoMap'),

      getBotInfo: (botUuid) => {
        return get().botInfoMap[botUuid];
      },

      getBotName: (botUuid) => {
        const info = get().botInfoMap[botUuid];
        return info?.bot_name;
      },

      // === Loading Setters ===
      setLoadingGroups: (loading) =>
        set({ isLoadingGroups: loading }, false, 'setLoadingGroups'),
      setHasMoreGroups: (hasMore) =>
        set({ hasMoreGroups: hasMore }, false, 'setHasMoreGroups'),
      setIsLoadingMoreGroups: (loading) =>
        set({ isLoadingMoreGroups: loading }, false, 'setIsLoadingMoreGroups'),
      setGroupsTotal: (total) =>
        set({ groupsTotal: total }, false, 'setGroupsTotal'),
      setLoadingDetail: (loading) =>
        set({ isLoadingDetail: loading }, false, 'setLoadingDetail'),
      setLoadingMessages: (loading) =>
        set({ isLoadingMessages: loading }, false, 'setLoadingMessages'),
      setAddingMember: (adding) =>
        set({ isAddingMember: adding }, false, 'setAddingMember'),
      setRemovingMember: (removing) =>
        set({ isRemovingMember: removing }, false, 'setRemovingMember'),
      setCreatingGroup: (creating) =>
        set({ isCreatingGroup: creating }, false, 'setCreatingGroup'),
      setUpdatingGroup: (updating) =>
        set({ isUpdatingGroup: updating }, false, 'setUpdatingGroup'),
      setDeletingGroup: (deleting) =>
        set({ isDeletingGroup: deleting }, false, 'setDeletingGroup'),

      // === Computed Getters ===
      getGroupById: (id) => {
        return get().groups.find((group) => group.id === id);
      },

      getActiveGroup: () => {
        const { groups, activeGroupId } = get();
        return groups.find((group) => group.id === activeGroupId);
      },

      // === Reset ===
      reset: () => set(initialState, false, 'reset'),

      resetMessages: () =>
        set(
          {
            messages: [],
            hasMoreMessages: false,
            messageCursor: null,
          },
          false,
          'resetMessages',
        ),
    }),
    { name: 'GroupChatStore' },
  ),
);
