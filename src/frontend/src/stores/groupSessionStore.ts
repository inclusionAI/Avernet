/**
 * GroupSession Store - 群内会话状态管理
 *
 * 管理协作群内的多会话（Session）状态
 * 遵循三层架构：Store层只管理纯数据状态，不包含API调用和Toast逻辑
 */

import type { GroupMessage, GroupSession } from '@/pages/GroupChat/types';
import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

interface GroupSessionState {
  // === 会话列表 ===
  sessions: GroupSession[];
  activeSessionId: string;
  isLoadingSessions: boolean;
  hasMoreSessions: boolean;
  sessionsTotal: number;

  // === 当前会话详情 ===
  currentSession: GroupSession | null;

  // === 会话消息 ===
  sessionMessages: GroupMessage[];
  isLoadingSessionMessages: boolean;
  hasMoreSessionMessages: boolean;
  sessionMessageCursor: string | null;

  // === 操作状态 ===
  isCreatingSession: boolean;
  isUpdatingSessionTitle: boolean;
  isJoiningSession: boolean;
  isLeavingSession: boolean;

  // === Actions: 会话列表 ===
  setSessions: (sessions: GroupSession[]) => void;
  appendSessions: (sessions: GroupSession[]) => void;
  addSession: (session: GroupSession) => void;
  updateSession: (sessionId: string, updates: Partial<GroupSession>) => void;
  removeSession: (sessionId: string) => void;
  setActiveSessionId: (id: string) => void;
  setHasMoreSessions: (hasMore: boolean) => void;
  setSessionsTotal: (total: number) => void;

  // === Actions: 当前会话 ===
  setCurrentSession: (session: GroupSession | null) => void;

  // === Actions: 会话消息 ===
  setSessionMessages: (messages: GroupMessage[]) => void;
  prependSessionMessages: (messages: GroupMessage[]) => void;
  setSessionMessageCursor: (cursor: string | null) => void;
  setHasMoreSessionMessages: (hasMore: boolean) => void;

  // === Loading Setters ===
  setLoadingSessions: (loading: boolean) => void;
  setLoadingSessionMessages: (loading: boolean) => void;
  setCreatingSession: (creating: boolean) => void;
  setUpdatingSessionTitle: (updating: boolean) => void;
  setJoiningSession: (joining: boolean) => void;
  setLeavingSession: (leaving: boolean) => void;

  // === Computed Getters ===
  getSessionById: (id: string) => GroupSession | undefined;

  // === Reset ===
  reset: () => void;
  resetSessionMessages: () => void;
}

const initialState = {
  sessions: [] as GroupSession[],
  activeSessionId: '',
  isLoadingSessions: false,
  hasMoreSessions: false,
  sessionsTotal: 0,

  currentSession: null as GroupSession | null,

  sessionMessages: [] as GroupMessage[],
  isLoadingSessionMessages: false,
  hasMoreSessionMessages: false,
  sessionMessageCursor: null as string | null,

  isCreatingSession: false,
  isUpdatingSessionTitle: false,
  isJoiningSession: false,
  isLeavingSession: false,
};

export const useGroupSessionStore = create<GroupSessionState>()(
  devtools(
    (set, get) => ({
      ...initialState,

      // === Actions: 会话列表 ===
      setSessions: (sessions) => set({ sessions }, false, 'setSessions'),

      appendSessions: (newSessions) =>
        set(
          (state) => ({
            sessions: [...state.sessions, ...newSessions],
          }),
          false,
          'appendSessions',
        ),

      addSession: (session) =>
        set(
          (state) => ({
            sessions: [session, ...state.sessions],
            sessionsTotal: state.sessionsTotal + 1,
          }),
          false,
          'addSession',
        ),

      updateSession: (sessionId, updates) =>
        set(
          (state) => ({
            sessions: state.sessions.map((s) =>
              s.sessionId === sessionId ? { ...s, ...updates } : s,
            ),
            currentSession:
              state.currentSession?.sessionId === sessionId
                ? { ...state.currentSession, ...updates }
                : state.currentSession,
          }),
          false,
          'updateSession',
        ),

      removeSession: (sessionId) =>
        set(
          (state) => ({
            sessions: state.sessions.filter((s) => s.sessionId !== sessionId),
            activeSessionId:
              state.activeSessionId === sessionId ? '' : state.activeSessionId,
            currentSession:
              state.currentSession?.sessionId === sessionId
                ? null
                : state.currentSession,
            sessionsTotal: Math.max(0, state.sessionsTotal - 1),
          }),
          false,
          'removeSession',
        ),

      setActiveSessionId: (id) =>
        set({ activeSessionId: id }, false, 'setActiveSessionId'),

      setHasMoreSessions: (hasMore) =>
        set({ hasMoreSessions: hasMore }, false, 'setHasMoreSessions'),

      setSessionsTotal: (total) =>
        set({ sessionsTotal: total }, false, 'setSessionsTotal'),

      // === Actions: 当前会话 ===
      setCurrentSession: (session) =>
        set({ currentSession: session }, false, 'setCurrentSession'),

      // === Actions: 会话消息 ===
      setSessionMessages: (messages) =>
        set({ sessionMessages: messages }, false, 'setSessionMessages'),

      prependSessionMessages: (messages) =>
        set(
          (state) => ({
            sessionMessages: [...messages, ...state.sessionMessages],
          }),
          false,
          'prependSessionMessages',
        ),

      setSessionMessageCursor: (cursor) =>
        set({ sessionMessageCursor: cursor }, false, 'setSessionMessageCursor'),

      setHasMoreSessionMessages: (hasMore) =>
        set(
          { hasMoreSessionMessages: hasMore },
          false,
          'setHasMoreSessionMessages',
        ),

      // === Loading Setters ===
      setLoadingSessions: (loading) =>
        set({ isLoadingSessions: loading }, false, 'setLoadingSessions'),
      setLoadingSessionMessages: (loading) =>
        set(
          { isLoadingSessionMessages: loading },
          false,
          'setLoadingSessionMessages',
        ),
      setCreatingSession: (creating) =>
        set({ isCreatingSession: creating }, false, 'setCreatingSession'),
      setUpdatingSessionTitle: (updating) =>
        set(
          { isUpdatingSessionTitle: updating },
          false,
          'setUpdatingSessionTitle',
        ),
      setJoiningSession: (joining) =>
        set({ isJoiningSession: joining }, false, 'setJoiningSession'),
      setLeavingSession: (leaving) =>
        set({ isLeavingSession: leaving }, false, 'setLeavingSession'),

      // === Computed Getters ===
      getSessionById: (id) => {
        return get().sessions.find((s) => s.sessionId === id);
      },

      // === Reset ===
      reset: () => set(initialState, false, 'reset'),

      resetSessionMessages: () =>
        set(
          {
            sessionMessages: [],
            hasMoreSessionMessages: false,
            sessionMessageCursor: null,
          },
          false,
          'resetSessionMessages',
        ),
    }),
    { name: 'GroupSessionStore' },
  ),
);
