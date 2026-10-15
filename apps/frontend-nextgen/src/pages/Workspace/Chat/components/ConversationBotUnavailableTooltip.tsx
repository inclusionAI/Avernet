// 不可用 Bot 行的整行悬停提示包装器:FAILED/PENDING 禁用行外包 Tooltip 解释原因
// (禁用不影响悬停);reason 为 null(可用 Bot)时原样透传 children,不挂 Tooltip。
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui';
import type { ReactNode } from 'react';

/** 非可用状态的原因文案(FAILED=创建或运行异常+运维恢复指引;PENDING=创建中+刷新重试指引)。 */
export const BOT_UNAVAILABLE_REASONS: Record<string, string> = {
  FAILED: '该 Bot 因创建或运行异常暂时无法使用；可在 Bot 管理中尝试运维恢复',
  PENDING: '该 Bot 正在创建中，暂时无法使用，完成后可刷新重试',
};

/** 非可用状态判定:FAILED/PENDING 返回原因与禁用标记;ACTIVE/online/缺省(好友关系等
 *  无 status 数据源)可用返回 null 原因。恢复 ACTIVE 后目录刷新即恢复。 */
export function resolveBotUnavailable(status: string | undefined): { reason: string | null; unavailable: boolean } {
  const reason = status ? BOT_UNAVAILABLE_REASONS[status] ?? null : null;
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