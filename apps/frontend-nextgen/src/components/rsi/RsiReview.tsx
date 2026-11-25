/** RSI 07–09：验证判定、门禁决策与人工评审的展示和操作面。 */
import { Badge, type BadgeTone } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { rsiErrorMessage } from '@/services/rsi/errors';
import {
  approveRsiCandidate,
  getRsiCandidateReport,
  rejectRsiCandidate,
  type RsiCandidate,
  type RsiCandidateReport,
  type RsiEvidence,
  type RsiReviewDecision,
} from '@/services/rsi/service';
import type { RsiScope } from '@/types/rsi';
import { useState } from 'react';

const pretty = (value: unknown) => JSON.stringify(value, null, 2);
const shortHash = (value: string) => {
  const hex = value.replace(/^sha256:/, '');
  return value.startsWith('sha256:') ? `sha256:${hex.slice(0, 12)}…` : value;
};
const timeOf = (iso: string) => {
  const parsed = new Date(iso);
  return Number.isNaN(parsed.getTime()) ? iso : parsed.toLocaleTimeString();
};

export const verdictTone = (verdict: string): BadgeTone =>
  verdict === 'accept' ? 'success' : verdict === 'reject' ? 'error' : verdict === 'inconclusive' ? 'warning' : 'neutral';
export const gateTone = (outcome: string): BadgeTone =>
  outcome === 'auto_promote' ? 'success' : outcome === 'needs_review' ? 'warning' : outcome === 'not_promotable' ? 'error' : 'neutral';
const checkTone = (status: string): BadgeTone =>
  status === 'pass' ? 'success' : status === 'fail' ? 'error' : status === 'soft' ? 'warning' : 'neutral';

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

function SplitTable({ report }: { report: RsiCandidateReport }) {
  if (!report.splits.length) return <p className="text-xs text-muted-foreground">判定未落定，暂无分片数据。</p>;
  return (
    <table className="w-full text-left text-xs">
      <thead>
        <tr className="text-muted-foreground">
          <th className="py-0.5 pr-2 font-medium">分片</th>
          <th className="py-0.5 pr-2 font-medium">均差</th>
          <th className="py-0.5 pr-2 font-medium">置信区间</th>
          <th className="py-0.5 font-medium">新增失败</th>
        </tr>
      </thead>
      <tbody>
        {report.splits.map((split) => (
          <tr key={split.split} className="border-t border-input">
            <td className="py-0.5 pr-2 font-mono">{split.split}</td>
            <td className="py-0.5 pr-2">{split.mean_delta ?? '—'}</td>
            <td className="py-0.5 pr-2">
              {split.ci_low ?? '—'} ~ {split.ci_high ?? '—'}
            </td>
            <td className="py-0.5">{split.newly_failing}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function ReviewBlock({
  report,
  decision,
  reason,
  setReason,
  onDecide,
  busy,
}: {
  report: RsiCandidateReport;
  decision: RsiReviewDecision | null;
  reason: string;
  setReason: (value: string) => void;
  onDecide: (action: 'approve' | 'reject') => void;
  busy: boolean;
}) {
  const review = decision?.review ?? report.review;
  if (review && review.status !== 'open') {
    return (
      <div className="space-y-1 text-xs">
        <p>
          评审结论：<Badge tone={review.status === 'approved' ? 'success' : 'error'}>{review.status}</Badge>
        </p>
        <p className="text-muted-foreground">
          {review.decided_by ?? '—'} · {review.decided_at ?? '—'}
        </p>
        {review.decision_reason ? <p className="break-all">理由：{review.decision_reason}</p> : null}
        {decision?.promotion_note ? <p className="text-muted-foreground">{decision.promotion_note}</p> : null}
      </div>
    );
  }
  if (review) {
    return (
      <div className="space-y-2">
        <p className="text-xs">
          评审单 <Badge tone="warning">open</Badge>，批准只记录人工结论；apply 未启用，不会改动 Bot 或 active 基线。
        </p>
        <textarea
          className="w-full rounded-md border border-input bg-background p-2 text-xs"
          rows={2}
          placeholder="审批理由（必填，随结论一起审计）"
          value={reason}
          disabled={busy}
          onChange={(event) => setReason(event.target.value)}
        />
        <div className="flex gap-2">
          <Button size="sm" disabled={busy || !reason.trim()} onClick={() => onDecide('approve')}>
            批准
          </Button>
          <Button size="sm" variant="outline" disabled={busy || !reason.trim()} onClick={() => onDecide('reject')}>
            拒绝
          </Button>
        </div>
      </div>
    );
  }
  return <p className="text-xs text-muted-foreground">门禁未要求人工评审。</p>;
}

/** 单个候选：状态徽标 + 可展开的 07–09 报告与审批操作。 */
export function RsiCandidateCard({
  scope,
  candidate,
  act,
  busy,
}: {
  scope: RsiScope;
  candidate: RsiCandidate;
  act: (action: () => Promise<void>) => Promise<void>;
  busy: boolean;
}) {
  const [open, setOpen] = useState(false);
  const [report, setReport] = useState<RsiCandidateReport | null>(null);
  const [decision, setDecision] = useState<RsiReviewDecision | null>(null);
  const [reason, setReason] = useState('');
  const [loadError, setLoadError] = useState('');
  const [keys, setKeys] = useState<{ approve?: string; reject?: string }>({});

  const load = () =>
    act(async () => {
      setLoadError('');
      try {
        setReport(await getRsiCandidateReport(scope, candidate.candidate_id));
      } catch (cause) {
        // 报告读取失败只落在卡片内，不覆盖面板的运行信息。
        setLoadError(rsiErrorMessage(cause, '候选报告读取失败，请稍后重试。'));
        throw cause;
      }
    });

  const decide = (action: 'approve' | 'reject') =>
    act(async () => {
      const key = keys[action] ?? crypto.randomUUID();
      setKeys((prev) => ({ ...prev, [action]: key }));
      const fn = action === 'approve' ? approveRsiCandidate : rejectRsiCandidate;
      const result = await fn(scope, candidate.candidate_id, key, reason.trim());
      setDecision(result);
      setReport((prev) => (prev ? { ...prev, review: result.review } : prev));
    });

  return (
    <div className="space-y-2 rounded-md border border-input p-2 text-xs">
      <div className="flex flex-wrap items-center gap-1.5">
        <span className="font-mono">{shortHash(candidate.candidate_id)}</span>
        <Badge tone={verdictTone(report?.verdict ?? candidate.verdict)}>{report?.verdict ?? candidate.verdict}</Badge>
        <Button
          size="sm"
          variant="outline"
          disabled={busy}
          onClick={() => {
            const next = !open;
            setOpen(next);
            if (next && !report) void load();
          }}
        >
          {open ? '收起报告' : '候选报告'}
        </Button>
        {open ? (
          <Button size="sm" variant="ghost" disabled={busy} onClick={() => void load()}>
            刷新
          </Button>
        ) : null}
      </div>
      {loadError ? (
        <p role="alert" className="text-destructive">
          {loadError}
        </p>
      ) : null}
      {open && report ? (
        <div className="space-y-3 border-t border-input pt-2">
          <div className="break-all text-muted-foreground">
            {report.strategy}@{report.strategy_version} · Run {report.run_id}
            <br />
            parent seq {report.parent_revision_seq ?? '—'} → revision seq {report.revision_seq ?? '—'}
          </div>
          {report.patch_summary.length ? (
            <div>
              <h5 className="font-medium">补丁摘要</h5>
              <ul className="list-inside list-disc">
                {report.patch_summary.map((item) => (
                  <li key={item.op_index}>
                    <span className="font-mono">{item.op}</span> {item.target}（{item.tier}）
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
          {report.rationale ? <p className="break-all text-muted-foreground">策略说明：{report.rationale}</p> : null}
          <div>
            <h5 className="font-medium">
              验证（07） <Badge tone={verdictTone(report.verdict)}>{report.verdict}</Badge>
              {report.flags.map((flag) => (
                <Badge key={flag} tone="warning" className="ml-1">
                  {flag}
                </Badge>
              ))}
            </h5>
            <SplitTable report={report} />
          </div>
          <div>
            <h5 className="font-medium">
              门禁（08）{' '}
              {report.gate ? <Badge tone={gateTone(report.gate.outcome)}>{report.gate.outcome}</Badge> : <Badge>待定</Badge>}
              {report.gate ? <span className="ml-1 text-muted-foreground">{report.gate.risk.tier}</span> : null}
            </h5>
            {report.gate ? (
              <ul className="space-y-0.5">
                {report.gate.checks.map((check) => (
                  <li key={check.name} className="flex items-center gap-1.5">
                    <Badge tone={checkTone(check.status)}>{check.status}</Badge>
                    <span className="font-mono">{check.name}</span>
                    <span className="text-muted-foreground">{check.detail}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-muted-foreground">判定落定后才会进行门禁决策。</p>
            )}
          </div>
          <div>
            <h5 className="font-medium">评审（09）</h5>
            <ReviewBlock
              report={report}
              decision={decision}
              reason={reason}
              setReason={setReason}
              onDecide={(action) => void decide(action)}
              busy={busy}
            />
          </div>
        </div>
      ) : null}
    </div>
  );
}
