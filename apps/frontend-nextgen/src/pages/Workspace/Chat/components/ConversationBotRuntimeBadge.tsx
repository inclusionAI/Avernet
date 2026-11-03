import { Badge } from '@/components/ui';
import { getBotEngineLabel } from '@/domain/botEngine';
import type { ChatBotView } from '@/services/workspace/botSessionService';
import { cn } from '@/utils/cn';

interface ConversationBotRuntimeBadgeProps {
  bot: ChatBotView;
  /** 行内让位控制类:由 Bot 行注入(2026-11-03 紧凑化:操作区可见/钉住时徽章隐藏,名称优先)。 */
  className?: string;
}

/** AgentCoding Bot 显示模板名/模板分类，普通 Bot 显示引擎名；
 *  色彩与 Bot 管理同源：统一默认中性底（不按引擎涂色）。 */
export function ConversationBotRuntimeBadge({ bot, className }: ConversationBotRuntimeBadgeProps) {
  const label = bot.isAgentCodingBot
    ? bot.templateName || bot.templateType || 'AgentCoding'
    : getBotEngineLabel(bot.engine);

  if (!label) return null;

  return (
    <Badge
      className={cn(
        'block max-w-full shrink-0 truncate rounded px-1.5 py-0.5 text-[10px] font-normal leading-3',
        className,
      )}
    >
      {label}
    </Badge>
  );
}
