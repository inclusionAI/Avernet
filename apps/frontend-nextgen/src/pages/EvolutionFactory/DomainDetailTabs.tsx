import { Button } from '@/components/ui/Button';

type DomainTab = 'detail' | 'tasks';

export function DomainDetailTabs({
  tab,
  onChange,
  writable,
  busy,
  selectedCount,
  onPublish,
}: {
  tab: DomainTab;
  onChange: (tab: DomainTab) => void;
  writable: boolean;
  busy: boolean;
  selectedCount: number;
  onPublish: () => Promise<void>;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div role="tablist" aria-label="评测详情" className="flex items-center gap-2">
        {(
          [
            ['detail', '目标详情'],
            ['tasks', '关联任务'],
          ] as const
        ).map(([value, label]) => (
          <Button
            key={value}
            variant="ghost"
            type="button"
            role="tab"
            aria-selected={tab === value}
            className={`h-8 rounded-md border-0 px-3 text-[13px] ${
              tab === value
                ? 'bg-primary/10 text-primary hover:bg-primary/10 hover:text-primary'
                : 'text-muted-foreground'
            }`}
            onClick={() => onChange(value)}
          >
            {label}
          </Button>
        ))}
      </div>
      {tab === 'detail' && writable && (
        <Button size="sm" disabled={!selectedCount || busy} onClick={() => void onPublish()}>
          批量发布
        </Button>
      )}
    </div>
  );
}
