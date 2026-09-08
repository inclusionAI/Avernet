import { BotEditLockAction } from '@/components/BotWorkshop/BotCard/BotEditLockAction';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';

export interface BotInfoCellProps {
  bot: BotDomain;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
}

function getInitial(name: string): string {
  const trimmed = name.trim();
  if (!trimmed) return 'B';
  return trimmed.charAt(0).toUpperCase();
}

/** 名称不再是独立按钮：整行已可点击进入详情，避免行内嵌套可交互元素。 */
const BotInfoCell: React.FC<BotInfoCellProps> = ({ bot, onClaimLock }) => {
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span
        aria-hidden
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold leading-none text-primary"
      >
        {getInitial(bot.name)}
      </span>
      <div className="flex min-w-0 flex-col">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-medium text-foreground">{bot.name}</span>
          <span className="inline-flex shrink-0">
            <BotEditLockAction bot={bot} onClaimLock={onClaimLock} />
          </span>
        </div>
        <span className="truncate text-xs text-muted-foreground">{bot.entityKey}</span>
      </div>
    </div>
  );
};

export default BotInfoCell;
