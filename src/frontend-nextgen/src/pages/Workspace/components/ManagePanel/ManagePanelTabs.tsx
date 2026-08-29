import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';

export interface ManagePanelTabOption<T extends string> {
  value: T;
  label: string;
}

export interface ManagePanelTabsProps<T extends string> {
  value: T;
  options: ManagePanelTabOption<T>[];
  onChange: (value: T) => void;
}

/** PRD 风格的 underline tabs，避免引入 antd。 */
export function ManagePanelTabs<T extends string>({ value, options, onChange }: ManagePanelTabsProps<T>) {
  return (
    <div className="sticky top-0 z-10 flex border-b border-[var(--color-border)] bg-white px-5 pt-3">
      {options.map((option) => {
        const active = option.value === value;
        return (
          <Button
            key={option.value}
            type="button"
            variant="ghost"
            size="sm"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(option.value)}
            className={cn(
              'relative h-10 justify-center rounded-none border-0 px-3 pb-3 text-sm font-medium',
              active
                ? 'text-[var(--color-primary)]'
                : 'text-[var(--color-muted)] hover:bg-transparent hover:text-[var(--color-fg)]',
            )}
          >
            {option.label}
            {active && (
              <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-[var(--color-primary)]" aria-hidden />
            )}
          </Button>
        );
      })}
    </div>
  );
}
