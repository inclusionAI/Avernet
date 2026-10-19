import { useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'
const labels = { ready:'已整理',queued:'建议整理排队中',organizing:'正在整理建议',retryable:'整理待重试',unavailable:'整理服务暂不可用，可单独处理一条原建议，或恢复服务后重试',uncertain:'待整合',stale:'旧基线建议：生成时会保留原始证据，基于最新生效 Pack 重新评估，不直接套用旧修改' }
export default function ProposalEvidence({item,canEdit=false}:{item:RepairInboxItem;canEdit?:boolean}) {
  const [retry,setRetry]=useState('')
  const plan=item.aggregation
  if(!plan)return null
  return <div className="mt-2 text-xs text-muted-foreground">
    {plan.status!=='ready'&&<p role="status">{labels[plan.status]}</p>}
    {plan.reevaluation&&plan.status!=='stale'&&<p role="status">{labels.stale}</p>}
    {canEdit&&['unavailable','retryable'].includes(plan.status)&&<button type="button" disabled={retry==='正在请求重试'} className="text-primary underline" onClick={async()=>{setRetry('正在请求重试');try{await repairBatches.retryProposalIntegration(item.workflowId);setRetry('已请求重试，整理结果将由后台更新')}catch{setRetry('暂时无法重试整理，请稍后重试')}}}>重试整理</button>}
    {retry&&<p role="status">{retry}</p>}
    {plan.causes?.length ? <details className="mt-1"><summary>原因与待确认项</summary>{plan.causes.map((cause,i)=><p key={i}>{ {supported:'证据支持',hypothesis:'推测',unknown:'未知'}[cause.certainty]}：{cause.title}</p>)}{plan.unknowns?.map((text,i)=><p key={i}>{text}</p>)}</details> : null}
    <details className="mt-1"><summary className="cursor-pointer">原始建议与证据（{plan.originals.length}）</summary>
      {plan.originals.map(original=><article key={original.itemId} className="mt-2 border-t border-border pt-2">
        <p className="font-medium text-foreground">{String(original.proposal?.summary||'原建议')}</p>
        <p className="whitespace-pre-wrap break-words">{original.instruction}</p>
        {plan.causes?.length ? <details className="mt-1"><summary>原因与待确认项</summary>{plan.causes.map((cause,i)=><p key={i}>{ {supported:'证据支持',hypothesis:'推测',unknown:'未知'}[cause.certainty]}：{cause.title}</p>)}{plan.unknowns?.map((text,i)=><p key={i}>{text}</p>)}</details> : null}
    {original.readSummary ? <OriginalDetail key={`${plan.fingerprint}:${original.itemId}`} item={original}/> : <details className="mt-1"><summary>完整修改、来源与证据</summary><pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify({itemId:original.itemId,contentRevision:original.contentRevision,state:original.state,proposal:original.proposal,sources:original.sources,context:original.context},null,2)}</pre></details>}
      </article>)}
    </details>
  </div>
}

function OriginalDetail({item}:{item:RepairInboxItem}) {
 const [detail,setDetail]=useState<RepairInboxItem|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(false)
 return <div className="mt-2">
  <p>摘要 · {item.readSummary?.sourceCount} 条来源 · {item.readSummary?.diagnosisCount} 条诊断；完整内容按需读取。</p>
  <button type="button" disabled={loading} className="text-primary underline" onClick={async()=>{setLoading(true);setError('');try{setDetail(await repairBatches.item(item.workflowId,item.itemId,true))}catch{setError('完整证据读取失败，请重试。')}finally{setLoading(false)}}}>{loading?'正在读取完整证据':'读取完整修改、来源与证据'}</button>
  {error&&<p role="alert">{error}</p>}
  {detail&&<pre className="max-h-72 overflow-auto whitespace-pre-wrap break-all">{JSON.stringify(detail,null,2)}</pre>}
 </div>
}
