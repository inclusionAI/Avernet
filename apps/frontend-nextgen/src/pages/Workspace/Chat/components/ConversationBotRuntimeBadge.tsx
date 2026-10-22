import { Badge } from '@/components/ui';
import { getBotEngineBadgeTone } from '@/components/BotEngineBadgeTone';
import { getBotEngineLabel } from '@/domain/botEngine';
import type { ChatBotView } from '@/services/workspace/botSessionService';

interface ConversationBotRuntimeBadgeProps {
  bot: ChatBotView;
}

/** AgentCoding Bot 显示模板名/模板分类，普通 Bot 显示引擎名；色彩与 Bot 管理同源（未知引擎中性兜底）。 */
export function ConversationBotRuntimeBadge({ bot }: ConversationBotRuntimeBadgeProps) {
  const label = bot.isAgentCodingBot
    ? bot.templateName || bot.templateType || 'AgentCoding'
    : getBotEngineLabel(bot.engine);

  if (!label) return null;

  return (
    <Badge
      tone={getBotEngineBadgeTone(bot.engine)}
      className="block max-w-full shrink-0 truncate rounded px-1.5 py-0.5 text-[10px] font-normal leading-3"
    >
      {label}
    </Badge>
  );
}
