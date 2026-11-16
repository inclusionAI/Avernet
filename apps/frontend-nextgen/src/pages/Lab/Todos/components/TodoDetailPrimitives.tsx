import { Button } from '@/components/ui';
import { cn } from '@/utils/cn';

export function TodoDetailItem({ label, value, icon }: { label: string; value: React.ReactNode; icon: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2">
      <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-lg bg-muted text-muted-foreground">
        {icon}
      </span>
      <div className="min-w-0">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-0.5 break-words text-xs font-medium text-foreground">{value || '—'}</div>
      </div>
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
        '-mb-px h-auto rounded-none border-x-0 border-t-0 border-b-2 bg-transparent px-1 pb-4 text-xl font-semibold outline-none hover:bg-transparent',
        active ? 'border-foreground text-foreground' : 'border-transparent text-muted-foreground hover:text-foreground',
      )}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}
