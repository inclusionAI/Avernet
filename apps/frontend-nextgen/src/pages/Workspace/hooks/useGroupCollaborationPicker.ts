import {
  collaborationCandidateService,
  type CollaborationBotView,
  type RecommendCandidateView,
} from '@/services/workspace/collaborationCandidateService';
import { useCallback, useEffect, useRef, useState } from 'react';

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

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 350;

const appendUnique = (current: CollaborationBotView[], incoming: CollaborationBotView[]) => [
  ...current,
  ...incoming.filter((bot) => !current.some((item) => item.id === bot.id)),
];

/**
 * 发起协作弹窗的候选数据层 Hook：好友/可协作 Bot 首屏、防抖搜索与上拉分页。
 * 列表下载与加载更多均归属 Hook；Picker 只消费状态与 scroll 事件。
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
  const [friends, setFriends] = useState<CollaborationBotView[]>([]);
  const [mine, setMine] = useState<CollaborationBotView[]>([]);
  const [candidates, setCandidates] = useState<CollaborationBotView[]>([]);
  const [isLoadingFriends, setIsLoadingFriends] = useState(false);
  const [isLoadingMine, setIsLoadingMine] = useState(false);
  const [isLoadingCandidates, setIsLoadingCandidates] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [friendsHasMore, setFriendsHasMore] = useState(false);
  const [mineHasMore, setMineHasMore] = useState(false);
  const [candidatesHasMore, setCandidatesHasMore] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const [retryNonce, setRetryNonce] = useState(0);
  const friendsSeenRef = useRef<string | null>(null);
  const wasEnabledRef = useRef(enabled);
  const friendsOffsetRef = useRef(0);
  const mineOffsetRef = useRef(0);
  const candidatesOffsetRef = useRef(0);
  const [recommendations, setRecommendations] = useState<RecommendCandidateView[]>([]);
  const [isLoadingRecommend, setIsLoadingRecommend] = useState(false);
  const [recommendError, setRecommendError] = useState<string | undefined>(undefined);
  const [recommendNonce, setRecommendNonce] = useState(0);
  // 推荐态来源标识：goal 推荐落地后由弹窗层自动全选；画像筛选搜索不自动选中。
  const [recommendSource, setRecommendSource] = useState<'goal' | 'profile' | null>(null);
  // 推荐交集以当前已加载的可协作列表为输入，ref 提供最新值。
  const candidatesRef = useRef<CollaborationBotView[]>([]);
  const effectiveActorKind: 'user' | 'bot' = actorKind ?? (showMineTab ? 'user' : 'bot');

  useEffect(() => {
    candidatesRef.current = candidates;
  }, [candidates]);

  // 弹窗关闭时清空搜索、筛选模式、Tab 与推荐态，下次打开回到默认状态。
  useEffect(() => {
    if (wasEnabledRef.current && !enabled) {
      setSearch('');
      setTab('friends');
      setFilterMode('name');
      setRecommendations([]);
      setRecommendError(undefined);
      setRecommendSource(null);
    }
    wasEnabledRef.current = enabled;
  }, [enabled]);

  // 好友列表在弹窗打开后按 actor 拉取第一页；关闭再打开同一 actor 时不重复请求。
  useEffect(() => {
    if (!enabled || !actorId || actorId === 'me') return;
    if (friendsSeenRef.current === actorId) return;
    friendsSeenRef.current = actorId;

    let cancelled = false;
    setIsLoadingFriends(true);
    setError(undefined);
    void collaborationCandidateService
      .listFriends(actorId, { offset: 0, limit: PAGE_SIZE, detailSource: 'collaboration' })
      .then((res) => {
        if (cancelled) return;
        setIsLoadingFriends(false);
        if (res.ok) {
          setFriends(res.data.items);
          setFriendsHasMore(res.data.hasMore);
          friendsOffsetRef.current = res.data.offset;
        } else {
          setError(res.error.friendlyMessage);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [actorId, enabled, retryNonce]);

  // 用户身份可查看已管理的 Bot；切到「已管理 Bot」Tab 时拉取第一页并本地搜索。
  useEffect(() => {
    if (!enabled || !showMineTab || tab !== 'mine') return;
    let cancelled = false;
    setIsLoadingMine(true);
    setError(undefined);
    void collaborationCandidateService.listMine({ offset: 0, limit: PAGE_SIZE }).then((res) => {
      if (cancelled) return;
      setIsLoadingMine(false);
      if (res.ok) {
        setMine(res.data.items);
        setMineHasMore(res.data.hasMore);
        mineOffsetRef.current = res.data.offset;
      } else {
        setError(res.error.friendlyMessage);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [enabled, retryNonce, showMineTab, tab]);

  // 可协作 Bot 在切到对应 tab 时加载第一页；搜索词变更做防抖并回退到第一页。
  useEffect(() => {
    if (!enabled || !actorId || actorId === 'me' || tab !== 'candidates' || filterMode === 'profile') return;
    let cancelled = false;
    setIsLoadingCandidates(true);
    setError(undefined);
    const timer = window.setTimeout(() => {
      void collaborationCandidateService
        .listCandidates(actorId, { name: search.trim() || undefined, offset: 0, limit: PAGE_SIZE })
        .then((res) => {
          if (cancelled) return;
          setIsLoadingCandidates(false);
          if (res.ok) {
            setCandidates(res.data.items);
            setCandidatesHasMore(res.data.hasMore);
            candidatesOffsetRef.current = res.data.offset;
          } else {
            setError(res.error.friendlyMessage);
          }
        });
    }, SEARCH_DEBOUNCE_MS);

    return () => {
      cancelled = true;
      window.clearTimeout(timer);
      setIsLoadingCandidates(false);
    };
  }, [actorId, enabled, filterMode, retryNonce, search, tab]);

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
            const firstPage = await collaborationCandidateService.listCandidates(actorId, { offset: 0, limit: PAGE_SIZE });
            if (cancelled) return;
            if (!firstPage.ok) throw new Error(firstPage.error.friendlyMessage);
            base = firstPage.data.items;
            candidatesOffsetRef.current = base.length;
            setCandidates(base);
            setCandidatesHasMore(firstPage.data.hasMore);
          }
          const res = await collaborationCandidateService.recommendCandidates({
            actorId,
            actorKind: effectiveActorKind,
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
  }, [actorId, effectiveActorKind, enabled, filterMode, recommendNonce, search, tab]);

  // 协作目标智能推荐：与画像搜索共用推荐链路（目标文本作为画像关键词）；来源标记为 goal，
  // 供弹窗层在推荐落地时自动全选（画像筛选搜索路径不自动选中）。
  const recommendByGoal = useCallback((goal: string) => {
    setTab('candidates');
    setFilterMode('profile');
    setRecommendSource('goal');
    setSearch(goal);
  }, []);

  // 手动输入搜索词归为画像筛选来源，覆盖此前的 goal 标记，防止 goal 推荐后的手输关键词被误判自动选中。
  const updateSearch = useCallback((value: string) => {
    setRecommendSource('profile');
    setSearch(value);
  }, []);

  const retryRecommend = useCallback(() => {
    setRecommendNonce((current) => current + 1);
  }, []);

  const loadMore = useCallback(() => {
    if (!enabled || !actorId || actorId === 'me' || isLoadingMore || error) return;
    // 画像推荐结果不分页，保持推荐结果与原列表数据隔离。
    if (tab === 'candidates' && filterMode === 'profile' && search.trim()) return;
    const loadingFriends = tab === 'friends';
    const loadingMine = tab === 'mine';
    const loadingCandidates = tab === 'candidates';
    if (!loadingFriends && !loadingMine && !loadingCandidates) return;
    const hasMore = loadingFriends ? friendsHasMore : loadingMine ? mineHasMore : candidatesHasMore;
    const initialLoading = loadingFriends ? isLoadingFriends : loadingMine ? isLoadingMine : isLoadingCandidates;
    if (!hasMore || initialLoading) return;

    setIsLoadingMore(true);
    const nextOffset =
      (loadingFriends ? friendsOffsetRef.current : loadingMine ? mineOffsetRef.current : candidatesOffsetRef.current) +
      PAGE_SIZE;
    const request = loadingFriends
      ? collaborationCandidateService.listFriends(actorId, {
          offset: nextOffset,
          limit: PAGE_SIZE,
          detailSource: 'collaboration',
        })
      : loadingMine
      ? collaborationCandidateService.listMine({ offset: nextOffset, limit: PAGE_SIZE })
      : collaborationCandidateService.listCandidates(actorId, {
          name: search.trim() || undefined,
          offset: nextOffset,
          limit: PAGE_SIZE,
        });

    void request.then((res) => {
      setIsLoadingMore(false);
      if (!res.ok) {
        setError(res.error.friendlyMessage);
        return;
      }
      if (loadingFriends) {
        setFriends((current) => appendUnique(current, res.data.items));
        setFriendsHasMore(res.data.hasMore);
        friendsOffsetRef.current = res.data.offset;
      } else if (loadingMine) {
        setMine((current) => appendUnique(current, res.data.items));
        setMineHasMore(res.data.hasMore);
        mineOffsetRef.current = res.data.offset;
      } else {
        setCandidates((current) => appendUnique(current, res.data.items));
        setCandidatesHasMore(res.data.hasMore);
        candidatesOffsetRef.current = res.data.offset;
      }
    });
  }, [
    actorId,
    candidatesHasMore,
    enabled,
    error,
    filterMode,
    friendsHasMore,
    isLoadingCandidates,
    isLoadingFriends,
    isLoadingMine,
    isLoadingMore,
    mineHasMore,
    search,
    tab,
  ]);

  const retry = () => {
    friendsSeenRef.current = null;
    setError(undefined);
    setRetryNonce((current) => current + 1);
  };

  return {
    tab,
    setTab,
    search,
    setSearch: updateSearch,
    filterMode,
    setFilterMode,
    friends,
    mine,
    candidates,
    isLoadingFriends,
    isLoadingMine,
    isLoadingCandidates,
    isLoadingMore,
    friendsHasMore,
    mineHasMore,
    candidatesHasMore,
    error,
    retry,
    loadMore,
    recommendations,
    isLoadingRecommend,
    recommendError,
    recommendSource,
    recommendByGoal,
    retryRecommend,
  };
}
