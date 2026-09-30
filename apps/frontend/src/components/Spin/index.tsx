import { cn } from '@/utils/utils';
import { Loader2 } from 'lucide-react';
import React from 'react';

export interface SpinProps {
  label?: string;
  className?: string;
}

export function Spin({ label = '加载中...', className }: SpinProps) {
  return (
    <span
      role="status"
      className={cn(
        'inline-flex items-center justify-center gap-2 text-sm text-slate-400',
        className,
      )}
    >
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      <span>{label}</span>
    </span>
  );
}

export default Spin;
