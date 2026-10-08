import {
  BOT_FRIEND_MESSAGE_PAGE_SIZE,
  botFriendConversationService,
} from '@/services/workspace/botFriendConversationService';
import type { ChatMessage } from '@tc-chat/core';
import { useCallback, useEffect, useRef, useState } from 'react';
import { prependUniqueMessages } from './groupChatHistoryUtils';

export interface UseBotFriendHistoryOptions {
  botIdentityId: string | null;
  friendUserId: string | null;
  sessionId: string | null;
}

export interface UseBotFriendHistoryResult {
  messages: ChatMessage[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreError: string | null;
  retry: () => void;
  loadMore: () => Promise<void>;
}

export function useBotFriendHistory({
  botIdentityId,
  friendUserId,
  sessionId,
}: UseBotFriendHistoryOptions): UseBotFriendHistoryResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const generationRef = useRef(0);
  const pageRef = useRef(1);
  const loadedRawCountRef = useRef(0);
  const totalRef = useRef(0);
  const loadMoreInFlightRef = useRef(false);

  useEffect(() => {
    generationRef.current += 1;
    const generation = generationRef.current;
    pageRef.current = 1;
    loadedRawCountRef.current = 0;
    totalRef.current = 0;
    loadMoreInFlightRef.current = false;
    setMessages([]);
    setLoading(false);
    setError(null);
    setHasMore(false);
    setIsLoadingMore(false);
    setLoadMoreError(null);
    if (!botIdentityId || !friendUserId || !sessionId) return;

    let cancelled = false;
    setLoading(true);
    void botFriendConversationService
      .listMessagesPage(botIdentityId, friendUserId, sessionId, 1, BOT_FRIEND_MESSAGE_PAGE_SIZE)
      .then((result) => {
        if (cancelled || generation !== generationRef.current) return;
        if (!result.ok) {
          setError(result.error.friendlyMessage);
          return;
        }
        pageRef.current = 1;
        loadedRawCountRef.current = result.data.rawCount;
        totalRef.current = result.data.total;
        setMessages(result.data.messages);
        setHasMore(loadedRawCountRef.current < totalRef.current);
      })
      .finally(() => {
        if (!cancelled && generation === generationRef.current) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [botIdentityId, friendUserId, retryNonce, sessionId]);

  const retry = useCallback(() => setRetryNonce((current) => current + 1), []);

  const loadMore = useCallback(async () => {
    if (!botIdentityId || !friendUserId || !sessionId || !hasMore || loadMoreInFlightRef.current) return;
    loadMoreInFlightRef.current = true;
    const generation = generationRef.current;
    const nextPage = pageRef.current + 1;
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const result = await botFriendConversationService.listMessagesPage(
        botIdentityId,
        friendUserId,
        sessionId,
        nextPage,
        BOT_FRIEND_MESSAGE_PAGE_SIZE,
      );
      if (generation !== generationRef.current) return;
      if (!result.ok) {
        setLoadMoreError(result.error.friendlyMessage);
        return;
      }
      pageRef.current = nextPage;
      loadedRawCountRef.current += result.data.rawCount;
      totalRef.current = result.data.total;
      setMessages((current) => prependUniqueMessages(current, result.data.messages));
      setHasMore(loadedRawCountRef.current < totalRef.current);
    } finally {
      loadMoreInFlightRef.current = false;
      if (generation === generationRef.current) setIsLoadingMore(false);
    }
  }, [botIdentityId, friendUserId, hasMore, sessionId]);

  return { messages, loading, error, hasMore, isLoadingMore, loadMoreError, retry, loadMore };
}
