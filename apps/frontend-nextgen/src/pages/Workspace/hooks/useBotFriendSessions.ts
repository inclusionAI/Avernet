import {
  BOT_FRIEND_SESSION_PAGE_SIZE,
  botFriendConversationService,
  type BotFriendSessionView,
  type BotIdentityFriendView,
} from '@/services/workspace/botFriendConversationService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

export interface UseBotFriendSessionsOptions {
  botIdentityId: string | null;
  humanFriends: BotIdentityFriendView[];
  directorySettled: boolean;
  directoryError: string | null;
}

export interface UseBotFriendSessionsResult {
  expandedFriendUserId: string | null;
  sessions: BotFriendSessionView[];
  selectedSession: BotFriendSessionView | null;
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreError: string | null;
  toggleFriend: (friendUserId: string) => void;
  selectSession: (sessionId: string) => void;
  retry: () => void;
  loadMore: () => Promise<void>;
}

function appendUniqueSessions(
  current: BotFriendSessionView[],
  incoming: BotFriendSessionView[],
): BotFriendSessionView[] {
  const ids = new Set(current.map((session) => session.sessionId));
  return [...current, ...incoming.filter((session) => !ids.has(session.sessionId))];
}

export function useBotFriendSessions({
  botIdentityId,
  humanFriends,
  directorySettled,
  directoryError,
}: UseBotFriendSessionsOptions): UseBotFriendSessionsResult {
  const expandedFriendUserId = useWorkspaceStore((state) => state.expandedFriendUserId);
  const selectedSessionId = useWorkspaceStore((state) => state.selectedFriendUserSessionId);
  const setExpandedFriendUser = useWorkspaceStore((state) => state.setExpandedFriendUser);
  const selectFriendUserSession = useWorkspaceStore((state) => state.selectFriendUserSession);
  const [sessions, setSessions] = useState<BotFriendSessionView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [nextPage, setNextPage] = useState(2);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const generationRef = useRef(0);
  const loadMoreInFlightRef = useRef(false);
  const selectedSessionIdRef = useRef(selectedSessionId);
  selectedSessionIdRef.current = selectedSessionId;

  useEffect(() => {
    if (!directorySettled || directoryError || !expandedFriendUserId) return;
    if (humanFriends.some((friend) => friend.actorId === expandedFriendUserId)) return;
    setExpandedFriendUser(null);
  }, [directoryError, directorySettled, expandedFriendUserId, humanFriends, setExpandedFriendUser]);

  useEffect(() => {
    generationRef.current += 1;
    loadMoreInFlightRef.current = false;
    const generation = generationRef.current;
    setSessions([]);
    setLoading(false);
    setError(null);
    setHasMore(false);
    setNextPage(2);
    setIsLoadingMore(false);
    setLoadMoreError(null);
    if (!botIdentityId || !expandedFriendUserId || !directorySettled || directoryError) return;

    let cancelled = false;
    setLoading(true);
    const selectedAtStart = selectedSessionIdRef.current;
    void (async () => {
      const collected: BotFriendSessionView[] = [];
      let page = 1;
      let more = false;
      while (true) {
        const result = await botFriendConversationService.listSessionsPage(
          botIdentityId,
          expandedFriendUserId,
          page,
          BOT_FRIEND_SESSION_PAGE_SIZE,
        );
        if (cancelled || generation !== generationRef.current) return;
        if (!result.ok) {
          setError(result.error.friendlyMessage);
          return;
        }
        collected.push(...result.data.items);
        more = result.data.hasMore;
        const selectedFound = selectedAtStart
          ? collected.some((session) => session.sessionId === selectedAtStart)
          : true;
        if (selectedFound || !more) break;
        page += 1;
      }
      setSessions(appendUniqueSessions([], collected));
      setHasMore(more);
      setNextPage(page + 1);
      if (selectedAtStart && !collected.some((session) => session.sessionId === selectedAtStart) && !more) {
        selectFriendUserSession(null);
      }
    })().finally(() => {
      if (!cancelled && generation === generationRef.current) setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [botIdentityId, directoryError, directorySettled, expandedFriendUserId, retryNonce, selectFriendUserSession]);

  const selectedSession = useMemo(
    () => sessions.find((session) => session.sessionId === selectedSessionId) ?? null,
    [selectedSessionId, sessions],
  );

  const toggleFriend = useCallback(
    (friendUserId: string) => setExpandedFriendUser(friendUserId),
    [setExpandedFriendUser],
  );
  const selectSession = useCallback(
    (sessionId: string) => selectFriendUserSession(sessionId),
    [selectFriendUserSession],
  );
  const retry = useCallback(() => setRetryNonce((current) => current + 1), []);

  const loadMore = useCallback(async () => {
    if (!botIdentityId || !expandedFriendUserId || !hasMore || loadMoreInFlightRef.current) return;
    loadMoreInFlightRef.current = true;
    const generation = generationRef.current;
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const result = await botFriendConversationService.listSessionsPage(
        botIdentityId,
        expandedFriendUserId,
        nextPage,
        BOT_FRIEND_SESSION_PAGE_SIZE,
      );
      if (generation !== generationRef.current) return;
      if (!result.ok) {
        setLoadMoreError(result.error.friendlyMessage);
        return;
      }
      setSessions((current) => appendUniqueSessions(current, result.data.items));
      setHasMore(result.data.hasMore);
      setNextPage(nextPage + 1);
    } finally {
      loadMoreInFlightRef.current = false;
      if (generation === generationRef.current) setIsLoadingMore(false);
    }
  }, [botIdentityId, expandedFriendUserId, hasMore, nextPage]);

  return {
    expandedFriendUserId,
    sessions,
    selectedSession,
    loading,
    error,
    hasMore,
    isLoadingMore,
    loadMoreError,
    toggleFriend,
    selectSession,
    retry,
    loadMore,
  };
}
