import { supportsBotSessionFavorites } from '@/domain/botEngine';
import type { ConversationBotView } from '@/domain/conversation';
import { useConversationStore } from '@/stores/conversationStore';
import { useEffect } from 'react';

/** 目录返回引擎信息后修正旧 URL/内存范围；先于会话加载 effect 执行。 */
export function useConversationFavoriteScope(managedBots: ConversationBotView[]): void {
  const scopes = useConversationStore((state) => state.scopeByManagedBotId);
  const effectiveScopes = useConversationStore((state) => state.effectiveScopeByManagedBotId);
  const selectedBotId = useConversationStore((state) => state.selectedBotId);
  const selectedScope = useConversationStore((state) => state.selectedScope);
  useEffect(() => {
    const store = useConversationStore.getState();
    for (const { bot } of managedBots) {
      if (supportsBotSessionFavorites(bot.engine)) continue;
      if (
        scopes[bot.botId] === 'favorite' ||
        effectiveScopes[bot.botId] === 'favorite' ||
        (selectedBotId === bot.botId && selectedScope === 'favorite')
      ) {
        store.setManagedBotScope(bot.botId, 'all');
      }
    }
  }, [managedBots, scopes, effectiveScopes, selectedBotId, selectedScope]);
}
