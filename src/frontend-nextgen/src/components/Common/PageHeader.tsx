import React from 'react';

interface PageHeaderProps {
  title: string;
  description?: string;
  actions?: React.ReactNode;
  eyebrow?: string;
}

export function PageHeader({ title, description, actions, eyebrow }: PageHeaderProps) {
  return (
    <header className="flex flex-wrap items-start justify-between gap-4">
      <div>
        {eyebrow && <p className="mb-1 text-xs font-medium text-[var(--color-primary)]">{eyebrow}</p>}
        <h1 className="m-0 text-2xl font-semibold tracking-tight text-[var(--color-fg)]">{title}</h1>
        {description && <p className="mt-2 max-w-3xl text-sm leading-6 text-[var(--color-muted)]">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}
