import { cn } from '@/utils/cn';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import { useState } from 'react';
import { Input } from './Input';
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
  /** 是否显示「跳至 X 页」输入框（Enter 或 Go 触发，越界钳制到末页） */
  showQuickJumper?: boolean;
  className?: string;
}

/** 简单分页器：上一页/下一页 + 当前页/总页数，可选每页条数选择与跳页输入 */
export function Pagination({
  current,
  pageSize,
  total,
  onChange,
  onPageSizeChange,
  pageSizeOptions = [10, 20, 50],
  showQuickJumper = false,
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const canPrev = current > 1;
  const canNext = current < totalPages;
  const showPageSizePicker = pageSizeOptions.length > 1 && Boolean(onPageSizeChange);
  const [jumpValue, setJumpValue] = useState('');

  const jumpToPage = () => {
    const target = Number(jumpValue);
    setJumpValue('');
    if (!Number.isInteger(target) || target < 1) return;
    const clamped = Math.min(target, totalPages);
    // 目标即当前页时不触发回调，避免重复请求
    if (clamped !== current) onChange(clamped);
  };

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
                // 页码重置归调用方负责（如 MyTask 在 handler 内 setPage(1)）；
                // 组件不代调 onChange(1)，避免回调驱动型消费方闭包旧 pageSize 造成重复请求竞态。
                onPageSizeChange?.(nextPageSize);
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
      {showQuickJumper ? (
        <div className="flex items-center gap-1 text-foreground">
          <span>跳至</span>
          <Input
            aria-label="跳至页码"
            inputMode="numeric"
            className="h-7 w-12 rounded-md px-0 text-center"
            value={jumpValue}
            onChange={(e) => setJumpValue(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') jumpToPage();
            }}
          />
          <span>页</span>
          <button
            type="button"
            className="inline-flex h-7 items-center justify-center rounded-md border border-border px-2 text-foreground transition-colors hover:bg-muted"
            onClick={jumpToPage}
          >
            Go
          </button>
        </div>
      ) : null}
    </div>
  );
}
