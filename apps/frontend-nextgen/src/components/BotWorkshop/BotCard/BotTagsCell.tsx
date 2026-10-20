import { getBotEngineBadgeTone } from '@/components/BotEngineBadgeTone';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { engineVersionTooltip, engineVersionUiState } from '@/domain/botEngineVersion';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
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
      <Badge tone={getBotEngineBadgeTone(bot.runtime.engine)}>{engineLabel}</Badge>
      <Badge tone={bot.deployment === 'local' ? 'warning' : 'primary'}>
        {bot.deployment === 'local' ? (
          <Laptop aria-hidden className="mr-1 size-3" />
        ) : (
          <Cloud aria-hidden className="mr-1 size-3" />
        )}
        {bot.deployment === 'local' ? '本地' : '云端'}
      </Badge>
      {bot.serviceMode === 'service' && <Badge tone="purple">服务化</Badge>}
      {onOpenEngineInfo &&
      engineVersionState?.kind === 'ready' &&
      engineVersionUiState(engineVersionState.observation) === 'updatable' ? (
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-auto rounded-full p-0"
                aria-label={`引擎有更新：${engineVersionTooltip(engineVersionState.observation)}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onOpenEngineInfo(bot);
                }}
              >
                <Badge tone="warning" className="cursor-pointer">
                  引擎有更新
                </Badge>
              </Button>
            </TooltipTrigger>
            <TooltipContent>{engineVersionTooltip(engineVersionState.observation)}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : null}
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
