import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Card } from '@/components/ui/Card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select';
import { labelOf, statusLabel } from '@/domain/evolution/types';
import { history } from '@umijs/max';
import type { ReactNode } from 'react';
import { useCallback, useEffect, useState } from 'react';

export function EvolutionHeader({ tab, actions }: { tab: 'tasks' | 'templates' | 'versions'; actions?: ReactNode }) {
  return (
    <div className="space-y-5">
      <header className="flex items-center justify-between gap-3">
        <h1 className="text-2xl font-semibold">智能进化</h1>
        {actions}
      </header>
      <div role="tablist" aria-label="智能进化" className="flex gap-6 border-b border-border">
        {(
          [
            ['tasks', '进化任务'],
            ['templates', '评测模板'],
            ['versions', '进化版本'],
          ] as const
        ).map(([value, label]) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            className={`border-b-2 px-3 pb-3 text-sm ${
              tab === value ? 'border-primary font-medium text-primary' : 'border-transparent text-muted-foreground'
            }`}
            onClick={() => history.push(`/evolution-factory${value === 'tasks' ? '' : `?tab=${value}`}`)}
          >
            {label}
          </button>
        ))}
      </div>
    </div>
  );
}

export function useResource<T>(load: () => Promise<T>, enabled = true) {
  const [data, setData] = useState<T>();
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(enabled);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((value) => value + 1), []);
  useEffect(() => {
    let current = true;
    setData(undefined);
    setError('');
    if (!enabled) {
      setLoading(false);
      return;
    }
    setLoading(true);
    load()
      .then((value) => {
        if (current) setData(value);
      })
      .catch((e: unknown) => {
        if (current) setError(e instanceof Error ? e.message : '加载失败');
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [load, enabled, revision]);
  return { data, error, loading, reload };
}
export function StatusBadge({ status, dot = false }: { status: string; dot?: boolean }) {
  const tone = ['completed', 'succeeded'].includes(status)
    ? 'success'
    : ['failed', 'canceled', 'cancelled'].includes(status)
    ? 'error'
    : status.startsWith('waiting')
    ? 'warning'
    : 'primary';
  if (dot)
    return (
      <span className="inline-flex items-center gap-2 whitespace-nowrap text-xs">
        <span
          aria-hidden="true"
          className={`h-1.5 w-1.5 rounded-full ${
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
export function Section({ title, children, index }: { title: string; children: ReactNode; index?: number }) {
  return (
    <Card className="p-5 sm:p-6">
      <h2 className="mb-4 flex items-center gap-2 text-[15px] font-semibold">
        {index !== undefined && (
          <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-primary text-xs text-primary-foreground">
            {index}
          </span>
        )}
        {title}
      </h2>
      {children}
    </Card>
  );
}
export function Field({ label, help, children }: { label: string; help?: string; children: ReactNode }) {
  return (
    <label className="flex min-w-0 flex-col gap-2 text-sm">
      <span className="font-medium">{label}</span>
      {children}
      {help && <span className="text-xs text-muted-foreground">{help}</span>}
    </label>
  );
}
export function Choice({
  value,
  onChange,
  options,
  label,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  options: { value: string; label: string }[];
  label: string;
  disabled?: boolean;
}) {
  return (
    <Select value={value || undefined} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={label}>
        <SelectValue placeholder="请选择" />
      </SelectTrigger>
      <SelectContent>
        {options
          .filter((item) => item.value)
          .map((item) => (
            <SelectItem key={item.value} value={item.value}>
              {item.label}
            </SelectItem>
          ))}
      </SelectContent>
    </Select>
  );
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
