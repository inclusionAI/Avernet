import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';

export function TodoDetailItem({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="text-xs leading-5">
      <span className="text-muted-foreground">{label}:</span>{' '}
      <span className="break-words text-foreground/85">{value || '—'}</span>
    </div>
  );
}

export function TodoDetailTab({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <Button
      variant="ghost"
      role="tab"
      aria-selected={active}
      className={cn(
        'relative h-auto rounded-none border-0 bg-transparent px-4 py-2.5 text-sm font-medium outline-none hover:bg-transparent',
        active
          ? 'text-foreground after:absolute after:inset-x-3 after:-bottom-px after:h-0.5 after:rounded-full after:bg-primary'
          : 'text-muted-foreground hover:text-foreground',
      )}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}
