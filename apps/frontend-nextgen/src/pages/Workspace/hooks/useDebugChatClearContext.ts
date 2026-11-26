import { botSessionService, type ChatBotView } from '@/services/workspace/botSessionService';
import { useCallback } from 'react';
import { toast } from 'sonner';

/** Debug-chat clear stays scoped to its own session, without changing workspace selection. */
export function useDebugChatClearContext(
  bot: ChatBotView,
  userId: string | null,
  sessionId: string | undefined,
  reloadHistory: () => Promise<void>,
) {
  return useCallback(async () => {
    if (!userId || !sessionId) return;
    const result = await botSessionService.clearContext(bot, userId, sessionId);
    if (!result.ok) {
      toast.error(result.error.friendlyMessage);
      return;
    }
    await reloadHistory();
  }, [bot, userId, sessionId, reloadHistory]);
}
