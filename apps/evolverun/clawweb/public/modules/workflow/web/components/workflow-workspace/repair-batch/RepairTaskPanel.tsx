import { repairTaskPresentation } from '../../../../server/contracts/repair-task-presentation'
import { useCallback, useEffect, useId, useRef, useState } from 'react'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import type { RepairRevision } from '../../../../server/contracts/repair-batch'
import { button, primary, JsonDetails } from './repair-view'
import { candidateLabel } from './repair-release-view'
import RepairCandidateReview from './RepairCandidateReview'
import RepairTaskScope from './RepairTaskScope'
import RepairReleaseFlow from './RepairReleaseFlow'
import RepairVerification from './RepairVerification'
const views=[['scope','优化目标'],['candidate','改动与发布'],['observe','发布后效果']] as const
type View=typeof views[number][0]
const executionLabels:Record<string,string>={created:'等待派发',dispatching:'正在派发，结果尚未确认',dispatched:'已派发，等待生成结果',running:'正在生成候选',dispatch_failed:'派发失败',dispatch_uncertain:'派发结果待核对',succeeded:'执行结果已返回',failed:'执行失败',cancelled:'已取消'}
function preference(key:string){try{return JSON.parse(sessionStorage.getItem(key)||'null')}catch{return null}}
function initialRevision(detail:RepairTaskDetail){
 const published=detail.release?.deployments.find(row=>row.status==='succeeded')
 return (['failed','no_change'].includes(detail.latestAttempt.phase)&&published?published.revision:detail.latestSuccessful?.revision)??detail.latestAttempt.revision
}
export default function RepairTaskPanel({detail,canEdit,onFeedback,onNextRound,onCancel,onRetryDispatch,onRetryGeneration,onRefresh,onHistoryPageChange,dispatchError,busy}:{
 detail:RepairTaskDetail;canEdit:boolean;onFeedback:()=>void;onCancel:()=>void;busy:boolean;onRetryGeneration:()=>void;
 onRefresh:()=>void;onRetryDispatch:()=>void;dispatchError:string;onHistoryPageChange?:(page:number)=>void;onNextRound?:(publishedRevision:number)=>void;
}){
 const storageKey=`repair-workspace:${detail.workflowId}:${detail.taskId}`
 const saved=useRef(preference(storageKey))
 const [selectedRevision,setSelectedRevision]=useState<number>(Number.isSafeInteger(saved.current?.revision)?saved.current.revision:initialRevision(detail))
 const [view,setView]=useState<View>(views.some(([key])=>key===saved.current?.view)?saved.current.view:detail.release?.deployments.some(row=>row.revision===initialRevision(detail)&&row.status==='succeeded')?'observe':'candidate')
 const manualSelection=useRef(!!saved.current && saved.current.followLatest!==true)
 const known=useRef(new Map<number,RepairRevision>())
 for(const row of [...detail.revisions,...(detail.latestSuccessful?[detail.latestSuccessful]:[]),detail.latestAttempt])known.current.set(row.revision,row)
 const revision=known.current.get(selectedRevision)??detail.latestSuccessful??detail.latestAttempt
 const revisions=[...known.current.values()].sort((a,b)=>b.revision-a.revision)
 const id=useId(),tabRefs=useRef<Array<HTMLButtonElement|null>>([])
 const latest=detail.latestAttempt
 const deployment=detail.release?.deployments.find(row=>row.revision===revision.revision&&row.status!=='failed')??detail.release?.deployments.find(row=>row.revision===revision.revision)
 const published=deployment?.status==='succeeded'&&!!deployment.receipt
 const alreadyPublished=detail.revisions.some(row=>row.phase==='published')||!!detail.release?.deployments.some(row=>row.status==='succeeded')
 const publishedRevision=published?revision.revision:detail.release?.deployments.find(row=>row.status==='succeeded'&&!!row.receipt)?.revision
 const automatic=!!latest.input.automation?.roundId
 const canGenerate=canEdit&&detail.capabilities.generation&&!busy&&!alreadyPublished
 const [observations,setObservations]=useState<Record<string,string>>({})
 const onObservation=useCallback((summary:string)=>{if(deployment)setObservations(previous=>previous[deployment.id]===summary?previous:{...previous,[deployment.id]:summary})},[deployment?.id])
 const status=published?`已发布部署 #${deployment.receipt!.deployNumber} · ${observations[deployment.id]??'观察状态待读取'}`:automatic && revision.revision===latest.revision && detail.automaticProgress ? detail.automaticProgress.publishable?'整轮核对完成，待审阅发布':`整轮核对中（${detail.automaticProgress.completedBatches}/${detail.automaticProgress.totalBatches}）`:candidateLabel(detail,revision)
 useEffect(()=>{if(!manualSelection.current)setSelectedRevision(initialRevision(detail))},[detail.latestSuccessful?.revision,latest.revision])
 useEffect(()=>{try{sessionStorage.setItem(storageKey,JSON.stringify({view,revision:revision.revision,followLatest:!manualSelection.current}))}catch{/* Inspection still works when browser storage is unavailable. */}},[storageKey,view,revision.revision])
 function switchRevision(value:number){manualSelection.current=true;setSelectedRevision(value)}
 const presentation=repairTaskPresentation([...known.current.values()].sort((a,b)=>a.revision-b.revision)[0].input)
 const source=(detail.source??presentation.source)==='automatic'?'自动任务':'手动任务'
 return <div className="min-w-0 space-y-5">
  <header role="region" aria-label="当前候选" className="rounded-xl border border-input bg-card p-5">
   <div className="flex flex-wrap items-start justify-between gap-4">
    <div className="min-w-0 w-full sm:w-auto sm:flex-1"><p className="text-xs text-muted-foreground">{detail.workflowId}</p><h2 className="mt-1 break-words text-lg font-semibold">{detail.title??presentation.title}</h2><div className="mt-2 flex flex-wrap items-center gap-2 text-xs"><span className="rounded border border-input px-2 py-1 text-muted-foreground">{source}</span><span className="rounded bg-primary/10 px-2 py-1 font-medium text-primary" aria-live="polite">{status}</span><span className="text-muted-foreground">当前查看 v{revision.revision}</span></div></div>
    <label className="text-xs text-muted-foreground">生成版本<select aria-label="查看修复版本" className="ml-2 max-w-full rounded border border-input bg-background p-2 text-foreground" value={revision.revision} onChange={event=>switchRevision(Number(event.target.value))}>{revisions.map(row=><option key={row.revision} value={row.revision}>v{row.revision} · {candidateLabel(detail,row)}</option>)}</select></label>
   </div>
   {!canEdit&&<p className="mt-3 text-xs text-warning-foreground">当前只有查看权限。</p>}
   {publishedRevision!==undefined&&onNextRound&&<div className="mt-4 space-y-2 border-t border-border pt-4">
    <button type="button" className={primary} disabled={!canEdit||busy} onClick={()=>onNextRound(publishedRevision)}>开始下一轮优化</button>
    <p className="text-xs text-muted-foreground">观察中也可选择其他问题与建议；提交时基于最新生效版本创建独立新任务，保留本轮观察记录。</p>
   </div>}
   {latest.revision!==revision.revision&&<p className="mt-3 text-xs text-muted-foreground">最近一次生成 v{latest.revision}：{candidateLabel(detail,latest)}；当前展示 v{revision.revision} 的内容。</p>}
   {latest.phase==='failed'&&<div className="mt-3 space-y-2 border-t border-border pt-3 text-sm">
    <p>{latest.error?.code==='GIT_RESULT_UNAVAILABLE'?'原候选结果尚未读取成功，请核对原结果。':detail.latestSuccessful?'最新生成失败，已有候选保留。':'首次生成失败，尚无候选。'}</p>
    <div className="flex flex-wrap gap-2"><button className={primary} disabled={!canGenerate} onClick={onRetryGeneration}>{latest.error?.code==='GIT_RESULT_UNAVAILABLE'?'重新读取候选':'重新生成 Pack'}</button>
    {!automatic&&<button className={button} disabled={!canGenerate||latest.error?.code==='GIT_RESULT_UNAVAILABLE'} onClick={onFeedback}>调整范围与要求</button>}
    {detail.latestSuccessful&&<button className={button} onClick={()=>{switchRevision(detail.latestSuccessful!.revision);setView('candidate')}}>返回上一成功候选审核</button>}</div>
   </div>}
   {detail.execution&&<details className="mt-3 text-xs text-muted-foreground"><summary>生成执行记录 · v{latest.revision}{latest.revision!==revision.revision?'（与当前候选分开保留）':''}</summary><p className="mt-2">{executionLabels[detail.execution.status]}</p><JsonDetails title="执行记录" value={detail.execution}/><JsonDetails title="最近尝试错误" value={latest.error}/></details>}
   {latest.phase==='drafting'&&detail.execution&&<div className="mt-3 flex flex-wrap items-center gap-2 text-sm"><p>{executionLabels[detail.execution.status]}</p>
    {['created','dispatch_failed'].includes(detail.execution.status)&&<button className={button} disabled={!canGenerate} onClick={onRetryDispatch}>重试派发</button>}
    {detail.execution.status==='dispatch_uncertain'&&<button className={button} disabled={busy} onClick={onRefresh}>核对任务状态</button>}
   </div>}
   {dispatchError&&<p role="alert" className="mt-3 text-xs text-destructive">{dispatchError}</p>}
  </header>
  <div role="tablist" aria-label="任务内容" className="grid grid-cols-3 rounded-lg border border-input bg-card p-1">
   {views.map(([key,label],index)=><button key={key} ref={element=>{tabRefs.current[index]=element}} id={`${id}-${key}`} type="button" role="tab" aria-selected={view===key} aria-controls={`${id}-${key}-panel`} tabIndex={view===key?0:-1} onClick={()=>setView(key)} onKeyDown={event=>{
    const next=event.key==='Home'?0:event.key==='End'?2:event.key==='ArrowRight'?(index+1)%3:event.key==='ArrowLeft'?(index+2)%3:-1
    if(next>=0){event.preventDefault();setView(views[next][0]);tabRefs.current[next]?.focus()}
   }} className={`min-w-0 rounded px-2 py-3 text-sm font-medium focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring ${view===key?'bg-primary/10 text-primary':'text-muted-foreground hover:bg-muted'}`}>{label}</button>)}
  </div>
  <section role="tabpanel" id={`${id}-scope-panel`} aria-labelledby={`${id}-scope`} hidden={view!=='scope'} className="space-y-4">
   <RepairTaskScope revision={revision}/>
   {detail.automaticProgress&&<p className="text-sm text-muted-foreground">本轮已纳入 {detail.scopeItemCount??revision.input.items.length} 项 · 核对进度 {detail.automaticProgress.completedBatches}/{detail.automaticProgress.totalBatches}</p>}
   {!automatic&&canGenerate&&latest.phase!=='failed'&&<button className={button} onClick={onFeedback}>调整范围与要求</button>}
  </section>
  <section role="tabpanel" id={`${id}-candidate-panel`} aria-labelledby={`${id}-candidate`} hidden={view!=='candidate'} className="space-y-4">
   <RepairCandidateReview key={revision.revision} detail={detail} revision={revision} active={view==='candidate'}/>
   <RepairReleaseFlow detail={detail} selectedRevision={revision.revision} canEdit={canEdit} onRefresh={onRefresh}/>
   {!automatic&&!alreadyPublished&&['review','blocked'].includes(latest.phase)&&<button className={button} disabled={!canGenerate||revision.revision!==detail.latestSuccessful?.revision} onClick={onFeedback}>反馈并生成下一版</button>}
  </section>
  <section role="tabpanel" id={`${id}-observe-panel`} aria-labelledby={`${id}-observe`} hidden={view!=='observe'} className="rounded-lg border border-input bg-card p-4">
   {view==='observe'&&(published?<RepairVerification key={deployment.id} detail={detail} deployment={deployment} canEdit={canEdit} onSummary={onObservation}/>:<div className="space-y-3 text-sm"><h3 className="font-semibold">本版本尚未发布</h3><p>{deployment&&['created','dispatching','running','uncertain'].includes(deployment.status)?'发布结果尚未确认，请先核对同一个发布请求。':'此候选尚未发布，暂无对应部署的观察证据。'}</p><button className={button} onClick={()=>setView('candidate')}>查看改动与发布</button></div>)}
  </section>
  <details className="text-xs text-muted-foreground"><summary>历史生成记录与任务操作</summary><ul className="mt-2 space-y-2">{detail.revisions.map(row=><li key={row.revision}>v{row.revision} · {candidateLabel(detail,row)}</li>)}</ul>
   {detail.revisionPage&&detail.revisionPage.totalPages>1&&<div className="mt-3 space-y-2"><p>历史记录第 {detail.revisionPage.page}/{detail.revisionPage.totalPages} 页 · 共 {detail.revisionPage.total} 次</p><button className={button} disabled={busy||detail.revisionPage.page<=1} onClick={()=>onHistoryPageChange?.(detail.revisionPage!.page-1)}>较新记录</button><button className={button} disabled={busy||detail.revisionPage.page>=detail.revisionPage.totalPages} onClick={()=>onHistoryPageChange?.(detail.revisionPage!.page+1)}>较早记录</button></div>}
   {!alreadyPublished&&(['drafting','review','blocked','failed'].includes(latest.phase)||latest.phase==='no_change'&&detail.automaticProgress?.publishable)&&<button className={`${button} mt-3`} disabled={!canEdit||busy} onClick={onCancel}>取消修复任务</button>}
  </details>
 </div>
}
