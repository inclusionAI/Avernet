import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import { ChevronDown, ChevronRight } from 'lucide-react';
import React from 'react';
import { lifecycleLabel } from './config';

export interface BotStatusCellProps {
  bot: BotDomain;
  progressExpanded?: boolean;
  onToggleProgress?: () => void;
}

function toneFor(lifecycle: BotDomain['lifecycle']): 'success' | 'error' | 'warning' | 'neutral' {
  switch (lifecycle) {
    case 'running':
      return 'success';
    case 'failed':
      return 'error';
    case 'unknown':
      return 'warning';
    default:
      return 'neutral';
  }
}

const BotStatusCell: React.FC<BotStatusCellProps> = ({ bot, progressExpanded, onToggleProgress }) => {
  const failureReason = bot.lifecycle === 'failed' ? bot.disabledActions.restart : undefined;
  const statusLabel = bot.serviceMode === 'service' && bot.lifecycle === 'failed' ? '部署失败' : lifecycleLabel[bot.lifecycle];
  const badge = <Badge tone={toneFor(bot.lifecycle)}>{statusLabel}</Badge>;

  if (bot.deployment === 'local' && ['deploying', 'failed', 'offline'].includes(bot.lifecycle))
    return (
      <div className="flex items-center gap-1">
        {badge}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                aria-label={progressExpanded ? '收起桌面 Bot 状态详情' : '展开桌面 Bot 状态详情'}
                aria-expanded={progressExpanded}
                aria-controls={`desktop-progress-${bot.id}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onToggleProgress?.();
                }}
              >
                {progressExpanded ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
              </Button>
            </TooltipTrigger>
            <TooltipContent>{progressExpanded ? '收起详情' : '查看详情'}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </div>
    );
  if (!failureReason) {
    return badge;
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">{badge}</span>
        </TooltipTrigger>
        <TooltipContent>{failureReason}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

export default BotStatusCell;
