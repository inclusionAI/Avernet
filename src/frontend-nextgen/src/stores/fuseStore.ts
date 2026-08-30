import { create } from 'zustand';

export interface FuseParticipant {
  id: string;
  name: string;
  avatar?: string;
}

export interface FuseMessage {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  isLoading?: boolean;
  participants?: FuseParticipant[];
}

interface FuseState {
  messagesMap: Record<string, FuseMessage[]>;
  fusingSessionIds: Record<string, boolean>;
  unreadSessionIds: Record<string, boolean>;
  addMessage: (sessionId: string, message: FuseMessage) => void;
  updateMessage: (sessionId: string, id: string, updates: Partial<FuseMessage>) => void;
  setSessionFusing: (sessionId: string, fusing: boolean) => void;
  setUnreadSession: (sessionId: string, value: boolean) => void;
  clearSessionMessages: (sessionId: string) => void;
  reset: () => void;
}

const initial = {
  messagesMap: {} as Record<string, FuseMessage[]>,
  fusingSessionIds: {} as Record<string, boolean>,
  unreadSessionIds: {} as Record<string, boolean>,
};

export const useFuseStore = create<FuseState>((set) => ({
  ...initial,
  addMessage: (sid, msg) =>
    set((s) => ({ messagesMap: { ...s.messagesMap, [sid]: [...(s.messagesMap[sid] || []), msg] } })),
  updateMessage: (sid, id, upd) =>
    set((s) => ({
      messagesMap: {
        ...s.messagesMap,
        [sid]: (s.messagesMap[sid] || []).map((m) => (m.id === id ? { ...m, ...upd } : m)),
      },
    })),
  setSessionFusing: (sid, fusing) => set((s) => ({ fusingSessionIds: { ...s.fusingSessionIds, [sid]: fusing } })),
  setUnreadSession: (sid, value) => set((s) => ({ unreadSessionIds: { ...s.unreadSessionIds, [sid]: value } })),
  clearSessionMessages: (sid) => set((s) => ({ messagesMap: { ...s.messagesMap, [sid]: [] } })),
  reset: () => set(initial),
}));
