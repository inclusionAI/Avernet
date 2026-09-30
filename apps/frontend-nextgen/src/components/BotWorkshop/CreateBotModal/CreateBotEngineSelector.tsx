import type { BotEngineOption } from '@/capabilities';
import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import type React from 'react';

interface CreateBotEngineSelectorProps {
  options: BotEngineOption[];
  value: string;
  onChange: (value: string) => void;
  renderPanel?: (option: BotEngineOption) => React.ReactNode;
  idPrefix?: string;
}

export function CreateBotEngineSelector({
  options,
  value,
  onChange,
  renderPanel,
  idPrefix = 'create-bot-engine',
}: CreateBotEngineSelectorProps) {
  return (
    <div className="flex flex-col gap-2">
      {options.map((option, index) => {
        const selected = value === option.value;
        const hasPanel = Boolean(option.createPanel);
        const panelId = `${idPrefix}-${index}-panel`;
        const titleId = `${idPrefix}-${index}-title`;
        const descriptionId = `${idPrefix}-${index}-description`;
        const Icon = option.icon;
        const title = option.cardLabel ?? option.label;

        return (
          <div
            key={option.value}
            className={cn(
              'overflow-visible rounded-xl border transition-colors',
              selected
                ? 'border-primary/30 bg-primary/[0.04] hover:border-primary/40 hover:bg-primary/[0.06]'
                : 'border-border bg-card hover:border-primary/40 hover:bg-muted/40',
            )}
          >
            <Button
              type="button"
              variant="secondary"
              aria-pressed={selected}
              aria-expanded={hasPanel ? selected : undefined}
              aria-controls={hasPanel && selected ? panelId : undefined}
              aria-labelledby={titleId}
              aria-describedby={option.description ? descriptionId : undefined}
              onClick={() => onChange(option.value)}
              className={cn(
                'h-auto w-full justify-start gap-3 rounded-xl border-transparent bg-transparent p-2.5 text-left hover:bg-transparent',
                hasPanel && selected ? 'rounded-b-none' : undefined,
              )}
            >
              {Icon ? (
                <span
                  aria-hidden
                  className={cn(
                    'flex size-8 shrink-0 items-center justify-center rounded-lg',
                    selected ? 'bg-primary/10 text-primary' : 'bg-muted text-muted-foreground',
                  )}
                >
                  <Icon className="size-4" />
                </span>
              ) : null}
              <span className="min-w-0 flex-1 space-y-0.5">
                <span
                  id={titleId}
                  className={cn(
                    'block truncate text-[13px] font-semibold',
                    selected ? 'text-primary' : 'text-foreground',
                  )}
                >
                  {title}
                </span>
                {option.description ? (
                  <span id={descriptionId} className="block text-xs font-normal leading-5 text-muted-foreground">
                    {option.description}
                  </span>
                ) : null}
              </span>
              <span
                aria-hidden
                className={cn(
                  'flex size-4 shrink-0 items-center justify-center rounded-full border-2',
                  selected ? 'border-primary bg-primary' : 'border-input bg-background',
                )}
              >
                {selected ? <span className="size-1.5 rounded-full bg-primary-foreground" /> : null}
              </span>
            </Button>

            {hasPanel && selected ? (
              <div id={panelId} className="rounded-b-xl border-t border-border/70 bg-background px-3 py-3">
                {renderPanel?.(option)}
              </div>
            ) : null}
          </div>
        );
      })}
    </div>
  );
}
