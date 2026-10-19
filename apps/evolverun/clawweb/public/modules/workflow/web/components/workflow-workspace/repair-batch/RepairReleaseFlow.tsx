import RepairTrialPanel from './RepairTrialPanel'
import type { RepairTrial } from '../../../../server/contracts/repair-trial'
import { useEffect, useRef, useState } from 'react'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import type { RepairConfirmRequest, RepairReleaseTarget } from '../../../../server/contracts/repair-release'
import { repairBatches } from '../../../api/repair-batches'
import { button, primary, RepairDialog } from './repair-view'
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
    const current = scope
    submitting.current = true; setBusy(true)
    try { await repairBatches.reconcileDeployment(detail.taskId, deployment.id); onRefresh() }
    catch (reason) { if (scopeRef.current === current) setError(repairReleaseError(reason)) }
    finally { submitting.current = false; setBusy(false) }
  }
  async function open() {
    const current = scope
    setDialog(true); setTarget(null); setAck(false); setError(''); setLoading(true)
    try {
      const response = await repairBatches.deploymentTargets(detail.taskId)
      const scopes = response.targets.filter(row => row.scope === 'workflow')
      if (scopeRef.current !== current) return
      if (scopes.length !== 1) {
        setError(scopes.length === 0 ? '没有可发布的工作流范围，请核对工作流权限和发布配置；尚未提交发布。' : '发布范围配置不明确，请管理员核对；尚未提交发布。')
        return
      }
      setTarget(scopes[0])
    } catch (reason) { if (scopeRef.current === current) setError(repairReleaseError(reason, 'prepare')) }
    finally { if (scopeRef.current === current) setLoading(false) }
  }
  async function publish() {
    if (!canPublish || !target || !ack || !revision || submitting.current) return
    const current = scope
    submitting.current = true; setBusy(true); setError('')
    request.current ??= { revision: revision.revision, expectedStateVersion: revision.stateVersion,
      requestId: crypto.randomUUID(), targetId: target.id, acknowledgeUncoveredChecks: ack }
    try {
      await repairBatches.publish(detail.taskId, request.current)
      if (scopeRef.current === current) { setDialog(false); request.current = null }
      onRefresh()
    } catch (reason) { if (scopeRef.current === current) setError(repairReleaseError(reason)); onRefresh() }
    finally { submitting.current = false; setBusy(false) }
  }
  async function resume() {
    if (!deployment || submitting.current || !canEdit) return
    const current = scope
    submitting.current = true; setBusy(true)
    try { await repairBatches.resumeDeployment(detail.taskId, deployment.id, crypto.randomUUID()); onRefresh() }
    catch (reason) { if (scopeRef.current === current) setError(repairReleaseError(reason)) }
    finally { submitting.current = false; setBusy(false) }
  }
  const title = succeeded ? `此候选已发布为部署 #${deployment.receipt?.deployNumber}`
    : pending ? '发布结果待核对' : deployment?.status === 'failed' ? '发布未完成，候选已保留' : '审阅并确认发布'
  const selected = detail.revisions.find(row => row.revision === selectedRevision) ?? (detail.latestAttempt.revision === selectedRevision ? detail.latestAttempt : detail.latestSuccessful?.revision === selectedRevision ? detail.latestSuccessful : undefined)
  if (selected && (['failed', 'drafting', 'cancelled'].includes(selected.phase) || selected.phase === 'no_change' && !automaticReady) && !succeeded && !pending) return <section aria-label="候选发布状态" className="rounded-xl border border-input bg-card p-5 text-sm text-muted-foreground">
    <h3 className="font-semibold text-foreground">{selected.phase === 'failed' ? '所选版本生成失败，尚无可发布候选' : selected.phase === 'no_change' ? detail.automaticProgress && detail.automaticProgress.completedBatches < detail.automaticProgress.totalBatches ? '本轮尚未完整核对' : '本轮无需重新发布' : selected.phase === 'cancelled' ? '任务已取消' : '正在生成候选，暂不进入发布'}</h3>
    <p className="mt-2">{selected.phase === 'failed' ? '查看本版本错误和最近任务状态；历史失败版本不会因后续成功而变为成功。' : '查看本版本范围与逐项结果；此入口不执行生成或发布。'}</p>
    {!!detail.release?.deployments.some(row => row.status === 'succeeded') && <p className="mt-2">已有生效版本的发布与观察记录保留，切换对应候选版本查看。</p>}
  </section>
  return <section aria-label="审阅部署与效果验证" className="rounded-xl border border-input bg-card p-5">
    <h3 className="text-base font-semibold">{title}</h3>
    <p className="mt-2 text-sm leading-6 text-muted-foreground">{succeeded ? '在发布后效果页查看对应版本的样本与判定。'
      : pending ? '先核对同一个发布请求，结果未明确前不重复发布。'
      : '审阅改动和检查范围后确认发布。试跑可选，发布后再观察业务效果。'}</p>
    {automatic && !automaticReady && !succeeded && <p className="mt-3 text-sm text-muted-foreground">本轮尚未完整校验，暂不可发布；保留各项进度与结果。</p>}
    {!isCurrent && !succeeded && <p className="mt-3 text-sm text-muted-foreground">当前查看的是历史候选，请切回最新可用候选。</p>}
    {!detail.capabilities.publication && <p role="alert">发布能力未启用，请检查宿主配置。</p>}
    {deployment?.error && <p className="mt-3 text-sm text-destructive">{deployment.error}</p>}
    {error && !dialog && <p role="alert" className="mt-3 text-sm text-destructive">{error}</p>}
    {selected?.draft?.candidateCommit && <RepairTrialPanel taskId={detail.taskId} revision={selected} enabled={!!canEdit && !!isCurrent && selected.phase === 'review'} open={trialOpen} onClose={() => setTrialOpen(false)} onTrials={(rows, configured) => { setTrials(rows); setTrialConfigured(configured) }} />}
    {dialog && <RepairDialog title={`审阅并发布候选 v${selectedRevision}`} busy={busy} onClose={() => setDialog(false)} footer={<div className="flex flex-wrap justify-end gap-3">
      <button className={button} disabled={busy} onClick={() => setDialog(false)}>返回查看改动</button>
      <button className={primary} disabled={busy || loading || !canPublish || !ack || !target} onClick={() => void publish()}>{busy ? '正在提交发布…' : '确认审阅并发布'}</button>
    </div>}><div className="space-y-3">
      {!trials.some(row => row.mode !== 'saved' && row.revision === selectedRevision && row.candidateCommit === selected?.draft?.candidateCommit && row.status === 'succeeded') && <p className="text-sm text-muted-foreground">此候选尚无成功试跑记录。可以明确跳过试跑，直接确认发布。</p>}
      {loading ? <p role="status">正在核对工作流发布范围…</p> : target && <p>{target.label}</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {!loading && !target && <button className={button} onClick={() => void open()}>重新读取发布信息</button>}
      <p className="text-sm text-muted-foreground">影响范围包括本次 Diff 中的工作流、脚本和资源。业务效果及未覆盖检查将在生效后观察；无有效验证规则的问题不会自动宣称已修复。</p>
      <label className="flex items-start gap-2 text-sm leading-6"><input type="checkbox" checked={ack} onChange={event => setAck(event.target.checked)} />我已审阅完整 Diff、检查报告和影响范围，接受未覆盖风险并确认发布此版本。</label>
    </div></RepairDialog>}
    {!succeeded && <footer aria-label="当前修复步骤与下一步" className="mt-4 flex flex-wrap items-center justify-end gap-3 border-t border-input pt-4">
      <div className="flex items-center gap-3">
        {trialConfigured && canEdit && isCurrent && selected?.phase === 'review' && !pending && !succeeded && <button className={button} disabled={busy} onClick={() => setTrialOpen(true)}>先试跑</button>}
        {pending && deployment.target.scope === 'workflow' && <button className={button} disabled={busy || !canEdit} onClick={() => void resume()}>继续原发布</button>}
        {!succeeded && <button className={primary} disabled={trialOpen || dialog || busy || (pending ? !canEdit : !canPublish)}
          onClick={() => { if (pending) void reconcile(); else void open() }}>
          {busy ? '提交中…' : pending ? '核对发布状态' : '审阅并发布'}</button>}
      </div>
    </footer>}
  </section>
}
