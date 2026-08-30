import React from 'react';

interface StatCardProps {
  label: string;
  value: string;
  color: 'green' | 'red' | 'yellow' | 'blue' | 'gray';
}

const COLORS: Record<StatCardProps['color'], string> = {
  green: 'bg-[var(--color-success-soft)] border-[var(--color-success-soft)] text-[var(--color-success)]',
  red: 'bg-[var(--color-error-soft)] border-[var(--color-error-soft)] text-[var(--color-error)]',
  yellow: 'bg-[var(--color-warning-soft)] border-[var(--color-warning-soft)] text-[var(--color-warning)]',
  blue: 'bg-[var(--color-primary-soft)] border-[var(--color-primary-soft)] text-[var(--color-primary)]',
  gray: 'bg-[var(--color-panel-muted)] border-[var(--color-border)] text-[var(--color-muted)]',
};

export const TaskEscortStatCard: React.FC<StatCardProps> = ({ label, value, color }) => (
  <div className={`rounded-md border px-3 py-2 ${COLORS[color]}`}>
    <div className="text-[10px] opacity-70">{label}</div>
    <div className="mt-0.5 text-sm font-semibold">{value}</div>
  </div>
);
