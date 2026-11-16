import { IconButton } from '@/components/ui';
import { cn } from '@/utils/cn';
import type { ReactNode } from 'react';

export function GroupHeaderIcon({
  label,
  icon,
  active,
  onClick,
  disabled,
  className,
}: {
  label: string;
  icon: ReactNode;
  active: boolean;
  onClick: () => void;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <IconButton
      label={label}
      icon={icon}
      size="sm"
      disabled={disabled}
      aria-pressed={active}
      data-workspace-panel-trigger
      onClick={onClick}
      className={cn('rounded-md text-muted-foreground', active && 'bg-selected text-foreground', className)}
    />
  );
}
