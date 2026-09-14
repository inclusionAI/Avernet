import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import { MESSAGE_VIEW_SCOPE_OPTIONS } from '@/domain/collaboration/messageViewScope';
import type { MessageViewScope } from '@/domain/collaboration/types';
import { cn } from '@/utils/cn';
import { Check, ChevronDown } from 'lucide-react';
import { useMemo, useState } from 'react';

export interface MessageViewScopeFieldProps {
  value: MessageViewScope;
  onChange: (scope: MessageViewScope) => void;
  disabled?: boolean;
  label?: string;
}

/** 消息视角下拉选择器，与发起协作面板中的 Bot 选择器保持一致。 */
export function MessageViewScopeField({ value, onChange, disabled, label = '消息视角' }: MessageViewScopeFieldProps) {
  const [open, setOpen] = useState(false);
  const selected = useMemo(() => MESSAGE_VIEW_SCOPE_OPTIONS.find((option) => option.value === value), [value]);

  return (
    <div>
      <label className="mb-1.5 block text-xs font-semibold text-foreground">{label}</label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="secondary"
            aria-label={label}
            disabled={disabled}
            className="h-9 w-full justify-between rounded-md border-border bg-background px-3 text-left"
          >
            <span className="min-w-0 flex-1 truncate text-xs text-foreground">{selected?.label ?? '请选择'}</span>
            <ChevronDown
              className={cn('h-4 w-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
              aria-hidden
            />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-1">
          <div className="app-scrollbar max-h-60 space-y-0.5 overflow-y-auto">
            {MESSAGE_VIEW_SCOPE_OPTIONS.map((option) => {
              const optionSelected = option.value === value;
              return (
                <Button
                  key={option.value}
                  type="button"
                  role="option"
                  aria-selected={optionSelected}
                  variant="ghost"
                  className={cn(
                    'h-auto w-full justify-start gap-2 rounded-md border-0 px-2 py-2 text-left text-xs',
                    optionSelected ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-muted',
                  )}
                  onClick={() => {
                    onChange(option.value);
                    setOpen(false);
                  }}
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-xs">{option.label}</span>
                    <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">
                      {option.description}
                    </span>
                  </span>
                  {optionSelected && <Check className="ml-auto h-3.5 w-3.5" aria-hidden />}
                </Button>
              );
            })}
          </div>
        </PopoverContent>
      </Popover>
    </div>
  );
}
