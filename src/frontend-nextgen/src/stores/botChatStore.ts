import type {
  BotChatContext,
  BotChatDetail,
  BotChatFilters,
  BotChatPage,
  BotChatRelationScope,
} from '@/domain/botChats';
import { create } from 'zustand';

const localDateTime = (date: Date) => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};

export const emptyBotChatFilters = (): BotChatFilters => {
  const now = new Date();
  return {
    traceId: '',
    sessionId: '',
    sessionKey: '',
    keyword: '',
    bizScene: '',
    bizTaskId: '',
    groupId: '',
    fromDate: localDateTime(new Date(now.getTime() - 72 * 60 * 60 * 1_000)),
    toDate: localDateTime(now),
  };
};

interface BotChatState {
  open: boolean;
  context?: BotChatContext;
  filters: BotChatFilters;
  appliedFilters: BotChatFilters;
  page?: BotChatPage;
  detail?: BotChatDetail;
  related?: BotChatPage;
  relationScope: BotChatRelationScope;
  loading: boolean;
  detailLoading: boolean;
  relatedLoading: boolean;
  error?: string;
  openFor: (context: BotChatContext) => void;
  close: () => void;
  setFilter: (key: keyof BotChatFilters, value: string) => void;
  applyFilters: () => void;
  clearFilters: () => void;
  setListState: (value: Partial<Pick<BotChatState, 'page' | 'loading' | 'error'>>) => void;
  setDetailState: (value: Partial<Pick<BotChatState, 'detail' | 'detailLoading' | 'error'>>) => void;
  setRelatedState: (
    value: Partial<Pick<BotChatState, 'related' | 'relatedLoading' | 'relationScope' | 'error'>>,
  ) => void;
  backToList: () => void;
  reset: () => void;
}

const initial = {
  open: false,
  context: undefined,
  filters: emptyBotChatFilters(),
  appliedFilters: emptyBotChatFilters(),
  page: undefined,
  detail: undefined,
  related: undefined,
  relationScope: 'session' as const,
  loading: false,
  detailLoading: false,
  relatedLoading: false,
  error: undefined,
};

export const useBotChatStore = create<BotChatState>((set) => ({
  ...initial,
  openFor: (context) => set({ ...initial, open: true, context }),
  close: () => set(initial),
  setFilter: (key, value) => set((state) => ({ filters: { ...state.filters, [key]: value } })),
  applyFilters: () => set((state) => ({ appliedFilters: { ...state.filters } })),
  clearFilters: () => set({ filters: emptyBotChatFilters(), appliedFilters: emptyBotChatFilters() }),
  setListState: (value) => set(value),
  setDetailState: (value) => set(value),
  setRelatedState: (value) => set(value),
  backToList: () =>
    set({ detail: undefined, related: undefined, detailLoading: false, relatedLoading: false, error: undefined }),
  reset: () => set(initial),
}));
