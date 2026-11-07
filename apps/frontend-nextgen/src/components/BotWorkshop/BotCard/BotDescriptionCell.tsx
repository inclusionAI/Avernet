import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import type { BotDomain } from '@/services/botWorkshop';
import React from 'react';

export interface BotDescriptionCellProps {
  bot: BotDomain;
}

/**
 * 描述列 cell —— dmore 稿（10/19 Bot管理首页）原为软灰底块，验收（bot-list 复核）
 * 改为裸文本：最多两行超出省略，hover 经深色 Tooltip 展示完整描述。
 */
const BotDescriptionCell: React.FC<BotDescriptionCellProps> = ({ bot }) => {
  const text = bot.description?.trim() || '暂无描述';
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="line-clamp-2 min-w-0 max-w-full whitespace-normal break-words text-xs leading-5 text-muted-foreground">
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