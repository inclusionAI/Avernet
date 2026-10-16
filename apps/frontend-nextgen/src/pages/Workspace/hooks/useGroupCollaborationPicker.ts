import { useEffect, useRef, useState } from 'react';
import type { CandidateFilterMode, CandidateTab, UseGroupCollaborationPickerResult } from './useGroupCollaborationPicker.types';
import { usePickerLists } from './usePickerLists';
import { useRecommendCandidates } from './useRecommendCandidates';

export type {
  CandidateFilterMode,
  CandidateTab,
  UseGroupCollaborationPickerResult,
} from './useGroupCollaborationPicker.types';

/**
 * 发起协作弹窗的候选数据层 Hook：组合列表加载（usePickerLists）与语义推荐（useRecommendCandidates），
 * 自持 Tab/搜索词/筛选模式并负责弹窗关闭重置；Picker 只消费状态与 scroll 事件。
 */
export function useGroupCollaborationPicker(
  actorId: string | null | undefined,
  enabled: boolean,
  showMineTab = false,
  actorKind?: 'user' | 'bot',
): UseGroupCollaborationPickerResult {
  const [tab, setTab] = useState<CandidateTab>('friends');
  const [search, setSearch] = useState('');
  const [filterMode, setFilterMode] = useState<CandidateFilterMode>('name');
  const wasEnabledRef = useRef(enabled);
  const effectiveActorKind: 'user' | 'bot' = actorKind ?? (showMineTab ? 'user' : 'bot');

  const lists = usePickerLists({ enabled, actorId, showMineTab, tab, filterMode, search });

  const recommend = useRecommendCandidates({
    enabled,
    actorId,
    actorKind: effectiveActorKind,
    tab,
    filterMode,
    search,
    candidatesRef: lists.candidatesRef,
    applyCandidatesSnapshot: lists.applyCandidatesSnapshot,
    setTab,
    setFilterMode,
    setSearch,
  });

  // 弹窗关闭时清空搜索、筛选模式、Tab 与推荐态，下次打开回到默认状态。
  useEffect(() => {
    if (wasEnabledRef.current && !enabled) {
      setSearch('');
      setTab('friends');
      setFilterMode('name');
      recommend.reset();
    }
    wasEnabledRef.current = enabled;
  }, [enabled, recommend.reset]);

  return {
    tab,
    setTab,
    search,
    setSearch: recommend.updateSearch,
    filterMode,
    setFilterMode,
    friends: lists.friends,
    mine: lists.mine,
    candidates: lists.candidates,
    isLoadingFriends: lists.isLoadingFriends,
    isLoadingMine: lists.isLoadingMine,
    isLoadingCandidates: lists.isLoadingCandidates,
    isLoadingMore: lists.isLoadingMore,
    friendsHasMore: lists.friendsHasMore,
    mineHasMore: lists.mineHasMore,
    candidatesHasMore: lists.candidatesHasMore,
    error: lists.error,
    retry: lists.retry,
    loadMore: lists.loadMore,
    recommendations: recommend.recommendations,
    isLoadingRecommend: recommend.isLoadingRecommend,
    recommendError: recommend.recommendError,
    recommendSource: recommend.recommendSource,
    recommendByGoal: recommend.recommendByGoal,
    retryRecommend: recommend.retryRecommend,
  };
}