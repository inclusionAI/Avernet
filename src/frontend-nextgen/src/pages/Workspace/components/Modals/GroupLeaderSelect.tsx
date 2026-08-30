import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { cn } from '@/utils/cn';
import { Check, ChevronDown } from 'lucide-react';
import { useMemo, useState } from 'react';

export interface GroupLeaderOption {
  id: string;
  name: string;
  current?: boolean;
}

export interface GroupLeaderSelectProps {
  id: string;
  label: string;
  value: string;
  options: GroupLeaderOption[];
  placeholder?: string;
  onChange: (id: string) => void;
}

/** 群主 Bot / Manager Bot 选择器。候选仅来自当前身份与已选成员 Bot。 */
export function GroupLeaderSelect({
  id,
  label,
  value,
  options,
  placeholder = '请选择',
  onChange,
}: GroupLeaderSelectProps) {
  const [open, setOpen] = useState(false);
  const selected = useMemo(() => options.find((option) => option.id === value), [options, value]);

  return (
    <div>
      <label className="mb-2 block text-[13px] font-bold text-[var(--color-muted)]" htmlFor={id}>
        {label}
      </label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            id={id}
            type="button"
            variant="secondary"
            aria-label={label}
            className="h-10 w-full justify-between rounded-xl border-[var(--color-border)] bg-white px-3 text-left"
          >
            <span className={cn('flex min-w-0 flex-1 items-center gap-2', !selected && 'text-[var(--color-muted)]')}>
              {selected ? (
                <>
                  <span className="max-w-44 truncate text-sm text-[var(--color-fg)]">{selected.name}</span>
                  {selected.current && (
                    <span className="rounded-full bg-[var(--color-primary-soft)] px-1.5 py-0.5 text-[10px] text-[var(--color-primary)]">
                      当前
                    </span>
                  )}
                </>
              ) : (
                <span className="truncate text-sm">{placeholder}</span>
              )}
            </span>
            <ChevronDown
              className={cn('h-4 w-4 shrink-0 text-[var(--color-muted)] transition-transform', open && 'rotate-180')}
              aria-hidden
            />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-1">
          {options.length === 0 ? (
            <div className="px-3 py-3 text-center text-xs text-[var(--color-muted)]">请先选择成员 Bot</div>
          ) : (
            <div className="app-scrollbar max-h-60 space-y-0.5 overflow-y-auto">
              {options.map((option) => {
                const optionSelected = option.id === value;
                return (
                  <Button
                    key={option.id}
                    type="button"
                    role="option"
                    aria-selected={optionSelected}
                    variant="ghost"
                    className={cn(
                      'h-auto w-full justify-start gap-2 rounded-md border-0 px-2 py-2 text-left',
                      optionSelected
                        ? 'bg-[var(--color-primary-soft)] text-[var(--color-primary)]'
                        : 'text-[var(--color-fg)] hover:bg-[var(--color-panel-muted)]',
                    )}
                    onClick={() => {
                      onChange(option.id);
                      setOpen(false);
                    }}
                  >
                    <span className="max-w-40 truncate">{option.name}</span>
                    {option.current && (
                      <span className="rounded-full bg-[var(--color-primary-soft)] px-1.5 py-0.5 text-[10px] text-[var(--color-primary)]">
                        当前
                      </span>
                    )}
                    {optionSelected && <Check className="ml-auto h-3.5 w-3.5" aria-hidden />}
                  </Button>
                );
              })}
            </div>
          )}
        </PopoverContent>
      </Popover>
    </div>
  );
}

export default GroupLeaderSelect;
