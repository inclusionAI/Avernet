// 不可用 Bot 行的整行悬停提示包装器:状态异常或好友服务 Bot 无 online 发布时解释原因
// (禁用不影响悬停);reason 为 null(可用 Bot)时原样透传 children,不挂 Tooltip。
// 可用性判定与状态词表已下沉 @/domain/conversation/botAvailability(shell 层全局搜索复用),此处 re-export 保持既有引用不变。
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { ReactNode } from 'react';

export {
  BOT_UNAVAILABLE_REASONS,
  resolveBotUnavailable,
} from '@/domain/conversation/botAvailability';

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
