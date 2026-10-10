import type { CommunityReply, CommunityScope, CommunityTopic } from '@/domain/community/types';
import { create } from 'zustand';

interface CommunityState {
  topics: CommunityTopic[];
  total: number;
  query: string;
  scope: CommunityScope;
  offset: number;
  loading: boolean;
  error: string | null;
  selectedTopic: CommunityTopic | null;
  replies: CommunityReply[];
  detailLoading: boolean;
  detailError: string | null;
  setTopics: (topics: CommunityTopic[], total: number) => void;
  appendTopics: (topics: CommunityTopic[]) => void;
  setQuery: (query: string) => void;
  setScope: (scope: CommunityScope) => void;
  setLoading: (loading: boolean) => void;
  setError: (error: string | null) => void;
  setSelectedTopic: (topic: CommunityTopic | null) => void;
  setReplies: (replies: CommunityReply[]) => void;
  setDetailLoading: (loading: boolean) => void;
  setDetailError: (error: string | null) => void;
  prependTopic: (topic: CommunityTopic) => void;
  markTopicClosed: (topicId: string) => void;
  reset: () => void;
}

const initialState = {
  topics: [],
  total: 0,
  query: '',
  scope: 'all' as CommunityScope,
  offset: 0,
  loading: true,
  error: null,
  selectedTopic: null,
  replies: [],
  detailLoading: false,
  detailError: null,
};

export const useCommunityStore = create<CommunityState>((set) => ({
  ...initialState,
  setTopics: (topics, total) => set({ topics, total }),
  // 无限滚动「加载更多」：追加下一页，total 由后端权威下发（loadMore 成功后更新一次）。
  appendTopics: (topics) => set((state) => ({ topics: [...state.topics, ...topics] })),
  setQuery: (query) => set({ query, offset: 0 }),
  setScope: (scope) => set({ scope, offset: 0 }),
  setLoading: (loading) => set({ loading }),
  setError: (error) => set({ error }),
  setSelectedTopic: (selectedTopic) => set({ selectedTopic, replies: [], detailError: null }),
  setReplies: (replies) => set({ replies }),
  setDetailLoading: (detailLoading) => set({ detailLoading }),
  setDetailError: (detailError) => set({ detailError }),
  prependTopic: (topic) => set((state) => ({ topics: [topic, ...state.topics], total: state.total + 1 })),
  markTopicClosed: (topicId) =>
    set((state) => ({
      topics: state.topics.map((topic) =>
        topic.id === topicId ? { ...topic, status: 'closed', canClose: false } : topic,
      ),
      selectedTopic:
        state.selectedTopic?.id === topicId
          ? { ...state.selectedTopic, status: 'closed', canClose: false }
          : state.selectedTopic,
    })),
  reset: () => set(initialState),
}));
