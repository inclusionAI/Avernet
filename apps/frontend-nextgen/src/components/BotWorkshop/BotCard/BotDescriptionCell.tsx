import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';

export interface BotDescriptionCellProps {
  bot: BotDomain;
}

/**
 * 描述列 cell —— dmore 稿（10/19 Bot管理首页）+ 验收口径：
 * 软灰底块内最多两行，超出省略；hover 经深色 Tooltip 展示完整描述。
 */
const BotDescriptionCell: React.FC<BotDescriptionCellProps> = ({ bot }) => {
  const text = bot.description?.trim() || '暂无描述';
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="line-clamp-2 min-w-0 max-w-full whitespace-normal break-words rounded-md bg-muted/50 px-2.5 py-1.5 text-xs leading-5 text-muted-foreground">
            {text}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top" className="max-w-sm whitespace-normal break-words text-left leading-5 bg-foreground text-background">
          {text}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

export default BotDescriptionCell;