import type { ConversationBotView } from '@/domain/conversation/types';
import { useConversationStore } from '@/stores/conversationStore';
import { useMemo } from 'react';
import { useManagedBotOthers } from './useManagedBotOthers';

/** 新入口使用 source 平铺；仅旧 friend= 深链需要好友目录/分组请求。 */
export function useLegacyFriendOthers(userId: string | null, bots: ConversationBotView[]) {
  const legacyBotId = useConversationStore((state) =>
    state.selectedOrigin === 'others' && state.selectedFriendUserId ? state.selectedBotId : null,
  );
  const managedBots = useMemo(() => bots.filter((view) => view.bot.botId === legacyBotId), [bots, legacyBotId]);
  return useManagedBotOthers({ userId, managedBots, enabled: Boolean(legacyBotId) });
}
