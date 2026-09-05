import { Button } from '@/components/ui';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import { MessageSquare } from 'lucide-react';
import React from 'react';

function parseDate(input: number | string | undefined): Date | null {
  if (input === undefined || input === '' || input === 0) return null;
  const date =
    typeof input === 'number' ? new Date(input) : new Date(input.includes('T') ? input : input.replace(/-/g, '/'));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** 秒/毫秒时间戳或后端时间字符串 → MM/DD(对齐会话卡片日期样式);无法解析时返回空串。 */
export function formatMonthDay(input: number | string | undefined): string {
  const date = parseDate(input);
  if (!date) return '';
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `${month}/${day}`;
}

/** 秒/毫秒时间戳或后端时间字符串 → MM/dd HH:mm;无法解析时返回空串。 */
export function formatMonthDayTime(input: number | string | undefined): string {
  const date = parseDate(input);
  if (!date) return '';
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  const hour = String(date.getHours()).padStart(2, '0');
  const minute = String(date.getMinutes()).padStart(2, '0');
  return `${month}/${day} ${hour}:${minute}`;
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

function startOfWeek(date: Date): number {
  const day = date.getDay();
  const mondayOffset = day === 0 ? 6 : day - 1;
  return startOfDay(new Date(date.getFullYear(), date.getMonth(), date.getDate() - mondayOffset));
}

/** 稳定相对时间：当天 HH:mm、昨天、同周星期、本年 MM/DD、跨年 YYYY/MM/DD。 */
export function formatSessionTime(input: number | string | undefined, now = new Date()): string {
  const date = parseDate(input);
  if (!date) return '';
  if (startOfDay(date) === startOfDay(now)) {
    return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
  }
  const yesterday = startOfDay(now) - 24 * 60 * 60 * 1000;
  if (startOfDay(date) === yesterday) return '昨天';
  if (startOfWeek(date) === startOfWeek(now)) {
    return ['周日', '周一', '周二', '周三', '周四', '周五', '周六'][date.getDay()];
  }
  if (date.getFullYear() === now.getFullYear()) return formatMonthDay(input);
  return `${date.getFullYear()}/${String(date.getMonth() + 1).padStart(2, '0')}/${String(date.getDate()).padStart(2, '0')}`;
}

export function formatSessionTimeTooltip(input: number | string | undefined): string {
  const date = parseDate(input);
  if (!date) return '';
  return date.toLocaleString([], { dateStyle: 'medium', timeStyle: 'short' });
}

interface SessionCardProps {
  title: string;
  /** 副行文案(真实预览/成员数等);未传时显示「暂无会话预览」。 */
  subtitle?: string;
  /** 右侧日期(MM/DD),无则不渲染。 */
  dateText?: string;
  /** 日期的完整本地时间 Tooltip。 */
  dateTooltip?: string;
  selected: boolean;
  onSelect: () => void;
  /** 右侧操作区（如更多菜单），由调用方决定可见性。 */
  trailing?: React.ReactNode;
  /** 无副行内容时使用更紧凑的单行布局。 */
  compact?: boolean;
  /** 对话使用消息 Icon，协作群会话保留圆点。 */
  indicator?: 'dot' | 'message';
  /** 调用方可按列表场景补充尺寸；不改变卡片的交互语义。 */
  className?: string;
}

/** 二级会话列表项:无卡片容器、缩进排列、会话圆点 + 标题/副行 + 日期。 */
export const SessionCard = React.memo(function SessionCard({
  title,
  subtitle = '暂无会话预览',
  dateText,
  selected,
  onSelect,
  trailing,
  compact = false,
  dateTooltip,
  indicator = 'dot',
  className,
}: SessionCardProps) {
  return (
    <div
      className={cn(
        'group relative flex items-stretch border-b border-border/70 text-sm transition-colors last:border-b-0',
        compact ? 'min-h-12' : 'min-h-[60px]',
        selected ? 'bg-primary/10' : 'bg-background hover:bg-primary/5',
        className,
      )}
    >
      <Button
        variant="ghost"
        aria-pressed={selected}
        aria-current={selected ? 'page' : undefined}
        onClick={onSelect}
        className={cn(
          'flex h-auto min-w-0 flex-1 justify-start gap-2 rounded-none px-2.5 text-left hover:bg-transparent focus-visible:z-10',
          compact ? 'items-center' : 'items-start',
          compact ? 'min-h-12 py-2' : 'min-h-[60px] py-2.5',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center',
            indicator === 'message' ? 'self-center' : !compact && 'mt-0.5',
          )}
        >
          {indicator === 'message' ? (
            <MessageSquare
              data-session-indicator
              className={cn(
                'h-3.5 w-3.5',
                selected ? 'text-primary' : 'text-muted-foreground group-hover:text-primary/70',
              )}
            />
          ) : (
            <span
              data-session-indicator
              className={cn(
                'h-1.5 w-1.5 rounded-full',
                selected ? 'bg-primary' : 'bg-muted-foreground/50 group-hover:bg-primary/60',
              )}
            />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <span
            className={cn(
              'block truncate text-xs leading-5',
              selected ? 'font-medium text-primary' : 'font-normal text-foreground',
            )}
          >
            {title}
          </span>
          {subtitle && (
            <span
              className={cn(
                'mt-0.5 block truncate text-xs leading-5',
                selected ? 'text-primary/80' : 'text-muted-foreground',
              )}
            >
              {subtitle}
            </span>
          )}
        </div>
      </Button>
      {(dateText || trailing) && (
        <div
          className={cn(
            'flex shrink-0 gap-2 pr-3 text-xs text-muted-foreground',
            compact ? 'items-center' : 'items-center pt-2',
          )}
        >
          {dateText && dateTooltip ? (
            <TooltipProvider delayDuration={300}>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="whitespace-nowrap leading-5">{dateText}</span>
                </TooltipTrigger>
                <TooltipContent>{dateTooltip}</TooltipContent>
              </Tooltip>
            </TooltipProvider>
          ) : (
            dateText && <span className="whitespace-nowrap leading-5">{dateText}</span>
          )}
          {trailing}
        </div>
      )}
    </div>
  );
});
