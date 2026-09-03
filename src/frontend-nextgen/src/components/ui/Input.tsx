import { cn } from '@/utils/cn';
import React from 'react';

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input
      ref={ref}
      className={cn(
        'h-8 w-full rounded-lg border border-input bg-background px-3 text-xs text-foreground outline-none placeholder:text-muted-foreground focus-visible:border-brand focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-brand/20 focus-visible:ring-offset-0 disabled:cursor-not-allowed disabled:opacity-50',
        className,
      )}
      {...props}
    />
  ),
);
Input.displayName = 'Input';
