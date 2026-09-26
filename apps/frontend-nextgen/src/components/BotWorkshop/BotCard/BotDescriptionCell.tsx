import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';

export interface BotDescriptionCellProps {
  bot: BotDomain;
}

const BotDescriptionCell: React.FC<BotDescriptionCellProps> = ({ bot }) => {
  const text = bot.description?.trim() || '暂无描述';
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <p className="m-0 line-clamp-2 max-w-full text-xs leading-5 text-muted-foreground">{text}</p>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm whitespace-normal break-words text-left leading-5">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

export default BotDescriptionCell;
