import { useQuery } from '@tanstack/react-query'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import type { RepairDeployment } from '../../../../server/contracts/repair-release'
import { repairBatches } from '../../../api/repair-batches'
import { repairReadError } from '../../../api/repair-read-error'
export default function RepairVerification({ detail, deployment }: {
  detail: RepairTaskDetail; deployment: RepairDeployment; canEdit: boolean;
}) {
  const query = useQuery({ queryKey: ['repair-verification', detail.taskId, deployment.id],
    queryFn: () => repairBatches.verification(detail.taskId, deployment.id), retry: false, refetchInterval: 15_000 })
  const evidence = query.data?.evidence
  return <div className="mt-5 space-y-3 border-t border-input pt-4">
    <h4 className="font-semibold">自动效果观察</h4>
    {query.isPending && <p role="status">正在读取实际运行证据…</p>}
    {query.error && <p role="alert">{repairReadError(query.error)} <button className="text-primary underline" onClick={() => void query.refetch()}>重试</button></p>}
    {evidence && <>
      <p className="text-sm text-muted-foreground">部署 #{evidence.deployNumber} · 已记录 {evidence.total} 个运行 · 排除 {evidence.excludedUnknownVersions} 个版本不明运行</p>
      {!evidence.total && <p>观察中：暂无新版本运行样本，不触发业务运行。</p>}
      {(evidence.automatic ?? []).map(row => <article key={row.itemId} className="rounded-lg border border-input p-3 text-sm">
        <p className="font-medium">{row.result === 'verified' ? '符合验证规则' : row.result === 'ineffective' ? '原问题复现，重新进入优化' : '观察中'}</p>
        <p className="mt-1 leading-6">{row.reason}</p>{row.observedDeployNumber && <p className="mt-1 text-xs text-muted-foreground">观察版本：部署 #{row.observedDeployNumber}</p>}
        {row.rule && <details className="mt-2 text-xs text-muted-foreground"><summary className="cursor-pointer">验证规则与阈值</summary>
          <p>{row.rule.version} · 节点 {row.rule.nodeId} · 签名 {row.rule.signature}</p>
          <p>窗口 {row.rule.windowDays} 天 · 至少 {row.rule.minimumSamples} 个有效样本 · 当前 {row.eligibleSamples} 个</p>
          <p>精确部署版本、相关节点完成、有效分析且无原问题签名；缺失分析、未执行节点和旧版本运行不算通过。</p>
        </details>}
      </article>)}
      {!evidence.automatic?.length && <p className="text-sm text-muted-foreground">观察中：尚未建立可判定的逐项验证规则。</p>}
      <details><summary className="cursor-pointer text-sm">实际运行与审计证据</summary>
        <ul className="mt-2 space-y-2 text-xs">{evidence.samples.map(sample => <li key={sample.flowId}>
          <a className="text-primary underline" href={`/runs/${encodeURIComponent(sample.flowId)}`} target="_blank" rel="noreferrer">{sample.flowId}</a> · {sample.status} · 部署 #{sample.deployNumber}
          <p className="text-muted-foreground">{sample.nodes.map(node => `${node.nodeId} ${node.status}`).join('；') || '无相关节点证据'}</p>
        </li>)}</ul>
        <ul className="mt-3 space-y-2 text-xs">{query.data?.conclusions.filter(row => row.deploymentId === deployment.id).map(row => <li key={row.id}>{row.result} · {row.reason}<p>{row.actorId} · {row.flowIds.join('、')}</p></li>)}</ul>
      </details>
    </>}
  </div>
}
