/** Selected Run evidence and immutable candidate reports. */
import { RsiEventTimeline } from '@/components/rsi/RsiEventTimeline';
import { RsiCandidateCard } from '@/components/rsi/RsiReview';
import { RsiStrategyOutputPanel } from '@/components/rsi/RsiStrategyOutput';
import { Button } from '@/components/ui/Button';
import { cancelRsiRun, isRsiTerminal, type RsiCandidate, type RsiEvidence, type RsiRun } from '@/services/rsi/service';
import { rsiScopeKey } from '@/stores/rsiPanelStore';
import type { RsiScope } from '@/types/rsi';

const pretty = (value: unknown) => JSON.stringify(value, null, 2);
export function RsiRunDetails({
  scope,
  selected,
  evidence,
  candidates,
  act,
  busy,
}: {
  scope: RsiScope;
  selected: RsiRun;
  evidence: RsiEvidence | null;
  candidates: RsiCandidate[] | null;
  act: (action: () => Promise<void>) => Promise<void>;
  busy: boolean;
}) {
  return (
    <>
      <p className="break-all text-xs text-muted-foreground">
        parent：{selected.parent_revision}
        <br />
        结束原因：{selected.end_reason ?? '—'}
      </p>
      {selected.summary ? (
        <details>
          <summary className="cursor-pointer text-xs text-muted-foreground">策略执行摘要</summary>
          <pre className="overflow-auto whitespace-pre-wrap break-words rounded bg-muted p-2 text-xs">
            {pretty(selected.summary)}
          </pre>
        </details>
      ) : null}
      <RsiStrategyOutputPanel key={`${rsiScopeKey(scope)}:${selected.id}`} scope={scope} run={selected} />
      {!isRsiTerminal(selected.status) ? (
        <Button
          variant="outline"
          disabled={busy}
          onClick={() =>
            void act(async () => {
              await cancelRsiRun(scope, selected.id);
            })
          }
        >
          取消运行
        </Button>
      ) : null}
      <h4 className="text-sm font-medium">执行证据（最多 500 条，保留 authority 来源）</h4>
      {evidence ? <RsiEventTimeline events={evidence.events} /> : <p className="text-xs text-muted-foreground">执行证据尚未读取成功。</p>}
      <details>
        <summary className="cursor-pointer text-xs text-muted-foreground">Job / Worker / Lease</summary>
        <pre className="max-h-80 overflow-auto rounded bg-muted p-2 text-xs">{pretty(evidence?.jobs ?? [])}</pre>
      </details>
      <h4 className="text-sm font-medium">候选版本（pending 不等于通过）</h4>
      {candidates === null ? (
        <p className="text-xs text-muted-foreground">候选尚未读取成功。</p>
      ) : candidates.length ? (
        candidates.map((candidate) => (
          <RsiCandidateCard key={`${rsiScopeKey(scope)}:${selected.id}:${candidate.candidate_id}`} scope={scope} candidate={candidate} act={act} busy={busy} />
        ))
      ) : (
        <p className="text-xs text-muted-foreground">该运行暂无候选。</p>
      )}
    </>
  );
}
