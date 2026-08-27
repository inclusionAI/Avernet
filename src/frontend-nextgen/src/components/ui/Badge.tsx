import { cn } from '@/utils/cn';
import React from 'react';

type Tone = 'neutral' | 'primary' | 'success' | 'warning' | 'error' | 'outline';
const tones: Record<Tone, string> = {
  // D3: token 迁到 shadcn 软色模式 (bg-X/10 text-X)，保历史软徽章观感；沿用 `tone` API 零 callsite 改动。
  neutral: 'bg-muted text-muted-foreground',
  primary: 'bg-primary/10 text-primary',
  success: 'bg-success/10 text-success',
  warning: 'bg-warning/10 text-warning',
  error: 'bg-destructive/10 text-destructive',
  outline: 'border border-border text-foreground',
};

export function Badge({
  tone = 'neutral',
  className,
  ...props
}: React.HTMLAttributes<HTMLSpanElement> & { tone?: Tone }) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium',
        tones[tone],
        className,
      )}
      {...props}
    />
  );
}
