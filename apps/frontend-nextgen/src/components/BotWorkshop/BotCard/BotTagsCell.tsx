import { Badge } from '@/components/ui/Badge';
import type { BotDomain } from '@/services/botWorkshop';
import { Cloud, Laptop } from 'lucide-react';
import React from 'react';

/**
 * 「标签」列 cell —— engine / deployment chip 恒显,serviceMode chip 仅服务 Bot 显示。
 *
 * 非服务 Bot 不补 `—` 占位:前两个 chip 无条件渲染,本列不存在「零标签」状态,
 * 补占位符只会与版本列的 `—` 在同一行重复出现。
 *
 * 三个 chip 统一 outline 描边:蓝被信息列名称与主要操作按钮占用,绿是状态列「运行中」
 * 的语义色,灰是 `neutral` tone 的填充色——三色都在本列的排除范围内。统一描边让本列
 * 保持视觉安静,不与状态列抢辨识度;chip 之间靠图标(Laptop / Cloud)与文案区分。
 */
export interface BotTagsCellProps {
  bot: BotDomain;
}

const BotTagsCell: React.FC<BotTagsCellProps> = ({ bot }) => {
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const engineLabel = isAgentCodingBot
    ? bot.runtime.templateName ?? bot.runtime.templateType ?? 'AgentCoding'
    : bot.runtime.engine;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone="outline">{engineLabel}</Badge>
      <Badge tone="outline">
        {bot.deployment === 'local' ? (
          <Laptop aria-hidden className="mr-1 size-3" />
        ) : (
          <Cloud aria-hidden className="mr-1 size-3" />
        )}
        {bot.deployment === 'local' ? '本地' : '云端'}
      </Badge>
      {bot.serviceMode === 'service' && <Badge tone="outline">服务化</Badge>}
    </div>
  );
};

export default BotTagsCell;
