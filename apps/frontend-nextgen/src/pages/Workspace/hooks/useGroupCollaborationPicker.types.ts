import type { CollaborationBotView, RecommendCandidateView } from '@/services/workspace/collaborationCandidateService';

export type CandidateTab = 'friends' | 'mine' | 'candidates';

/** 可协作 Bot Tab 的搜索模式：按名称筛选 / 按画像筛选。 */
export type CandidateFilterMode = 'name' | 'profile';

export interface UseGroupCollaborationPickerResult {
  tab: CandidateTab;
  setTab: (tab: CandidateTab) => void;
  search: string;
  setSearch: (value: string) => void;
  filterMode: CandidateFilterMode;
  setFilterMode: (mode: CandidateFilterMode) => void;
  friends: CollaborationBotView[];
  mine: CollaborationBotView[];
  candidates: CollaborationBotView[];
  isLoadingFriends: boolean;
  isLoadingMine: boolean;
  isLoadingCandidates: boolean;
  isLoadingMore: boolean;
  friendsHasMore: boolean;
  mineHasMore: boolean;
  candidatesHasMore: boolean;
  error?: string;
  retry: () => void;
  loadMore: () => void;
  /** 画像/协作目标语义推荐结果，仅可协作 Bot Tab 使用。 */
  recommendations: RecommendCandidateView[];
  isLoadingRecommend: boolean;
  recommendError?: string;
  /** 最近一次推荐态的来源：goal=协作目标智能推荐（结果默认全选），profile=按画像筛选（不自动选中），null=未处于推荐来源态。 */
  recommendSource: 'goal' | 'profile' | null;
  /** 协作目标智能推荐入口：切换到可协作 Bot Tab 并以目标文本触发画像推荐。 */
  recommendByGoal: (goal: string) => void;
  /** 推荐失败后的重试触发。 */
  retryRecommend: () => void;
}
