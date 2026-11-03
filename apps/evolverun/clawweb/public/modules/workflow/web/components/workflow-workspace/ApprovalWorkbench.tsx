import { useCallback, useEffect, useRef, useState } from 'react'
import ApprovalSlidePanel from '../ApprovalSlidePanel'

type Item = { cardId:number; flowId:string; nodeId:string; workflowId:string; workflowTitle:string|null; message:string|null;
  approverIds:string[]; status:string; personalStatus:'pending'|'approved'|'rejected'; actionability?:'available'|'unknown'|'none'; createdAt:string; approvalUrl:string }
type Filters = { workflowId:string; empId:string; from:string; to:string; decision:string }
const empty: Filters = { workflowId:'', empId:'', from:'', to:'', decision:'handled' }

export default function ApprovalWorkbench({isAdmin}:{isAdmin:boolean}) {
  const [tab,setTab]=useState<'pending'|'handled'>('pending')
  const [draft,setDraft]=useState<Filters>(empty), [filters,setFilters]=useState<Filters>(empty)
  const [items,setItems]=useState<Item[]>([]), [cursor,setCursor]=useState<string|null>(null)
  const [loading,setLoading]=useState(false), [error,setError]=useState<string|null>(null), [selected,setSelected]=useState<Item|null>(null)
  const [revision,setRevision]=useState(0)
  const queryAbort=useRef<AbortController|null>(null)
  const makeParams=useCallback((nextCursor?:string)=>{
    const p=new URLSearchParams({personalStatus:tab==='pending'?'pending':filters.decision,limit:'20'})
    if(filters.workflowId.trim())p.set('workflowId',filters.workflowId.trim())
    if(isAdmin && filters.empId.trim())p.set('empId',filters.empId.trim())
    if(filters.from)p.set('createdFrom',String(Math.floor(new Date(`${filters.from}T00:00:00`).getTime()/1000)))
    if(filters.to){const date=new Date(`${filters.to}T00:00:00`);date.setDate(date.getDate()+1);p.set('createdTo',String(Math.floor(date.getTime()/1000)))}
    if(nextCursor)p.set('cursor',nextCursor)
    return p
  },[filters,isAdmin,tab])
  useEffect(()=>{
    const abort=new AbortController();queryAbort.current=abort;setLoading(true);setError(null);setItems([]);setCursor(null)
    fetch(`/api/approval/search?${makeParams()}`,{signal:abort.signal}).then(async response=>{
      const body=await response.json();if(!response.ok)throw new Error(body.message || `查询失败 (${response.status})`)
      return body as {items:Item[];nextCursor:string|null}
    }).then(body=>{if(!abort.signal.aborted){setItems(body.items);setCursor(body.nextCursor)}}).catch(err=>{if(!abort.signal.aborted)setError(err instanceof Error?err.message:'查询失败')}).finally(()=>{if(!abort.signal.aborted)setLoading(false)})
    return ()=>abort.abort()
  },[makeParams,revision])
  const loadMore=async()=>{
    const abort=queryAbort.current
    if(!cursor || loading || !abort || abort.signal.aborted)return
    setLoading(true);setError(null)
    try {const response=await fetch(`/api/approval/search?${makeParams(cursor)}`,{signal:abort.signal});const body=await response.json();if(!response.ok)throw new Error(body.message || '查询失败');if(!abort.signal.aborted){setItems(previous=>[...previous,...body.items]);setCursor(body.nextCursor)}}
    catch(err){if(!abort.signal.aborted)setError(err instanceof Error?err.message:'查询失败')}finally{if(!abort.signal.aborted)setLoading(false)}
  }
  return <section className="mx-auto max-w-7xl space-y-5 p-6" aria-label="审批工作台">
    <header><h1 className="text-xl font-semibold text-slate-950">工作台</h1><p className="mt-1 text-sm text-slate-500">查看并处理审批任务{filters.empId && isAdmin ? ` · 当前查看工号 ${filters.empId}` : ' · 我的审批'}</p></header>
    <div className="flex gap-2 border-b border-slate-200" aria-label="审批分类"><button type="button" aria-pressed={tab==='pending'} onClick={()=>setTab('pending')} className={`px-4 py-2 text-sm ${tab==='pending'?'border-b-2 border-primary font-semibold text-primary':'text-slate-500'}`}>待审批</button><button type="button" aria-pressed={tab==='handled'} onClick={()=>setTab('handled')} className={`px-4 py-2 text-sm ${tab==='handled'?'border-b-2 border-primary font-semibold text-primary':'text-slate-500'}`}>已办</button></div>
    <form className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4" onSubmit={e=>{e.preventDefault();setFilters({...draft});setRevision(n=>n+1)}}>
      <label className="text-xs text-slate-600">工作流 ID<input value={draft.workflowId} onChange={e=>setDraft({...draft,workflowId:e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
      {isAdmin && <label className="text-xs text-slate-600">审批人工号<input aria-label="审批人工号" value={draft.empId} onChange={e=>setDraft({...draft,empId:e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>}
      <label className="text-xs text-slate-600">创建日期从<input type="date" value={draft.from} onChange={e=>setDraft({...draft,from:e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
      <label className="text-xs text-slate-600">创建日期至<input type="date" value={draft.to} onChange={e=>setDraft({...draft,to:e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
      {tab==='handled' && <label className="text-xs text-slate-600">处理结果<select value={draft.decision} onChange={e=>setDraft({...draft,decision:e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm"><option value="handled">全部已办</option><option value="approved">已同意</option><option value="rejected">已驳回</option></select></label>}
      <button type="submit" className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground">查询</button><button type="button" onClick={()=>{setDraft({...empty});setFilters({...empty});setRevision(n=>n+1)}} className="rounded-md border border-slate-300 px-4 py-2 text-sm">重置</button>
    </form>
    {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs text-slate-500"><tr>{['工作流','审批内容','审批人工号','创建时间','个人状态','操作'].map(h=><th className="px-4 py-3 font-medium" key={h}>{h}</th>)}</tr></thead><tbody>{items.map(item=><tr key={item.cardId} className="border-t border-slate-100"><td className="px-4 py-3"><div>{item.workflowTitle || '未知工作流'}</div><div className="text-xs text-slate-400">{item.workflowId}</div></td><td className="px-4 py-3">{item.message || '—'}</td><td className="px-4 py-3">{item.approverIds.join('、')}</td><td className="px-4 py-3">{new Date(item.createdAt).toLocaleString()}</td><td className="px-4 py-3">{item.personalStatus==='pending'?(item.actionability==='unknown'?'待确认':'待审批'):item.personalStatus==='approved'?'已同意':'已驳回'}</td><td className="px-4 py-3"><button type="button" onClick={()=>setSelected(item)} className="text-primary hover:underline">查看详情</button></td></tr>)}</tbody></table>{!loading && !error && items.length===0 && <p className="py-12 text-center text-sm text-slate-400">当前没有符合条件的审批</p>}</div>
    {loading && <p role="status" className="text-sm text-slate-500">加载中…</p>}{cursor && <button type="button" disabled={loading} onClick={()=>void loadMore()} className="rounded-md border border-slate-300 px-4 py-2 text-sm">加载更多</button>}
    {selected && <ApprovalSlidePanel workbench card={{id:selected.cardId}} run={{flow_id:selected.flowId,workflow_id:selected.workflowId,workflow_title:selected.workflowTitle}} onClose={()=>setSelected(null)} onResolved={()=>setRevision(n=>n+1)} />}
  </section>
}
