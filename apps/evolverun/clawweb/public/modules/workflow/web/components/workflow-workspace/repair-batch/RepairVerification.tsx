import { useEffect, useId, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import type { RepairDeployment } from '../../../../server/contracts/repair-release'
import { repairBatches } from '../../../api/repair-batches'
import { repairReadError } from '../../../api/repair-read-error'
export default function RepairVerification({ detail, deployment, onSummary }: {
 detail: RepairTaskDetail; deployment: RepairDeployment; canEdit: boolean; onSummary?: (summary:string)=>void;
}) {
 const query=useQuery({queryKey:['repair-verification',detail.taskId,deployment.id],queryFn:()=>repairBatches.verification(detail.taskId,deployment.id),retry:false,refetchInterval:15_000})
 const evidence=query.data?.evidence
 const frozen=detail.revisions.find(row=>row.revision===deployment.revision)??(detail.latestSuccessful?.revision===deployment.revision?detail.latestSuccessful:null)
 const evidenceId=useId(),evidenceRef=useRef<HTMLDetailsElement>(null)
 const assessments=evidence?.automatic??[]
 const hasSamples=assessments.length?assessments.some(row=>row.result==='verified'||row.eligibleSamples>0||(row.sampleSummary?.related??0)>0
  ||((!row.observedDeployNumber||row.observedDeployNumber===evidence?.deployNumber)&&(evidence?.total??0)>0)):(evidence?.total??0)>0
 const conclusion=assessments.some(row=>row.result==='ineffective')?'原问题复现，需继续优化。'
  : !hasSamples?'暂无新版本运行样本，暂不能判断效果。'
  : !assessments.length||assessments.every(row=>!row.rule)?'暂无适用验证规则，暂不能判断效果。'
  : assessments.every(row=>row.result==='verified')?'现有逐项验证规则已满足，结论仅限这些问题。':'观察中，尚未满足判定条件。'
 const summary=query.error?'观察读取失败':query.isPending?'观察证据读取中':!evidence?'观察证据未知':assessments.some(row=>row.result==='ineffective')?'原问题复现':assessments.length&&assessments.every(row=>row.result==='verified')?'符合现有验证规则':hasSamples&&(!assessments.length||assessments.every(row=>!row.rule))?'尚无适用验证规则':'观察中'
 useEffect(()=>{onSummary?.(summary)},[onSummary,summary])
 return <div className="min-w-0 space-y-4">
  <h3 className="text-base font-semibold">效果观察</h3>
  {deployment.receipt&&<div className="flex flex-wrap items-center justify-between gap-3 text-sm text-muted-foreground">
   <p>观察部署 #{deployment.receipt.deployNumber}{deployment.baselineDeployNumber>0?` · 发布前基线 #${deployment.baselineDeployNumber}`:' · 发布前基线未知'}</p>
   {deployment.baselineDeployNumber>0&&<a className="text-primary underline" href={`/workflows/workspace?workflowId=${encodeURIComponent(detail.workflowId)}&tab=overview&view=version-comparison&baseline=${deployment.baselineDeployNumber}&current=${deployment.receipt.deployNumber}`}>查看版本对比</a>}
  </div>}
  {query.isPending&&<p role="status">正在读取实际运行证据…</p>}
  {query.error&&<div role="alert" className="space-y-2 rounded-lg border border-destructive/30 p-4 text-sm"><p className="font-medium">观察数据读取失败，暂不能判断效果。</p><p className="text-muted-foreground">{repairReadError(query.error)}</p>{evidence&&<p>保留上次读取的证据，本次未更新。</p>}<button className="text-primary underline" onClick={()=>void query.refetch()}>重试读取证据</button></div>}
  {evidence&&<>
   <p role="status" className="rounded-lg bg-muted p-4 text-sm font-semibold">{conclusion}</p>
   <p className="text-xs text-muted-foreground">已记录 {evidence.total} 个对应版本运行 · 排除 {evidence.excludedUnknownVersions} 个版本不明运行</p>
   {assessments.map(row=><article key={row.itemId} className="rounded-lg border border-input p-4 text-sm">
    <h4 className="font-semibold">{String(frozen?.input.items.find(item=>item.itemId===row.itemId)?.proposal?.summary??'本版优化项')}</h4>
    <p className="mt-2 font-medium">{row.result==='verified'?'符合验证规则':row.result==='ineffective'?'原问题复现，重新进入优化':'尚不能判定'}</p>
    {row.observedDeployNumber&&row.observedDeployNumber!==evidence.deployNumber&&<p className="mt-2 text-warning-foreground">当前观察部署 #{row.observedDeployNumber}，与原发布部署 #{evidence.deployNumber} 不同，请核对版本证据。</p>}
    {row.rule?<>
     <p className="mt-2">有效样本 {row.eligibleSamples}/{row.rule.minimumSamples} · 观察窗口 {row.rule.windowDays} 天</p>
     {row.sampleSummary&&<p className="mt-1 text-xs text-muted-foreground">相关节点已完成 {row.sampleSummary.related} 次 · 缺少完整分析 {row.sampleSummary.missingAnalysis} 次 · 节点未成功 {row.sampleSummary.unsuccessfulNode} 次（可能重叠）</p>}
    </>:<p className="mt-2 text-muted-foreground">此项暂无适用验证规则。</p>}
    <details className="mt-3 text-xs text-muted-foreground"><summary className="cursor-pointer">判定依据与规则</summary><p className="mt-2 whitespace-pre-wrap">{row.reason}</p>{row.rule&&<p className="mt-2 break-all">{row.rule.version} · 节点 {row.rule.nodeId} · 签名 {row.rule.signature}</p>}
     <a className="mt-2 inline-block text-primary underline" href={`#${evidenceId}`} onClick={()=>{if(evidenceRef.current)evidenceRef.current.open=true}}>查看实际运行与证据</a>
    </details>
   </article>)}
   <details id={evidenceId} ref={evidenceRef} className="text-xs text-muted-foreground"><summary className="cursor-pointer text-sm">实际运行与审计证据</summary>
    <ul className="mt-3 space-y-3">{evidence.samples.map(sample=><li key={sample.flowId} className="break-words"><a className="text-primary underline" href={`/runs/${encodeURIComponent(sample.flowId)}`} target="_blank" rel="noreferrer">{sample.flowId}</a> · {sample.status} · 部署 #{sample.deployNumber}<p>{sample.nodes.map(node=>`${node.nodeId} ${node.status}`).join('；')||'无相关节点证据'}</p></li>)}</ul>
    <ul className="mt-3 space-y-3">{query.data?.conclusions.filter(row=>row.deploymentId===deployment.id).map(row=><li key={row.id}>{row.result} · {row.reason}<p className="break-all">{row.actorId} · {row.flowIds.join('、')}</p></li>)}</ul>
   </details>
  </>}
 </div>
}
