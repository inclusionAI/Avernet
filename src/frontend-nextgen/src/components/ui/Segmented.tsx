import { cn } from '@/utils/cn';
import type { ReactNode } from 'react';
import { Button } from './Button';
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from './Tooltip';

interface Option<T extends string> {
  value: T;
  label: string;
  disabledReason?: string;
  icon?: ReactNode;
}
interface SegmentedProps<T extends string> {
  value: T;
  options: Option<T>[];
  onChange: (value: T) => void;
  className?: string;
}

/** 分段控件：禁用项的原因通过统一 Tooltip 呈现，不使用 title；支持 icon 项与等宽不换行。
 *  选中态用 bg-background/text-primary（修复暗色——旧值 bg-white/text-color-primary 在暗色下错配）。 */
export function Segmented<T extends string>({ value, options, onChange, className }: SegmentedProps<T>) {
  return (
    <TooltipProvider>
      <div className={cn('flex items-center rounded-lg bg-muted p-0.5', className)}>
        {options.map((option) => {
          const button = (
            <Button
              key={option.value}
              variant="ghost"
              size="sm"
              disabled={!!option.disabledReason}
              onClick={() => onChange(option.value)}
              className={cn(
                'flex-1 whitespace-nowrap border-0',
                value === option.value && 'bg-background text-primary shadow-sm hover:bg-background',
              )}
            >
              {option.icon ? <span className="flex items-center">{option.icon}</span> : null}
              {option.label}
            </Button>
          );
          if (!option.disabledReason) return button;
          return (
            <Tooltip key={option.value}>
              <TooltipTrigger asChild>{button}</TooltipTrigger>
              <TooltipContent>{option.disabledReason}</TooltipContent>
            </Tooltip>
          );
        })}
      </div>
    </TooltipProvider>
  );
}
