import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import { ChevronDown, ChevronRight } from 'lucide-react';
import React from 'react';
import { lifecycleLabel } from './config';
import { BotStatusPublicationMenu } from './BotStatusPublicationMenu';

export interface BotStatusCellProps {
  bot: BotDomain;
  progressExpanded?: boolean;
  onToggleProgress?: () => void;
  onManagePublication?: (bot: BotDomain) => void;
}

/**
 * 状态圆点配 dmore 稿（10/19 Bot管理首页）：8px 圆点 + 13px 文字，不再用彩色底 Badge。
 * 圆点色走语义 token（草稿=warning 橙、预发/部署中=info 蓝、运行中=success 绿、
 * 离线/未知=zinc、失败=destructive 红），暗色模式随 token 自动翻转。
 */
function dotColor(lifecycle: BotDomain['lifecycle']): string {
  switch (lifecycle) {
    case 'running':
      return 'bg-success';
    case 'deploying':
    case 'prestable':
      return 'bg-info';
    case 'failed':
      return 'bg-destructive';
    case 'offline':
    case 'unknown':
      return 'bg-content-icon';
    default:
      return 'bg-warning';
  }
}

const BotStatusCell: React.FC<BotStatusCellProps> = ({ bot, progressExpanded, onToggleProgress, onManagePublication }) => {
  const failureReason = bot.lifecycle === 'failed' ? bot.disabledActions.restart : undefined;
  const statusLabel = bot.serviceMode === 'service' && bot.lifecycle === 'failed' ? '部署失败' : lifecycleLabel[bot.lifecycle];
  const statusDisplay = (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className={`size-2 shrink-0 rounded-full ${dotColor(bot.lifecycle)}`} />
      <span className="whitespace-nowrap text-[13px] text-muted-foreground">{statusLabel}</span>
    </span>
  );

  const status = (
    <BotStatusPublicationMenu bot={bot} onManagePublication={onManagePublication}>
      {statusDisplay}
    </BotStatusPublicationMenu>
  );

  if (bot.deployment === 'local' && ['deploying', 'failed', 'offline'].includes(bot.lifecycle))
    return (
      <div className="flex items-center gap-1">
        {status}
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
    return status;
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="inline-flex">{status}</span>
        </TooltipTrigger>
        <TooltipContent>{failureReason}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

export default BotStatusCell;