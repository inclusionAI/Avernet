import { Button } from '@/components/ui';
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

interface SessionCardProps {
  title: string;
  /** 副行文案(真实预览/成员数等);未传时显示「暂无会话预览」。 */
  subtitle?: string;
  /** 右侧日期(MM/DD),无则不渲染。 */
  dateText?: string;
  selected: boolean;
  onSelect: () => void;
  /** 右侧操作区（如更多菜单），由调用方决定可见性。 */
  trailing?: React.ReactNode;
  /** 无副行内容时使用更紧凑的单行布局。 */
  compact?: boolean;
  /** 调用方可按列表场景补充尺寸；不改变卡片的交互语义。 */
  className?: string;
}

/** 二级会话列表项:无卡片容器、缩进排列、轻量图标 + 标题/副行 + 日期；选中态只用浅底色，不复用一级资源指示条。 */
export const SessionCard = React.memo(function SessionCard({
  title,
  subtitle = '暂无会话预览',
  dateText,
  selected,
  onSelect,
  trailing,
  compact = false,
  className,
}: SessionCardProps) {
  return (
    <div
      className={cn(
        'group relative flex items-stretch border-t border-border/70 text-sm transition-colors',
        compact ? 'min-h-[56px]' : 'min-h-[70px]',
        selected ? 'bg-primary/5' : 'bg-background hover:bg-accent/30',
        className,
      )}
    >
      <Button
        variant="ghost"
        aria-pressed={selected}
        onClick={onSelect}
        className={cn(
          'flex h-auto min-w-0 flex-1 justify-start gap-2 rounded-none px-2.5 text-left hover:bg-transparent focus-visible:z-10',
          compact ? 'items-center' : 'items-start',
          compact ? 'min-h-[56px] py-2' : 'min-h-[70px] py-3',
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            'flex h-4 w-4 shrink-0 items-center justify-center',
            !compact && 'mt-0.5',
            selected ? 'text-primary/70' : 'text-muted-foreground',
          )}
        >
          <MessageSquare className="h-3.5 w-3.5" />
        </span>
        <div className="min-w-0 flex-1">
          <span className="block truncate text-[13px] font-normal leading-5 text-foreground">{title}</span>
          {subtitle && (
            <span className="mt-0.5 block truncate text-xs leading-5 text-muted-foreground">{subtitle}</span>
          )}
        </div>
      </Button>
      {(dateText || trailing) && (
        <div
          className={cn(
            'flex shrink-0 gap-2 pr-3 text-xs text-muted-foreground',
            compact ? 'items-center' : 'items-start pt-3',
          )}
        >
          {dateText && <span className="whitespace-nowrap leading-5">{dateText}</span>}
          {trailing}
        </div>
      )}
    </div>
  );
});
