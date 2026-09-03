import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import { useRef } from 'react';

interface ChoiceOption<T extends string> {
  value: T;
  label: string;
  description: string;
}

interface ChoiceGroupProps<T extends string> {
  value: T;
  options: Array<ChoiceOption<T>>;
  ariaLabel: string;
  onChange: (value: T) => void;
  className?: string;
}

export function ChoiceGroup<T extends string>({ value, options, ariaLabel, onChange, className }: ChoiceGroupProps<T>) {
  const optionRefs = useRef<Record<string, HTMLButtonElement | null>>({});

  const focusOption = (index: number) => {
    const nextIndex = (index + options.length) % options.length;
    const nextOption = options[nextIndex];
    if (!nextOption) return;
    optionRefs.current[nextOption.value]?.focus();
    onChange(nextOption.value);
  };

  return (
    <div role="radiogroup" aria-label={ariaLabel} className={cn('grid gap-2', className)}>
      {options.map((option, index) => {
        const selected = value === option.value;
        return (
          <Button
            key={option.value}
            ref={(element) => {
              optionRefs.current[option.value] = element;
            }}
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            variant={selected ? 'default' : 'secondary'}
            className={cn('h-auto min-h-20 flex-col items-start px-3 py-3 text-left', !selected && 'text-foreground')}
            onClick={() => onChange(option.value)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowRight' || event.key === 'ArrowDown') {
                event.preventDefault();
                focusOption(index + 1);
              } else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') {
                event.preventDefault();
                focusOption(index - 1);
              } else if (event.key === 'Home') {
                event.preventDefault();
                focusOption(0);
              } else if (event.key === 'End') {
                event.preventDefault();
                focusOption(options.length - 1);
              }
            }}
          >
            <span>{option.label}</span>
            <span className={cn('text-xs', selected ? 'text-primary-foreground/80' : 'text-muted-foreground')}>
              {option.description}
            </span>
          </Button>
        );
      })}
    </div>
  );
}
