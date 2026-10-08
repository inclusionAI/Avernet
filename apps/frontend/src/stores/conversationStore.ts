/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 *
 * Conversation Store - 全局会话状态管理
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

/**
 * 从会话 ID 中提取用户 ID
 * 会话 ID 格式:
 * - 普通会话：`agent:main:session:{uuid}:user:100000`
 * - 钉钉会话："agent:main:dingtalk:direct:100001"
 * @param sessionId 会话 ID
 * @returns 用户 ID 或 null
 */
export function extractUserIdFromSessionId(sessionId: string): string | null {
  if (!sessionId) return null;
  // 匹配 "user:" 或 "direct:" 后面的用户 ID
  const match = sessionId.match(/(?:user|direct):([^:]+)$/);
  return match ? match[1] : null;
}

export interface Conversation {
  id: string;
  title?: string;
  message_count?: number;
  last_message?: {
    content?: string;
  };
  gmt_created?: string;
  gmt_modified?: string;
  user_id?: string;
  isPinned?: boolean;
  unread?: boolean;
  updatedAt?: number;
  model?: string;
  /** 会话来源：当前用户创建的会话还是他人分享的会话 */
  source?: 'mine' | 'others';
  /** 会话创建者名称（用于他人会话显示） */
  creator_name?: string;
  /** 创建者用户 ID（用于他人会话显示） */
  creator_user_id?: string | null;
  /** 工作区路径（AICoding 引擎） */
  workspace_path?: string;
  /** 权限模式（AICoding 引擎） */
  permission_mode?: string;
  /** 引擎类型 */
  engine_type?:
    | 'openclaw'
    | 'moltis'
    | 'hermes'
    | 'aicoding'
    | 'claude_code'
    | 'teclaw';
  /** 当前工作目录（AICoding 引擎） */
  cwd?: string;
  /** AICoding / 应用 Bot 的会话运行状态（来自 /api/aicoding/sessions.run_status），用于右下角展示原始状态文案 */
  run_status?: string | null;
  /** AICoding / 应用 Bot 的会话扩展信息（来自 /api/aicoding/sessions.ext_info） */
  ext_info?: { dimaItemId?: string; [key: string]: unknown } | null;
}

interface ConversationState {
  // State
  conversations: Conversation[];
  activeConvId: string;
  isLoading: boolean;
  isCreating: boolean;
  /** 会话来源筛选：mine-我的会话，others-他人会话 */
  sessionSource: 'mine' | 'others';
  /** 正在清除消息的会话 ID（null 表示无清除操作进行中） */
  clearingConvId: string | null;
  /** 待发送的初始化消息（VSCode 新建对话时使用） */
  pendingInitMessage: string | null;

  // Pagination State
  hasMoreConversations: boolean;
  isLoadingMoreConversations: boolean;
  conversationsOffset: number;
  conversationsTotal: number;

  /**
   * 静默刷新心跳计数器。
   * 应用 Bot 轮询 run_status 时会同步 bump 这个值，
   * 订阅它的组件（如 FileTree / DiffTreePanel）可在不显示 loading 的前提下重新拉数据。
   * 与 ChatLayout 中用户主动点刷新触发的 fileTreeRefreshKey 是两条独立通道。
   */
  silentRefreshTick: number;

  // Actions
  setConversations: (conversations: Conversation[]) => void;
  appendConversations: (conversations: Conversation[]) => void;
  setActiveConvId: (id: string) => void;
  addConversation: (conversation: Conversation) => void;
  updateConversation: (id: string, updates: Partial<Conversation>) => void;
  removeConversation: (id: string) => void;
  setLoading: (loading: boolean) => void;
  setCreating: (creating: boolean) => void;
  setSessionSource: (source: 'mine' | 'others') => void;
  setClearingConvId: (id: string | null) => void;
  setPendingInitMessage: (message: string | null) => void;

  // Pagination Actions
  setHasMoreConversations: (hasMore: boolean) => void;
  setIsLoadingMoreConversations: (loading: boolean) => void;
  setConversationsOffset: (offset: number) => void;
  setConversationsTotal: (total: number) => void;

  /** 触发一次静默刷新（递增 silentRefreshTick） */
  bumpSilentRefreshTick: () => void;

  reset: () => void;

  // Computed
  getConversationById: (id: string) => Conversation | undefined;
  getSortedConversations: () => Conversation[];
}

const initialState = {
  conversations: [],
  activeConvId: '',
  isLoading: false,
  isCreating: false,
  sessionSource: 'mine' as 'mine' | 'others',
  clearingConvId: null,
  pendingInitMessage: null,
  // Pagination
  hasMoreConversations: true,
  isLoadingMoreConversations: false,
  conversationsOffset: 0,
  conversationsTotal: 0,
  silentRefreshTick: 0,
};

export const useConversationStore = create<ConversationState>()(
  devtools(
    (set, get) => ({
      // Initial State
      ...initialState,

      // Actions
      setConversations: (conversations) =>
        set({ conversations }, false, 'setConversations'),

      appendConversations: (conversations) =>
        set(
          (state) => ({
            conversations: [...state.conversations, ...conversations],
          }),
          false,
          'appendConversations',
        ),

      setActiveConvId: (id) =>
        set({ activeConvId: id }, false, 'setActiveConvId'),

      addConversation: (conversation) =>
        set(
          (state) => ({
            conversations: [conversation, ...state.conversations],
          }),
          false,
          'addConversation',
        ),

      updateConversation: (id, updates) =>
        set(
          (state) => ({
            conversations: state.conversations.map((conv) =>
              conv.id === id ? { ...conv, ...updates } : conv,
            ),
          }),
          false,
          'updateConversation',
        ),

      removeConversation: (id) =>
        set(
          (state) => ({
            conversations: state.conversations.filter((conv) => conv.id !== id),
            // 如果删除的是当前活动会话，清空 activeConvId
            activeConvId: state.activeConvId === id ? '' : state.activeConvId,
          }),
          false,
          'removeConversation',
        ),

      setLoading: (loading) => set({ isLoading: loading }, false, 'setLoading'),

      setCreating: (creating) =>
        set({ isCreating: creating }, false, 'setCreating'),

      setSessionSource: (source) =>
        set({ sessionSource: source }, false, 'setSessionSource'),

      setClearingConvId: (id) =>
        set({ clearingConvId: id }, false, 'setClearingConvId'),

      setPendingInitMessage: (message) =>
        set({ pendingInitMessage: message }, false, 'setPendingInitMessage'),

      // Pagination Actions
      setHasMoreConversations: (hasMore) =>
        set(
          { hasMoreConversations: hasMore },
          false,
          'setHasMoreConversations',
        ),
      setIsLoadingMoreConversations: (loading) =>
        set(
          { isLoadingMoreConversations: loading },
          false,
          'setIsLoadingMoreConversations',
        ),
      setConversationsOffset: (offset) =>
        set({ conversationsOffset: offset }, false, 'setConversationsOffset'),
      setConversationsTotal: (total) =>
        set({ conversationsTotal: total }, false, 'setConversationsTotal'),

      bumpSilentRefreshTick: () =>
        set(
          (state) => ({ silentRefreshTick: state.silentRefreshTick + 1 }),
          false,
          'bumpSilentRefreshTick',
        ),

      reset: () => set(initialState, false, 'reset'),

      // Computed Getters
      getConversationById: (id) => {
        return get().conversations.find((conv) => conv.id === id);
      },

      getSortedConversations: () => {
        const { conversations } = get();
        // 按置顶和更新时间排序
        return [...conversations].sort((a, b) => {
          if (a.isPinned && !b.isPinned) return -1;
          if (!a.isPinned && b.isPinned) return 1;
          const timeA = a.gmt_modified ? new Date(a.gmt_modified).getTime() : 0;
          const timeB = b.gmt_modified ? new Date(b.gmt_modified).getTime() : 0;
          return timeB - timeA;
        });
      },
    }),
    { name: 'ConversationStore' },
  ),
);
