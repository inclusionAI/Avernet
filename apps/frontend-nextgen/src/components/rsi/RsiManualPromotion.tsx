/** Audited manual intervention: authorize exact candidate, then separately Apply. */
import { Button } from '@/components/ui/Button';
import { rsiErrorMessage } from '@/services/rsi/errors';
import { createRsiRequestKey } from '@/utils/rsiRequestKey';
import {
  applyRsiCandidate, authorizeRsiPromotion, getRsiCandidateReport, isGenomePromotionTerminal,
  type RsiAuthorizationRequest, type RsiCandidateReport, type RsiManualPromotion as ManualPromotionState,
} from '@/services/rsi/service';
import type { RsiScope } from '@/types/rsi';
import { useCallback, useEffect, useRef, useState } from 'react';

type Props = { scope: RsiScope; report: RsiCandidateReport };

function ManualPromotion({ scope, report }: Props) {
  const [current, setCurrent] = useState(report);
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [applyConfirmed, setApplyConfirmed] = useState(false);
  const [attempt, setAttempt] = useState<{ key: string; body: RsiAuthorizationRequest } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const alive = useRef(true);
  const inFlight = useRef(false);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  useEffect(() => { setCurrent(report); }, [report]);

  const state = current.manual_promotion;
  const auth = state?.authorization;
  const promotion = state?.promotion;
  const blocked = current.verdict === 'reject' || current.splits.some((s) => s.newly_failing > 0)
    || ['rejected', 'superseded'].includes(current.review?.status ?? '');
  const canAuthorize = !auth || promotion?.status === 'apply_failed';

  const validate = useCallback((value: ManualPromotionState) => {
    const a = value.authorization;
    if (a && (a.basis !== 'human' || a.candidate_id !== report.candidate_id || a.run_id !== report.run_id
      || a.revision_id !== report.revision_id || a.parent_revision_id !== report.parent_revision_id)) {
      throw new Error('授权回执与当前候选不匹配；请刷新核实，勿重复提交。');
    }
    const p = value.promotion;
    if (p && (!a || p.id !== a.id || p.revision !== a.revision_id || p.from_revision !== a.parent_revision_id)) {
      throw new Error('交付回执与授权不匹配；结果未知，请刷新核实。');
    }
    return value;
  }, [report.candidate_id, report.run_id, report.revision_id, report.parent_revision_id]);

  async function refresh() {
    const next = await getRsiCandidateReport(scope, report.candidate_id);
    if (next.manual_promotion) validate(next.manual_promotion);
    if (alive.current) setCurrent(next);
  }

  async function act(work: () => Promise<void>) {
    if (inFlight.current) return;
    inFlight.current = true; setBusy(true); setError('');
    try { await work(); }
    catch (cause) {
      if (alive.current) setError(rsiErrorMessage(cause, '操作结果未确认，请刷新状态或使用原请求重试，勿重复授权。'));
    } finally {
      inFlight.current = false;
      if (alive.current) setBusy(false);
    }
  }

  useEffect(() => {
    if (!promotion || isGenomePromotionTerminal(promotion.status)) return;
    let cancelled = false;
    // Read-only polling; never automatically replay an authorization or Apply POST.
    const timer = setTimeout(() => {
      void getRsiCandidateReport(scope, report.candidate_id).then((next) => {
        if (cancelled) return;
        if (next.manual_promotion) validate(next.manual_promotion);
        setCurrent(next);
      }).catch((cause) => {
        if (!cancelled) setError(rsiErrorMessage(cause, '交付状态读取失败；请手动刷新核实，勿重新 Apply。'));
      });
    }, 2000);
    return () => { cancelled = true; clearTimeout(timer); };
  }, [promotion, scope, report.candidate_id, validate]);

  const authorize = () => act(async () => {
    const request = attempt ?? {
      key: createRsiRequestKey(),
      body: { revision_id: report.revision_id, parent_revision_id: report.parent_revision_id, reason: reason.trim() },
    };
    setAttempt(request); // Freeze both key and body even when the response is lost.
    const result = validate(await authorizeRsiPromotion(scope, report.candidate_id, request.key, request.body));
    if (!result.authorization) throw new Error('未收到有效授权回执，请刷新核实。');
    if (alive.current) {
      setCurrent((prev) => ({ ...prev, manual_promotion: result }));
      setAttempt(null); setReason(''); setConfirmed(false); setApplyConfirmed(false);
    }
  });
  const apply = () => act(async () => {
    if (!auth) return;
    const result = validate(await applyRsiCandidate(scope, report.candidate_id, auth.id));
    if (!result.promotion) throw new Error('未收到交付回执，请刷新核实；不要新建授权。');
    if (alive.current) { setCurrent((prev) => ({ ...prev, manual_promotion: result })); setApplyConfirmed(false); }
  });

  if (!state) return <p className="text-muted-foreground">当前部署未启用人工晋升授权；不会绕过评测直接改 Bot。</p>;
  return (
    <section className="space-y-2 rounded-md border border-input p-2" aria-label="人工晋升授权与 Apply">
      <h5 className="font-medium">人工晋升授权 → 显式 Apply</h5>
      <p className="text-muted-foreground">测试 / 人工干预入口，不是评测通过。正常自动化仍须沙箱 benchmark 评测。
        原评测结论保留为 {current.verdict}；人工授权不修改该结论，也不能覆盖已知失败、安全检查、权限、带外变更或过期基线。</p>
      <dl className="break-all font-mono">
        <dt>Bot / Owner</dt><dd>{scope.botId} / {scope.ownerId}</dd>
        <dt>Run</dt><dd>{report.run_id}</dd>
        <dt>Candidate</dt><dd>{report.candidate_id}</dd>
        <dt>固定 parent → 目标 revision</dt><dd>{report.parent_revision_id} → {report.revision_id}</dd>
      </dl>
      <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(refresh)}>刷新授权与交付状态</Button>
      {blocked ? <p className="text-destructive">当前候选有拒绝 / 已知新增失败 / 已终止评审，不能人工覆盖。</p> : null}
      {auth ? (
        <div className="space-y-1 break-all">
          <p>授权依据：human（非 benchmark pass） · {auth.id}</p>
          <p>授权人：{auth.actor}（{auth.user_id}） · {auth.authorized_at}</p>
          <p>授权理由：{auth.reason}</p>
          <p>授权时原判定：{auth.checks_at_authorization.verdict}；静态检查：{auth.checks_at_authorization.floor}</p>
          {auth.checks_at_authorization.floor !== 'pass' ? <p className="text-muted-foreground">未执行的静态检查不等于通过：{auth.checks_at_authorization.floor_problems.join('；')}</p> : null}
          {!promotion ? <p>已授权，尚未确认启动 Apply；真实 Bot 和 active 基线未因授权而改变。</p> : null}
        </div>
      ) : null}
      {promotion ? (
        <div className="space-y-1 break-all" role="status">
          <p>交付 {promotion.id} · {promotion.status} · Apply {promotion.apply_id ?? '尚无回执'}</p>
          {promotion.apply_detail ? <p>{promotion.apply_detail}</p> : null}
          {['pending', 'applying'].includes(promotion.status) ? <p>等待 Backend Apply 结算；现在不能视为新基线。</p> : null}
          {promotion.status === 'applied' ? <p>Apply 已完成：本次交付结算时 active 为目标 revision，previous 为固定 parent。此为历史回执；当前基线与现场漂移请在基线管理中复查。原评测结论仍不变。</p> : null}
          {promotion.status === 'applied_partial' ? <p className="text-destructive">部分交付：本次交付结算时 active / previous 已推进，但存在未完成项；此为历史回执，不代表当前现场或全部功能验收成功，请检查 Apply 详情和现场漂移。</p> : null}
          {promotion.status === 'apply_failed' ? <p className="text-destructive">本次 Apply 失败，未因此晋升为新基线；不代表当前基线未再变化或现场完全未改动。排查后才能重新人工授权。</p> : null}
        </div>
      ) : null}
      {canAuthorize ? (
        <div className="space-y-2">
          <label className="block">人工晋升理由（必填）
            <textarea rows={3} maxLength={1000} className="mt-1 w-full rounded-md border border-input bg-background p-2"
              disabled={busy || !!attempt} value={reason} onChange={(e) => { setReason(e.target.value); setConfirmed(false); }} />
          </label>
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={confirmed} disabled={busy || !!attempt} onChange={(e) => setConfirmed(e.target.checked)} />
            我已检查上方实际 Diff，确认人工判断替代本次评测授权；这一步不 Apply。
          </label>
          {attempt ? <p>上次授权请求结果未确认，理由和幂等键已冻结；重试只恢复同一请求，或先刷新状态。</p> : null}
          <Button size="sm" disabled={busy || blocked || (!attempt && (!confirmed || !reason.trim()))} onClick={() => void authorize()}>
            {attempt ? '使用原请求重试授权' : '人工授权晋升（不 Apply）'}
          </Button>
        </div>
      ) : null}
      {auth && !promotion ? (
        <div className="space-y-2 border-t border-input pt-2">
          <label className="flex items-start gap-2">
            <input type="checkbox" checked={applyConfirmed} disabled={busy} onChange={(e) => setApplyConfirmed(e.target.checked)} />
            我确认将该已授权候选应用到此真实 Bot；由 Backend Apply 完成后结算新基线。
          </label>
          <Button size="sm" disabled={busy || blocked || !applyConfirmed || (!!scope.actorId && scope.actorId !== auth.user_id)} onClick={() => void apply()}>Apply 到真实 Bot</Button>
          <p className="text-muted-foreground">需要原授权人当前仍有 ADMIN 权限；响应丢失可刷新或按同一授权恢复，不会创建第二次 Apply。</p>
        </div>
      ) : null}
      {error ? <p role="alert" className="text-destructive">{error}</p> : null}
    </section>
  );
}

export function RsiManualPromotion(props: Props) {
  const { scope, report } = props;
  // Remount on scope/target change: a late response must never enable another Bot's Apply.
  return <ManualPromotion key={JSON.stringify([scope, report.candidate_id, report.revision_id, report.parent_revision_id])} {...props} />;
}
