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
  const frozen = detail.revisions.find(row => row.revision === deployment.revision)
  return <div className="mt-5 space-y-3 border-t border-input pt-4">
    <h4 className="font-semibold">自动效果观察</h4>
    {deployment.status === 'succeeded' && deployment.receipt && deployment.baselineDeployNumber > 0 && <p className="text-sm text-muted-foreground">
      已生效部署 #{deployment.receipt.deployNumber}，相对发布前基线 #{deployment.baselineDeployNumber} 继续观察。
      {' '}<a className="text-primary underline" href={`/workflows/workspace?workflowId=${encodeURIComponent(detail.workflowId)}&tab=overview&view=version-comparison&baseline=${deployment.baselineDeployNumber}&current=${deployment.receipt.deployNumber}`}>查看版本对比</a>
    </p>}
    {query.isPending && <p role="status">正在读取实际运行证据…</p>}
    {query.error && <p role="alert">{repairReadError(query.error)} <button className="text-primary underline" onClick={() => void query.refetch()}>重试</button></p>}
    {evidence && <>
      <p className="text-sm text-muted-foreground">本轮发布：候选 v{deployment.revision} → 部署 #{evidence.deployNumber} · 任务基线部署 #{frozen?.input.baseline.activeDeployNumber ?? '未知'}</p>
      <p className="text-sm text-muted-foreground">已记录 {evidence.total} 个运行 · 排除 {evidence.excludedUnknownVersions} 个版本不明运行。样本来自实际运行，不主动发起业务运行。</p>
      {!evidence.total && <p>观察中：暂无新版本运行样本，不触发业务运行。</p>}
      {(evidence.automatic ?? []).map(row => <article key={row.itemId} className="rounded-lg border border-input p-3 text-sm">
        <h5 className="font-semibold">{String(frozen?.input.items.find(item => item.itemId === row.itemId)?.proposal?.summary ?? '本轮优化项')}</h5>
        <p className="font-medium">{row.result === 'verified' ? '符合验证规则' : row.result === 'ineffective' ? '原问题复现，重新进入优化' : '观察中'}</p>
        <p className="mt-1 leading-6">{row.reason}</p>{row.observedDeployNumber && <p className="mt-1 text-xs text-muted-foreground">观察版本：部署 #{row.observedDeployNumber}</p>}
        {row.rule ? <div aria-label="验证规则与样本" className="mt-2 space-y-1 rounded bg-muted p-3 text-xs text-foreground">
          <p>{row.rule.version} · 节点 {row.rule.nodeId} · 签名 {row.rule.signature}</p>
          <p>滚动 {row.rule.windowDays} 天 · 有效样本 {row.eligibleSamples}/{row.rule.minimumSamples}</p>
          {row.sampleSummary && <p>相关节点已完成 {row.sampleSummary.related} 次；缺少完整分析 {row.sampleSummary.missingAnalysis} 次；节点未成功 {row.sampleSummary.unsuccessfulNode} 次（两类可能重叠）。</p>}
          <p>同一准确生效版本，相关节点成功、完整分析且无原签名才是有效样本；窗口中明确复现即判无效，其他失败或未知证据继续观察。只证明此超时问题满足规则。</p>
          <a href="#repair-verification-evidence" onClick={() => { const target = document.getElementById('repair-verification-evidence'); if (target instanceof HTMLDetailsElement) target.open = true }} className="text-primary underline">查看实际运行与证据</a>
        </div> : <p className="mt-2 text-xs text-muted-foreground">此类问题尚无适用验证器，继续观察；不会凭工作流成功或无诊断判为已修复。</p>}
      </article>)}
      {!evidence.automatic?.length && <p className="text-sm text-muted-foreground">观察中：尚未建立可判定的逐项验证规则。</p>}
      <details id="repair-verification-evidence"><summary className="cursor-pointer text-sm">实际运行与审计证据</summary>
        <ul className="mt-2 space-y-2 text-xs">{evidence.samples.map(sample => <li key={sample.flowId}>
          <a className="text-primary underline" href={`/runs/${encodeURIComponent(sample.flowId)}`} target="_blank" rel="noreferrer">{sample.flowId}</a> · {sample.status} · 部署 #{sample.deployNumber}
          <p className="text-muted-foreground">{sample.nodes.map(node => `${node.nodeId} ${node.status}`).join('；') || '无相关节点证据'}</p>
        </li>)}</ul>
        <ul className="mt-3 space-y-2 text-xs">{query.data?.conclusions.filter(row => row.deploymentId === deployment.id).map(row => <li key={row.id}>{row.result} · {row.reason}<p>{row.actorId} · {row.flowIds.join('、')}</p></li>)}</ul>
      </details>
    </>}
  </div>
}
