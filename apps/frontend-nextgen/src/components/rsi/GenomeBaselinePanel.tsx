/** Explicit owner reconciliation using the existing Genome capture/diff/promotion contract. */
import { GenomeDiffView } from '@/components/rsi/GenomeDiff';
import { Button } from '@/components/ui/Button';
import { Checkbox } from '@/components/ui/Checkbox';
import { genomeErrorMessage } from '@/services/rsi/errors';
import {
  captureGenomeRevision, getGenomeDiff, getGenomeDrift, getGenomePromotion, getGenomeRefs,
  isGenomePromotionTerminal, listGenomePromotions, promoteGenomeRevision,
  type GenomeCapture, type GenomeDiff, type GenomeDrift, type GenomePromotion, type GenomeRef,
} from '@/services/rsi/service';
import { genomeOperationTag, genomeScopeKey, useGenomePromotionStore } from '@/stores/genomePromotionStore';
import { useIdentityStore } from '@/stores/identityStore';
import type { RsiScope } from '@/types/rsi';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

interface BaselineState {
  active: GenomeRef | null;
  drift: GenomeDrift | null;
  promotions: GenomePromotion[];
}
interface Preview { capture: GenomeCapture; expected: string; diff: GenomeDiff }
const promotable = (capture: GenomeCapture) => ['draft', 'accepted', 'promoted'].includes(capture.status);
const hasApplying = (state: BaselineState) => state.promotions.some((item) => !isGenomePromotionTerminal(item.status));
const matchesPreview = (state: BaselineState, preview: Preview) =>
  state.active?.revision === preview.expected && state.drift?.active === preview.expected &&
  state.drift.observed === preview.capture.id;
const changedMessage = '现场或 active 已变化，请重新读取、捕获并检查全部 Diff；本次未提交晋升。';

async function readState(scope: RsiScope): Promise<BaselineState> {
  const [refs, observation, history] = await Promise.all([
    getGenomeRefs(scope), getGenomeDrift(scope), listGenomePromotions(scope),
  ]);
  const active = refs.refs.find((item) => item.name === 'active') ?? null;
  if (observation.drift && observation.drift.active !== active?.revision) throw new Error('active changed during read');
  return { active, drift: observation.drift, promotions: history.items };
}

function BaselineBody({ scope }: { scope: RsiScope }) {
  const [state, setState] = useState<BaselineState | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [reason, setReason] = useState('');
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<GenomePromotion | null>(null);
  const [pollRetry, setPollRetry] = useState(0);
  const pending = useGenomePromotionStore((store) => store.attempts[genomeScopeKey(scope)]);
  const alive = useRef(true);
  const locked = useRef(false);
  const readSequence = useRef(0);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  const refresh = useCallback(async () => {
    const sequence = ++readSequence.current;
    if (alive.current) setState(null);
    const value = await readState(scope);
    if (alive.current && sequence === readSequence.current) setState(value);
    return value;
  }, [scope]);
  useEffect(() => {
    void refresh().catch((cause: unknown) => {
      if (alive.current) setError(genomeErrorMessage(cause, '基线状态读取失败；不能当作没有漂移。'));
    });
  }, [refresh]);

  useEffect(() => {
    if (!pending?.promotionId) return;
    let active = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        const value = await getGenomePromotion(scope, pending.promotionId!);
        if (!active) return;
        if (value.id !== pending.promotionId || value.revision !== pending.revision ||
          value.from_revision !== pending.expectedRevision || !value.reason.endsWith(genomeOperationTag(pending))) throw new Error('promotion identity mismatch');
        setResult(value);
        if (isGenomePromotionTerminal(value.status)) {
          // Delivery settlement is not inferred from a request receipt. Re-read active and live drift.
          await refresh();
          if (active) useGenomePromotionStore.getState().settle(scope);
        } else timer = setTimeout(() => void poll(), 3000);
      } catch (cause) {
        if (active) setError(genomeErrorMessage(cause, '晋升状态读取失败，结果仍未确认；请重新读取状态，不要重复提交。'));
      }
    };
    void poll();
    return () => { active = false; clearTimeout(timer); };
  }, [scope, pending?.promotionId, pending?.revision, pending?.expectedRevision, pending?.correlationId, refresh, pollRetry]);

  async function act(action: () => Promise<void>) {
    if (locked.current) return;
    locked.current = true;
    setBusy(true);
    setError('');
    try { await action(); } catch (cause) {
      if (alive.current) setError(genomeErrorMessage(cause, cause instanceof Error && cause.message === changedMessage
        ? changedMessage : '操作未确认。请核对权限及基线/晋升记录；不会自动重试晋升。'));
    } finally {
      locked.current = false;
      if (alive.current) setBusy(false);
    }
  }
  async function capture() {
    setPreview(null);
    setConfirmed(false);
    const before = await refresh();
    if (!alive.current || !before.active || !before.drift || hasApplying(before)) return;
    const captured = await captureGenomeRevision(scope);
    if (!alive.current) return;
    const diff = await getGenomeDiff(scope, captured.id, before.active.revision);
    const next = { capture: captured, expected: before.active.revision, diff };
    const after = await refresh();
    if (!alive.current) return;
    if (diff.revision !== captured.id || diff.against !== next.expected || !matchesPreview(after, next)) {
      throw new Error(changedMessage);
    }
    setPreview(next);
  }
  async function promote() {
    if (!preview || !confirmed || !reason.trim() || !promotable(preview.capture)) return;
    const current = await refresh();
    if (!alive.current) return;
    if (!matchesPreview(current, preview) || hasApplying(current)) {
      setPreview(null);
      setConfirmed(false);
      throw new Error(changedMessage);
    }
    const store = useGenomePromotionStore.getState();
    // Persist before POST. There is no idempotency contract for this endpoint: never replay an unknown result.
    const attempt = store.begin(scope, { revision: preview.capture.id, expectedRevision: preview.expected, promotionId: null });
    setPreview(null);
    setConfirmed(false);
    try {
      const auditReason = `${reason.trim()}\n${genomeOperationTag(attempt)}`;
      const value = await promoteGenomeRevision(scope, preview.capture.id, preview.expected, auditReason);
      if (value.revision !== preview.capture.id || value.from_revision !== preview.expected || value.reason !== auditReason) {
        throw new Error('promotion identity mismatch');
      }
      store.track(scope, value.id); // Also retain the receipt if the user closed/switched the panel while submitting.
      if (alive.current) setResult(value);
    } catch (cause) {
      const status = (cause as { status?: number })?.status;
      // Explicit client refusals are safe to correct; timeouts, 5xx and malformed receipts remain uncertain.
      if (status && [400, 401, 403, 404, 409, 422].includes(status)) store.settle(scope);
      if (alive.current && status === 409) {
        setError('晋升冲突（409）：active 已移动或有 Apply 正在进行。请重新读取后检查，不会自动重提。');
        return;
      }
      throw cause;
    }
  }
  const reconciled = result?.status === 'applied' && state?.active?.revision === result.revision &&
    !state.active.labels.includes('partial') && !state.drift;
  return (
    <div className="space-y-3 pt-2 text-xs">
      <p className="text-muted-foreground">
        基线管理与候选审批分开。捕获只存快照；只有 Owner 明确提交正式 Apply 后才可能移动 active。
        这不是行为质量评测，也不会自动重跑失败的 RSI。
      </p>
      {error ? <p role="alert" className="text-destructive">{error}</p> : null}
      <Button size="sm" variant="outline" disabled={busy} onClick={() => void act(async () => {
        setPreview(null); setConfirmed(false); await refresh(); setPollRetry((value) => value + 1);
      })}>重新读取基线状态</Button>
      {state ? <>
        <p className="break-all">active：{state.active?.revision ?? '未建立（drift 为空不代表已有基线）'}</p>
        {state.active?.labels.includes('partial') ? <p className="text-destructive">active 标记为 partial：上次只部分交付，不能视为完整验收成功。</p> : null}
        {state.drift ? <p className="break-all">检测到带外变更（out_of_band_change）<br />现场：{state.drift.observed}</p>
          : state.active ? <p>本次观察未检测到漂移。</p> : null}
        {hasApplying(state) ? <p>有 pending / applying 晋升，请先核对交付结果。</p> : null}
        <Button size="sm" variant="outline" disabled={busy || Boolean(pending) || !state.drift || hasApplying(state)}
          onClick={() => void act(capture)}>捕获现场并预览全量差异</Button>
      </> : <p>基线状态尚未读取成功。</p>}
      {preview ? <div className="space-y-2 rounded border border-input p-2">
        <p>已捕获 {preview.capture.created ? '新' : '已有'}版本 · 状态 {preview.capture.status}；尚未 Apply。</p>
        <GenomeDiffView diff={preview.diff} />
        {!promotable(preview.capture) ? <p className="text-destructive">
          此现场内容对应 {preview.capture.status} 版本，不能通过重新捕获绕过候选评测或晋升门禁。
        </p> : <>
          <p className="text-muted-foreground">这是整个 Genome 快照的 Apply，不只是当前 Skill。可能重载配置或重启 Bot。
            请暂停聊天和其他编辑；提交前会复查，但它不是对运行时文件修改的原子锁。</p>
          <label className="block">调和理由（必填）
            <textarea className="mt-1 w-full rounded border border-input bg-background p-2" maxLength={900}
              value={reason} disabled={busy} onChange={(event) => { setReason(event.target.value); setConfirmed(false); }} />
          </label>
          <label className="flex items-start gap-2">
            <Checkbox checked={confirmed} disabled={busy} onCheckedChange={setConfirmed} />
            我已检查全部差异，确认将此完整快照通过 Apply 调和为基线
          </label>
          <Button size="sm" disabled={busy || Boolean(pending) || !confirmed || !reason.trim()}
            onClick={() => void act(promote)}>确认调和并启动 Apply</Button>
        </>}
      </div> : null}
      {pending ? <p role="status" className="break-all text-muted-foreground">
        {pending.promotionId ? `正在核对晋升 ${pending.promotionId}` : '提交结果未知：禁止自动或直接重复提交。请读取历史并跟踪对应晋升；未找到记录不能证明提交失败，需服务端核对。'}
        <br />操作关联：{pending.correlationId}<br />目标：{pending.revision}。本标签页刷新/关闭面板后保留待确认标识；不要换标签重复提交。
      </p> : null}
      {result ? <div className="space-y-1 break-all rounded border border-input p-2">
        <p>晋升 {result.id} · {result.status}</p>
        <p>Apply：{result.apply_id ?? '尚无回执'} · {result.apply_detail}</p>
        {reconciled ? <p>Apply 已完成，active 已指向目标，本次复查无漂移。可由你提交新的 RSI 任务。</p>
          : result.status === 'applied_partial' ? <p className="text-destructive">仅部分交付，active 也可能已移动；请检查失败项，不能视为调和成功。</p>
          : result.status === 'apply_failed' ? <p className="text-destructive">Apply 失败；不能视为新基线已生效，请核对当前 active。</p>
          : result.status === 'applied' ? <p>交付记录为 applied，但当前 active / 漂移尚未确认与目标一致，不能视为当前已调和。</p> : null}
      </div> : null}
      {state?.promotions.length ? <details>
        <summary className="cursor-pointer">最近晋升记录（最多 20 条）</summary>
        <ul className="space-y-2 pt-2">{state.promotions.map((item) => (
          <li key={item.id} className="break-all rounded border border-input p-2">
            <p>{item.id} · {item.status} · {item.created_at}</p>
            <p>{item.from_revision ?? '无基线'} → {item.revision}</p>
            <p>理由：{item.reason}</p><p>Apply：{item.apply_id ?? '—'} · {item.apply_detail}</p>
            {pending && !pending.promotionId && item.revision === pending.revision &&
              item.from_revision === pending.expectedRevision && item.reason.endsWith(genomeOperationTag(pending)) ? <Button size="sm" variant="outline" disabled={busy}
                onClick={() => useGenomePromotionStore.getState().track(scope, item.id)}>跟踪这条晋升</Button> : null}
          </li>
        ))}</ul>
      </details> : null}
    </div>
  );
}

export function GenomeBaselinePanel({ scope }: { scope: RsiScope }) {
  const [open, setOpen] = useState(false);
  const identity = useIdentityStore((store) => store.currentIdentityId);
  const resolved = useMemo(() => ({ ...scope, actorId: scope.actorId || identity || undefined }), [scope, identity]);
  return <section aria-label="Genome基线管理" className="rounded border border-input p-2">
    <Button size="sm" variant="ghost" aria-expanded={open} onClick={() => setOpen((value) => !value)}>
      {open ? '收起基线管理' : '查看基线与现场差异'}
    </Button>
    {open ? <BaselineBody key={genomeScopeKey(resolved)} scope={resolved} /> : null}
  </section>;
}
