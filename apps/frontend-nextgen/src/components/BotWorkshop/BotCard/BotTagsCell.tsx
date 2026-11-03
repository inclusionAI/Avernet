import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import type { BotDomain } from '@/services/botWorkshop';
import { Cloud, Laptop } from 'lucide-react';
import React from 'react';

/**
 * 「标签」列 cell —— engine / deployment chip 恒显,serviceMode chip 仅服务 Bot 显示。
 * 容器实例概览单独放在「容器信息」列，避免与标签相互挤占。
 * 「引擎有更新」tag 位于「Bot信息」列 bot 名称右侧（见 BotInfoCell），此处只保留
 * 多实例场景的「选择实例」入口。
 *
 * 非服务 Bot 不补 `—` 占位:前两个 chip 无条件渲染,本列不存在「零标签」状态,
 * 补占位符只会与版本列的 `—` 在同一行重复出现。
 *
 * 标签统一用中性(default)底色，不再按引擎/部署/服务化涂装彩色，避免标签列表过杂；
 * 健康状态仍只由「状态」列表达。
 */
export interface BotTagsCellProps {
  bot: BotDomain;
  engineVersionState?: BotEngineVersionState;
  onOpenEngineInfo?: (bot: BotDomain) => void;
}

const BotTagsCell: React.FC<BotTagsCellProps> = ({ bot, engineVersionState, onOpenEngineInfo }) => {
  const isAgentCodingBot = bot.runtime.isAgentCodingBot;
  const engineLabel = isAgentCodingBot
    ? bot.runtime.templateName ?? bot.runtime.templateType ?? 'AgentCoding'
    : bot.runtime.engine;

  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Badge tone="neutral">{engineLabel}</Badge>
      <Badge tone="neutral">
        {bot.deployment === 'local' ? (
          <Laptop aria-hidden className="mr-1 size-3" />
        ) : (
          <Cloud aria-hidden className="mr-1 size-3" />
        )}
        {bot.deployment === 'local' ? '本地' : '云端'}
      </Badge>
      {bot.serviceMode === 'service' && <Badge tone="neutral">服务化</Badge>}
      {onOpenEngineInfo && engineVersionState?.kind === 'select_instance' ? (
        <Button
          variant="ghost"
          size="sm"
          className="h-auto rounded-full p-0"
          aria-label="选择引擎实例"
          onClick={(event) => {
            event.stopPropagation();
            onOpenEngineInfo(bot);
          }}
        >
          <Badge tone="neutral" className="cursor-pointer">
            选择实例
          </Badge>
        </Button>
      ) : null}
    </div>
  );
};

export default BotTagsCell;
