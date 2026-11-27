import { Badge } from '@/components/ui/Badge';
import { runStatusLabel, runStatusTone } from '@/services/agentswarm/types';

/**
 * Renders the gateway's run state.
 *
 * The tone comes from the shared `runStatusTone` map in `services/agentswarm/types`
 * rather than a local table: this component previously carried its own copy that
 * disagreed with the rest of the product (it called TIMED_OUT a warning and a
 * guard kill an error), so the same run showed different colours on different
 * surfaces.
 *
 * An unrecognized state keeps the gateway's own string and a neutral tone rather
 * than being coerced into one of the seven — the surface must never claim a
 * state the server did not report.
 */
export function RunStatusBadge({ status }: { status: string | null | undefined }) {
  return <Badge tone={runStatusTone(status)}>{runStatusLabel(status)}</Badge>;
}