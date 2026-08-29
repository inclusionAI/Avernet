import type { BotSearchMode, PublicBot, PublicBotProfile, PublicGroup, PublicGroupMember } from '@/domain/collaborationSquare/types';
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
  setBots: (bots: PublicBot[]) => void;
  setGroups: (groups: PublicGroup[]) => void;
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
  updateBotRelationship: (id: string, status: PublicBot['relationshipStatus']) => void;
  removeBot: (id: string) => void;
  removeGroup: (id: string) => void;
  reset: () => void;
}

const initialState = {
  bots: [], groups: [], botProfile: null, groupMembers: [], loading: true, detailLoading: false,
  error: null, botQuery: '', groupQuery: '', botSearchMode: 'name' as BotSearchMode,
  selectedBotId: null, selectedGroupId: null, busyKeys: [],
};

export const useCollaborationSquareStore = create<CollaborationSquareState>((set) => ({
  ...initialState,
  setBots: (bots) => set({ bots }),
  setGroups: (groups) => set({ groups }),
  setBotProfile: (botProfile) => set({ botProfile }),
  setGroupMembers: (groupMembers) => set({ groupMembers }),
  setLoading: (loading) => set({ loading }),
  setDetailLoading: (detailLoading) => set({ detailLoading }),
  setError: (error) => set({ error }),
  setQuery: (resource, query) => set(resource === 'bot' ? { botQuery: query } : { groupQuery: query }),
  setBotSearchMode: (botSearchMode) => set({ botSearchMode }),
  setSelectedBotId: (selectedBotId) => set({ selectedBotId, botProfile: null }),
  setSelectedGroupId: (selectedGroupId) => set({ selectedGroupId, groupMembers: [] }),
  setBusy: (key, busy) => set((state) => ({ busyKeys: busy ? [...new Set([...state.busyKeys, key])] : state.busyKeys.filter((item) => item !== key) })),
  updateBotRelationship: (id, relationshipStatus) => set((state) => ({ bots: state.bots.map((bot) => bot.id === id ? { ...bot, relationshipStatus } : bot) })),
  removeBot: (id) => set((state) => ({ bots: state.bots.filter((bot) => bot.id !== id), selectedBotId: state.selectedBotId === id ? null : state.selectedBotId, botProfile: state.botProfile?.id === id ? null : state.botProfile })),
  removeGroup: (id) => set((state) => ({ groups: state.groups.filter((group) => group.id !== id), selectedGroupId: state.selectedGroupId === id ? null : state.selectedGroupId, groupMembers: state.selectedGroupId === id ? [] : state.groupMembers })),
  reset: () => set(initialState),
}));
