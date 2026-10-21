import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';

export interface BotVersionCellProps {
  bot: BotDomain;
}

/** 版本列：dmore 稿为纯文本「V1」（13px），不用彩色 Badge。 */
const BotVersionCell: React.FC<BotVersionCellProps> = ({ bot }) => {
  const publicationVersion = bot.serviceMode === 'service' ? bot.publicationVersion : undefined;

  if (publicationVersion === undefined) {
    return (
      <span className="text-xs text-muted-foreground" aria-label="无发布版本">
        —
      </span>
    );
  }

  return <span className="text-[13px] text-foreground">V{publicationVersion}</span>;
};

export default BotVersionCell;