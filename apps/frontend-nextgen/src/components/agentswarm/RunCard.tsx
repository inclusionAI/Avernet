import { RunStatusBadge } from './RunStatusBadge';
import { Button } from '@/components/ui/Button';
import { cn } from '@/utils/cn';
import type { RunSummaryDTO } from '@/services/agentswarm/types';

/**
 * The gateway reports epoch **milliseconds** (`started_at: 1794365805609`), not
 * seconds. Treating them as seconds yields a date far outside the valid range.
 * Accept either so an older payload cannot render as an invalid date.
 */
function formatTime(value: number | null | undefined): string | null {
  if (!value) return null;
  const millis = value > 10_000_000_000 ? value : value * 1000;
  const date = new Date(millis);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleString('zh-CN', { hour12: false });
}

export interface RunCardProps {
  run: RunSummaryDTO;
  /**
   * The run's goal, when known. The list response carries no goal — only the
   * detail does — so this is filled from the submit that created the run or from
   * a detail read, and omitted rather than invented.
   */
  goal?: string;
  selected?: boolean;
  onSelect: (runId: string) => void;
}

/** One run in the list: what the gateway reported, plus a goal when known. */
export function RunCard({ run, goal, selected = false, onSelect }: RunCardProps) {
  const started = formatTime(run.started_at);
  const ended = formatTime(run.ended_at);
  const title = goal?.trim();
  return (
    <Button
      type="button"
      variant="outline"
      onClick={() => onSelect(run.run_id)}
      className={cn(
        'flex h-auto w-full flex-col items-stretch justify-start gap-1 rounded-md bg-card p-3 text-left font-normal',
        selected && 'border-primary bg-primary/5',
      )}
    >
      <div className="flex items-start justify-between gap-2">
        {/* The goal is what tells two runs apart; the id is the identifier. */}
        <span className="min-w-0 flex-1 break-words text-xs font-medium text-foreground">
          {title || <span className="font-normal text-muted-foreground">未命名运行</span>}
        </span>
        <RunStatusBadge status={run.status} />
      </div>
      <span className="truncate font-mono text-[11px] text-muted-foreground">{run.run_id}</span>
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-muted-foreground">
        {started ? <span>开始 {started}</span> : null}
        {ended ? <span>结束 {ended}</span> : null}
        {run.termination_reason ? <span>原因 {run.termination_reason}</span> : null}
      </div>
      {run.error ? <p className="line-clamp-2 text-xs text-destructive">{run.error}</p> : null}
    </Button>
  );
}