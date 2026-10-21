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
  /** 布局：end = 总数与控件整体靠右（历史默认）；between = 总数靠左、控件靠右（dmore 分页稿） */
  layout?: 'end' | 'between';
  /** 只透传页数字号；第 X/Y 页信息已由数字页码按钮表达 */
  showPageInfo?: boolean;
  className?: string;
}

/** 页码按钮基础样式：26×26 r6 与 dmore 稿一致；当前页蓝底白字。 */
const pageButtonClass =
  'inline-flex h-[26px] min-w-[26px] items-center justify-center rounded-md border border-[var(--color-border)] px-1 text-xs tabular-nums text-[var(--color-fg)] transition-colors cursor-pointer hover:bg-[var(--color-panel-muted)] disabled:cursor-not-allowed disabled:opacity-50';

/** 生成 1..totalPages 的页码序列，长列表折叠中间段为 -1（省略号占位）。 */
function pageSequence(current: number, totalPages: number): number[] {
  if (totalPages <= 7) return Array.from({ length: totalPages }, (_, index) => index + 1);
  const pages = new Set<number>([1, totalPages, current, current - 1, current + 1]);
  if (current <= 3) [2, 3, 4].forEach((page) => pages.add(page));
  if (current >= totalPages - 2) [totalPages - 3, totalPages - 2, totalPages - 1].forEach((page) => pages.add(page));
  const sorted = [...pages].filter((page) => page >= 1 && page <= totalPages).sort((a, b) => a - b);
  const result: number[] = [];
  sorted.forEach((page, index) => {
    if (index > 0 && page - sorted[index - 1] > 1) result.push(-1);
    result.push(page);
  });
  return result;
}

/** 简单分页器：数字页码 + 上一页/下一页，可选每页条数选择与跳页输入 */
export function Pagination({
  current,
  pageSize,
  total,
  onChange,
  onPageSizeChange,
  pageSizeOptions = [10, 20, 50],
  showQuickJumper = false,
  layout = 'end',
  showPageInfo = false,
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
    <div
      className={cn(
        'flex flex-wrap items-center gap-3 text-xs text-[var(--color-muted)]',
        layout === 'between' ? 'justify-between' : 'justify-end',
        className,
      )}
    >
      <span className="tabular-nums">
        共 {total} 条{showPageInfo ? ` · 第 ${current}/${totalPages} 页` : ''}
      </span>
      <div className={cn('flex flex-wrap items-center gap-3', layout === 'between' ? '' : 'ml-auto')}>
        {showPageSizePicker ? (
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
            <SelectTrigger className="h-[26px] w-[6.5rem] rounded-md text-xs">
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
        ) : null}
        <div className="flex items-center gap-1">
          <button
            type="button"
            aria-label="上一页"
            disabled={!canPrev}
            className={cn(pageButtonClass, 'px-0')}
            onClick={() => canPrev && onChange(current - 1)}
          >
            <ChevronLeft className="size-4" />
          </button>
          {pageSequence(current, totalPages).map((page, index) =>
            page === -1 ? (
              <span key={`ellipsis-${index}`} className="inline-flex h-[26px] items-center px-1 text-xs">
                …
              </span>
            ) : (
              <button
                key={page}
                type="button"
                aria-label={`第 ${page} 页`}
                aria-current={page === current ? 'page' : undefined}
                className={cn(
                  pageButtonClass,
                  page === current
                    ? 'border-transparent bg-primary text-primary-foreground hover:opacity-90'
                    : 'hover:bg-[var(--color-panel-muted)]',
                )}
                onClick={() => page !== current && onChange(page)}
              >
                {page}
              </button>
            ),
          )}
          <button
            type="button"
            aria-label="下一页"
            disabled={!canNext}
            className={cn(pageButtonClass, 'px-0')}
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
    </div>
  );
}