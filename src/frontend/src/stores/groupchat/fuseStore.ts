/**
 * Fuse Store - 智能问答状态管理
 *
 * 遵循四层架构：Store 层只管理纯数据状态，不包含 API 调用和 Toast 逻辑
 * 按 sessionId 隔离消息缓存，切换会话时保留各 session 的 fuse 对话
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

/** 问答消息中引用的参与者 */
export interface FuseParticipant {
  id: string;
  name: string;
  avatar?: string;
}

/** 问答消息类型 */
export interface FuseMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  /** 是否正在加载回答 */
  isLoading?: boolean;
  /** 该问题选中的融合 Bot（仅 user 消息） */
  participants?: FuseParticipant[];
}

interface FuseState {
  /** 按 sessionId 缓存的问答消息 */
  messagesMap: Record<string, FuseMessage[]>;
  /** 按 session 追踪正在请求融合回答的 session */
  fusingSessionIds: Record<string, boolean>;
  /** 按 session 标记未读（弹窗关闭期间收到回答的 session） */
  unreadSessionIds: Record<string, boolean>;

  // === Actions ===
  addMessage: (sessionId: string, message: FuseMessage) => void;
  updateMessage: (
    sessionId: string,
    id: string,
    updates: Partial<FuseMessage>,
  ) => void;
  setSessionFusing: (sessionId: string, fusing: boolean) => void;
  setUnreadSession: (sessionId: string, value: boolean) => void;
  clearSessionMessages: (sessionId: string) => void;
  reset: () => void;
}

const initialState = {
  messagesMap: {} as Record<string, FuseMessage[]>,
  fusingSessionIds: {} as Record<string, boolean>,
  unreadSessionIds: {} as Record<string, boolean>,
};

export const useFuseStore = create<FuseState>()(
  devtools(
    (set) => ({
      ...initialState,

      addMessage: (sessionId, message) =>
        set(
          (state) => ({
            messagesMap: {
              ...state.messagesMap,
              [sessionId]: [...(state.messagesMap[sessionId] || []), message],
            },
          }),
          false,
          'addMessage',
        ),

      updateMessage: (sessionId, id, updates) =>
        set(
          (state) => ({
            messagesMap: {
              ...state.messagesMap,
              [sessionId]: (state.messagesMap[sessionId] || []).map((msg) =>
                msg.id === id ? { ...msg, ...updates } : msg,
              ),
            },
          }),
          false,
          'updateMessage',
        ),

      setSessionFusing: (sessionId, fusing) =>
        set(
          (state) => ({
            fusingSessionIds: {
              ...state.fusingSessionIds,
              [sessionId]: fusing,
            },
          }),
          false,
          'setSessionFusing',
        ),

      setUnreadSession: (sessionId, value) =>
        set(
          (state) => ({
            unreadSessionIds: {
              ...state.unreadSessionIds,
              [sessionId]: value,
            },
          }),
          false,
          'setUnreadSession',
        ),

      clearSessionMessages: (sessionId) =>
        set(
          (state) => ({
            messagesMap: {
              ...state.messagesMap,
              [sessionId]: [],
            },
          }),
          false,
          'clearSessionMessages',
        ),

      reset: () => set(initialState, false, 'reset'),
    }),
    { name: 'FuseStore' },
  ),
);

// === Selector 辅助函数 ===

/** 获取指定 session 的消息列表 */
export function getMessagesBySession(sessionId: string): FuseMessage[] {
  return useFuseStore.getState().messagesMap[sessionId] || [];
}

/** 判断指定 session 是否有未读回答 */
export function isSessionUnread(sessionId: string): boolean {
  return !!useFuseStore.getState().unreadSessionIds[sessionId];
}

/** 判断是否存在任何未读回答 */
export function hasAnyUnread(): boolean {
  return Object.values(useFuseStore.getState().unreadSessionIds).some(Boolean);
}
