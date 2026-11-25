import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';

export interface ManagePanelTabOption<T extends string> {
  value: T;
  label: string;
  count?: number;
}

export interface ManagePanelTabsProps<T extends string> {
  value: T;
  options: ManagePanelTabOption<T>[];
  onChange: (value: T) => void;
}

/** PRD 风格的 underline tabs，避免引入 antd。tab 多时横向滚动（不换行不收缩）。 */
export function ManagePanelTabs<T extends string>({ value, options, onChange }: ManagePanelTabsProps<T>) {
  return (
    <div className="sticky top-0 z-10 flex overflow-x-auto border-b border-divider bg-background px-5 pt-3">
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
              'relative h-10 shrink-0 justify-center rounded-none border-0 px-3 pb-3 text-xs font-medium',
              active ? 'text-primary' : 'text-muted-foreground hover:bg-transparent hover:text-foreground',
            )}
          >
            {option.label}
            {option.count !== undefined && (
              <span className="rounded-full bg-muted px-2 py-0.5 tabular-nums text-muted-foreground">{option.count}</span>
            )}
            {active && <span className="absolute inset-x-0 bottom-0 h-0.5 rounded-full bg-primary" aria-hidden />}
          </Button>
        );
      })}
    </div>
  );
}
