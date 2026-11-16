import {
  buildConversationSessionRequestRange,
  supportsConversationInstanceScope,
  type ChatBotView,
} from '@/services/workspace/botSessionService';
import { useConversationStore } from '@/stores/conversationStore';
import { useMemo } from 'react';

/** Keep session requests scoped to the selected Service Bot instance. */
export function useBotSessionRange(bot: ChatBotView | null) {
  const instanceSelection = useConversationStore((state) =>
    bot ? state.instanceSelectionByBotId[bot.botId] : undefined,
  );
  return useMemo(() => {
    if (!bot || !instanceSelection || !supportsConversationInstanceScope(bot)) return undefined;
    return buildConversationSessionRequestRange(bot, instanceSelection);
  }, [bot, instanceSelection]);
}
