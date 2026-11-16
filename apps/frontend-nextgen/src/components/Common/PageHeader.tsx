import { Button } from '@/components/ui/Button';
import { ArrowLeft } from 'lucide-react';
import React from 'react';

interface PageHeaderProps {
  title: string;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  eyebrow?: string;
  onBack?: () => void;
  compact?: boolean;
}

export function PageHeader({ title, description, actions, eyebrow, onBack, compact = false }: PageHeaderProps) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div className="flex items-start gap-2">
        {onBack && (
          <Button variant="ghost" size="icon" onClick={onBack} aria-label="返回" className="-ml-2 mt-0.5 shrink-0">
            <ArrowLeft className="size-5" aria-hidden />
          </Button>
        )}
        <div>
          {eyebrow && <p className="mb-1 text-xs font-medium text-primary">{eyebrow}</p>}
          <h1
            className={
              compact
                ? 'm-0 text-[20px] font-semibold leading-[30px] tracking-tight text-foreground'
                : 'm-0 text-2xl font-semibold tracking-tight text-foreground'
            }
          >
            {title}
          </h1>
          {description && (
            <p
              className={
                compact
                  ? 'mt-1 max-w-3xl text-[13px] font-normal leading-[20px] text-muted-foreground'
                  : 'mt-2 max-w-3xl text-sm leading-6 text-muted-foreground'
              }
            >
              {description}
            </p>
          )}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}
