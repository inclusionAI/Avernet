import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import type { ChatMessage } from '@tc-chat/core';
import { SystemNotice } from '@tc-chat/ui/es/SystemNotice';
import { useEffect, useRef, useState } from 'react';
import { getMessageTime } from './messageHelpers';

/**
 * 系统消息项——参照 open-claw「我的协作」展示风格：
 * 居中、左侧带时间、单行截断、hover 上去显示完整内容。
 *
 * SystemNotice 内部 Content 节点为 overflow:hidden + white-space:nowrap，
 * 截断时 scrollWidth > clientWidth，ResizeObserver 检测溢出来控制 Tooltip 显隐。
 * tooltip="" 关闭 SystemNotice 自带 tooltip，改由外层 shadcn Tooltip 承接全文。
 */
export function SystemMessageItem({ message }: { message: ChatMessage }) {
  const wrapperRef = useRef<HTMLDivElement>(null);
  const [isOverflowing, setIsOverflowing] = useState(false);

  const time = getMessageTime(message);

  useEffect(() => {
    const root = wrapperRef.current;
    if (!root) return;
    const check = () => {
      let overflowing = false;
      root.querySelectorAll<HTMLElement>('div').forEach((d) => {
        if (d.scrollWidth > d.clientWidth + 1) overflowing = true;
      });
      setIsOverflowing(overflowing);
    };
    check();
    const ro = new ResizeObserver(check);
    ro.observe(root);
    return () => ro.disconnect();
  }, [message.content]);

  return (
    <div className="flex min-w-0 max-w-full items-center justify-center gap-1 pb-3">
      {time && <span className="shrink-0 text-[11px] text-muted-foreground">{time}</span>}
      <TooltipProvider delayDuration={250}>
        <Tooltip>
          <TooltipTrigger asChild>
            <div ref={wrapperRef} className={cn('min-w-0 max-w-[60%]', isOverflowing && 'cursor-help')}>
              <SystemNotice variant="inline" severity="neutral" maxLines={1} tooltip="" className="min-w-0 max-w-full">
                {message.content}
              </SystemNotice>
            </div>
          </TooltipTrigger>
          {isOverflowing && (
            <TooltipContent
              side="top"
              align="center"
              sideOffset={6}
              className="max-w-[420px] whitespace-pre-wrap break-words break-all text-left text-xs leading-5 shadow-lg"
            >
              {message.content}
            </TooltipContent>
          )}
        </Tooltip>
      </TooltipProvider>
    </div>
  );
}
