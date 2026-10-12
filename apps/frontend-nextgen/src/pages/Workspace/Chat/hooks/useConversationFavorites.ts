import { supportsBotSessionFavorites } from '@/domain/botEngine';
import type { ConversationBotSection, ConversationBotView, ConversationSessionListState } from '@/domain/conversation';
import { isManagedConversationSection } from '@/domain/conversation/types';
import { conversationFavoriteService } from '@/services/workspace/conversationFavoriteService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

export interface ConversationFavoritesModel {
  toggleFavorite(botId: string, section: ConversationBotSection, sessionId: string): Promise<boolean>;
  isPending(botId: string, section: ConversationBotSection, sessionId: string): boolean;
}

const keyOf = (botId: string, section: ConversationBotSection, sessionId: string) =>
  JSON.stringify([section, botId, sessionId]);

function currentList(botId: string, section: ConversationBotSection): ConversationSessionListState | undefined {
  const store = useConversationStore.getState();
  if (section === 'friend') return store.friendBotSessionsByBotId[botId];
  const cache = store.sessionsByBotId[botId];
  if ((store.originByManagedBotId[botId] ?? 'mine') !== 'mine' || cache?.origin !== 'mine') return undefined;
  if (cache.scope !== (store.effectiveScopeByManagedBotId[botId] ?? 'all')) return undefined;
  return cache.sessions;
}

function favoriteListLoading(botId: string, section: ConversationBotSection): boolean {
  const cache = useConversationStore.getState().sessionsByBotId[botId];
  return (
    isManagedConversationSection(section) &&
    cache?.origin === 'mine' &&
    cache.scope === 'favorite' &&
    cache.sessions.isLoadingMore
  );
}

/** 收藏请求独立于列表加载；只回写当前用户、当前可写视图的最新缓存。 */
export function useConversationFavorites(options: {
  userId: string | null;
  managedBots: ConversationBotView[];
  friendBots: ConversationBotView[];
}): ConversationFavoritesModel {
  const { userId, managedBots, friendBots } = options;
  const contextRef = useRef(options);
  contextRef.current = options;
  const generation = useRef(0);
  const requests = useRef(new Map<string, symbol>());
  const [pending, setPending] = useState<Record<string, true>>({});
  useEffect(() => {
    generation.current += 1;
    requests.current.clear();
    setPending({});
    return () => {
      generation.current += 1;
    };
  }, [userId]);

  const toggleFavorite = useCallback(
    async (botId: string, section: ConversationBotSection, sessionId: string) => {
      const view = (isManagedConversationSection(section) ? managedBots : friendBots).find(
        (item) => item.bot.botId === botId,
      );
      const current = currentList(botId, section)?.items.find((item) => item.sessionId === sessionId);
      const key = keyOf(botId, section, sessionId);
      if (
        !userId ||
        !view?.bot.chatable ||
        !supportsBotSessionFavorites(view.bot.engine) ||
        current?.favorite === undefined ||
        requests.current.has(key) ||
        favoriteListLoading(botId, section)
      )
        return false;
      const request = Symbol(key);
      const started = generation.current;
      const isCurrent = () => generation.current === started && contextRef.current.userId === userId;
      requests.current.set(key, request);
      setPending((state) => ({ ...state, [key]: true }));
      try {
        const result = await conversationFavoriteService.toggle(view.bot, userId, sessionId, !current.favorite);
        if (!isCurrent()) return false;
        if (!result.ok) {
          toast.error(result.error.friendlyMessage);
          return false;
        }
        const store = useConversationStore.getState();
        const list = currentList(botId, section);
        if (!list?.items.some((item) => item.sessionId === sessionId)) return true;
        const cache = store.sessionsByBotId[botId];
        const remove = isManagedConversationSection(section) && cache?.scope === 'favorite' && !result.data;
        const next = {
          ...list,
          items: remove
            ? list.items.filter((item) => item.sessionId !== sessionId)
            : list.items.map((item) => (item.sessionId === sessionId ? { ...item, favorite: result.data } : item)),
          total: remove ? Math.max(0, list.total - 1) : list.total,
        };
        if (isManagedConversationSection(section)) store.setManagedBotCache(botId, { ...cache, sessions: next });
        else store.setFriendBotSessions(botId, next);
        if (
          remove &&
          store.selectedSection === section &&
          store.selectedBotId === botId &&
          store.selectedSessionId === sessionId
        ) {
          store.selectConversation({
            botId,
            section,
            origin: 'mine',
            scope: 'favorite',
            friendUserId: null,
            sessionId: null,
          });
        }
        return true;
      } catch {
        if (isCurrent()) toast.error('更新会话收藏失败，请稍后重试。');
        return false;
      } finally {
        if (isCurrent() && requests.current.get(key) === request) {
          requests.current.delete(key);
          setPending((state) => {
            const next = { ...state };
            delete next[key];
            return next;
          });
        }
      }
    },
    [userId, managedBots, friendBots],
  );

  return {
    toggleFavorite,
    isPending: (botId, section, sessionId) =>
      Boolean(pending[keyOf(botId, section, sessionId)]) || favoriteListLoading(botId, section),
  };
}
