import {
  collaborationCandidateService,
  type CollaborationBotView,
} from '@/services/workspace/collaborationCandidateService';
import type { MutableRefObject } from 'react';
import { useCallback, useEffect, useRef, useState } from 'react';
import type { CandidateFilterMode, CandidateTab } from './useGroupCollaborationPicker.types';

const PAGE_SIZE = 50;
const SEARCH_DEBOUNCE_MS = 350;

const appendUnique = (current: CollaborationBotView[], incoming: CollaborationBotView[]) => [
  ...current,
  ...incoming.filter((bot) => !current.some((item) => item.id === bot.id)),
];

export interface PickerListsController {
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
  /** 推荐交集输入：当前已加载的可协作列表（ref 提供最新值）。 */
  candidatesRef: MutableRefObject<CollaborationBotView[]>;
  /** 候选列表为空时的首屏落地回调（供推荐链路兜底拉取后写入列表/分页状态）。 */
  applyCandidatesSnapshot: (items: CollaborationBotView[], hasMore: boolean, offset: number) => void;
}

interface UsePickerListsInput {
  enabled: boolean;
  actorId: string | null | undefined;
  showMineTab: boolean;
  tab: CandidateTab;
  filterMode: CandidateFilterMode;
  search: string;
}

/**
 * 发起协作弹窗的列表数据层：好友/已管理/可协作三列表的首屏加载、防抖搜索与上拉分页。
 * 状态自持，Picker 与推荐链路只消费 Controller。
 */
export function usePickerLists(input: UsePickerListsInput): PickerListsController {
  const { enabled, actorId, showMineTab, tab, filterMode, search } = input;
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
  const friendsOffsetRef = useRef(0);
  const mineOffsetRef = useRef(0);
  const candidatesOffsetRef = useRef(0);
  // 推荐交集以当前已加载的可协作列表为输入，ref 提供最新值。
  const candidatesRef = useRef<CollaborationBotView[]>([]);

  useEffect(() => {
    candidatesRef.current = candidates;
  }, [candidates]);

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

  const retry = useCallback(() => {
    friendsSeenRef.current = null;
    setError(undefined);
    setRetryNonce((current) => current + 1);
  }, []);

  const applyCandidatesSnapshot = useCallback((items: CollaborationBotView[], hasMore: boolean, offset: number) => {
    candidatesOffsetRef.current = offset;
    setCandidates(items);
    setCandidatesHasMore(hasMore);
  }, []);

  return {
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
    candidatesRef,
    applyCandidatesSnapshot,
  };
}
