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
}

/** 会话协作卡片:左侧「会话」角标 + 标题/副行 + 日期;选中态浅蓝底、蓝边与左侧蓝色指示条。 */
export const SessionCard = React.memo(function SessionCard({
  title,
  subtitle = '暂无会话预览',
  dateText,
  selected,
  onSelect,
  trailing,
}: SessionCardProps) {
  const handleClick = (e?: React.MouseEvent) => {
    e?.stopPropagation();
    onSelect();
  };

  return (
    <div
      role="button"
      tabIndex={0}
      aria-pressed={selected}
      onClick={handleClick}
      onKeyDown={(e) => {
        if (e.key === 'Enter' || e.key === ' ') {
          e.preventDefault();
          e.stopPropagation();
          onSelect();
        }
      }}
      className={cn(
        'group relative flex cursor-pointer items-center gap-2.5 rounded-xl border bg-[var(--color-card)] px-3 py-2.5 text-sm transition-colors',
        selected
          ? 'border-transparent bg-[var(--color-primary-soft)]'
          : 'border-[var(--color-border)] hover:border-[var(--color-primary-weak)]',
      )}
    >
      {selected && (
        <span
          aria-hidden="true"
          className="absolute left-0 top-2 bottom-2 w-[3px] rounded-full bg-[var(--color-primary)]"
        />
      )}
      <span
        aria-hidden="true"
        className={cn(
          'flex h-9 w-9 shrink-0 items-center justify-center rounded-lg',
          selected
            ? 'bg-[var(--color-primary)]/10 text-[var(--color-primary)]'
            : 'bg-[var(--color-panel-strong)] text-[var(--color-muted)]',
        )}
      >
        <MessageCircle className="h-4 w-4" />
      </span>
      <div className="min-w-0 flex-1">
        {/* 第一行：标题 + 横点操作 */}
        <div className="flex items-center gap-1">
          <span
            className={cn(
              'truncate text-[13px] font-medium',
              selected ? 'text-[var(--color-primary)]' : 'text-[var(--color-fg)]',
            )}
          >
            {title}
          </span>
          {trailing && <div className="ml-auto flex shrink-0 items-center">{trailing}</div>}
        </div>
        {/* 第二行：消息数/成员数 + 时间右对齐 */}
        <div className="mt-0.5 flex items-center gap-1">
          {subtitle && <span className="truncate text-xs leading-5 text-[var(--color-muted)]">{subtitle}</span>}
          {dateText && <span className="ml-auto shrink-0 text-xs leading-5 text-[var(--color-muted)]">{dateText}</span>}
        </div>
      </div>
    </div>
  );
});
