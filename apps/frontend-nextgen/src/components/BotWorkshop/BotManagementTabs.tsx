import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';

export type BotManagementTab = 'tc' | 'external';

export function BotManagementTabs({
  value,
  onChange,
}: {
  value: BotManagementTab;
  onChange: (tab: BotManagementTab) => void;
}) {
  return (
    <div className="flex border-b border-border" role="tablist" aria-label="Bot 类型">
      {(
        [
          ['tc', 'TC Bot'],
          ['external', '外部 Bot'],
        ] as const
      ).map(([tab, label]) => (
        <Button
          key={tab}
          role="tab"
          aria-selected={value === tab}
          variant="ghost"
          className={cn(
            'relative rounded-none px-4 text-muted-foreground hover:bg-transparent hover:text-foreground',
            value === tab && 'text-primary after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:bg-primary',
          )}
          onClick={() => onChange(tab)}
        >
          {label}
        </Button>
      ))}
    </div>
  );
}
