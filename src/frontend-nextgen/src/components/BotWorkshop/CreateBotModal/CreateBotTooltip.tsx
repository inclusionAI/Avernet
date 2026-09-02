import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import React from 'react';

/** 创建 Bot 配置区专用 Tooltip：保持紧凑的深色提示，避免被全局浅色 Tooltip 风格干扰。 */
const CreateBotTooltipContent = React.forwardRef<
  React.ElementRef<typeof TooltipContent>,
  React.ComponentPropsWithoutRef<typeof TooltipContent>
>(({ className, ...props }, ref) => (
  <TooltipContent
    ref={ref}
    className={cn('create-bot-tooltip-content border-transparent bg-foreground text-background shadow-md', className)}
    {...props}
  />
));
CreateBotTooltipContent.displayName = 'CreateBotTooltipContent';

export {
  Tooltip as CreateBotTooltip,
  CreateBotTooltipContent,
  TooltipProvider as CreateBotTooltipProvider,
  TooltipTrigger as CreateBotTooltipTrigger,
};
