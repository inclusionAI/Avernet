import { Button, IconButton } from '@/components/ui';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/Popover';
import { cn } from '@/utils/cn';
import { Check, ListFilter } from 'lucide-react';
import { useState } from 'react';

export type SessionScope = 'all' | 'favorite';

interface SessionScopeFilterProps {
  value: SessionScope;
  onChange: (value: SessionScope) => void;
  allCount?: number;
  favoriteCount?: number;
  /** 紧凑形态：按钮与图标调小，用于协作群条目行内按钮组。 */
  compact?: boolean;
}

const SCOPE_OPTIONS: Array<{ value: SessionScope; label: string }> = [
  { value: 'all', label: '全部会话' },
  { value: 'favorite', label: '已收藏会话' },
];

export function SessionScopeFilter({ value, onChange, allCount, favoriteCount, compact }: SessionScopeFilterProps) {
  const [open, setOpen] = useState(false);
  const activeLabel = value === 'favorite' ? '已收藏会话' : '全部会话';
  const iconSize = compact ? 'h-3.5 w-3.5' : 'h-4 w-4';

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <IconButton
          label={`会话范围：${activeLabel}`}
          size="sm"
          aria-pressed={value === 'favorite'}
          icon={
            <span className={cn('relative flex items-center justify-center', iconSize)}>
              <ListFilter className={iconSize} aria-hidden="true" />
              {value === 'favorite' && (
                <span className="absolute -right-0.5 -top-0.5 h-1.5 w-1.5 rounded-full bg-primary" aria-hidden="true" />
              )}
            </span>
          }
          className={cn(
            'rounded-md text-muted-foreground hover:bg-primary/10 hover:text-primary',
            compact && 'h-6 w-6',
            value === 'favorite' && 'bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary',
          )}
          onClick={(event) => event.stopPropagation()}
        />
      </PopoverTrigger>
      <PopoverContent align="end" className="w-48 p-1" onClick={(event) => event.stopPropagation()}>
        <p className="m-0 px-2 pb-1 pt-1 text-xs font-medium text-muted-foreground">会话范围</p>
        <div role="radiogroup" aria-label="会话范围">
          {SCOPE_OPTIONS.map((option) => {
            const selected = value === option.value;
            const count = option.value === 'all' ? allCount : favoriteCount;
            return (
              <Button
                key={option.value}
                variant="ghost"
                size="sm"
                role="radio"
                aria-checked={selected}
                onClick={(event) => {
                  event.stopPropagation();
                  onChange(option.value);
                  setOpen(false);
                }}
                className={cn(
                  'h-8 w-full justify-start gap-2 rounded-sm px-2 text-xs font-normal',
                  selected && 'bg-accent font-medium text-primary',
                )}
              >
                <span className="flex h-4 w-4 shrink-0 items-center justify-center">
                  {selected && <Check className="h-3.5 w-3.5" aria-hidden="true" />}
                </span>
                <span className="flex-1 text-left">{option.label}</span>
                <span className="tabular-nums text-muted-foreground">{count ?? '…'}</span>
              </Button>
            );
          })}
        </div>
      </PopoverContent>
    </Popover>
  );
}
