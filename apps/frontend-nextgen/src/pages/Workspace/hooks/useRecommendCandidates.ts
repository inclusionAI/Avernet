import {
  collaborationCandidateService,
  type CollaborationBotView,
  type RecommendCandidateView,
} from '@/services/workspace/collaborationCandidateService';
import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { useCallback, useEffect, useState } from 'react';
import type { CandidateFilterMode, CandidateTab } from './useGroupCollaborationPicker.types';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 350;

export interface RecommendCandidatesController {
  recommendations: RecommendCandidateView[];
  isLoadingRecommend: boolean;
  recommendError?: string;
  /** 最近一次推荐态的来源：goal=协作目标智能推荐（结果默认全选），profile=按画像筛选（不自动选中）。 */
  recommendSource: 'goal' | 'profile' | null;
  /** 手动输入搜索词（归为画像筛选来源，覆盖此前的 goal 标记）。 */
  updateSearch: (value: string) => void;
  /** 协作目标智能推荐入口：切换到可协作 Bot Tab 并以目标文本触发画像推荐。 */
  recommendByGoal: (goal: string) => void;
  /** 推荐失败后的重试触发。 */
  retryRecommend: () => void;
  /** 弹窗关闭时重置推荐态（结果/错误/来源）。 */
  reset: () => void;
}

interface UseRecommendCandidatesInput {
  enabled: boolean;
  actorId: string | null | undefined;
  actorKind: 'user' | 'bot';
  tab: CandidateTab;
  filterMode: CandidateFilterMode;
  search: string;
  /** 推荐交集输入：当前已加载的可协作列表（ref 提供最新值）。 */
  candidatesRef: MutableRefObject<CollaborationBotView[]>;
  /** 候选列表为空时的首屏落地回调（兜底拉取后写入列表/分页状态）。 */
  applyCandidatesSnapshot: (items: CollaborationBotView[], hasMore: boolean, offset: number) => void;
  setTab: Dispatch<SetStateAction<CandidateTab>>;
  setFilterMode: Dispatch<SetStateAction<CandidateFilterMode>>;
  setSearch: Dispatch<SetStateAction<string>>;
}

/**
 * 发起协作弹窗的语义推荐链路：画像关键词搜索与协作目标智能推荐共用。
 * 与可协作列表求交集（推荐即可选）；350ms 防抖，失败可重试。
 */
export function useRecommendCandidates(input: UseRecommendCandidatesInput): RecommendCandidatesController {
  const {
    enabled,
    actorId,
    actorKind,
    tab,
    filterMode,
    search,
    candidatesRef,
    applyCandidatesSnapshot,
    setTab,
    setFilterMode,
    setSearch,
  } = input;
  const [recommendations, setRecommendations] = useState<RecommendCandidateView[]>([]);
  const [isLoadingRecommend, setIsLoadingRecommend] = useState(false);
  const [recommendError, setRecommendError] = useState<string | undefined>(undefined);
  const [recommendNonce, setRecommendNonce] = useState(0);
  // 推荐态来源标识：goal 推荐落地后由弹窗层自动全选；画像筛选搜索不自动选中。
  const [recommendSource, setRecommendSource] = useState<'goal' | 'profile' | null>(null);

  // 画像筛选模式：可协作 Bot Tab 内按画像关键词做语义推荐；清空关键词或切回按名称即退出推荐态恢复原列表。
  useEffect(() => {
    if (!enabled || !actorId || actorId === 'me' || tab !== 'candidates') return;
    const keyword = filterMode === 'profile' ? search.trim() : '';
    if (!keyword) {
      setRecommendations([]);
      setRecommendError(undefined);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void (async () => {
        setIsLoadingRecommend(true);
        setRecommendError(undefined);
        try {
          let base = candidatesRef.current;
          if (base.length === 0) {
            const firstPage = await collaborationCandidateService.listCandidates(actorId, {
              offset: 0,
              limit: PAGE_SIZE,
            });
            if (cancelled) return;
            if (!firstPage.ok) throw new Error(firstPage.error.friendlyMessage);
            base = firstPage.data.items;
            applyCandidatesSnapshot(base, firstPage.data.hasMore, base.length);
          }
          const res = await collaborationCandidateService.recommendCandidates({
            actorId,
            actorKind,
            keyword,
            candidates: base,
          });
          if (cancelled) return;
          if (!res.ok) throw new Error(res.error.friendlyMessage);
          setRecommendations(res.data);
        } catch (error) {
          if (!cancelled) {
            setRecommendError(error instanceof Error ? error.message : '推荐可协作 Bot 失败，请稍后重试。');
          }
        } finally {
          if (!cancelled) setIsLoadingRecommend(false);
        }
      })();
    }, SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [
    actorId,
    actorKind,
    applyCandidatesSnapshot,
    candidatesRef,
    enabled,
    filterMode,
    recommendNonce,
    search,
    tab,
  ]);

  // 协作目标智能推荐：与画像搜索共用推荐链路（目标文本作为画像关键词）；来源标记为 goal，
  // 供弹窗层在推荐落地时自动全选（画像筛选搜索路径不自动选中）。
  const recommendByGoal = useCallback((goal: string) => {
    setTab('candidates');
    setFilterMode('profile');
    setRecommendSource('goal');
    setSearch(goal);
  }, [setFilterMode, setSearch, setTab]);

  // 手动输入搜索词归为画像筛选来源，覆盖此前的 goal 标记，防止 goal 推荐后的手输关键词被误判自动选中。
  const updateSearch = useCallback((value: string) => {
    setRecommendSource('profile');
    setSearch(value);
  }, [setSearch]);

  const retryRecommend = useCallback(() => {
    setRecommendNonce((current) => current + 1);
  }, []);

  const reset = useCallback(() => {
    setRecommendations([]);
    setRecommendError(undefined);
    setRecommendSource(null);
  }, []);

  return {
    recommendations,
    isLoadingRecommend,
    recommendError,
    recommendSource,
    updateSearch,
    recommendByGoal,
    retryRecommend,
    reset,
  };
}
