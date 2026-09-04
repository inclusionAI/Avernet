import { DIMENSIONS_INFO } from '@/components/BotWorkshop/BotHealthCheckDrawer/constants';
import { Button } from '@/components/ui/Button';
import type { BotHealthDimensionKey } from '@/domain/botHealthCheck';
import { cn } from '@/utils/cn';

interface DimensionTabsProps {
  activeKey: BotHealthDimensionKey;
  dimensions: BotHealthDimensionKey[];
  onChange: (key: BotHealthDimensionKey) => void;
}

export function DimensionTabs({ activeKey, dimensions, onChange }: DimensionTabsProps) {
  const allowedDimensions = new Set(dimensions);

  return (
    <div
      className="flex flex-wrap gap-2 border-b border-[var(--color-border)] px-6 py-3"
      role="tablist"
      aria-label="体检维度"
    >
      {DIMENSIONS_INFO.filter((dim) => allowedDimensions.has(dim.dimensionKey)).map((dim) => {
        const active = activeKey === dim.dimensionKey;
        return (
          <Button
            key={dim.key}
            variant="ghost"
            size="sm"
            role="tab"
            aria-selected={active}
            onClick={() => onChange(dim.dimensionKey)}
            className={cn(
              'rounded-full px-4 py-1.5 text-sm font-medium',
              active
                ? 'bg-[var(--color-primary-soft)] text-[var(--color-primary)] hover:bg-[var(--color-primary-soft)]'
                : 'text-[var(--color-muted)] hover:bg-[var(--color-panel-muted)] hover:text-[var(--color-fg)]',
            )}
          >
            {dim.name}
          </Button>
        );
      })}
    </div>
  );
}
