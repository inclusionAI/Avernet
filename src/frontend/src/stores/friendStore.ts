/**
 * Friend Store - 好友关系状态管理
 *
 * 遵循三层架构：Store层只管理纯数据状态，不包含API调用和Toast逻辑
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

// === 类型定义 ===

/** 好友请求状态 */
export type FriendRequestStatus = 'pending' | 'accepted' | 'rejected';

/** 好友请求方向 */
export type FriendRequestDirection = 'received' | 'sent' | 'all';

/** 动态状态 */
export interface DynamicStatus {
  status: 'active' | 'offline';
  last_active_at?: number;
}

/** 好友信息 */
export interface FriendInfo {
  bot_uuid: string;
  bot_name: string;
  summary?: string;
  is_online: boolean;
  dynamic_status?: DynamicStatus;
}

/** 好友请求 */
export interface FriendRequest {
  id: string;
  from_bot: string;
  to_bot: string;
  status: FriendRequestStatus;
  created_at: number;
  updated_at: number;
  // 扩展信息（前端填充）
  from_bot_name?: string;
  to_bot_name?: string;
}

// === Store State ===

interface FriendState {
  // === 好友列表 ===
  friends: FriendInfo[];
  isLoadingFriends: boolean;

  // === 收到的好友请求 ===
  receivedRequests: FriendRequest[];
  // === 发出的好友请求 ===
  sentRequests: FriendRequest[];
  isLoadingRequests: boolean;

  // === 未读请求数量 ===
  unreadRequestCount: number;

  // === 操作状态 ===
  isSendingRequest: boolean;
  isAcceptingRequest: boolean;
  isRejectingRequest: boolean;

  // === Actions: 好友列表 ===
  setFriends: (friends: FriendInfo[]) => void;
  addFriend: (friend: FriendInfo) => void;
  removeFriend: (botUuid: string) => void;

  // === Actions: 好友请求 ===
  setReceivedRequests: (requests: FriendRequest[]) => void;
  setSentRequests: (requests: FriendRequest[]) => void;
  addReceivedRequest: (request: FriendRequest) => void;
  addSentRequest: (request: FriendRequest) => void;
  updateRequest: (requestId: string, updates: Partial<FriendRequest>) => void;
  removeRequest: (requestId: string) => void;

  // === Actions: 未读数量 ===
  setUnreadRequestCount: (count: number) => void;
  decrementUnreadCount: () => void;

  // === Loading Setters ===
  setLoadingFriends: (loading: boolean) => void;
  setLoadingRequests: (loading: boolean) => void;
  setSendingRequest: (sending: boolean) => void;
  setAcceptingRequest: (accepting: boolean) => void;
  setRejectingRequest: (rejecting: boolean) => void;

  // === Computed ===
  areFriends: (botA: string, botB: string) => boolean;
  getFriendByUuid: (botUuid: string) => FriendInfo | undefined;
  getPendingReceivedCount: () => number;

  // === Reset ===
  reset: () => void;
}

const initialState = {
  friends: [] as FriendInfo[],
  isLoadingFriends: false,
  receivedRequests: [] as FriendRequest[],
  sentRequests: [] as FriendRequest[],
  isLoadingRequests: false,
  unreadRequestCount: 0,
  isSendingRequest: false,
  isAcceptingRequest: false,
  isRejectingRequest: false,
};

export const useFriendStore = create<FriendState>()(
  devtools(
    (set, get) => ({
      ...initialState,

      // === Actions: 好友列表 ===
      setFriends: (friends) => set({ friends }, false, 'setFriends'),

      addFriend: (friend) =>
        set(
          (state) => ({ friends: [...state.friends, friend] }),
          false,
          'addFriend',
        ),

      removeFriend: (botUuid) =>
        set(
          (state) => ({
            friends: state.friends.filter((f) => f.bot_uuid !== botUuid),
          }),
          false,
          'removeFriend',
        ),

      // === Actions: 好友请求 ===
      setReceivedRequests: (requests) =>
        set({ receivedRequests: requests }, false, 'setReceivedRequests'),

      setSentRequests: (requests) =>
        set({ sentRequests: requests }, false, 'setSentRequests'),

      addReceivedRequest: (request) =>
        set(
          (state) => ({
            receivedRequests: [request, ...state.receivedRequests],
          }),
          false,
          'addReceivedRequest',
        ),

      addSentRequest: (request) =>
        set(
          (state) => ({ sentRequests: [request, ...state.sentRequests] }),
          false,
          'addSentRequest',
        ),

      updateRequest: (requestId, updates) =>
        set(
          (state) => ({
            receivedRequests: state.receivedRequests.map((r) =>
              r.id === requestId ? { ...r, ...updates } : r,
            ),
            sentRequests: state.sentRequests.map((r) =>
              r.id === requestId ? { ...r, ...updates } : r,
            ),
          }),
          false,
          'updateRequest',
        ),

      removeRequest: (requestId) =>
        set(
          (state) => ({
            receivedRequests: state.receivedRequests.filter(
              (r) => r.id !== requestId,
            ),
            sentRequests: state.sentRequests.filter((r) => r.id !== requestId),
          }),
          false,
          'removeRequest',
        ),

      // === Actions: 未读数量 ===
      setUnreadRequestCount: (count) =>
        set({ unreadRequestCount: count }, false, 'setUnreadRequestCount'),

      decrementUnreadCount: () =>
        set(
          (state) => ({
            unreadRequestCount: Math.max(0, state.unreadRequestCount - 1),
          }),
          false,
          'decrementUnreadCount',
        ),

      // === Loading Setters ===
      setLoadingFriends: (loading) =>
        set({ isLoadingFriends: loading }, false, 'setLoadingFriends'),
      setLoadingRequests: (loading) =>
        set({ isLoadingRequests: loading }, false, 'setLoadingRequests'),
      setSendingRequest: (sending) =>
        set({ isSendingRequest: sending }, false, 'setSendingRequest'),
      setAcceptingRequest: (accepting) =>
        set({ isAcceptingRequest: accepting }, false, 'setAcceptingRequest'),
      setRejectingRequest: (rejecting) =>
        set({ isRejectingRequest: rejecting }, false, 'setRejectingRequest'),

      // === Computed ===
      areFriends: (botA, botB) => {
        const { friends } = get();
        return friends.some((f) => f.bot_uuid === botA || f.bot_uuid === botB);
      },

      getFriendByUuid: (botUuid) => {
        return get().friends.find((f) => f.bot_uuid === botUuid);
      },

      getPendingReceivedCount: () => {
        return get().receivedRequests.filter((r) => r.status === 'pending')
          .length;
      },

      // === Reset ===
      reset: () => set(initialState, false, 'reset'),
    }),
    { name: 'FriendStore' },
  ),
);
