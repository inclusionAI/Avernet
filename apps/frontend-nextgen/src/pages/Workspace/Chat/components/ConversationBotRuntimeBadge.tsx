import { Badge } from '@/components/ui';
import { getBotEngineLabel } from '@/domain/botEngine';
import type { ChatBotView } from '@/services/workspace/botSessionService';

interface ConversationBotRuntimeBadgeProps {
  bot: ChatBotView;
}

/**
 * AgentCoding Bot 显示模板名/模板分类，普通 Bot 显示引擎名。
 * 2026-10-20 对话菜单设计稿还原：徽章与名称同行跟随，透明底 + --border(#E4E4E7) 边框 + 深灰字；
 * 引擎分色（getBotEngineBadgeTone）仅保留给 Bot 管理页，对话菜单不再共用。
 */
export function ConversationBotRuntimeBadge({ bot }: ConversationBotRuntimeBadgeProps) {
  const label = bot.isAgentCodingBot
    ? bot.templateName || bot.templateType || 'AgentCoding'
    : getBotEngineLabel(bot.engine);

  if (!label) return null;

  return (
    <Badge
      tone="outline"
      className="max-w-full shrink-0 truncate rounded px-1.5 py-0.5 text-[10px] font-normal leading-3 text-muted-foreground"
    >
      {label}
    </Badge>
  );
}
