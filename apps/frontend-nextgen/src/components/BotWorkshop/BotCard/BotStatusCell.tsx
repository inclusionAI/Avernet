import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';
import { lifecycleLabel } from './config';
import { DesktopStartProgress } from './DesktopStartProgress';

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

  if (bot.deployment === 'local' && ['deploying', 'failed'].includes(bot.lifecycle))
    return <DesktopStartProgress botId={bot.id} />;
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
