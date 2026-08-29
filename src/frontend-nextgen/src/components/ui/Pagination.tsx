import { cn } from '@/utils/cn';
import { ChevronLeft, ChevronRight } from 'lucide-react';

export interface PaginationProps {
  /** 当前页码（1-based） */
  current: number;
  /** 每页条数 */
  pageSize: number;
  /** 总条数 */
  total: number;
  /** 页码变化回调 */
  onChange: (page: number) => void;
  className?: string;
}

/** 简单分页器：上一页/下一页 + 当前页/总页数 */
export function Pagination({ current, pageSize, total, onChange, className }: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const canPrev = current > 1;
  const canNext = current < totalPages;

  if (total === 0) return null;

  return (
    <div className={cn('flex items-center gap-4 text-xs text-muted-foreground', className)}>
      <span className="tabular-nums">
        共 {total} 条 · 第 {current}/{totalPages} 页
      </span>
      <div className="flex items-center gap-1">
        <button
          type="button"
          aria-label="上一页"
          disabled={!canPrev}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => canPrev && onChange(current - 1)}
        >
          <ChevronLeft className="size-4" />
        </button>
        <button
          type="button"
          aria-label="下一页"
          disabled={!canNext}
          className="inline-flex h-7 w-7 items-center justify-center rounded-md border border-border text-foreground transition-colors hover:bg-accent disabled:cursor-not-allowed disabled:opacity-50"
          onClick={() => canNext && onChange(current + 1)}
        >
          <ChevronRight className="size-4" />
        </button>
      </div>
    </div>
  );
}
