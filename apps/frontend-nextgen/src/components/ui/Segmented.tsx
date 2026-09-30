import { cn } from '@/utils/cn';
import { type KeyboardEvent, type ReactNode, useRef } from 'react';
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
  activeOptionClassName?: string;
  inactiveOptionClassName?: string;
  'aria-label'?: string;
  'aria-labelledby'?: string;
}

/** 分段控件：禁用项的原因通过统一 Tooltip 呈现，不使用 title；支持 icon 项与等宽不换行。
 *  默认选中态用 bg-background/text-primary；业务场景可通过 activeOptionClassName
 *  强化选中态的品牌表达，未选中态通过 inactiveOptionClassName 定制。 */
export function Segmented<T extends string>({
  value,
  options,
  onChange,
  className,
  activeOptionClassName,
  inactiveOptionClassName,
  'aria-label': ariaLabel,
  'aria-labelledby': ariaLabelledBy,
}: SegmentedProps<T>) {
  const buttonRefs = useRef<Array<HTMLButtonElement | null>>([]);

  const selectAndFocus = (index: number) => {
    const option = options[index];
    if (!option || option.disabledReason) return;
    onChange(option.value);
    buttonRefs.current[index]?.focus();
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLButtonElement>, index: number) => {
    if (!['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();

    if (event.key === 'Home' || event.key === 'End') {
      const enabledIndexes = options
        .map((option, optionIndex) => (option.disabledReason ? -1 : optionIndex))
        .filter((optionIndex) => optionIndex >= 0);
      const targetIndex = event.key === 'Home' ? enabledIndexes[0] : enabledIndexes.at(-1);
      if (targetIndex !== undefined) selectAndFocus(targetIndex);
      return;
    }

    const direction = event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? -1 : 1;
    for (let offset = 1; offset <= options.length; offset += 1) {
      const targetIndex = (index + direction * offset + options.length) % options.length;
      if (!options[targetIndex]?.disabledReason) {
        selectAndFocus(targetIndex);
        return;
      }
    }
  };

  return (
    <TooltipProvider>
      <div
        role="group"
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        className={cn('flex items-center rounded-lg bg-muted p-0.5', className)}
      >
        {options.map((option, index) => {
          const button = (
            <Button
              key={option.value}
              ref={(node) => {
                buttonRefs.current[index] = node;
              }}
              variant="ghost"
              size="sm"
              disabled={!!option.disabledReason}
              aria-pressed={value === option.value}
              onClick={() => onChange(option.value)}
              onKeyDown={(event) => handleKeyDown(event, index)}
              className={cn(
                'flex-1 whitespace-nowrap border-0',
                value !== option.value && inactiveOptionClassName,
                value === option.value && 'bg-background text-primary shadow-sm hover:bg-background',
                value === option.value && activeOptionClassName,
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
