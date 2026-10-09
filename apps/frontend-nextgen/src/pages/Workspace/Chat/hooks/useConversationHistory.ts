// 只读历史消息 Hook(origin=others 场景):委托 managedBotConversationService.listOtherMessages
// 做只读分页(旧→新升序),hasMore 直接沿用 Service 依据 raw count/total 的产出。
// 纯只读:不实例化任何 chat provider,不请求 Connection/IAM token,也不写 Store。
// 并发保护:Bot/好友/会话三元组任一变化即换代,陈旧响应写回被丢弃;
// loadMore 由 in-flight guard 防重复,page 由 Service 回传结果推进。
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { managedBotConversationService } from '@/services/workspace/managedBotConversationService';
import type { ChatMessage } from '@tc-chat/core';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface UseConversationHistoryOptions {
  bot: ChatBotView | null;
  friendUserId: string | null;
  sessionId: string | null;
}

export interface UseConversationHistoryResult {
  messages: ChatMessage[];
  loading: boolean;
  error: string | null;
  hasMore: boolean;
  isLoadingMore: boolean;
  loadMoreError: string | null;
  retry: () => void;
  loadMore: () => Promise<void>;
}

export function useConversationHistory({
  bot,
  friendUserId,
  sessionId,
}: UseConversationHistoryOptions): UseConversationHistoryResult {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [isLoadingMore, setIsLoadingMore] = useState(false);
  const [loadMoreError, setLoadMoreError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);

  const botRef = useRef(bot);
  botRef.current = bot;
  const generationRef = useRef(0);
  const pageRef = useRef(0);
  const loadMoreInFlightRef = useRef(false);

  // 首拉/重试:Bot/好友/会话任一变化(或 retryNonce 变化)即换代并清态;
  // 三元组不完整时只清态、不发请求。
  useEffect(() => {
    generationRef.current += 1;
    const generation = generationRef.current;
    pageRef.current = 0;
    loadMoreInFlightRef.current = false;
    setMessages([]);
    setLoading(false);
    setError(null);
    setHasMore(false);
    setIsLoadingMore(false);
    setLoadMoreError(null);
    const currentBot = botRef.current;
    if (!currentBot || !friendUserId || !sessionId) return;

    setLoading(true);
    void managedBotConversationService
      .listOtherMessages(currentBot, friendUserId, sessionId, 1)
      .then((result) => {
        if (generation !== generationRef.current) return;
        if (!result.ok) {
          setError(result.error.friendlyMessage);
          return;
        }
        pageRef.current = result.data.page;
        setMessages(result.data.messages);
        setHasMore(result.data.hasMore);
      })
      .finally(() => {
        if (generation === generationRef.current) setLoading(false);
      });
  }, [bot?.botId, friendUserId, retryNonce, sessionId]);

  const retry = useCallback(() => setRetryNonce((current) => current + 1), []);

  const loadMore = useCallback(async () => {
    const currentBot = botRef.current;
    if (!currentBot || !friendUserId || !sessionId || !hasMore || loadMoreInFlightRef.current) {
      return;
    }
    loadMoreInFlightRef.current = true;
    const generation = generationRef.current;
    const nextPage = pageRef.current + 1;
    setIsLoadingMore(true);
    setLoadMoreError(null);
    try {
      const result = await managedBotConversationService.listOtherMessages(
        currentBot,
        friendUserId,
        sessionId,
        nextPage,
      );
      if (generation !== generationRef.current) return;
      if (!result.ok) {
        setLoadMoreError(result.error.friendlyMessage);
        return;
      }
      pageRef.current = result.data.page;
      setHasMore(result.data.hasMore);
      // 更早的一页在旧→新升序的整体序列之前,前置拼接。
      setMessages((current) => [...result.data.messages, ...current]);
    } finally {
      loadMoreInFlightRef.current = false;
      if (generation === generationRef.current) setIsLoadingMore(false);
    }
  }, [friendUserId, hasMore, sessionId]);

  return { messages, loading, error, hasMore, isLoadingMore, loadMoreError, retry, loadMore };
}
