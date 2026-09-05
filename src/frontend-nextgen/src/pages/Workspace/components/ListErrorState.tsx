import { Button } from '@/components/ui';
import { AlertCircle } from 'lucide-react';

interface ListErrorStateProps {
  message: string;
  onRetry?: () => void;
  className?: string;
}

export function ListErrorState({ message, onRetry, className }: ListErrorStateProps) {
  return (
    <div
      role="alert"
      className={`flex items-center justify-between gap-3 border-y border-destructive/30 bg-destructive/5 px-3 py-3 text-xs ${className ?? ''}`}
    >
      <div className="flex min-w-0 items-center gap-2 text-destructive">
        <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
        <span className="truncate">{message}</span>
      </div>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} className="h-7 shrink-0 px-2 text-xs">
          重试
        </Button>
      )}
    </div>
  );
}
