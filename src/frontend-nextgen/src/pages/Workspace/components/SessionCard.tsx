import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';
import { MessageCircle } from 'lucide-react';
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

interface SessionCardProps {
  title: string;
  /** 副行文案(真实预览/成员数等);未传时显示「暂无会话预览」。 */
  subtitle?: string;
  /** 右侧日期(MM/DD),无则不渲染。 */
  dateText?: string;
  selected: boolean;
  onSelect: () => void;
  /** 右侧操作区(收藏等),由调用方决定可见性。 */
  trailing?: React.ReactNode;
  /** 调用方可按列表场景补充尺寸；不改变卡片的交互语义。 */
  className?: string;
}

/** 会话列表项:左侧会话图标 + 标题/副行 + 日期;选中态浅蓝底与左侧蓝色指示条。 */
export const SessionCard = React.memo(function SessionCard({
  title,
  subtitle = '暂无会话预览',
  dateText,
  selected,
  onSelect,
  trailing,
  className,
}: SessionCardProps) {
  return (
    <div
      className={cn(
        'group relative flex min-h-12 items-center border-b border-border/70 text-sm transition-colors last:border-b-0',
        selected ? 'bg-primary/5' : 'bg-card hover:bg-accent/50',
        className,
      )}
    >
      {selected && (
        <span aria-hidden="true" className="absolute bottom-2 left-0 top-2 z-10 w-[3px] rounded-r-sm bg-primary" />
      )}
      <Button
        variant="ghost"
        aria-pressed={selected}
        onClick={onSelect}
        className="flex h-auto min-h-12 min-w-0 flex-1 items-center justify-start gap-2 rounded-none px-2.5 py-1.5 text-left hover:bg-transparent focus-visible:z-10"
      >
        <span
          aria-hidden="true"
          className={cn(
            'flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
            selected ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
          )}
        >
          <MessageCircle className="h-4 w-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1">
            <span className={cn('truncate text-[13px] font-medium', selected ? 'text-primary' : 'text-foreground')}>
              {title}
            </span>
          </div>
          <div className="mt-0.5 flex items-center gap-1">
            {subtitle && <span className="truncate text-xs leading-5 text-muted-foreground">{subtitle}</span>}
            {dateText && <span className="ml-auto shrink-0 text-xs leading-5 text-muted-foreground">{dateText}</span>}
          </div>
        </div>
      </Button>
      {trailing && <div className="flex shrink-0 items-start pr-2 pt-2">{trailing}</div>}
    </div>
  );
});
