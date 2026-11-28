/** Navigation to the runs owning open candidate reviews. */
import { Button } from '@/components/ui/Button';
import type { RsiReviewItem, RsiRun } from '@/services/rsi/service';

export function RsiReviewQueue({
  queue,
  onSelect,
}: {
  queue: { total: number; items: RsiReviewItem[] } | null;
  onSelect: (runId: string) => void;
}) {
  return queue && queue.total > 0 ? (
    <section className="space-y-2" aria-label="RSI评审队列">
      <h3 className="font-medium">待评审（{queue.total}）</h3>
      {queue.items.map((item) => (
        <Button
          key={`${item.run_id}:${item.candidate_id}`}
          variant="outline"
          onClick={() => onSelect(item.run_id)}
          className="h-auto w-full flex-col items-start gap-0 whitespace-normal rounded-md p-2 text-left text-xs"
        >
          <span className="block font-mono">
            {item.candidate_id.slice(0, 19)}… · {item.risk_tier}
          </span>
          <span className="text-muted-foreground">
            {item.strategy}@{item.strategy_version} · Run {item.run_id}
          </span>
        </Button>
      ))}
      <p className="text-xs text-muted-foreground">选中对应运行后展开候选报告即可审批。</p>
    </section>
  ) : null;
}

export function RsiRunList({
  runs,
  selectedId,
  onSelect,
}: {
  runs: RsiRun[];
  selectedId: string;
  onSelect: (runId: string) => void;
}) {
  return (
    <>
      {runs.map((run) => (
        <Button
          key={run.id}
          variant="outline"
          onClick={() => onSelect(run.id)}
          className={`h-auto w-full flex-col items-start gap-0 whitespace-normal rounded-md p-2 text-left text-xs ${
            run.id === selectedId ? 'border-primary bg-primary/10' : 'border-input'
          }`}
        >
          <span className="block">
            {run.id} · {run.status} · attempt {run.attempt}
          </span>
          <span className="text-muted-foreground">
            {run.binding_id} · candidate {run.candidate_count}
          </span>
        </Button>
      ))}
    </>
  );
}
