import { Badge } from '@/components/ui/Badge';
import type { BotDomain } from '@/services/botWorkshop';
import { Cloud, Laptop } from 'lucide-react';
import React from 'react';

/**
 * 「标签」列 cell —— engine / deployment chip 恒显,serviceMode chip 仅服务 Bot 显示。
 * 容器实例概览单独放在「容器信息」列，避免与标签相互挤占。
 *
 * 非服务 Bot 不补 `—` 占位:前两个 chip 无条件渲染,本列不存在「零标签」状态,
 * 补占位符只会与版本列的 `—` 在同一行重复出现。
 *
 * 标签用浅色语义底区分类别，不承载运行健康度；健康状态仍只由「状态」列表达。
 */
export interface BotTagsCellProps {
  bot: BotDomain;
}

function engineTone(engine: string): 'primary' | 'success' | 'warning' | 'purple' | 'neutral' {
  switch (engine.toLowerCase()) {
    case 'openclaw':
      return 'primary';
    case 'teclaw':
      return 'success';
    case 'hermes':
      return 'warning';
    case 'claude_code':
    case 'claude-code':
      return 'purple';
    default:
      return 'neutral';
  }
}

const BotTagsCell: React.FC<BotTagsCellProps> = ({ bot }) => {
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const engineLabel = isAgentCodingBot
    ? bot.runtime.templateName ?? bot.runtime.templateType ?? 'AgentCoding'
    : bot.runtime.engine;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone={engineTone(bot.runtime.engine)}>{engineLabel}</Badge>
      <Badge tone={bot.deployment === 'local' ? 'warning' : 'primary'}>
        {bot.deployment === 'local' ? (
          <Laptop aria-hidden className="mr-1 size-3" />
        ) : (
          <Cloud aria-hidden className="mr-1 size-3" />
        )}
        {bot.deployment === 'local' ? '本地' : '云端'}
      </Badge>
      {bot.serviceMode === 'service' && <Badge tone="purple">服务化</Badge>}
    </div>
  );
};

export default BotTagsCell;
