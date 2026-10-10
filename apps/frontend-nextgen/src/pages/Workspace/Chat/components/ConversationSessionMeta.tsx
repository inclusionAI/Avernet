import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import type { ReactNode } from 'react';
import { formatSessionTime, formatSessionTimeTooltip } from '../../components/SessionCard';

/** 与群聊一致：时间占位，hover/focus 时原位显示操作；触屏两者并排。 */
export function ConversationSessionMeta({
  createdAt,
  menuOpen = false,
  children,
}: {
  createdAt: string;
  menuOpen?: boolean;
  children?: ReactNode;
}) {
  const text = formatSessionTime(createdAt);
  const date = (
    <span
      data-session-created-time
      className={cn(
        'min-w-9 shrink-0 whitespace-nowrap text-right leading-5 transition-opacity',
        children &&
          '[@media(hover:hover)]:group-hover/row:opacity-0 [@media(hover:hover)]:group-focus-within/row:opacity-0',
        children && menuOpen && '[@media(hover:hover)]:opacity-0',
      )}
    >
      {text}
    </span>
  );
  if (!text && !children) return null;
  return (
    <div
      data-session-meta
      className="relative ml-2 flex min-w-9 shrink-0 items-center gap-2 text-xs text-muted-foreground"
    >
      {text ? (
        <TooltipProvider delayDuration={300}>
          <Tooltip>
            <TooltipTrigger asChild>{date}</TooltipTrigger>
            <TooltipContent>{formatSessionTimeTooltip(createdAt)}</TooltipContent>
          </Tooltip>
        </TooltipProvider>
      ) : (
        date
      )}
      {children && (
        <span
          data-session-meta-actions
          className={cn(
            'absolute right-0 flex items-center opacity-0 transition-opacity group-hover/row:opacity-100 group-focus-within/row:opacity-100 [@media(hover:none)]:relative [@media(hover:none)]:opacity-100',
            menuOpen && 'opacity-100',
          )}
        >
          {children}
        </span>
      )}
    </div>
  );
}
