import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import type { RepairDeployment } from '../../../../server/contracts/repair-release'
import { repairBatches } from '../../../api/repair-batches'
import { button, primary } from './repair-view'
import { repairReleaseError } from '../../../api/repair-release-error'

export default function RepairVerification({ detail, deployment, canEdit }: { detail: RepairTaskDetail; deployment: RepairDeployment; canEdit: boolean }) {
  const [itemId, setItemId] = useState('')
  const [result, setResult] = useState<'verified' | 'ineffective' | 'inconclusive'>('inconclusive')
  const [reason, setReason] = useState('')
  const [flowIds, setFlowIds] = useState<string[]>([])
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const evidence = useQuery({ queryKey: ['repair-verification', detail.taskId, deployment.id],
    queryFn: () => repairBatches.verification(detail.taskId, deployment.id), refetchInterval: 30_000 })
  const revision = detail.revisions.find(row => row.revision === deployment.revision)
  const items = revision?.input.items.filter(item => Array.isArray(revision.draft?.itemResults)
    && revision.draft.itemResults.some(outcome => outcome.itemId === item.itemId && outcome.status === 'changed')
    && !evidence.data?.conclusions.some(conclusion => conclusion.deploymentId === deployment.id && conclusion.itemId === item.itemId && conclusion.result !== 'inconclusive')) ?? []
  async function conclude() {
    setBusy(true); setError('')
    try {
      const current = await repairBatches.item(detail.workflowId, itemId)
      await repairBatches.conclude(detail.taskId, deployment.id, { requestId: crypto.randomUUID(), itemId,
        expectedStateVersion: current.stateVersion, result, reason, flowIds })
      setReason(''); setFlowIds([]); await evidence.refetch()
    } catch (value) { setError(repairReleaseError(value)) }
    finally { setBusy(false) }
  }
  async function reopen(id: string) {
    setBusy(true); setError('')
    try {
      const current = await repairBatches.item(detail.workflowId, id)
      await repairBatches.reopenItem(detail.taskId, deployment.id, { requestId: crypto.randomUUID(), itemId: id, expectedStateVersion: current.stateVersion })
      setNotice('已重新加入待修复。返回问题列表选择此建议，创建新任务时会读取当前部署作为新基线；本次部署和验证记录仍保留。')
    } catch (value) { setError(repairReleaseError(value)) }
    finally { setBusy(false) }
  }
  return <div className="mt-5 border-t border-border pt-4">
    <div className="flex items-center justify-between"><h4 className="font-semibold">效果验证 · 部署 #{deployment.receipt?.deployNumber}</h4><button className={button} onClick={() => void evidence.refetch()}>刷新运行证据</button></div>
    {evidence.isLoading && <p role="status" className="mt-3 text-sm">读取本部署的运行样本…</p>}
    {evidence.error && <p role="alert" className="mt-3 text-sm text-destructive">证据读取失败，请重试。</p>}
    {evidence.data && <>
      <p className="mt-2 text-sm text-muted-foreground">{evidence.data.evidence.total ? `新部署共 ${evidence.data.evidence.total} 次运行，展示最近 50 次。` : '尚无新版本运行，等待业务正常触发。不自动执行生产业务。'}</p>
      <p className="mt-1 text-xs text-muted-foreground">基线 #{evidence.data.evidence.baseline.deployNumber}：{evidence.data.evidence.baseline.total} 次，成功 {evidence.data.evidence.baseline.succeeded} 次，失败 {evidence.data.evidence.baseline.failed} 次。不同运行条件不能直接比较为改善结论。</p>
      <p className="mt-1 text-xs text-muted-foreground">基线平均耗时：{evidence.data.evidence.baseline.averageDurationMs == null ? '未采集' : `${Math.round(evidence.data.evidence.baseline.averageDurationMs)} ms`}；平均 Token：{evidence.data.evidence.baseline.averageTokens == null ? '未采集' : Math.round(evidence.data.evidence.baseline.averageTokens)}。节点展示每次运行最近 200 条执行记录，完整证据请打开运行详情。</p>
      <p className="mt-2 text-xs text-muted-foreground">{evidence.data.evidence.note}</p>
      <dl className="mt-3 grid grid-cols-2 gap-3 rounded bg-muted/50 p-3 text-sm sm:grid-cols-4">
        <div><dt className="text-muted-foreground">已完成样本成功率</dt><dd>{evidence.data.evidence.metrics.successRate == null ? '暂无已完成样本' : `${(evidence.data.evidence.metrics.successRate * 100).toFixed(1)}%`}</dd></div>
        <div><dt className="text-muted-foreground">平均耗时</dt><dd>{evidence.data.evidence.metrics.averageDurationMs == null ? '未采集' : `${Math.round(evidence.data.evidence.metrics.averageDurationMs)} ms`}</dd></div>
        <div><dt className="text-muted-foreground">平均 Token</dt><dd>{evidence.data.evidence.metrics.averageTokens == null ? '未采集' : Math.round(evidence.data.evidence.metrics.averageTokens)}</dd></div>
        <div><dt className="text-muted-foreground">超时诊断 / 已诊断运行</dt><dd>{evidence.data.evidence.metrics.diagnosedTimeoutRuns} / {evidence.data.evidence.metrics.diagnosedRuns}</dd></div>
      </dl>
      <p className="mt-2 text-xs text-muted-foreground">排除 {evidence.data.evidence.excludedUnknownVersions} 次版本不明运行。样本窗口：{evidence.data.evidence.metrics.window.from == null ? '暂无样本' : `${new Date(evidence.data.evidence.metrics.window.from * (evidence.data.evidence.metrics.window.from < 1e12 ? 1000 : 1)).toLocaleString()} — ${new Date(evidence.data.evidence.metrics.window.to! * (evidence.data.evidence.metrics.window.to! < 1e12 ? 1000 : 1)).toLocaleString()}`}</p>
      <ul className="mt-2 text-xs text-muted-foreground">{evidence.data.evidence.recurrence.map(row => <li key={row.itemId}>原问题签名复现：{row.signature == null ? '缺少可匹配签名' : `${row.matchingRuns} 次；已有 ${row.diagnosedRuns} 次运行诊断，未诊断不代表未复现`}</li>)}</ul>
      <div className="mt-3 max-h-64 space-y-2 overflow-auto">{evidence.data.evidence.samples.map(sample => <label key={sample.flowId} className="flex items-start gap-3 rounded border border-input p-3 text-sm">
        <input type="checkbox" aria-label={`验证样本 ${sample.flowId}`} disabled={!canEdit || sample.completedAt == null} checked={flowIds.includes(sample.flowId)} onChange={() => setFlowIds(ids => ids.includes(sample.flowId) ? ids.filter(id => id !== sample.flowId) : [...ids, sample.flowId])} />
        <div><a className="text-primary underline" href={`/runs/${encodeURIComponent(sample.flowId)}`} target="_blank" rel="noreferrer">{sample.flowId}</a><p>{sample.status} · 部署 #{sample.deployNumber}</p><p className="text-xs text-muted-foreground">节点：{sample.nodes.map(node => `${node.nodeId} ${node.status}`).join('；') || '尚无节点记录'}</p></div>
      </label>)}</div>
      <ul className="mt-3 space-y-2">{evidence.data.conclusions.filter(row => row.deploymentId === deployment.id).map(row => <li key={row.id} className="rounded bg-muted/50 p-3 text-sm">{row.result === 'verified' ? '修复有效' : row.result === 'ineffective' ? '仍未解决' : '暂不能判断'} · {row.reason}<p className="text-xs text-muted-foreground">{row.actorId} · {new Date(row.createdAtMs).toLocaleString()} · {row.flowIds.length} 个样本</p>{row.result === 'ineffective' && canEdit && <button className={`${button} mt-2`} disabled={busy} onClick={() => void reopen(row.itemId)}>重新加入待修复</button>}</li>)}</ul>
    </>}
    {notice && <p role="status" className="mt-3 text-sm text-primary">{notice}</p>}
    {canEdit && items.length > 0 && <fieldset className="mt-4 space-y-3 rounded border border-input p-4"><legend className="px-1 text-sm font-semibold">逐项记录效果结论</legend>
      <select aria-label="验证处理项" value={itemId} onChange={event => setItemId(event.target.value)} className="w-full rounded border border-input bg-background p-2 text-sm text-foreground"><option value="">选择本次已修改的建议</option>{items.map(item => <option key={item.itemId} value={item.itemId}>{String(item.proposal?.summary ?? item.instruction ?? item.itemId)}</option>)}</select>
      <select aria-label="效果结论" value={result} onChange={event => setResult(event.target.value as typeof result)} className="rounded border border-input bg-background p-2 text-sm text-foreground"><option value="inconclusive">暂不能判断，继续观察</option><option value="verified">原问题已解决</option><option value="ineffective">原问题仍存在，需要继续修复</option></select>
      <textarea aria-label="效果验证依据" value={reason} onChange={event => setReason(event.target.value)} placeholder="说明相关节点、原问题是否复现、业务结果及判断依据。不能仅凭一次运行成功关闭问题。" rows={3} maxLength={4000} className="w-full rounded border border-input bg-background p-3 text-sm text-foreground" />
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <button className={primary} disabled={busy || !itemId || !reason.trim() || result !== 'inconclusive' && !flowIds.length} onClick={() => void conclude()}>{busy ? '记录中…' : '记录效果结论'}</button>
    </fieldset>}
  </div>
}
