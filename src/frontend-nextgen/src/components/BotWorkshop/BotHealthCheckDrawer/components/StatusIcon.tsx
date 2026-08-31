import { AlertTriangle, CheckCircle2, HelpCircle, Loader2, XCircle } from 'lucide-react';

type StatusLike =
  | 'passed'
  | 'pass'
  | 'warning'
  | 'warn'
  | 'error'
  | 'fail'
  | 'failed'
  | 'scanning'
  | 'running'
  | 'pending'
  | string
  | null
  | undefined;

function normalizeStatus(status: StatusLike): Exclude<StatusLike, 'pass' | 'warn' | 'fail' | 'failed'> {
  const value = (status ?? '').toString().toLowerCase();
  if (['pass', 'passed'].includes(value)) return 'passed';
  if (['warn', 'warning'].includes(value)) return 'warning';
  if (['fail', 'failed', 'error'].includes(value)) return 'error';
  if (['scanning', 'running', 'patching', 'pending'].includes(value)) return 'scanning';
  return 'unknown';
}

interface StatusIconProps {
  status: StatusLike;
  className?: string;
}

export function StatusIcon({ status, className }: StatusIconProps) {
  const normalized = normalizeStatus(status);
  if (normalized === 'passed')
    return <CheckCircle2 className={`size-4 text-[var(--color-success)] ${className ?? ''}`} aria-hidden />;
  if (normalized === 'warning')
    return <AlertTriangle className={`size-4 text-[var(--color-warning)] ${className ?? ''}`} aria-hidden />;
  if (normalized === 'error')
    return <XCircle className={`size-4 text-[var(--color-error)] ${className ?? ''}`} aria-hidden />;
  if (normalized === 'scanning')
    return <Loader2 className={`size-4 animate-spin text-[var(--color-primary)] ${className ?? ''}`} aria-hidden />;
  return <HelpCircle className={`size-4 text-[var(--color-muted)] ${className ?? ''}`} aria-hidden />;
}
