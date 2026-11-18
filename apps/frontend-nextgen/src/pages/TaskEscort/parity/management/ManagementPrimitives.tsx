import { Button } from '@/components/ui/Button';
import { Skeleton } from '@/components/ui/Skeleton';
import { MANAGEMENT_WRITE_REASON } from '@/domain/taskEscortParity/management';
import type { ReactNode } from 'react';
import { useId } from 'react';

export function ManagementSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      aria-label={title}
      className="min-w-0 space-y-4 rounded-xl border border-border bg-card p-4 text-card-foreground shadow-sm"
    >
      <h2 className="text-base font-semibold">{title}</h2>
      {children}
    </section>
  );
}
export function ReadState({ resource }: { resource: { loading: boolean; error: string | null; refresh: () => void } }) {
  if (resource.loading)
    return (
      <div role="status" className="space-y-2 text-muted-foreground">
        正在读取…
        <Skeleton.Block className="h-8 w-full" />
      </div>
    );
  if (resource.error)
    return (
      <div role="alert" className="space-y-2 text-destructive">
        <p>{resource.error}</p>
        <Button variant="outline" size="sm" onClick={resource.refresh}>
          重试读取
        </Button>
      </div>
    );
  return null;
}
export function DisabledWrite({ children, destructive = false }: { children: ReactNode; destructive?: boolean }) {
  const id = useId();
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <Button size="sm" variant={destructive ? 'destructive' : 'outline'} disabled aria-describedby={id}>
        {children}
      </Button>
      <span id={id} className="text-xs text-muted-foreground">
        {MANAGEMENT_WRITE_REASON}
      </span>
    </span>
  );
}
export function Fields({ fields }: { fields: [string, ReactNode][] }) {
  return (
    <dl className="grid min-w-0 gap-3 sm:grid-cols-2">
      {fields.map(([label, value]) => (
        <div key={label} className="min-w-0">
          <dt className="text-muted-foreground">{label}</dt>
          <dd className="mt-1 break-words [overflow-wrap:anywhere]">{value ?? '未记录'}</dd>
        </div>
      ))}
    </dl>
  );
}
export const yesNo = (value: boolean | undefined | null) =>
  value === null || value === undefined ? '未记录' : value ? '是' : '否';
