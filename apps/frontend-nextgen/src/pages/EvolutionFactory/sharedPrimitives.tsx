import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/Tooltip';
import { cn } from '@/utils/cn';
import { labelOf, statusLabel } from '@/domain/evolution/types';
import type { ReactElement, ReactNode } from 'react';

/** 结果面板容器：数据视图 = 白底 + 细边框 r10（与 Bot 管理列表面板同规范）。 */
export function TablePanel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('overflow-hidden rounded-[10px] border border-border/60 bg-card', className)}>{children}</div>;
}
/** 结果面板容器：空态 / 错误态 = 40% 灰底留白面板，居中引导（同规范）。 */
export function PlaceholderPanel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('rounded-[10px] bg-muted/40', className)}>{children}</div>;
}

/** 列表骨架：44px 表头条 + 72px 行高，与 DataTable(h-18) 几何一致。首列两行占位，其余单行。 */
export function ListSkeleton({
  widths,
  rows = 6,
  ariaLabel = '列表加载中',
}: {
  widths: string[];
  rows?: number;
  ariaLabel?: string;
}) {
  return (
    <div role="status" aria-label={ariaLabel} className="w-full overflow-x-auto">
      <table aria-hidden className="w-full table-fixed border-collapse text-sm">
        <thead className="bg-muted/50">
          <tr>
            {widths.map((width, index) => (
              <th key={index} className={cn('h-11 px-4 text-left', width)}>
                <Skeleton.Line className="w-14" />
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {Array.from({ length: rows }, (_, rowIndex) => (
            <tr key={rowIndex} className="h-18 border-b border-divider/60">
              {widths.map((width, columnIndex) => (
                <td key={columnIndex} className={cn('px-4 align-middle', width)}>
                  {columnIndex === 0 ? (
                    <div className="space-y-2">
                      <Skeleton.Line className="w-2/3" />
                      <Skeleton.Line className="w-1/3" />
                    </div>
                  ) : (
                    <Skeleton.Line className="w-12" />
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function StatusBadge({ status, dot = false }: { status: string; dot?: boolean }) {
  const tone = ['completed', 'succeeded'].includes(status)
    ? 'success'
    : ['failed', 'canceled', 'cancelled'].includes(status)
    ? 'error'
    : status.startsWith('waiting')
    ? 'warning'
    : 'primary';
  // dot 态与 Bot 管理状态列（dmore 规范）几何一致：8px 圆点 + 13px muted 说明文字。
  if (dot)
    return (
      <span className="inline-flex items-center gap-2 whitespace-nowrap text-[13px] text-muted-foreground">
        <span
          aria-hidden="true"
          className={`size-2 rounded-full ${
            tone === 'success'
              ? 'bg-success'
              : tone === 'error'
              ? 'bg-destructive'
              : tone === 'warning'
              ? 'bg-warning'
              : 'bg-primary'
          }`}
        />
        {statusLabel(status)}
      </span>
    );
  return <Badge tone={tone}>{statusLabel(status)}</Badge>;
}
export function ErrorBox({ message, retry }: { message: string; retry?: () => void }) {
  if (!message) return null;
  return (
    <div
      role="alert"
      className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive"
    >
      <span>{message}</span>
      {retry && (
        <Button variant="outline" size="sm" onClick={retry}>
          重新加载
        </Button>
      )}
    </div>
  );
}
export function JsonView({ value }: { value: unknown }) {
  return (
    <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted p-4 text-xs">
      {JSON.stringify(value, null, 2)}
    </pre>
  );
}

export function typeTone(type: string) {
  return type === 'full'
    ? 'bg-[hsl(var(--purple)/0.08)] text-[hsl(var(--purple))]'
    : ['optimize', 'bench_optimize'].includes(type)
    ? 'bg-warning/10 text-warning'
    : type === 'bench'
    ? 'bg-info/10 text-info'
    : 'bg-primary/10 text-primary';
}
export function TypeTag({ type }: { type: string }) {
  return (
    <span className={`inline-flex whitespace-nowrap rounded px-2 py-0.5 text-xs ${typeTone(type)}`}>
      {labelOf(type)}
    </span>
  );
}

/** Overflow labels retain hover and keyboard access through the shared UI primitive. */
export function Hint({ content, children }: { content?: ReactNode; children: ReactElement }) {
  if (!content) return children;
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild tabIndex={0}>
          {children}
        </TooltipTrigger>
        <TooltipContent>{content}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}