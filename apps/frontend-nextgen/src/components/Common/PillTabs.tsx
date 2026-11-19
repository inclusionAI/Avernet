import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';

export function PillTabs<T extends string>({
  value, options, onChange, label, className,
}: {
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  label: string;
  className?: string;
}) {
  return (
    <div className={cn('flex items-center gap-1', className)} role="tablist" aria-label={label}>
      {options.map(({ value: tab, label }) => (
        <Button
          key={tab}
          variant="ghost"
          role="tab"
          type="button"
          aria-selected={value === tab}
          className={cn(
            'inline-flex h-8 items-center rounded-md px-3 text-[13px] transition-colors cursor-pointer',
            value === tab
              ? 'bg-primary/10 font-medium text-primary'
              : 'font-normal text-muted-foreground hover:bg-muted hover:text-foreground',
          )}
          onClick={() => onChange(tab)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}
