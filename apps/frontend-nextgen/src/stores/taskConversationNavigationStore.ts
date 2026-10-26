import type { PanelTab } from '@tc-chat/core';
import { create } from 'zustand';

export type TaskConversationPathname = '/workspace/chat' | '/workspace/collaboration';

export interface TaskConversationOrigin {
  routeUrl: string;
  sessionId: string;
  kind: 'group' | 'bot' | 'friend' | 'unknown';
  groupId?: string | null;
  botId?: string | null;
}

export type TaskPanelTabSnapshot = Pick<
  PanelTab,
  'id' | 'title' | 'type' | 'params' | 'payload' | 'closable' | 'defaultWidth'
>;

export interface TaskConversationNavigation {
  id: number;
  taskId: string;
  sessionId: string;
  pathname: TaskConversationPathname;
  tab: TaskPanelTabSnapshot | null;
  routeCommitted: boolean;
  routeHydrated: boolean;
}

interface TaskConversationNavigationState {
  originsByTask: Record<string, TaskConversationOrigin>;
  tabsByTask: Record<string, TaskPanelTabSnapshot>;
  navigation: TaskConversationNavigation | null;
  rememberOrigin: (taskId: string, origin: TaskConversationOrigin) => void;
  rememberTaskTab: (taskId: string, tab: PanelTab) => void;
  beginNavigation: (taskId: string, pathname: TaskConversationPathname, sessionId: string) => number;
  markRouteCommitted: (id: number) => void;
  markRouteHydrated: (id: number) => void;
  completeNavigation: (id: number) => void;
  resetAll: () => void;
}

let navigationSequence = 0;
const initial = {
  originsByTask: {} as Record<string, TaskConversationOrigin>,
  tabsByTask: {} as Record<string, TaskPanelTabSnapshot>,
  navigation: null as TaskConversationNavigation | null,
};

function snapshotTaskTab(tab: PanelTab): TaskPanelTabSnapshot {
  const { id, title, type, params, payload, closable, defaultWidth } = tab;
  return { id, title, type, params, payload, closable, defaultWidth };
}

export const useTaskConversationNavigationStore = create<TaskConversationNavigationState>((set) => ({
  ...initial,
  rememberOrigin: (taskId, origin) =>
    set((state) =>
      !taskId || state.originsByTask[taskId] ? state : { originsByTask: { ...state.originsByTask, [taskId]: origin } },
    ),
  rememberTaskTab: (taskId, tab) =>
    set((state) => (!taskId ? state : { tabsByTask: { ...state.tabsByTask, [taskId]: snapshotTaskTab(tab) } })),
  beginNavigation: (taskId, pathname, sessionId) => {
    const id = ++navigationSequence;
    set((state) => ({
      navigation: {
        id,
        taskId,
        pathname,
        sessionId,
        tab: state.tabsByTask[taskId] ?? null,
        routeCommitted: false,
        routeHydrated: false,
      },
    }));
    return id;
  },
  markRouteCommitted: (id) =>
    set((state) =>
      state.navigation?.id === id ? { navigation: { ...state.navigation, routeCommitted: true } } : state,
    ),
  markRouteHydrated: (id) =>
    set((state) =>
      state.navigation?.id === id ? { navigation: { ...state.navigation, routeHydrated: true } } : state,
    ),
  completeNavigation: (id) => set((state) => (state.navigation?.id === id ? { navigation: null } : state)),
  resetAll: () => set(initial),
}));
