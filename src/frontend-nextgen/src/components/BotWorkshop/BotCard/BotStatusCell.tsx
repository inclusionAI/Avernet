import { Badge } from '@/components/ui/Badge';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';
import { lifecycleLabel } from './config';

export interface BotStatusCellProps {
  bot: BotDomain;
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

const BotStatusCell: React.FC<BotStatusCellProps> = ({ bot }) => {
  const failureReason = bot.lifecycle === 'failed' ? bot.disabledActions.restart : undefined;
  const badge = <Badge tone={toneFor(bot.lifecycle)}>{lifecycleLabel[bot.lifecycle]}</Badge>;

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
