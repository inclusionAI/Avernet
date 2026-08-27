import { cn } from '@/utils/cn';
import { LoaderCircle } from 'lucide-react';

export function Spin({ tip, className }: { tip?: string; className?: string }) {
  return (
    <div className={cn('flex flex-col items-center justify-center gap-2 py-8 text-muted-foreground', className)}>
      <LoaderCircle className="h-6 w-6 animate-spin text-primary" aria-hidden />
      {tip && <span className="text-sm">{tip}</span>}
    </div>
  );
}
