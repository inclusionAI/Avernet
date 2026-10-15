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
  const [error, setError] = useState('')
  const request = useRef({ key: '', id: '' })
  const approval = detail.release?.approval?.status === 'approved' ? detail.release.approval : null
  const deployment = detail.release?.deployments[0]
  const revision = detail.latestSuccessful
  const isCurrent = revision?.revision === selectedRevision && ['review', 'failed', 'no_change', 'blocked', 'published', 'publishing'].includes(detail.latestAttempt.phase)
  const pending = deployment && ['dispatching', 'running', 'uncertain', 'created'].includes(deployment.status)
  useEffect(() => { setDialog(null); setError(''); request.current = { key: '', id: '' } }, [detail.taskId, selectedRevision])
  const refreshRef = useRef(onRefresh)
  refreshRef.current = onRefresh
  async function reconcile() {
    if (!deployment || !canEdit) return
    try {
      const current = await repairBatches.reconcileDeployment(detail.taskId, deployment.id)
      if (current.status !== deployment.status) refreshRef.current()
    } catch (reason) { setError(repairReleaseError(reason)) }
  }
  useEffect(() => {
    if (!pending || !canEdit) return
    const timer = setInterval(() => { void reconcile() }, 15_000)
    return () => clearInterval(timer)
  }, [pending, canEdit, detail.taskId, deployment?.id, deployment?.status])
  async function openConfirm() {
    setDialog('confirm'); setAck(false); setTargetId(''); setTargets([]); setBusy(true); setError('')
    try { setTargets((await repairBatches.deploymentTargets(detail.taskId)).targets) }
    catch (reason) { setError(repairReleaseError(reason)) }
    finally { setBusy(false) }
  }
  async function submit() {
    if (!revision || busy || !canEdit || !isCurrent) return
    const key = JSON.stringify([dialog, detail.taskId, revision.revision, revision.stateVersion, targetId, ack, approval?.id])
    if (request.current.key !== key) request.current = { key, id: crypto.randomUUID() }
    setBusy(true); setError('')
    try {
      if (dialog === 'confirm') await repairBatches.confirm(detail.taskId, { revision: revision.revision,
        expectedStateVersion: revision.stateVersion, targetId, acknowledgeUncoveredChecks: ack, requestId: request.current.id })
      else if (dialog === 'stop' && deployment) await repairBatches.stopDeployment(detail.taskId, deployment.id, request.current.id)
      else if (dialog === 'deploy' && approval) await repairBatches.deploy(detail.taskId, { revision: revision.revision,
        expectedApprovalVersion: approval.stateVersion, requestId: request.current.id })
      request.current = { key: '', id: '' }; setDialog(null); onRefresh()
    } catch (reason) { setError(repairReleaseError(reason)); onRefresh() }
    finally { setBusy(false) }
  }
  async function revoke() {
    if (!approval || busy) return
    setBusy(true); setError('')
    try { await repairBatches.revokeConfirmation(detail.taskId, { revision: approval.revision, expectedApprovalVersion: approval.stateVersion, requestId: crypto.randomUUID() }); onRefresh() }
    catch (reason) { setError(repairReleaseError(reason)) }
    finally { setBusy(false) }
  }
  async function resume() {
    if (!deployment || busy) return
    setBusy(true); setError('')
    try { await repairBatches.resumeDeployment(detail.taskId, deployment.id, crypto.randomUUID()); onRefresh() }
    catch (reason) { setError(repairReleaseError(reason)) }
    finally { setBusy(false) }
  }
  const succeeded = deployment?.status === 'succeeded'
  return <section aria-label="审阅部署与效果验证" className="rounded-lg border border-input bg-card p-5">
    <div className="flex flex-wrap items-center justify-between gap-4">
      <div><h3 className="text-base font-semibold">{succeeded ? `已部署 #${deployment.receipt?.deployNumber}，下一步验证效果` : pending ? '部署已提交，请核对执行结果' : approval ? `候选 v${approval.revision} 已确认，下一步部署` : '审阅完成后，确认此候选'}</h3>
        <p className="mt-1 text-sm text-muted-foreground">{succeeded ? '部署成功不代表问题已解决。等待新版本实际运行，再逐项记录结论。' : pending ? '不重复派发，不生成新候选。只核对这一发布请求的真实结果。' : approval ? `目标：${approval.target.label}。部署使用已确认的完整 Pack，不会重新生成内容。` : '确认会锁定候选 commit、检查报告和目标 Bot；不会直接部署。'}</p></div>
      {!succeeded && !pending && <button className={primary} disabled={!canEdit || busy || !isCurrent || !detail.capabilities.publication || revision?.phase !== 'review'}
        onClick={() => approval ? setDialog('deploy') : void openConfirm()}>{approval ? deployment?.status === 'failed' ? '重试部署此候选' : '部署此候选' : '确认候选'}</button>}
      {approval && !deployment && <button className={button} disabled={!canEdit || busy || !isCurrent} onClick={() => void revoke()}>撤销确认</button>}
      {pending && <button className={button} disabled={busy || !canEdit} onClick={() => { void reconcile().then(onRefresh) }}>核对部署状态</button>}
      {deployment?.status === 'uncertain' && <button className={primary} disabled={busy || !canEdit} onClick={() => void resume()}>继续完成此部署</button>}
      {pending && <button className={button} disabled={busy || !canEdit} onClick={() => setDialog('stop')}>停止未激活的部署</button>}
    </div>
    {!isCurrent && !succeeded && <p className="mt-2 text-sm text-warning-foreground">当前查看的是历史候选。请切回最新候选后处理。</p>}
    {!detail.capabilities.publication && !succeeded && <p className="mt-2 text-sm text-warning-foreground">当前无法部署：请管理员配置审核候选发布能力；可继续审阅，不会隐式部署。</p>}
    {deployment?.error && <p role="alert" className="mt-2 text-sm text-destructive">{deployment.error}</p>}
    {error && <p role="alert" className="mt-2 text-sm text-destructive">{error}</p>}
    {succeeded && <RepairVerification detail={detail} deployment={deployment} canEdit={canEdit} />}
    {dialog && <div role="dialog" aria-label={dialog === 'stop' ? '停止未激活的部署' : dialog === 'confirm' ? '确认审核候选' : '确认部署候选'} className="mt-5 rounded-lg border border-primary/30 bg-background p-4">
      <h4 className="font-semibold">{dialog === 'stop' ? '停止未激活的部署' : dialog === 'confirm' ? '确认审核候选' : '确认部署候选'}</h4>
      <p className="mt-2 text-sm">v{revision?.revision} · commit <code>{String(revision?.draft?.candidateCommit ?? '').slice(0, 12)}</code></p>
      {dialog === 'confirm' ? <>
        <label className="mt-3 block text-sm">部署目标<select aria-label="部署目标 Bot" value={targetId} onChange={event => setTargetId(event.target.value)} className="ml-3 rounded border border-input bg-background p-2 text-foreground"><option value="">请选择目标 Bot</option>{targets.map(target => <option key={target.id} value={target.id}>{target.label}</option>)}</select></label>
        {!busy && !targets.length && !error && <p className="mt-2 text-sm text-warning-foreground">没有可部署的目标。需要可访问、支持审核发布且具有此工作流编辑权限的 Bot。</p>}
        <label className="mt-3 flex items-start gap-2 text-sm"><input type="checkbox" checked={ack} onChange={event => setAck(event.target.checked)} />我已审阅完整 Diff 与检查报告，知悉未覆盖的业务验证需要在部署后核对。</label>
      </> : dialog === 'stop' ? <p className="mt-3 text-sm">仅停止尚未激活的发布请求，并阻止迟到执行激活。保留候选和历史，不回滚当前版本。若实际部署已完成，将核对部署事实而不是取消。</p> : <p className="mt-3 text-sm">即将把此完整 Pack 部署到 {approval?.target.label}，影响后续新运行。已有运行不切换版本。确认后才发起部署。</p>}
      <div className="mt-4 flex justify-end gap-2"><button className={button} disabled={busy} onClick={() => setDialog(null)}>返回审阅</button>
        <button className={primary} disabled={busy || dialog === 'confirm' && (!targetId || !ack)} onClick={() => void submit()}>{busy ? '提交中…' : dialog === 'stop' ? '确认停止，不回滚' : dialog === 'confirm' ? '仅确认候选' : '确认并部署'}</button></div>
    </div>}
  </section>
}
