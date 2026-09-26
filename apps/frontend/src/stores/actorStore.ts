/**
 * ActorStore - Bot Actor 状态管理
 */

import { create } from 'zustand';
import { devtools } from 'zustand/middleware';

/** Actor 能力信息 */
export interface ActorCapabilities {
  description: string;
  domains: string[];
  name: string;
  scopes: string[];
  skills: { name: string }[];
}

/** 动态状态 */
export interface DynamicStatus {
  status: 'active' | 'offline';
  last_active_at?: number;
}

/** Actor Bot 信息 */
export interface ActorBot {
  bot_uuid: string;
  bot_name?: string;
  summary?: string;
  avatar_url?: string;
  capabilities: ActorCapabilities;
  is_friend: boolean;
  status: 'online' | 'offline';
  visibility: 'public' | 'protected' | 'private';
  is_online?: boolean;
  score?: number;
  short_profile?: string;
  dynamic_status?: DynamicStatus;
}

/** 分页信息 */
export interface ActorPagination {
  page_no: number;
  page_size: number;
  total: number;
}

interface ActorState {
  // State
  actors: ActorBot[];
  isLoading: boolean;
  hasLoaded: boolean;
  pagination: ActorPagination;
  searchQuery: string;
  cooperatableOnly: boolean;

  // Actions
  setActors: (actors: ActorBot[], total: number, append?: boolean) => void;
  setLoading: (loading: boolean) => void;
  setHasLoaded: (hasLoaded: boolean) => void;
  setPagination: (pagination: Partial<ActorPagination>) => void;
  setSearchQuery: (query: string) => void;
  setCooperatableOnly: (value: boolean) => void;
  reset: () => void;
  updateActor: (botUuid: string, updates: Partial<ActorBot>) => void;
}

const defaultPagination: ActorPagination = {
  page_no: 1,
  page_size: 20,
  total: 0,
};

const initialState = {
  actors: [],
  isLoading: false,
  hasLoaded: false,
  pagination: defaultPagination,
  searchQuery: '',
  cooperatableOnly: false,
};

export const useActorStore = create<ActorState>()(
  devtools(
    (set, get) => ({
      // Initial State
      ...initialState,

      // Actions
      setActors: (actors, total, append = false) =>
        set(
          {
            actors: append ? [...get().actors, ...actors] : actors,
            pagination: {
              ...get().pagination,
              total,
            },
          },
          false,
          'setActors',
        ),

      setLoading: (loading) => set({ isLoading: loading }, false, 'setLoading'),

      setHasLoaded: (hasLoaded) => set({ hasLoaded }, false, 'setHasLoaded'),

      setPagination: (pagination) =>
        set(
          {
            pagination: { ...get().pagination, ...pagination },
          },
          false,
          'setPagination',
        ),

      setSearchQuery: (query) =>
        set({ searchQuery: query }, false, 'setSearchQuery'),

      setCooperatableOnly: (value) =>
        set({ cooperatableOnly: value }, false, 'setCooperatableOnly'),

      reset: () => set(initialState, false, 'reset'),

      updateActor: (botUuid, updates) =>
        set(
          {
            actors: get().actors.map((actor) =>
              actor.bot_uuid === botUuid ? { ...actor, ...updates } : actor,
            ),
          },
          false,
          'updateActor',
        ),
    }),
    { name: 'ActorStore' },
  ),
);
