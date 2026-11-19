import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui';
import { cn } from '@/utils/cn';
import { RunStatus, STATUS_LABEL } from './model';

export function DemoBadge() {
  return (
    <span className="rounded border border-primary/20 bg-primary/5 px-1.5 py-0.5 text-[10px] font-semibold tracking-wider text-primary">
      DEMO
    </span>
  );
}
export function StatusBadge({ status }: { status: RunStatus }) {
  const tone =
    status === 'success'
      ? 'bg-success/10 text-success'
      : ['failed', 'terminated', 'blocked'].includes(status)
      ? 'bg-destructive/10 text-destructive'
      : status === 'waiting'
      ? 'bg-warning/10 text-foreground'
      : status === 'running'
      ? 'bg-primary/10 text-primary'
      : 'bg-muted text-muted-foreground';
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1 text-[11px] font-medium',
        tone,
      )}
    >
      <span aria-hidden className="size-1.5 rounded-full bg-current" />
      {STATUS_LABEL[status]}
    </span>
  );
}
export function DemoSelect<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: T;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <Select value={value} onValueChange={onChange}>
      <SelectTrigger aria-label={label} className={cn('h-8 w-auto min-w-32', className)}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map((option) => (
          <SelectItem key={option.value} value={option.value}>
            {option.label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  );
}
