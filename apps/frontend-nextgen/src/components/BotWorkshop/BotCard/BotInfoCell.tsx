import { BotEditLockAction } from '@/components/BotWorkshop/BotCard/BotEditLockAction';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';

export interface BotInfoCellProps {
  bot: BotDomain;
  onClaimLock?: (bot: BotDomain) => Promise<void>;
  onReleaseLock?: (bot: BotDomain) => Promise<void>;
}

/** 名称不再是独立按钮：整行已可点击进入详情，避免行内嵌套可交互元素。 */
const BotInfoCell: React.FC<BotInfoCellProps> = ({ bot, onClaimLock, onReleaseLock }) => {
  const avatarUrl = bot.avatarUrl || generateBotAvatar(bot.id || bot.name);
  return (
    <div className="flex min-w-0 items-center gap-3">
      <span
        aria-hidden
        className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-primary/10 text-[10px] font-semibold leading-none text-primary"
      >
        <img src={avatarUrl} alt="" className="h-5 w-5 rounded-full object-cover" />
      </span>
      <div className="flex min-w-0 flex-col">
        <div className="flex min-w-0 items-center gap-1.5">
          <span className="truncate text-sm font-medium text-foreground">{bot.name}</span>
          <span className="inline-flex shrink-0">
            <BotEditLockAction bot={bot} onClaimLock={onClaimLock} onReleaseLock={onReleaseLock} />
          </span>
        </div>
        <span className="truncate text-xs text-muted-foreground">{bot.entityKey}</span>
      </div>
    </div>
  );
};

export default BotInfoCell;
