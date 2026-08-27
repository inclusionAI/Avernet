import type { CollaborationBot, CollaborationPrivacyOverview, CurrentUserIdentity } from '@/domain/collaborationPrivacy/types';
import { create } from 'zustand';

export interface CollaborationPrivacyState {
  overview: CollaborationPrivacyOverview | null;
  loading: boolean;
  error: string | null;
  busyAction: string | null;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setOverview: (overview: CollaborationPrivacyOverview | null) => void;
  updateBot: (bot: CollaborationBot) => void;
  updateCurrentUser: (currentUser: CurrentUserIdentity) => void;
  setBusyAction: (busyAction: string | null) => void;
  reset: () => void;
}

const initialState = { overview: null, loading: true, error: null, busyAction: null };

export const useCollaborationPrivacyStore = create<CollaborationPrivacyState>((set) => ({
  ...initialState,
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  setOverview: (overview) => set({ overview }),
  updateBot: (bot) => set((state) => ({
    overview: state.overview ? { ...state.overview, bots: state.overview.bots.map((item) => item.id === bot.id ? bot : item) } : null,
  })),
  updateCurrentUser: (currentUser) => set((state) => ({
    overview: state.overview ? { ...state.overview, currentUser } : null,
  })),
  setBusyAction: (busyAction) => set({ busyAction }),
  reset: () => set(initialState),
}));
