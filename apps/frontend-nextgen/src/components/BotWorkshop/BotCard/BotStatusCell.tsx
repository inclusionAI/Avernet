import { Button } from '@/components/ui/Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotCopyStatus, BotDomain } from '@/services/botWorkshop';
import { BOT_COPY_FEATURE_ENABLED } from '@/hooks/useBotWorkshopCopyHost';
import { ChevronDown, ChevronRight } from 'lucide-react';
import React from 'react';
import { BotStatusPublicationMenu } from './BotStatusPublicationMenu';
import { lifecycleLabel } from './config';

export interface BotStatusCellProps {
  bot: BotDomain;
  progressExpanded?: boolean;
  onToggleProgress?: () => void;
  onPublicationChanged?: () => Promise<void>;
  onPublicationGateBlocked?: (bot: BotDomain) => void;
}

/**
 * 复制态文案:中间态一律「复制中」;终态只有成功(收敛后 copy_state 已为 null →
 * 不显徽标)与「复制失败」。故非 null 的 copy_state 必为进行中或 FAILED,二分即可。
 */
const copyStatusLabel = (status: BotCopyStatus): string =>
  status === 'FAILED' ? '复制失败' : '复制中';

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

const BotStatusCell: React.FC<BotStatusCellProps> = ({
  bot,
  progressExpanded,
  onToggleProgress,
  onPublicationChanged,
  onPublicationGateBlocked,
}) => {
  // 复制进行中(gated by 特性开关):copy_state 非空时,该 bot 仍是「复制中」态,
  // 状态列直接展示复制进度文案替代生命周期状态(部署/发布菜单在此态无意义),
  // 操作列则只保留「重试」按钮(见 useBotTableColumns 的 actions 列)。
  const copyState = BOT_COPY_FEATURE_ENABLED ? bot.copyState : undefined;
  if (copyState) {
    const failed = copyState.status === 'FAILED';
    return (
      <span className="inline-flex items-center gap-1.5">
        <span aria-hidden className={`size-2 shrink-0 rounded-full ${failed ? 'bg-destructive' : 'bg-info'}`} />
        <span className="whitespace-nowrap text-[13px] text-muted-foreground">
          {copyStatusLabel(copyState.status)}
        </span>
      </span>
    );
  }

  const failureReason = bot.lifecycle === 'failed' ? bot.disabledActions.restart : undefined;
  const personalCloud = bot.serviceMode === 'non-service' && bot.deployment === 'cloud';
  const recoveryLabel = personalCloud
    ? ({ RECYCLED: '已回收', REACTIVATING: '激活中', RECYCLING: '回收中' } as Record<string, string>)[
        bot.rawStatus?.toUpperCase() ?? ''
      ]
    : undefined;
  const statusLabel =
    recoveryLabel ??
    (personalCloud && bot.rawDisplayState === 'dormant'
      ? '已回收'
      : bot.serviceMode === 'service' && bot.lifecycle === 'failed'
      ? '部署失败'
      : lifecycleLabel[bot.lifecycle]);
  const statusDisplay = (
    <span className="inline-flex items-center gap-1.5">
      <span aria-hidden className={`size-2 shrink-0 rounded-full ${dotColor(bot.lifecycle)}`} />
      <span className="whitespace-nowrap text-[13px] text-muted-foreground">{statusLabel}</span>
    </span>
  );

  const status = (
    <BotStatusPublicationMenu
      bot={bot}
      onPublicationChanged={onPublicationChanged}
      onPublicationGateBlocked={onPublicationGateBlocked}
    >
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
