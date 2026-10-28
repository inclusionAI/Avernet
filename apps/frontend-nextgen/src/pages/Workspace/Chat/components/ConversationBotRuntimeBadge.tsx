import { Badge } from '@/components/ui';
import { getBotEngineLabel } from '@/domain/botEngine';
import type { ChatBotView } from '@/services/workspace/botSessionService';

interface ConversationBotRuntimeBadgeProps {
  bot: ChatBotView;
}

/** AgentCoding Bot 显示模板名/模板分类，普通 Bot 显示引擎名；
 *  色彩与 Bot 管理同源：统一默认中性底（不按引擎涂色）。 */
export function ConversationBotRuntimeBadge({ bot }: ConversationBotRuntimeBadgeProps) {
  const label = bot.isAgentCodingBot
    ? bot.templateName || bot.templateType || 'AgentCoding'
    : getBotEngineLabel(bot.engine);

  if (!label) return null;

  return (
    <Badge className="block max-w-full shrink-0 truncate rounded px-1.5 py-0.5 text-[10px] font-normal leading-3">
      {label}
    </Badge>
  );
}
