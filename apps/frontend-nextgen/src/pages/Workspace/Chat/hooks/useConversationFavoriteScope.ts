import { supportsBotSessionFavorites } from '@/domain/botEngine';
import type { ConversationBotView } from '@/domain/conversation';
import { useConversationStore } from '@/stores/conversationStore';
import { useEffect } from 'react';

/** 目录返回引擎信息后修正旧 URL/内存范围;先于会话加载 effect 执行。
 *  好友 Bot 同样遵守收藏能力边界:不支持收藏的引擎回退 all,不向收藏接口发请求。 */
export function useConversationFavoriteScope(
  managedBots: ConversationBotView[],
  friendBots: ConversationBotView[],
): void {
  const scopes = useConversationStore((state) => state.scopeByManagedBotId);
  const effectiveScopes = useConversationStore((state) => state.effectiveScopeByManagedBotId);
  const friendScopes = useConversationStore((state) => state.scopeByFriendBotId);
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
    for (const { bot } of friendBots) {
      if (supportsBotSessionFavorites(bot.engine)) continue;
      // 好友范围仅存内存(URL 不投影收藏范围),修正记忆范围即足够。
      if (friendScopes[bot.botId] === 'favorite') store.setFriendBotScope(bot.botId, 'all');
    }
  }, [managedBots, friendBots, friendScopes, scopes, effectiveScopes, selectedBotId, selectedScope]);
}
