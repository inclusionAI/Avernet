import { cn } from '@/utils/cn';
import { Inbox } from 'lucide-react';
import React from 'react';

interface EmptyProps {
  title?: string;
  description?: string;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
  className?: string;
}

export function Empty({ title = '暂无数据', description, icon, action, compact, className }: EmptyProps) {
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-6 text-center',
        compact ? 'py-8' : 'min-h-64 py-14',
        className,
      )}
    >
      <div className="mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10 text-primary">
        {icon ?? <Inbox className="h-5 w-5" aria-hidden />}
      </div>
      <p className="m-0 text-sm font-medium text-foreground">{title}</p>
      {description && <p className="mt-1 max-w-sm text-sm leading-6 text-muted-foreground">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}
