import type { ConversationBotView } from '@/domain/conversation';
import {
  conversationBotSelectionService,
  type ConversationBotSelectionTarget,
} from '@/services/workspace/conversationBotSelectionService';
import { useConversationStore } from '@/stores/conversationStore';
import { useCallback, useEffect, useRef } from 'react';

/** 只对用户主动展开记录首选意图；不监听所有展开来抢占明确的深链/手动选中。 */
export function useConversationBotExpansion(userId: string | null) {
  const state = useConversationStore();
  const pending = useRef<{ userId: string; target: ConversationBotSelectionTarget } | null>(null);
  useEffect(() => {
    const intent = pending.current;
    if (!intent) return;
    if (intent.userId !== userId || conversationBotSelectionService.complete(intent.target)) pending.current = null;
  }, [state, userId]);
  return useCallback(
    (view: ConversationBotView) => {
      if (!userId || !view.bot.chatable || view.bot.isAgentCodingBot) return;
      const target = conversationBotSelectionService.begin(view);
      pending.current = conversationBotSelectionService.complete(target) ? null : { userId, target };
    },
    [userId],
  );
}
