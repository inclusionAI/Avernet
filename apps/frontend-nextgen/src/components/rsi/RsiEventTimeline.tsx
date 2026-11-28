/** Structured evidence timeline, with raw JSON kept expandable. */
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import type { RsiEvidence } from '@/services/rsi/service';
const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const timeOf = (iso: string) => {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleTimeString();
};

const EVENT_KIND_LABEL: Record<string, string> = {
  'verdict.finalized': '验证判定落定',
  'gate.decided': '门禁决策',
  'review.decided': '评审结论',
  'candidate.persisted': '候选已记录',
  'candidate.rejected': '候选被拒绝',
  'ledger.submission.persisted': '实验台账已记录',
};
const eventTone = (kind: string): BadgeTone =>
  kind === 'verdict.finalized' ? 'primary' : kind === 'gate.decided' ? 'purple' : kind === 'review.decided' ? 'success' : 'neutral';

/** 执行证据时间线：结构化呈现，原始 JSON 折叠保留。 */
export function RsiEventTimeline({ events }: { events: RsiEvidence['events'] }) {
  if (!events.length) return <p className="text-xs text-muted-foreground">暂无证据事件。</p>;
  return (
    <div className="space-y-1">
      <ol className="space-y-1">
        {events.map((event) => (
          <li key={event.id} className="rounded border border-input p-1.5 text-xs">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="text-muted-foreground">{timeOf(event.at)}</span>
              <Badge tone={eventTone(event.kind)}>{EVENT_KIND_LABEL[event.kind] ?? event.kind}</Badge>
              <span className="font-mono text-[11px] text-muted-foreground">{event.kind}</span>
              <span className="ml-auto text-[11px] text-muted-foreground">{event.authority}</span>
            </div>
            {event.details !== null && event.details !== undefined && pretty(event.details) !== '{}' ? (
              <details className="mt-1">
                <summary className="cursor-pointer text-[11px] text-muted-foreground">详情</summary>
                <pre className="mt-1 max-h-40 overflow-auto rounded bg-muted p-1.5 text-[11px]">{pretty(event.details)}</pre>
              </details>
            ) : null}
          </li>
        ))}
      </ol>
      <details>
        <summary className="cursor-pointer text-[11px] text-muted-foreground">原始 JSON</summary>
        <pre className="mt-1 max-h-80 overflow-auto rounded bg-muted p-2 text-xs">{pretty(events)}</pre>
      </details>
    </div>
  );
}
