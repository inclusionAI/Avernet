import { Button } from '@/components/ui/Button';
import { ArrowLeft } from 'lucide-react';
import React from 'react';

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  eyebrow?: string;
  onBack?: () => void;
}

export function PageHeader({ title, description, actions, eyebrow, onBack }: PageHeaderProps) {
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
          <h1 className="m-0 text-2xl font-semibold tracking-tight text-foreground">{title}</h1>
          {description && <p className="mt-2 max-w-3xl text-sm leading-6 text-muted-foreground">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}
