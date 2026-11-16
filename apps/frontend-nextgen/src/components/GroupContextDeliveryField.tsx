import { Button, Popover, PopoverContent, PopoverTrigger } from '@/components/ui';
import type { GroupContextDelivery } from '@/domain/collaboration/types';
import { cn } from '@/utils/cn';
import { Check, ChevronDown } from 'lucide-react';
import { useState } from 'react';

const OPTIONS = [
  { value: 'send', label: 'Send', description: '发送群上下文，触发群主 Bot 回复' },
  { value: 'inject', label: 'Inject', description: '仅注入群上下文，不触发群主 Bot 回复' },
] as const;

interface GroupContextDeliveryFieldProps {
  value: GroupContextDelivery;
  onChange: (value: GroupContextDelivery) => void;
}

export function GroupContextDeliveryField({ value, onChange }: GroupContextDeliveryFieldProps) {
  const [open, setOpen] = useState(false);
  const selected = OPTIONS.find((option) => option.value === value)!;
  const label = 'Group context 投递方式';

  return (
    <div>
      <label className="mb-1.5 block text-xs font-semibold text-foreground">{label}</label>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            type="button"
            variant="secondary"
            aria-label={label}
            className="h-9 w-full justify-between rounded-md border-border bg-background px-3 text-left"
          >
            <span className="min-w-0 flex-1 truncate text-xs text-foreground">{selected.label}</span>
            <ChevronDown
              className={cn('h-4 w-4 shrink-0 text-muted-foreground transition-transform', open && 'rotate-180')}
              aria-hidden
            />
          </Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-[var(--radix-popover-trigger-width)] p-1">
          <div className="space-y-0.5">
            {OPTIONS.map((option) => (
              <Button
                key={option.value}
                type="button"
                role="option"
                aria-selected={option.value === value}
                variant="ghost"
                className={cn(
                  'h-auto w-full justify-start gap-2 rounded-md border-0 px-2 py-2 text-left text-xs',
                  option.value === value ? 'bg-primary/10 text-primary' : 'text-foreground hover:bg-muted',
                )}
                onClick={() => {
                  onChange(option.value);
                  setOpen(false);
                }}
              >
                <span className="min-w-0 flex-1">
                  <span className="block truncate text-xs">{option.label}</span>
                  <span className="mt-0.5 block text-[11px] leading-4 text-muted-foreground">{option.description}</span>
                </span>
                {option.value === value && <Check className="ml-auto h-3.5 w-3.5" aria-hidden />}
              </Button>
            ))}
          </div>
        </PopoverContent>
      </Popover>
      <p className="mt-1.5 text-[11px] leading-4 text-muted-foreground">{selected.description}</p>
    </div>
  );
}
