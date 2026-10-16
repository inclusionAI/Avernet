import RepairTrialPanel from './RepairTrialPanel'
import type { RepairTrial } from '../../../../server/contracts/repair-trial'
import { useEffect, useRef, useState } from 'react'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import type { RepairConfirmRequest, RepairReleaseTarget } from '../../../../server/contracts/repair-release'
import { repairBatches } from '../../../api/repair-batches'
import { button, primary } from './repair-view'
import RepairVerification from './RepairVerification'
import { repairReleaseError } from '../../../api/repair-release-error'

export default function RepairReleaseFlow({ detail, selectedRevision, canEdit, onRefresh }: {
  detail: RepairTaskDetail; selectedRevision: number; canEdit: boolean; onRefresh: () => void;
}) {
  const [trialOpen, setTrialOpen] = useState(false)
  const [trialConfigured, setTrialConfigured] = useState(false)
  const [trials, setTrials] = useState<RepairTrial[]>([])
  const [dialog, setDialog] = useState(false)
  const [target, setTarget] = useState<RepairReleaseTarget | null>(null)
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const submitting = useRef(false)
  const request = useRef<RepairConfirmRequest | null>(null)
  const scope = `${detail.taskId}:${selectedRevision}`
  const scopeRef = useRef(scope); scopeRef.current = scope
  const panel = useRef<HTMLElement>(null)
  const revision = detail.latestSuccessful
  const automatic = !!detail.latestAttempt.input?.automation?.roundId
  const automaticReady = automatic && detail.automaticProgress?.publishable === true && detail.latestAttempt.revision === selectedRevision
  const isCurrent = revision?.revision === selectedRevision && detail.latestAttempt.phase !== 'drafting' && (!automatic || automaticReady)
  const deployment = detail.release?.deployments.find(row => row.revision === selectedRevision && row.status !== 'failed')
    ?? detail.release?.deployments.find(row => row.revision === selectedRevision)
  const pending = !!deployment && ['created', 'dispatching', 'running', 'uncertain'].includes(deployment.status)
  const succeeded = deployment?.status === 'succeeded'
  const canPublish = canEdit && isCurrent && detail.capabilities.publication && (revision?.phase === 'review' || automaticReady && revision?.phase === 'no_change')
  useEffect(() => { setDialog(false); setTrialOpen(false); setTrialConfigured(false); setTrials([]); setTarget(null); setAck(false); setError(''); request.current = null }, [scope])
  async function reconcile() {
    if (!deployment || !canEdit || submitting.current) return
    submitting.current = true; setBusy(true)
    try { await repairBatches.reconcileDeployment(detail.taskId, deployment.id); onRefresh() }
    catch (reason) { setError(repairReleaseError(reason)) }
    finally { submitting.current = false; setBusy(false) }
  }
  useEffect(() => {
    if (!pending || !canEdit) return
    const timer = setInterval(() => { void reconcile() }, 15_000)
    return () => clearInterval(timer)
  }, [pending, canEdit, deployment?.id, deployment?.status])
  async function open() {
    const current = scope
    setDialog(true); setTarget(null); setAck(false); setError(''); setLoading(true)
    panel.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
    try {
      const response = await repairBatches.deploymentTargets(detail.taskId)
      const scopes = response.targets.filter(row => row.scope === 'workflow')
      if (scopeRef.current !== current) return
      if (scopes.length !== 1) throw new Error('没有唯一且已授权的工作流发布范围，请检查权限和宿主配置')
      setTarget(scopes[0])
    } catch (reason) { if (scopeRef.current === current) setError(repairReleaseError(reason)) }
    finally { if (scopeRef.current === current) setLoading(false) }
  }
  async function publish() {
    if (!canPublish || !target || !ack || !revision || submitting.current) return
    submitting.current = true; setBusy(true); setError('')
    request.current ??= { revision: revision.revision, expectedStateVersion: revision.stateVersion,
      requestId: crypto.randomUUID(), targetId: target.id, acknowledgeUncoveredChecks: ack }
    try {
      await repairBatches.publish(detail.taskId, request.current)
      setDialog(false); request.current = null; onRefresh()
    } catch (reason) { setError(repairReleaseError(reason)); onRefresh() }
    finally { submitting.current = false; setBusy(false) }
  }
  async function resume() {
    if (!deployment || submitting.current || !canEdit) return
    submitting.current = true; setBusy(true)
    try { await repairBatches.resumeDeployment(detail.taskId, deployment.id, crypto.randomUUID()); onRefresh() }
    catch (reason) { setError(repairReleaseError(reason)) }
    finally { submitting.current = false; setBusy(false) }
  }
  const title = succeeded ? `已生效 #${deployment.receipt?.deployNumber}，系统自动观察效果`
    : pending ? '发布结果待核对' : deployment?.status === 'failed' ? '发布未完成，候选已保留' : '审阅并确认发布'
  const selected = detail.revisions.find(row => row.revision === selectedRevision) ?? (detail.latestAttempt.revision === selectedRevision ? detail.latestAttempt : detail.latestSuccessful?.revision === selectedRevision ? detail.latestSuccessful : undefined)
  if (selected && (['failed', 'drafting', 'cancelled'].includes(selected.phase) || selected.phase === 'no_change' && !automaticReady) && !succeeded && !pending) return <section aria-label="候选发布状态" className="rounded-xl border border-input bg-card p-5 text-sm text-muted-foreground">
    <h3 className="font-semibold text-foreground">{selected.phase === 'failed' ? '所选版本生成失败，尚无可发布候选' : selected.phase === 'no_change' ? '本轮无需重新发布' : selected.phase === 'cancelled' ? '任务已取消' : '正在生成候选，暂不进入发布'}</h3>
    <p className="mt-2">{selected.phase === 'failed' ? '查看本版本错误和最近任务状态；历史失败版本不会因后续成功而变为成功。' : '查看本版本范围与逐项结果；此入口不执行生成或发布。'}</p>
    {!!detail.release?.deployments.some(row => row.status === 'succeeded') && <p className="mt-2">已有生效版本的发布与观察记录保留，切换对应候选版本查看。</p>}
  </section>
  return <section ref={panel} aria-label="审阅部署与效果验证" className="scroll-mt-6 rounded-xl border border-input bg-card p-5">
    <h3 className="text-base font-semibold">{title}</h3>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">{succeeded ? '仅以生效版本、相关节点及可判定样本验证；证据不足时持续观察。'
      : pending ? '先核对同一个发布请求，结果未明确前不重复发布。'
      : '发布已审阅的完整 Pack，作用于此工作流在当前环境的后续新运行。已有运行保留原版本。'}</p>
    {automatic && !automaticReady && !succeeded && <p className="mt-3 text-sm text-muted-foreground">本轮尚未完整校验，暂不可发布；保留各项进度与结果。</p>}
    {!isCurrent && !succeeded && <p className="mt-3 text-sm text-muted-foreground">当前查看的是历史候选，请切回最新可用候选。</p>}
    {!detail.capabilities.publication && <p role="alert">发布能力未启用，请检查宿主配置。</p>}
    {deployment?.error && <p className="mt-3 text-sm text-destructive">{deployment.error}</p>}
    {error && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
    {selected?.draft?.candidateCommit && <RepairTrialPanel taskId={detail.taskId} revision={selected} enabled={!!canEdit && !!isCurrent && selected.phase === 'review'} open={trialOpen} onClose={() => setTrialOpen(false)} onTrials={(rows, configured) => { setTrials(rows); setTrialConfigured(configured) }} />}
    {dialog && <div role="region" aria-label="确认发布候选" className="mt-4 space-y-3 border-t border-input pt-4">
      <h4 className="font-semibold">发布候选 v{selectedRevision}</h4>
      {!trials.some(row => row.mode !== 'saved' && row.revision === selectedRevision && row.candidateCommit === selected?.draft?.candidateCommit && row.status === 'succeeded') && <p className="text-sm text-muted-foreground">此候选尚无成功试跑记录。可以明确跳过试跑，直接确认发布。</p>}
      {loading ? <p role="status">正在核对工作流发布范围…</p> : target && <p>{target.label}</p>}
      <p className="text-sm text-muted-foreground">影响范围包括本次 Diff 中的工作流、脚本和资源。业务效果及未覆盖检查将在生效后观察；无有效验证规则的问题不会自动宣称已修复。</p>
      <label className="flex items-start gap-2 text-sm leading-6"><input type="checkbox" checked={ack} onChange={event => setAck(event.target.checked)} />我已审阅完整 Diff、检查报告和影响范围，接受未覆盖风险并确认发布此版本。</label>
    </div>}
    {succeeded && <RepairVerification detail={detail} deployment={deployment} canEdit={canEdit} />}
    <footer aria-label="当前修复步骤与下一步" className="fixed bottom-3 left-6 right-6 z-50 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-input bg-card px-5 py-3 shadow-lg">
      <div><p className="text-sm font-semibold">{title}</p><p className="text-xs text-muted-foreground">生成优化草稿 → 审阅并确认发布 → 自动验证</p></div>
      <div className="flex items-center gap-3">
        {trialConfigured && canEdit && isCurrent && selected?.phase === 'review' && !pending && !succeeded && <button className={button} disabled={busy} onClick={() => setTrialOpen(true)}>先试跑</button>}
        {dialog && <button className={button} disabled={busy} onClick={() => setDialog(false)}>返回审阅</button>}
        {pending && deployment.target.scope === 'workflow' && <button className={button} disabled={busy || !canEdit} onClick={() => void resume()}>继续原发布</button>}
        {!succeeded && <button className={primary} disabled={busy || loading || (pending ? !canEdit : !canPublish || dialog && (!ack || !target))}
          onClick={() => { if (pending) void reconcile(); else if (dialog) void publish(); else void open() }}>
          {busy ? '提交中…' : pending ? '核对发布状态' : dialog ? '确认发布' : '发布此版本'}</button>}
      </div>
    </footer>
  </section>
}
