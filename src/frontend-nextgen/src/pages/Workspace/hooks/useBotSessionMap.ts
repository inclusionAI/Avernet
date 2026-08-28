import type { BotChatSessionView, ChatBotView } from '@/services/workspace/botSessionService';
import { botSessionService } from '@/services/workspace/botSessionService';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { useCallback, useEffect, useRef, useState } from 'react';

export interface UseBotSessionMapResult {
  rawByBotId: Record<string, BotChatSessionView[]>;
  isLoading: boolean;
  updateBotSessions: (botId: string, fn: (list: BotChatSessionView[]) => BotChatSessionView[]) => void;
  reloadBot: (bot: ChatBotView, userId: string) => Promise<void>;
  /** 按 section 展开 bot，记录归属 section 并懒加载会话。 */
  toggleBotExpanded: (botId: string, sectionKey?: string) => void;
}

/** 以 botId 键控缓存各 bot 会话;展开的 bot 未缓存时静默加载一次;身份切换清缓存。 */
export function useBotSessionMap(
  chatBots: ChatBotView[],
  expandedBotIds: string[],
  activeIdentityId: string | null,
): UseBotSessionMapResult {
  const [rawByBotId, setRawByBotId] = useState<Record<string, BotChatSessionView[]>>({});
  const [isLoading, setIsLoading] = useState(false);
  const inFlightRef = useRef<Set<string>>(new Set());

  useEffect(() => {
    setRawByBotId({});
    inFlightRef.current.clear();
  }, [activeIdentityId]);

  useEffect(() => {
    if (!activeIdentityId) return;
    for (const bot of chatBots) {
      if (!bot.chatable) continue;
      if (!expandedBotIds.includes(bot.botId)) continue;
      if (rawByBotId[bot.botId] !== undefined) continue;
      if (inFlightRef.current.has(bot.botId)) continue;
      inFlightRef.current.add(bot.botId);
      setIsLoading(true);
      botSessionService
        .listSessions(bot, activeIdentityId)
        .then((res) => {
          setRawByBotId((cur) => ({ ...cur, [bot.botId]: res.ok ? res.data : [] }));
        })
        .finally(() => {
          inFlightRef.current.delete(bot.botId);
          setIsLoading(false);
        });
    }
  }, [chatBots, expandedBotIds, activeIdentityId, rawByBotId]);

  const updateBotSessions = useCallback(
    (botId: string, fn: (list: BotChatSessionView[]) => BotChatSessionView[]) =>
      setRawByBotId((cur) => ({ ...cur, [botId]: fn(cur[botId] ?? []) })),
    [],
  );
  const reloadBot = useCallback(async (bot: ChatBotView, userId: string): Promise<void> => {
    const res = await botSessionService.listSessions(bot, userId);
    setRawByBotId((cur) => ({ ...cur, [bot.botId]: res.ok ? res.data : [] }));
  }, []);

  const toggleBotExpanded = useCallback(
    (botId: string, sectionKey: string = 'mine') => {
      const store = useWorkspaceStore.getState();
      const willExpand = !store.expandedBotIds[botId] || store.expandedBotSectionKey[botId] !== sectionKey;
      store.toggleBotExpanded(botId);
      if (willExpand) useWorkspaceStore.getState().setBotExpandedSection(botId, sectionKey);
      if (willExpand) {
        const bot = chatBots.find((b) => b.botId === botId);
        if (bot && activeIdentityId)
          void botSessionService
            .listSessions(bot, activeIdentityId)
            .then((res) => setRawByBotId((cur) => ({ ...cur, [botId]: res.ok ? res.data : [] })));
      }
    },
    [chatBots, activeIdentityId],
  );

  return { rawByBotId, isLoading, updateBotSessions, reloadBot, toggleBotExpanded };
}
