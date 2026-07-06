/**
 * GoldBadge - 官方推荐金标组件
 *
 * 当 Bot 的 tags.trust_level === 'trusted' 时显示金色 V 标
 * 鼠标 hover 显示 tooltip：官方推荐
 */

import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from '@/components/ui/tooltip';
import { Sparkles } from 'lucide-react';
import React from 'react';

interface GoldBadgeProps {
  /** Bot 信息对象，内部会读取 tags */
  botInfo?: {
    tags?: {
      trust_level?: string;
    };
  };
  /** 自定义 tooltip 内容，默认显示"官方推荐" */
  tooltipContent?: React.ReactNode;
  /** 自定义 className */
  className?: string;
}

/**
 * 检查是否显示金标
 */
export function isTrustedBot(botInfo?: {
  tags?: { trust_level?: string };
}): boolean {
  return botInfo?.tags?.trust_level === 'trusted';
}

/**
 * 官方推荐金标
 */
const GoldBadge: React.FC<GoldBadgeProps> = ({
  botInfo,
  tooltipContent,
  className,
}) => {
  if (!isTrustedBot(botInfo)) {
    return null;
  }

  const defaultTooltip = (
    <div className="flex items-center gap-1">
      <Sparkles className="w-3 h-3 text-amber-400" />
      <span className="font-medium text-amber-400">官方推荐</span>
    </div>
  );

  return (
    <TooltipProvider delayDuration={100}>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            className={
              className ??
              'flex-shrink-0 w-4 h-4 rounded-full bg-gradient-to-br from-amber-300 to-amber-500 flex items-center justify-center cursor-pointer shadow-sm'
            }
          >
            <span className="text-white text-[10px] font-bold">V</span>
          </div>
        </TooltipTrigger>
        <TooltipContent side="top" className="bg-slate-800 border-slate-700">
          {tooltipContent ?? defaultTooltip}
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
};

export default GoldBadge;
