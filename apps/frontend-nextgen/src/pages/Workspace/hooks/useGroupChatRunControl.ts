import type { GroupChatProvider } from '@/services/workspace/groupChatProvider';
import { useGroupChatAbort } from './useGroupChatAbort';
import { useMessageDeliveries } from './useMessageDeliveries';

/**
 * 群聊运行控制组合 Hook：按 Bot 终止（abort）+ 消息投递队列（deliveries）。
 * 仅组合既有 Hook 以控制 useGroupChat 体积（门禁阈值 250 行），不引入新业务规则。
 */
export function useGroupChatRunControl(provider: GroupChatProvider | null, sessionId: string | null) {
  const { abortBot, abortingBotIds, unsupportedAbortBotId, dismissAbortUnsupported } = useGroupChatAbort(
    provider,
    sessionId,
  );
  const { activeDeliveries, queuedDeliveries, processingDeliveries, cancelDelivery, cancellingDeliveryIds } =
    useMessageDeliveries(provider, sessionId);

  return {
    abortBot,
    abortingBotIds,
    unsupportedAbortBotId,
    dismissAbortUnsupported,
    activeDeliveries,
    queuedDeliveries,
    processingDeliveries,
    cancelDelivery,
    cancellingDeliveryIds,
  };
}
