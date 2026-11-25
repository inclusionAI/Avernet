// Bot 可会话可用性的纯判定（原 pages/Workspace/Chat/components/ConversationBotUnavailableTooltip 内联逻辑下沉）。
// shell 层（全局搜索）与对话页目录共用同一状态词表，避免两处口径漂移；本文件只含纯逻辑，不碰 I/O。
import type { ConversationBotSection } from './types';

/** Bot 生命周期中不能进入对话的状态，及其用户可读的不可用原因。 */
export const BOT_UNAVAILABLE_REASONS: Record<string, string> = {
  FAILED: '该 Bot 因创建或运行异常暂时无法使用；可在 Bot 管理中尝试运维恢复',
  PENDING: '该 Bot 正在创建或重启中，暂时无法使用，完成后可刷新重试',
  OFFLINE: '该 Bot 已下线，暂时无法发起会话',
  RECYCLED: '该 Bot 已回收，暂时无法发起会话',
  RELEASED: '该 Bot 已回收，暂时无法发起会话',
};

/** resolveBotUnavailable 只依赖的结构面；ChatBotView 等视图天然满足，避免 domain 反向依赖 services。 */
export interface ConversationAvailabilityBot {
  status?: string;
  botType?: string;
  onlineAvailable?: boolean | null;
}

/** 好友服务 Bot 无 online 发布时优先提示；其他情况按 Bot 生命周期判定（缺省状态视为可用）。 */
export function resolveBotUnavailable(
  bot: ConversationAvailabilityBot,
  section: ConversationBotSection,
): { reason: string | null; unavailable: boolean } {
  const reason =
    section === 'friend' && bot.botType === 'service' && bot.onlineAvailable === false
      ? '该服务 Bot 尚未处于正式运行状态'
      : bot.status
        ? BOT_UNAVAILABLE_REASONS[bot.status.toUpperCase()] ?? null
        : null;
  return { reason, unavailable: reason !== null };
}
