import { cn } from '@/utils/cn';
import React from 'react';

export interface CheckboxProps extends React.InputHTMLAttributes<HTMLInputElement> {
  /** 勾选状态变化回调（受控用 checked + onCheckedChange）。 */
  onCheckedChange?: (checked: boolean) => void;
}

/** Checkbox：勾选框，受控/非受控两用；复用设计 token，不引入新依赖。 */
const Checkbox = React.forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, onChange, onCheckedChange, ...props }, ref) => (
    <input
      ref={ref}
      type="checkbox"
      className={cn(
        'size-4 shrink-0 cursor-pointer rounded-[5px] border border-input bg-background accent-[var(--color-primary)] transition-colors',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]/30',
        'disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      onChange={(event) => {
        onChange?.(event);
        onCheckedChange?.(event.target.checked);
      }}
      {...props}
    />
  ),
);
Checkbox.displayName = 'Checkbox';
export { Checkbox };
