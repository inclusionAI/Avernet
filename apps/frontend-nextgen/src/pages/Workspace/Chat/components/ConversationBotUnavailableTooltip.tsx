// 不可用 Bot 行的整行悬停提示包装器:状态异常或好友服务 Bot 无 online 发布时解释原因
// (禁用不影响悬停);reason 为 null(可用 Bot)时原样透传 children,不挂 Tooltip。
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { ConversationBotSection } from '@/domain/conversation/types';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import type { ReactNode } from 'react';

/** 非可用状态的原因文案(FAILED=创建或运行异常+运维恢复指引;PENDING=创建或重启中+刷新重试指引)。 */
export const BOT_UNAVAILABLE_REASONS: Record<string, string> = {
  FAILED: '该 Bot 因创建或运行异常暂时无法使用；可在 Bot 管理中尝试运维恢复',
  PENDING: '该 Bot 正在创建或重启中，暂时无法使用，完成后可刷新重试',
};

/** 好友服务 Bot 无 online 发布时优先提示；其他情况保留 FAILED/PENDING 判定。 */
export function resolveBotUnavailable(
  bot: Pick<ChatBotView, 'status' | 'botType' | 'onlineAvailable'>,
  section: ConversationBotSection,
): { reason: string | null; unavailable: boolean } {
  const reason =
    section === 'friend' && bot.botType === 'service' && bot.onlineAvailable === false
      ? '非运行中服务 Bot'
      : bot.status
      ? BOT_UNAVAILABLE_REASONS[bot.status] ?? null
      : null;
  return { reason, unavailable: reason !== null };
}

/** 整行悬停提示:children 为 Bot 行,不可用时悬停约 0.3s 显示原因,可用时原样透传。 */
export function ConversationBotUnavailableTooltip({
  reason,
  children,
}: {
  reason: string | null;
  children: ReactNode;
}) {
  if (reason === null) return children;
  return (
    <TooltipProvider delayDuration={300}>
      <Tooltip>
        <TooltipTrigger asChild>{children}</TooltipTrigger>
        <TooltipContent className="max-w-[280px] whitespace-normal">{reason}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}