import FrozenRepairEvidence from './FrozenRepairEvidence'
import type { RepairItem, RepairModelInput, RepairRevision } from '../../../../server/contracts/repair-batch'
import { JsonDetails } from './repair-view'
function impact(items:RepairItem[]) {
 const labels:Record<string,string>={low:'低',medium:'中',high:'高',critical:'严重'}
 const summaryLabels=items.map(item=>['','低','中','高','严重'][(item.context?.evidenceSummary as {severity?:number}|undefined)?.severity??0]).filter(Boolean)
 const values=items.flatMap(item=>(Array.isArray(item.context?.diagnoses)?item.context.diagnoses:[]).flatMap(row=>labels[String(row?.severity)]?[labels[String(row.severity)]]:[]))
 return [...new Set([...values,...summaryLabels])].join('、')||'未知'
}
function relatedNodes(items:RepairItem[]) {
 const nodes=items.flatMap(item=>[
  item.context?.nodeId,
  ...(Array.isArray(item.proposal?.operations)?item.proposal.operations:[]).map(operation=>operation?.nodeId),
  ...(Array.isArray(item.context?.diagnoses)?item.context.diagnoses:[]).map(diagnosis=>diagnosis?.nodeId),
  ...(Array.isArray(item.context?.suggestions)?item.context.suggestions:[]).map(suggestion=>suggestion?.nodeId),
 ])
 return [...new Set(nodes.filter((node):node is string=>typeof node==='string'&&!!node.trim()))].join('、')||'未知'
}
function selectedGoal(item:RepairItem) {
 const summary=item.proposal?.summary
 return typeof summary==='string'&&summary.trim()?summary:item.instruction.trim()||'待补充优化目标'
}
export default function RepairTaskScope({revision}:{revision:RepairRevision}) {
 const flows=new Set(revision.input.items.flatMap(item=>[
  ...item.sources.flatMap(source=>source.kind==='diagnosis_candidate'?[source.flowId]:[]),
  ...(Array.isArray(item.context?.diagnoses)?item.context.diagnoses:[]).flatMap(row=>[row?.flowId,...(Array.isArray(row?.flowIds)?row.flowIds:[])]),
  ...(Array.isArray(item.context?.suggestions)?item.context.suggestions:[]).flatMap(row=>Array.isArray(row?.runIds)?row.runIds:Array.isArray(row?.impactRunIds)?row.impactRunIds:[]),
 ]).filter((id):id is string=>typeof id==='string'&&!!id))
 for(const item of revision.input.items){const summary=item.context?.evidenceSummary as {flowIds?:string[]}|undefined;for(const id of summary?.flowIds??[])if(typeof id==='string'&&id)flows.add(id)}
 const plans=revision.input.integration?.plans
 const modelInputs=Array.isArray(revision.draft?.modelInputs)?revision.draft.modelInputs.filter((entry):entry is RepairModelInput=>
  !!entry&&typeof entry.itemId==='string'&&typeof entry.coreInput==='string'&&!!entry.materials&&typeof entry.materials==='object'&&!Array.isArray(entry.materials)):[]
 return <section aria-label="所选版本处理范围" className="rounded-lg border border-input bg-card p-5">
  <h3 className="text-base font-semibold">本版优化目标</h3>
  {revision.input.materials&&<p className="mt-2 text-xs text-muted-foreground">完整原始材料已独立冻结（{(revision.input.materials.bytes/1024/1024).toFixed(2)} MiB）；展开下方原始建议按需查看。</p>}
  <p className="mt-2 text-sm text-muted-foreground">{revision.input.items.length} 项原始建议 · {flows.size ? `依据覆盖 ${flows.size} 个运行` : '来源运行数未知'}</p>
  {plans?.length ? <div className="mt-4 space-y-3">{plans.map((plan,index)=>{
   const items=revision.input.items.filter(item=>plan.originalItemIds.includes(item.itemId))
   return <article key={plan.id} className="rounded-lg border border-input p-4">
   <h4 className="text-sm font-medium">目标组 {index+1} · {items.length} 条建议</h4>
   <ul className="mt-2 list-inside list-disc space-y-1 text-sm">{items.map(item=><li key={item.itemId} className="whitespace-pre-wrap break-words">{selectedGoal(item)}</li>)}</ul>
   <p className="mt-2 text-xs text-muted-foreground">相关节点：{relatedNodes(items)}</p>
   <p className="mt-1 text-xs text-muted-foreground">诊断影响：{impact(items)}</p>
   {(plan.causes?.length||plan.unknowns?.length)?<details className="mt-2 text-xs text-muted-foreground"><summary>原因与依据</summary>{plan.causes?.map((cause,i)=><p key={i} className="mt-2">{{supported:'证据支持',hypothesis:'推测',unknown:'未知'}[cause.certainty]}：{cause.title}</p>)}{plan.unknowns?.map((unknown,i)=><p key={i} className="mt-2">待确认：{unknown}</p>)}</details>:null}
   <details className="mt-2 text-xs text-muted-foreground"><summary>来源建议与依据</summary>{revision.input.materials?items.map(item=><FrozenRepairEvidence key={`${revision.revision}:${item.itemId}`} revision={revision} item={item}/>):<JsonDetails title="完整来源与证据" value={items}/>}</details>
   <JsonDetails title="分组技术详情" value={plan}/>
  </article>})}</div> : <ul className="mt-4 space-y-3">{revision.input.items.map(item=><li key={item.itemId} className="rounded-lg border border-input p-4 text-sm">
    <p className="font-medium">{String(item.proposal?.summary||(item.instruction.startsWith('当前生效 Pack 已变化')?'重新评估原修改的适用性':item.instruction)||'本版优化项')}</p>
    {item.instruction.startsWith('当前生效 Pack 已变化')&&<p className="mt-2 rounded bg-warning/10 p-2 text-xs text-warning-foreground">基线变化提醒：{item.instruction}</p>}
    <p className="mt-1 text-xs text-muted-foreground">诊断影响：{impact([item])}</p>
    <p className="mt-1 text-xs text-muted-foreground">相关节点：{typeof item.context?.nodeId==='string'?item.context.nodeId:'未知'}</p>
    <FrozenRepairEvidence key={`${revision.revision}:${item.itemId}`} revision={revision} item={item}/>
  </li>)}</ul>}
  {(revision.input.instructions||revision.input.feedback)&&<div className="mt-4 text-sm"><h4 className="font-medium">本版要求与反馈</h4>{revision.input.instructions&&<p className="mt-2 whitespace-pre-wrap">{revision.input.instructions}</p>}{revision.input.feedback&&<p className="mt-2 whitespace-pre-wrap">{revision.input.feedback}</p>}</div>}
  <details className="mt-4 border-t border-input pt-3 text-sm">
   <summary className="cursor-pointer text-xs text-muted-foreground">模型输入与材料</summary>
   {modelInputs.length?<><p className="mt-1 text-xs text-muted-foreground">材料路径对应模型调用时的 AIS 本地文件；此处仅展示索引。</p>{modelInputs.map((entry,index)=><article key={index} className="mt-3">
    <p className="text-xs text-muted-foreground">第 {index+1} 次模型调用{entry.phase==='planning'?' · 全局规划':entry.phase==='generation'?' · 生成修改':''}</p>
    <details className="mt-2 text-xs"><summary className="cursor-pointer text-muted-foreground">模型核心输入</summary><pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-muted/50 p-3 text-[11px]">{entry.coreInput}</pre></details>
    <JsonDetails title="可选材料索引" value={entry.materials}/>
   </article>)}</>:<p className="mt-2 text-xs text-muted-foreground">暂无实际模型输入记录；该次结果尚未回传记录、使用旧版生成器，或未调用模型。</p>}
  </details>
  <details className="mt-4 text-xs text-muted-foreground"><summary>基线与来源版本</summary><p className="mt-2">基线部署 #{revision.input.baseline.activeDeployNumber??'未知'}</p><JsonDetails title="完整版本记录" value={revision.input.baseline}/></details>
 </section>
}
