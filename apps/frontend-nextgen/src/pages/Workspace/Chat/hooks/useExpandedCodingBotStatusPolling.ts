import { getCapabilities } from '@/capabilities';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useConversationStore } from '@/stores/conversationStore';
import { useMemo } from 'react';

import { useCodingSessionStatusPolling } from './useCodingSessionStatusPolling';

/**
 * Drives Coding Bot session status from the expanded sidebar bot, independently
 * of which session is selected in the main stage. The capability is assembled
 * once at startup, so its session-status provider reference is intentionally
 * captured once for the lifetime of the conversation page mount.
 */
export function useExpandedCodingBotStatusPolling({
  chatBots,
  userId,
}: {
  chatBots: ChatBotView[];
  userId: string | null;
}): void {
  const provider = useMemo(
    () => getCapabilities().getCodingBotConversationHost().value?.sessionStatus ?? null,
    [],
  );
  const expandedBotIds = useConversationStore((state) => state.expandedBotIds);
  const activeBot = useMemo(() => {
    const expandedBotId = Object.keys(expandedBotIds)[0] ?? null;
    if (!expandedBotId) return null;
    const bot = chatBots.find((item) => item.botId === expandedBotId);
    return bot?.isAgentCodingBot === true
      ? { botId: bot.botId, realBotId: bot.realBotId, ownerId: bot.ownerId }
      : null;
  }, [chatBots, expandedBotIds]);

  useCodingSessionStatusPolling({ activeBot, userId, provider });
}
