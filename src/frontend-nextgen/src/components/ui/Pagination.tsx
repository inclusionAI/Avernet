import { cn } from '@/utils/cn';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from './Select';

export interface PaginationProps {
  /** 当前页码（1-based） */
  current: number;
  /** 每页条数 */
  pageSize: number;
  /** 总条数 */
  total: number;
  /** 页码变化回调 */
  onChange: (page: number) => void;
  /** 每页条数变化回调 */
  onPageSizeChange?: (pageSize: number) => void;
  /** 每页条数可选项 */
  pageSizeOptions?: number[];
  className?: string;
}

/** 简单分页器：上一页/下一页 + 当前页/总页数 */
export function Pagination({
  current,
  pageSize,
  total,
  onChange,
  onPageSizeChange,
  pageSizeOptions = [10, 20, 50],
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const canPrev = current > 1;
  const canNext = current < totalPages;
  const showPageSizePicker = pageSizeOptions.length > 1 && Boolean(onPageSizeChange);

  if (total === 0) return null;

  return (
    <div className={cn('flex flex-wrap items-center justify-end gap-3 text-xs text-[var(--color-muted)]', className)}>
      <span className="tabular-nums">
        共 {total} 条 · 第 {current}/{totalPages} 页
      </span>
      {showPageSizePicker ? (
        <div className="flex items-center gap-2">
          <span>每页</span>
          <Select
            value={String(pageSize)}
            onValueChange={(value) => {
              const nextPageSize = Number(value);
              if (Number.isFinite(nextPageSize) && nextPageSize > 0) {
                onPageSizeChange?.(nextPageSize);
                onChange(1);
              }
            }}
          >
            <SelectTrigger className="h-8 w-[7.5rem] text-xs">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {pageSizeOptions.map((item) => (
                <SelectItem key={item} value={String(item)}>
                  {item} 条/页
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      ) : null}
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="上一页"
          disabled={!canPrev}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-[var(--color-border)] text-[var(--color-fg)] transition-colors hover:bg-[var(--color-panel-muted)] disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => canPrev && onChange(current - 1)}
        >
          <ChevronLeft className="size-4" />
        </button>
        <button
          type="button"
          aria-label="下一页"
          disabled={!canNext}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-[var(--color-border)] text-[var(--color-fg)] transition-colors hover:bg-[var(--color-panel-muted)] disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => canNext && onChange(current + 1)}
        >
          <ChevronRight className="size-4" />
        </button>
      </div>
    </div>
  );
}
