import { isChatEngineVersionCandidate, type ChatEngineVersionInput } from '@/domain/chatEngineVersionTarget';
import { botEngineVersionService } from '@/services/botWorkshop/botEngineVersionService';
import { loadChatEngineContext } from '@/services/workspace/chatEngineContextService';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { useErrorNotifyStore } from '@/stores/errorNotifyStore';
import { useEffect, useState } from 'react';

export function useSelectedChatEngineContext(input: ChatEngineVersionInput): ChatBotView | null {
  const { bot, userId } = input;
  const eligible = isChatEngineVersionCandidate(input);
  const key = JSON.stringify([userId, input.section, input.origin, input.sessionId, bot, eligible]);
  const [resolved, setResolved] = useState<{ key: string; bot: ChatBotView | null }>();
  useEffect(() => {
    const controller = new AbortController();
    if (eligible && bot && userId) {
      void loadChatEngineContext(bot, userId, controller.signal).then((context) => {
        if (!controller.signal.aborted) setResolved({ key, bot: context });
      }).catch((error) => {
        const refusal = botEngineVersionService.getInspectionRefusal(error);
        if (refusal?.notificationKey) useErrorNotifyStore.getState().cancel(refusal.notificationKey);
        if (!controller.signal.aborted) setResolved({ key, bot: null });
      });
    }
    return () => controller.abort();
  }, [key]);
  return eligible && resolved?.key === key ? resolved.bot : null;
}
