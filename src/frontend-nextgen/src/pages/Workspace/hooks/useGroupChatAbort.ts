import { toGroupChatAbortErrorView } from '@/services/workspace/groupChatAbortService';
import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'sonner';

/** 群聊按 Bot 终止的本地去重与反馈；切换 Session 时立即清理旧 loading。 */
export function useGroupChatAbort(provider: GroupChatProvider | null, sessionId: string | null) {
  const [abortingBotIds, setAbortingBotIds] = useState<Set<string>>(() => new Set());
  const [unsupportedAbortBotId, setUnsupportedAbortBotId] = useState<string | null>(null);
  const abortingBotIdsRef = useRef(new Set<string>());

  useEffect(() => {
    abortingBotIdsRef.current.clear();
    setAbortingBotIds(new Set());
    setUnsupportedAbortBotId(null);
  }, [sessionId]);

  const abortBot = useCallback(
    async (botId: string) => {
      if (!provider || !botId || abortingBotIdsRef.current.has(botId)) return;
      abortingBotIdsRef.current.add(botId);
      setAbortingBotIds(new Set(abortingBotIdsRef.current));
      try {
        const result = await provider.abortBot(botId);
        if (result.abortedRunIds.length > 0) {
          toast.success(`已终止 ${result.abortedRunIds.length} 个运行中的输出`);
        } else {
          toast.info('该 Bot 当前已无可终止的输出');
        }
      } catch (error: unknown) {
        const view = toGroupChatAbortErrorView(error);
        if (view.restartRequired) setUnsupportedAbortBotId(botId);
        if (view.partial && view.abortedCount > 0) {
          toast.warning(`已终止 ${view.abortedCount} 个输出，部分输出终止失败，可重试`);
        } else if (!view.restartRequired) {
          toast.error(view.message);
        }
      } finally {
        abortingBotIdsRef.current.delete(botId);
        setAbortingBotIds(new Set(abortingBotIdsRef.current));
      }
    },
    [provider],
  );

  return {
    abortBot,
    abortingBotIds,
    unsupportedAbortBotId,
    dismissAbortUnsupported: () => setUnsupportedAbortBotId(null),
  };
}
