import { Button } from '@/components/ui/Button';
import { Empty } from '@/components/ui/Empty';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { Skeleton } from '@/components/ui/Skeleton';
import { useRef } from 'react';
export function ReadState({
  loading,
  error,
  refresh,
}: {
  loading?: boolean;
  error?: string | null;
  refresh?: () => void;
}) {
  if (error)
    return (
      <div
        role="alert"
        className="rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"
      >
        {error}
        {refresh && (
          <Button variant="outline" size="sm" onClick={refresh} className="ml-3">
            重试读取
          </Button>
        )}
      </div>
    );
  if (loading)
    return (
      <div role="status" aria-label="正在读取真实数据" className="space-y-3 p-4">
        <Skeleton.Block className="h-5 w-2/3" />
        <Skeleton.Block className="h-20 w-full" />
      </div>
    );
  return null;
}
export function NoData({ text = '暂无记录' }: { text?: string }) {
  return <Empty compact title={text} />;
}
export function ReadSelect({
  label,
  value,
  onChange,
  options,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
}) {
  return (
    <label className="grid min-w-32 gap-1 text-xs text-muted-foreground">
      {label}
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger aria-label={label}>
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </label>
  );
}
export function ReadTabs<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (v: T) => void;
}) {
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const select = (index: number) => {
    onChange(options[index].value);
    refs.current[index]?.focus();
  };
  return (
    <nav aria-label={label} className="shrink-0 overflow-x-auto border-b border-border">
      <div role="tablist" aria-label={label} className="flex min-w-max items-stretch gap-6 px-1">
        {options.map((option, index) => (
          <Button
            key={option.value}
            ref={(node) => {
              refs.current[index] = node;
            }}
            variant="ghost"
            role="tab"
            aria-selected={value === option.value}
            tabIndex={value === option.value ? 0 : -1}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => {
              let next = index;
              if (event.key === 'ArrowRight') next = (index + 1) % options.length;
              else if (event.key === 'ArrowLeft') next = (index - 1 + options.length) % options.length;
              else if (event.key === 'Home') next = 0;
              else if (event.key === 'End') next = options.length - 1;
              else return;
              event.preventDefault();
              select(next);
            }}
            className={`relative h-[54px] justify-center px-1 text-sm hover:bg-transparent ${
              value === option.value
                ? 'font-medium text-foreground'
                : 'font-normal text-muted-foreground hover:text-primary'
            }`}
          >
            {option.label}
            <span
              aria-hidden
              className={`absolute inset-x-0 bottom-0 h-[3px] rounded-t-full bg-primary transition-transform ${
                value === option.value ? 'scale-x-100' : 'scale-x-0'
              }`}
            />
          </Button>
        ))}
      </div>
    </nav>
  );
}
export function ReadPager({
  label,
  page,
  totalPages,
  total,
  onChange,
  pageSize,
  onSize,
}: {
  label: string;
  page: number;
  totalPages: number;
  total?: number;
  onChange: (n: number) => void;
  pageSize?: number;
  onSize?: (n: number) => void;
}) {
  return (
    <nav
      aria-label={`${label}分页`}
      className="flex flex-wrap items-center justify-between gap-3 py-3 text-xs text-muted-foreground"
    >
      <span>
        第 {page} / {Math.max(1, totalPages)} 页{total !== undefined ? ` · 共 ${total} 条` : ''}
      </span>
      <div className="flex items-end gap-2">
        {onSize && (
          <ReadSelect
            label={`${label}每页条数`}
            value={String(pageSize)}
            onChange={(s) => onSize(Number(s))}
            options={[10, 20, 50].map((n) => ({ value: String(n), label: `${n} 条` }))}
          />
        )}
        <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => onChange(page - 1)}>
          上一页{label}
        </Button>
        <Button variant="outline" size="sm" disabled={page >= totalPages} onClick={() => onChange(page + 1)}>
          下一页{label}
        </Button>
      </div>
    </nav>
  );
}
export function JsonDetails({ label, value, open = false }: { label: string; value: unknown; open?: boolean }) {
  return (
    <details open={open} className="my-3 rounded-lg border border-border p-3 text-xs">
      <summary className="cursor-pointer font-medium">{label}</summary>
      <pre className="mt-3 max-h-[32rem] overflow-auto whitespace-pre-wrap break-all font-mono leading-5">
        {JSON.stringify(value, null, 2) ?? '未提供'}
      </pre>
    </details>
  );
}
export function WriteUnavailable({
  canEdit,
  reason,
  actions = ['生成优化任务'],
}: {
  canEdit: boolean;
  reason?: string | null;
  actions?: string[];
}) {
  return (
    <section aria-label="只读能力说明" className="space-y-2 rounded-lg border border-border bg-muted/40 p-3 text-xs">
      <p>
        {!canEdit ? '当前账号为只读权限。 ' : ''}
        {reason ? `服务端：${reason}。 ` : ''}
        前端尚未集成经确认的写操作；仅展示真实读取，不会生成、应用、发布或启动试运行。
      </p>
      <div className="flex flex-wrap gap-2">
        {actions.map((a) => (
          <Button size="sm" key={a} disabled>
            {a}
          </Button>
        ))}
      </div>
    </section>
  );
}

export function ReadCapabilities({
  capabilities,
  canEdit,
}: {
  capabilities: { generation: boolean; diff: boolean; publication: boolean; reason: string | null };
  canEdit: boolean;
}) {
  return (
    <section aria-label="服务端修复能力" className="space-y-1 text-xs text-muted-foreground">
      <p>
        服务端能力：生成 {capabilities.generation ? '已配置' : '不可用'} · 差异{' '}
        {capabilities.diff ? '已配置' : '不可用'} · 发布 {capabilities.publication ? '已配置' : '不可用'} · 编辑权限{' '}
        {canEdit ? '有' : '无'}
      </p>
      {capabilities.reason && <p>{capabilities.reason}</p>}
      <p>服务端能力已配置不代表前端已接入写操作。</p>
    </section>
  );
}
