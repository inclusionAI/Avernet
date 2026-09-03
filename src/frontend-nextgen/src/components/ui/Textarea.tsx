import { cn } from '@/utils/cn';
import React from 'react';

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /** 多行输入尺寸。 */
  size?: 'sm' | 'md' | 'lg';
  /** 多行输入状态。 */
  variant?: 'default' | 'error' | 'success';
}

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, size = 'md', variant = 'default', ...props }, ref) => (
    <textarea
      ref={ref}
      className={cn(
        'min-h-20 w-full resize-y rounded-md border bg-background px-3 text-xs text-foreground outline-none transition-colors placeholder:text-muted-foreground',
        'focus-visible:border-ring focus-visible:ring-2 focus-visible:ring-ring/40 disabled:cursor-not-allowed disabled:opacity-50',
        size === 'sm' && 'py-1.5 text-xs',
        size === 'md' && 'py-2',
        size === 'lg' && 'py-3 text-base',
        variant === 'default' && 'border-input',
        variant === 'error' && 'border-destructive focus-visible:ring-destructive',
        variant === 'success' && 'border-success focus-visible:ring-success',
        className,
      )}
      {...props}
    />
  ),
);
Textarea.displayName = 'Textarea';

export { Textarea };
