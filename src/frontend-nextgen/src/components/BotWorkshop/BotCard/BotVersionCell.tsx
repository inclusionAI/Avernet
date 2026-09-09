import { Badge } from '@/components/ui/Badge';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';

export interface BotVersionCellProps {
  bot: BotDomain;
}

const BotVersionCell: React.FC<BotVersionCellProps> = ({ bot }) => {
  const publicationVersion = bot.serviceMode === 'service' ? bot.publicationVersion : undefined;

  if (publicationVersion === undefined) {
    return (
      <span className="text-xs text-muted-foreground" aria-label="无发布版本">
        —
      </span>
    );
  }

  return <Badge tone="primary">V{publicationVersion}</Badge>;
};

export default BotVersionCell;
