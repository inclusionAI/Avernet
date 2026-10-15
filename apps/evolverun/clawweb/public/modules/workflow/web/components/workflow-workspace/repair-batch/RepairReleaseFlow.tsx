import { useEffect, useRef, useState } from 'react'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import type { RepairReleaseTarget } from '../../../../server/contracts/repair-release'
import { repairBatches } from '../../../api/repair-batches'
import { button, primary } from './repair-view'
import RepairVerification from './RepairVerification'
import { repairReleaseError } from '../../../api/repair-release-error'

export default function RepairReleaseFlow({ detail, selectedRevision, canEdit, onRefresh }: {
  detail: RepairTaskDetail; selectedRevision: number; canEdit: boolean; onRefresh: () => void;
}) {
  const [dialog, setDialog] = useState<'confirm' | 'deploy' | 'stop' | null>(null)
  const [targets, setTargets] = useState<RepairReleaseTarget[]>([])
  const [targetId, setTargetId] = useState('')
  const [ack, setAck] = useState(false)
  const [busy, setBusy] = useState(false)
  const [loadingTargets, setLoadingTargets] = useState(false)
  const [error, setError] = useState('')
  const request = useRef({ key: '', id: '' })
  const panel = useRef<HTMLElement>(null)
  const approval = detail.release?.approval?.status === 'approved' && detail.release.approval.revision === selectedRevision
    ? detail.release.approval : null
  const deployment = detail.release?.deployments.find(item => item.revision === selectedRevision
    && (item.status !== 'failed' || !approval || item.approvalId === approval.id))
  const revision = detail.latestSuccessful
  const isCurrent = revision?.revision === selectedRevision && detail.latestAttempt.phase !== 'drafting'
  const pending = !!deployment && ['dispatching', 'running', 'uncertain', 'created'].includes(deployment.status)
  const succeeded = deployment?.status === 'succeeded'
  const failed = deployment?.status === 'failed'
  const productionApproved = approval?.target.environment === 'prod'
  const canProceed = canEdit && isCurrent && detail.capabilities.publication && revision?.phase === 'review'
  const refreshRef = useRef(onRefresh)
  const scope = `${detail.taskId}:${selectedRevision}`
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  refreshRef.current = onRefresh
  useEffect(() => {
    setDialog(null); setError(''); setTargets([]); setTargetId(''); setAck(false); setLoadingTargets(false)
    request.current = { key: '', id: '' }
  }, [scope])
  useEffect(() => {
    if (dialog) panel.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' })
  }, [dialog, loadingTargets])
  async function reconcile() {
    if (!deployment || !canEdit) return
    try { await repairBatches.reconcileDeployment(detail.taskId, deployment.id); refreshRef.current() }
    catch (reason) { setError(repairReleaseError(reason)) }
  }
  useEffect(() => {
    if (!pending || !canEdit) return
    const timer = setInterval(() => { void reconcile() }, 15_000)
    return () => clearInterval(timer)
  }, [pending, canEdit, detail.taskId, deployment?.id, deployment?.status])
  function reveal() { panel.current?.scrollIntoView?.({ block: 'center', behavior: 'smooth' }) }
  async function openConfirm() {
    const currentScope = scope
    setDialog('confirm'); setAck(false); setTargetId(''); setTargets([]); setLoadingTargets(true); setError(''); reveal()
    try {
      const response = await repairBatches.deploymentTargets(detail.taskId)
      if (scopeRef.current === currentScope) setTargets(response.targets.filter(target => target.environment === 'prod'))
    } catch (reason) { if (scopeRef.current === currentScope) setError(repairReleaseError(reason)) }
    finally { if (scopeRef.current === currentScope) setLoadingTargets(false) }
  }
  async function submit() {
    if (!revision || busy || !canEdit || !isCurrent || !dialog) return
    if (dialog === 'confirm' && (!ack || !targets.some(target => target.id === targetId && target.environment === 'prod'))) return
    const key = JSON.stringify([dialog, detail.taskId, revision.revision, revision.stateVersion, targetId, ack, approval?.id])
    if (request.current.key !== key) request.current = { key, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try {
      if (dialog === 'confirm') await repairBatches.confirm(detail.taskId, { revision: revision.revision,
        expectedStateVersion: revision.stateVersion, targetId, acknowledgeUncoveredChecks: ack, requestId: request.current.id })
      else if (dialog === 'stop' && deployment) await repairBatches.stopDeployment(detail.taskId, deployment.id, request.current.id)
      else if (dialog === 'deploy' && approval && productionApproved) await repairBatches.deploy(detail.taskId, {
        revision: revision.revision, expectedApprovalVersion: approval.stateVersion, requestId: request.current.id })
      else return
      request.current = { key: '', id: '' }; setDialog(null); onRefresh()
    } catch (reason) { setError(repairReleaseError(reason)); onRefresh() }
    finally { setBusy(false) }
  }
  async function resume() {
    if (!deployment || busy || deployment.target.environment !== 'prod') return
    setBusy(true); setError('')
    try { await repairBatches.resumeDeployment(detail.taskId, deployment.id, crypto.randomUUID()); onRefresh() }
    catch (reason) { setError(repairReleaseError(reason)) }
    finally { setBusy(false) }
  }
  const title = succeeded ? `已部署 #${deployment.receipt?.deployNumber}，下一步验证效果`
    : pending ? deployment.status === 'uncertain' ? '部署结果尚未确认' : '部署正在执行'
    : failed ? '部署未完成，候选已保留' : approval ? `候选 v${approval.revision} 已确认` : '审阅完成后，选择生产部署目标'
  const actionLabel = busy ? '提交中…' : loadingTargets ? '正在读取生产 Bot…'
    : dialog === 'confirm' ? '确认候选' : dialog === 'deploy' ? '确认部署' : dialog === 'stop' ? '确认停止，不回滚'
    : succeeded ? '查看效果验证' : pending ? '核对部署状态'
    : approval && productionApproved ? failed ? '重试部署到生产 Bot' : '部署到生产 Bot'
    : approval ? '更换目标并重新确认' : '审阅完成，选择生产 Bot'
  const footerTitle = dialog === 'confirm' ? `确认候选 v${revision?.revision} 的生产目标`
    : dialog === 'deploy' ? `即将部署候选 v${approval?.revision}` : dialog === 'stop' ? '停止未激活的发布请求' : title
  const actionDisabled = busy || loadingTargets || !canEdit && !succeeded || (!pending && !succeeded && !canProceed)
    || dialog === 'confirm' && (!targetId || !ack || !!error)
  function act() {
    if (dialog) { void submit(); return }
    if (succeeded) { reveal(); return }
    if (pending) { void reconcile(); return }
    if (approval && productionApproved) { setDialog('deploy'); setError(''); reveal(); return }
    void openConfirm()
  }
  return <section ref={panel} aria-label="审阅部署与效果验证" className="scroll-mt-6 rounded-xl border border-input bg-card p-5">
    <div className="flex items-start gap-3"><span className={`mt-1 h-2.5 w-2.5 shrink-0 rounded-full ${failed ? 'bg-destructive' : succeeded ? 'bg-emerald-500' : 'bg-primary'}`} />
      <div><h3 className="text-base font-semibold">{title}</h3><p className="mt-1 text-sm leading-6 text-muted-foreground">
        {succeeded ? '部署成功不代表问题已解决。核对新版本实际运行后，再记录效果结论。'
          : pending ? '请核对同一个发布请求；结果明确前不能更换目标或再次部署。'
          : approval ? `已确认目标：${approval.target.label}。${productionApproved ? '部署不会重新生成候选。' : '该历史目标为非生产 Bot，请重新选择生产目标。'}`
          : '先选择有权限的生产 Bot 并确认候选，再单独确认部署。此时不会改变线上工作流。'}</p></div>
    </div>
    {!isCurrent && !succeeded && <p className="mt-3 text-sm text-warning-foreground">当前查看的是历史候选。请切回最新可用候选后处理。</p>}
    {!detail.capabilities.publication && !succeeded && <p role="alert" className="mt-3 text-sm text-warning-foreground">部署能力未启用，仍可继续审阅。请管理员检查发布配置。</p>}
    {isCurrent && revision?.phase !== 'review' && !pending && !succeeded && <p className="mt-3 text-sm text-warning-foreground">当前候选未处于可部署状态，请核对检查报告与任务状态；无需变更的版本不创建部署。</p>}
    {failed && deployment.error && <div className="mt-4 rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm">
      <p className="font-medium text-destructive">上次部署失败</p><p className="mt-1 leading-6">{deployment.error}</p>
      <p className="mt-1 text-muted-foreground">{productionApproved ? '修复配置后可重试原目标，或更换生产 Bot 并重新确认；不需要重新生成候选。' : '旧预发目标不再支持重试。请选择生产 Bot 并重新确认；不需要重新生成候选。'}</p></div>}
    {error && <div role="alert" className="mt-4 rounded-lg border border-destructive/20 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>}
    {(error && dialog === 'confirm' || !detail.capabilities.publication && !succeeded) && <div className="mt-3 flex items-center gap-4 text-sm">
      <a className="text-primary hover:underline" href="/app-config" target="_blank" rel="noopener noreferrer">查看发布配置</a>
      {dialog === 'confirm' && <button className="text-primary hover:underline" disabled={loadingTargets} onClick={() => void openConfirm()}>重新读取生产 Bot</button>}</div>}
    {dialog && <div role="region" aria-label={dialog === 'confirm' ? '确认审核候选' : dialog === 'deploy' ? '确认部署候选' : '停止部署'} className="mt-5 border-t border-input pt-5">
      <h4 className="text-sm font-semibold">{dialog === 'confirm' ? `确认候选 v${revision?.revision} 与生产目标` : dialog === 'deploy' ? '请确认生产部署' : '停止尚未激活的部署'}</h4>
      {dialog === 'confirm' ? <>
        {loadingTargets ? <p role="status" className="mt-4 text-sm text-muted-foreground">正在读取有权限的生产 Bot…</p>
          : !error && targets.length ? <label className="mt-4 block text-sm">部署目标 Bot<select aria-label="部署目标 Bot" value={targetId}
            onChange={event => setTargetId(event.target.value)} className="mt-2 block w-full rounded-lg border border-input bg-background px-3 py-2.5 text-foreground">
            <option value="">请选择生产 Bot</option>{targets.map(target => <option key={target.id} value={target.id}>{target.label}</option>)}</select></label>
          : !error && <p className="mt-4 rounded-lg bg-muted/50 p-3 text-sm text-muted-foreground">暂无有权限的生产 Bot。需要 Bot 在线、引擎受支持，并具有当前工作流编辑权限；预发 Bot 不在部署范围内。</p>}
        {!error && targets.length > 0 && <label className="mt-4 flex items-start gap-2.5 text-sm leading-6"><input type="checkbox" className="mt-1" checked={ack} onChange={event => setAck(event.target.checked)} />
          我已审阅 Diff 和检查报告；确认后锁定本候选及目标，未覆盖的业务效果将在部署后验证。</label>}
      </> : dialog === 'deploy' ? <div className="mt-3 rounded-lg bg-muted/50 p-4 text-sm leading-6"><p className="font-medium">候选 v{approval?.revision} → {approval?.target.label}</p>
        <p className="mt-2 text-muted-foreground">将影响该生产 Bot 后续新运行；已有运行不切换版本。只部署已确认的完整 Pack，不重新生成。</p></div>
        : <p className="mt-3 text-sm leading-6">停止未激活请求，阻止旧进程继续激活。保留候选和历史，不回滚已部署版本；若实际已部署，会先核对真实结果。</p>}
    </div>}
    {succeeded && <RepairVerification detail={detail} deployment={deployment} canEdit={canEdit} />}
    <footer aria-label="当前修复步骤与下一步" className="fixed bottom-3 left-6 right-6 z-50 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-input bg-card px-5 py-3 shadow-lg">
      <div className="min-w-0"><p className="text-sm font-semibold">{footerTitle}</p><p className="mt-0.5 text-xs text-muted-foreground">{dialog === 'deploy' ? '确认后才会向生产 Bot 发起部署' : dialog === 'confirm' ? '确认候选不会部署' : pending ? '结果未确认，不重复部署' : '审阅 → 确认目标 → 部署 → 验证'}</p></div>
      <div className="flex flex-wrap items-center gap-3">
        {dialog && <button className={button} disabled={busy} onClick={() => setDialog(null)}>返回审阅</button>}
        {!dialog && !pending && !succeeded && approval && productionApproved && <button className={button} disabled={!canProceed || busy} onClick={() => void openConfirm()}>更换部署目标</button>}
        {!dialog && pending && <button className={button} disabled={!canEdit || busy} onClick={() => { setDialog('stop'); reveal() }}>停止此请求</button>}
        {!dialog && deployment?.status === 'uncertain' && deployment.target.environment === 'prod' && <button className={button} disabled={!canEdit || busy} onClick={() => void resume()}>继续原部署</button>}
        <button className={primary} disabled={actionDisabled} onClick={act}>{actionLabel}</button>
      </div>
    </footer>
  </section>
}
