import { cn } from '@/utils/cn';
import { Inbox } from 'lucide-react';
import React from 'react';

interface EmptyProps {
  title?: string;
  description?: React.ReactNode;
  icon?: React.ReactNode;
  action?: React.ReactNode;
  compact?: boolean;
  /**
   * `sm` is for narrow list regions (for example the conversation sidebar).
   * `md` keeps the original full empty-state scale for panels and dialogs.
   */
  size?: 'sm' | 'md';
  className?: string;
}

const EMPTY_SIZE = {
  sm: {
    iconBox: 'mb-2 flex h-8 w-8 items-center justify-center rounded-lg bg-primary/10 text-primary',
    icon: 'h-4 w-4',
    title: 'text-[13px] font-medium text-foreground',
    description: 'max-w-xs text-[11px] leading-4 text-muted-foreground',
    action: 'mt-2',
  },
  md: {
    iconBox: 'mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-primary/10 text-primary',
    icon: 'h-5 w-5',
    title: 'text-sm font-medium text-foreground',
    description: 'max-w-sm text-sm leading-6 text-muted-foreground',
    action: 'mt-4',
  },
} as const;

export function Empty({
  title = '暂无数据',
  description,
  icon,
  action,
  compact,
  size = 'md',
  className,
}: EmptyProps) {
  const styles = EMPTY_SIZE[size];
  return (
    <div
      className={cn(
        'flex flex-col items-center justify-center px-6 text-center',
        compact ? (size === 'sm' ? 'py-4' : 'py-8') : 'min-h-64 py-14',
        className,
      )}
    >
      <div className={styles.iconBox}>
        {icon ? (
          <span className={cn('flex items-center justify-center', styles.icon, '[&>svg]:h-full [&>svg]:w-full')}>
            {icon}
          </span>
        ) : (
          <Inbox className={styles.icon} aria-hidden />
        )}
      </div>
      <p className={cn('m-0', styles.title)}>{title}</p>
      {description && <p className={cn('mt-1', styles.description)}>{description}</p>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  );
}
