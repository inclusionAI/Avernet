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
  const badge = <Badge tone={toneFor(bot.lifecycle)}>{lifecycleLabel[bot.lifecycle]}</Badge>;

  if (bot.deployment === 'local' && ['deploying', 'failed'].includes(bot.lifecycle))
    return (
      <div className="flex items-center gap-1">
        {badge}
        <Button
          variant="ghost"
          size="sm"
          aria-expanded={progressExpanded}
          aria-controls={`desktop-progress-${bot.id}`}
          leftIcon={progressExpanded ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
          onClick={(event) => {
            event.stopPropagation();
            onToggleProgress?.();
          }}
        >
          进度
        </Button>
      </div>
    );
  if (bot.deployment === 'local' && bot.lifecycle === 'offline')
    return (
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          window.location.href = 'teamclaw://open';
        }}
      >
        设备离线 · 打开客户端
      </Button>
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
