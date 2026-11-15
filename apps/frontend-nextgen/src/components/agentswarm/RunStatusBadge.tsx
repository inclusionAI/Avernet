import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { isRunStatus, runStatusLabel, type RunStatus } from '@/services/agentswarm/types';

const TONES: Record<RunStatus, BadgeTone> = {
  RUNNING: 'primary',
  COMPLETED: 'success',
  FAILED: 'error',
  CANCELLED: 'neutral',
  TIMED_OUT: 'warning',
  GUARD_KILLED: 'error',
  STUCK: 'warning',
};

/**
 * Renders the gateway's run state.
 *
 * An unrecognized state keeps the gateway's own string and a neutral tone rather
 * than being coerced into one of the seven — the surface must never claim a
 * state the server did not report.
 */
export function RunStatusBadge({ status }: { status: string | null | undefined }) {
  const tone: BadgeTone = isRunStatus(status) ? TONES[status] : 'neutral';
  return <Badge tone={tone}>{runStatusLabel(status)}</Badge>;
}