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
    /*
      标签宽度契约:
      - 不再使用固定比例上限,优先按自然宽度展示完整类型文案;
      - 名称与 tag 总宽超出标题内容盒时,tag 以更高 flex-shrink 权重优先收缩并省略;
      - Bot 名称是主信息,仍由同级 min-w-12 保留最小可视宽;
      - 操作按钮出现时可由调用方通过 className 进一步隐藏,优先保障 Bot 名称。
    */
    <Badge
      className={cn(
        'block min-w-0 max-w-full shrink-[4] truncate rounded px-1.5 py-0.5 text-[10px] font-normal leading-3 @max-[8.5rem]/conversation-bot-title:hidden',
        className,
      )}
    >
      {label}
    </Badge>
  );
}
