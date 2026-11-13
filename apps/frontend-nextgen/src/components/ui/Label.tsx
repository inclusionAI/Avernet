import { cn } from '@/utils/cn';
import React from 'react';

/**
 * Form label.
 *
 * A native `<label>` rather than a Radix primitive: the app already styles labels
 * this way, and the surface needs association plus disabled styling, not a new
 * dependency.
 */
export const Label = React.forwardRef<HTMLLabelElement, React.LabelHTMLAttributes<HTMLLabelElement>>(
  ({ className, ...props }, ref) => (
    <label
      ref={ref}
      className={cn('block text-xs font-medium leading-none text-foreground', className)}
      {...props}
    />
  ),
);
Label.displayName = 'Label';