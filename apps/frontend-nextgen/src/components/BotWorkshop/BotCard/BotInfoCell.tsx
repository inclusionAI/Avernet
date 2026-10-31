import { BotEditLockAction } from '@/components/BotWorkshop/BotCard/BotEditLockAction';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { engineVersionTooltip, engineVersionUiState } from '@/domain/botEngineVersion';
import type { BotEngineVersionState } from '@/hooks/useBotEngineVersions';
import type { BotDomain } from '@/services/botWorkshop';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import { useBotWorkshopStore } from '@/stores/botWorkshopStore';
import React from 'react';

export interface BotInfoCellProps {
  bot: BotDomain;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
  onReleaseLock?: (bot: BotDomain) => Promise<void>;
  engineVersionState?: BotEngineVersionState;
  onOpenEngineUpdate?: (bot: BotDomain) => void;
}

/**
 * 名称不再是独立按钮：整行已可点击进入详情，避免行内嵌套可交互元素。
 * 「引擎有更新」tag 紧贴名称右侧，点击直达更新确认弹窗（dmore image 19）。
 */
const BotInfoCell: React.FC<BotInfoCellProps> = ({
  bot,
  onClaimLock,
  onReleaseLock,
  engineVersionState,
  onOpenEngineUpdate,
}) => {
  const highlighted = useBotWorkshopStore((state) => state.highlightBotId === bot.id);
  const cell = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    if (highlighted) cell.current?.scrollIntoView?.({ block: 'center' });
  }, [highlighted]);
  const avatarUrl = bot.avatarUrl || generateBotAvatar(bot.id || bot.name);
  const engineUpdatable =
    engineVersionState?.kind === 'ready' && engineVersionUiState(engineVersionState.observation) === 'updatable';
  return (
    <div
      ref={cell}
      className={`flex min-w-0 items-center gap-3 ${highlighted ? 'rounded-md bg-primary/10 ring-2 ring-primary' : ''}`}
      aria-label={highlighted ? `刚创建的 Bot：${bot.name}` : undefined}
    >
      <span
        aria-hidden
        className="flex size-10 shrink-0 items-center justify-center overflow-hidden rounded-full bg-primary/10"
      >
        <img src={avatarUrl} alt="" className="size-10 rounded-full object-cover" />
      </span>
      <div className="flex min-w-0 flex-col">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-semibold text-foreground">{bot.name}</span>
          {onOpenEngineUpdate && engineUpdatable ? (
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-auto shrink-0 rounded-full p-0"
                    aria-label={`引擎有更新：${engineVersionTooltip(engineVersionState.observation)}`}
                    onClick={(event) => {
                      event.stopPropagation();
                      onOpenEngineUpdate(bot);
                    }}
                  >
                    <Badge tone="warning" className="cursor-pointer whitespace-nowrap">
                      引擎有更新
                    </Badge>
                  </Button>
                </TooltipTrigger>
                <TooltipContent>{engineVersionTooltip(engineVersionState.observation)}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ) : null}
          <span className="inline-flex shrink-0">
            <BotEditLockAction bot={bot} onClaimLock={onClaimLock} onReleaseLock={onReleaseLock} />
          </span>
        </div>
        {bot.expertOriginName && (
          <span className="truncate text-xs text-muted-foreground">来自专家市场 · {bot.expertOriginName}</span>
        )}
        <span className="truncate text-xs text-content-icon">{bot.entityKey}</span>
      </div>
    </div>
  );
};

export default BotInfoCell;
