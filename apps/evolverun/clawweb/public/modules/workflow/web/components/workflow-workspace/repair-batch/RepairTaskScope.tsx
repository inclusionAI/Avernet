import type { RepairItem, RepairRevision } from '../../../../server/contracts/repair-batch'
import { JsonDetails } from './repair-view'
function impact(items:RepairItem[]) {
 const labels:Record<string,string>={low:'低',medium:'中',high:'高',critical:'严重'}
 const values=items.flatMap(item=>(Array.isArray(item.context?.diagnoses)?item.context.diagnoses:[]).flatMap(row=>labels[String(row?.severity)]?[labels[String(row.severity)]]:[]))
 return [...new Set(values)].join('、')||'未知'
}
export default function RepairTaskScope({revision}:{revision:RepairRevision}) {
 const flows=new Set(revision.input.items.flatMap(item=>[
  ...item.sources.flatMap(source=>source.kind==='diagnosis_candidate'?[source.flowId]:[]),
  ...(Array.isArray(item.context?.diagnoses)?item.context.diagnoses:[]).flatMap(row=>[row?.flowId,...(Array.isArray(row?.flowIds)?row.flowIds:[])]),
  ...(Array.isArray(item.context?.suggestions)?item.context.suggestions:[]).flatMap(row=>Array.isArray(row?.runIds)?row.runIds:Array.isArray(row?.impactRunIds)?row.impactRunIds:[]),
 ]).filter((id):id is string=>typeof id==='string'&&!!id))
 const plans=revision.input.integration?.plans
 return <section aria-label="所选版本处理范围" className="rounded-lg border border-input bg-card p-5">
  <h3 className="text-base font-semibold">本版处理范围</h3>
  <p className="mt-2 text-sm text-muted-foreground">{revision.input.items.length} 项原始建议 · {flows.size ? `依据覆盖 ${flows.size} 个运行` : '来源运行数未知'} · 近期影响次数未知</p>
  {plans?.length ? <div className="mt-4 space-y-3">{plans.map((plan,index)=><article key={plan.id} className="rounded-lg border border-input p-4">
   <h4 className="text-sm font-medium">方案 {index+1} · {plan.originalItemIds.length} 项来源</h4>
   <p className="mt-1 text-xs text-muted-foreground">诊断影响：{impact(revision.input.items.filter(item=>plan.originalItemIds.includes(item.itemId)))}</p>
   {!plan.goals.length&&<p className="mt-2 text-sm">{String(revision.input.items.find(item=>plan.originalItemIds.includes(item.itemId))?.proposal?.summary||'待补充优化目标')}</p>}
   <ul className="mt-2 list-inside list-disc text-sm">{plan.goals.map((goal,i)=><li key={i}>{goal}</li>)}</ul>
   {(plan.causes?.length||plan.unknowns?.length)?<details className="mt-2 text-xs text-muted-foreground"><summary>原因与依据</summary>{plan.causes?.map((cause,i)=><p key={i} className="mt-2">{{supported:'证据支持',hypothesis:'推测',unknown:'未知'}[cause.certainty]}：{cause.title}</p>)}{plan.unknowns?.map((unknown,i)=><p key={i} className="mt-2">待确认：{unknown}</p>)}</details>:null}
   <details className="mt-2 text-xs text-muted-foreground"><summary>来源建议与依据</summary>{revision.input.items.filter(item=>plan.originalItemIds.includes(item.itemId)).map(item=><p key={item.itemId} className="mt-2">{String(item.proposal?.summary||item.instruction||'原始建议')}</p>)}<JsonDetails title="完整来源与证据" value={revision.input.items.filter(item=>plan.originalItemIds.includes(item.itemId))}/></details>
  </article>)}</div> : <ul className="mt-4 space-y-3">{revision.input.items.map(item=><li key={item.itemId} className="rounded-lg border border-input p-4 text-sm">
    <p className="font-medium">{String(item.proposal?.summary||item.instruction||'本版优化项')}</p>
    <p className="mt-1 text-xs text-muted-foreground">诊断影响：{impact([item])}</p>
    <p className="mt-1 text-xs text-muted-foreground">相关节点：{typeof item.context?.nodeId==='string'?item.context.nodeId:'未知'}</p>
    <JsonDetails title="原始建议与证据" value={item}/>
  </li>)}</ul>}
  <details className="mt-4 text-xs text-muted-foreground"><summary>基线与来源版本</summary><p className="mt-2">基线部署 #{revision.input.baseline.activeDeployNumber??'未知'}</p><JsonDetails title="完整版本记录" value={revision.input.baseline}/></details>
 </section>
}
