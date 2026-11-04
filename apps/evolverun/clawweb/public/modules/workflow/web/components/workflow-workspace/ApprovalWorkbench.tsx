import { useCallback, useEffect, useState } from 'react'
import ApprovalSlidePanel from '../ApprovalSlidePanel'

type PersonalStatus = 'pending' | 'approved' | 'rejected'
type Item = {
  cardId: number; flowId: string; nodeId: string; workflowId: string; workflowTitle: string | null
  message: string | null; approverIds: string[]; status: string; personalStatus: PersonalStatus | null
  matchedApprovers?: Array<{empId: string; personalStatus: PersonalStatus}>
  actionability?: 'available' | 'unknown' | 'none'; createdAt: string; approvalUrl: string
}
type Filters = { workflowId: string; empId: string; from: string; to: string; decision: string }
type Navigation = { index: number; cursors: Array<string | null> }
const empty: Filters = { workflowId: '', empId: '', from: '', to: '', decision: 'handled' }
const firstPage = (): Navigation => ({ index: 0, cursors: [null] })

function statusLabel(status: PersonalStatus | null, actionability?: Item['actionability']) {
  return status == null ? '—' : status === 'pending' ? (actionability === 'unknown' ? '待确认' : '待审批') : status === 'approved' ? '已同意' : '已驳回'
}

function absoluteApprovalUrl(approvalUrl: string): string {
  return new URL(approvalUrl, window.location.origin).href
}

export default function ApprovalWorkbench({isAdmin, userId = ''}: {isAdmin: boolean; userId?: string}) {
  const [tab, setTab] = useState<'pending' | 'handled'>('pending')
  const [adminMode, setAdminMode] = useState(false)
  const activeAdminMode = isAdmin && adminMode
  const [draft, setDraft] = useState<Filters>(empty), [filters, setFilters] = useState<Filters>(empty)
  const [items, setItems] = useState<Item[]>([]), [nextCursor, setNextCursor] = useState<string | null>(null)
  const [navigation, setNavigation] = useState<Navigation>(firstPage)
  const [pageSize, setPageSize] = useState(20)
  const [loading, setLoading] = useState(false), [error, setError] = useState<string | null>(null), [selected, setSelected] = useState<Item | null>(null)
  const [copyStates, setCopyStates] = useState<Record<number, 'copied' | 'failed'>>({})
  const [revision, setRevision] = useState(0)
  const cursor = navigation.cursors[navigation.index]

  useEffect(() => {
    if (isAdmin || !adminMode) return
    setAdminMode(false)
    setDraft(current => ({...current, empId: ''}))
    setFilters(current => ({...current, empId: ''}))
    setNavigation(firstPage())
    setSelected(null)
  }, [isAdmin, adminMode])

  const makeParams = useCallback(() => {
    const p = new URLSearchParams({personalStatus: tab === 'pending' ? 'pending' : filters.decision, limit: String(pageSize)})
    if (filters.workflowId.trim()) p.set('workflowId', filters.workflowId.trim())
    if (activeAdminMode) {
      if (filters.empId.trim()) p.set('empId', filters.empId.trim())
      else p.set('scope', 'all')
    }
    if (filters.from) p.set('createdFrom', String(Math.floor(new Date(`${filters.from}T00:00:00`).getTime() / 1000)))
    if (filters.to) { const date = new Date(`${filters.to}T00:00:00`); date.setDate(date.getDate() + 1); p.set('createdTo', String(Math.floor(date.getTime() / 1000))) }
    if (cursor) p.set('cursor', cursor)
    return p
  }, [activeAdminMode, cursor, filters, pageSize, tab])

  useEffect(() => {
    const abort = new AbortController()
    setLoading(true); setError(null); setItems([]); setNextCursor(null)
    const params = makeParams()
    fetch(`/api/approval/search?${params}`, {signal: abort.signal}).then(async response => {
      const body = await response.json()
      if (!response.ok) throw new Error(body.message || `查询失败 (${response.status})`)
      if (params.get('scope') === 'all' && body.scope !== 'all') throw new Error('当前服务尚不支持管理员全部人员查询')
      return body as {items: Item[]; nextCursor: string | null}
    }).then(body => {
      if (!abort.signal.aborted) { setItems(body.items); setNextCursor(body.nextCursor) }
    }).catch(err => {
      if (!abort.signal.aborted) setError(err instanceof Error ? err.message : '查询失败')
    }).finally(() => {
      if (!abort.signal.aborted) setLoading(false)
    })
    return () => abort.abort()
  }, [makeParams, revision])

  const resetPage = () => setNavigation(firstPage())
  const changeTab = (next: 'pending' | 'handled') => { setTab(next); resetPage(); setSelected(null) }
  const toggleAdmin = () => {
    setAdminMode(!activeAdminMode)
    setDraft(current => ({...current, empId: ''})); setFilters(current => ({...current, empId: ''}))
    resetPage(); setSelected(null)
  }
  const copyApprovalLink = useCallback(async (item: Item) => {
    try {
      await navigator.clipboard.writeText(absoluteApprovalUrl(item.approvalUrl))
      setCopyStates(current => ({...current, [item.cardId]: 'copied'}))
    } catch {
      setCopyStates(current => ({...current, [item.cardId]: 'failed'}))
    }
  }, [])
  useEffect(() => { setCopyStates({}) }, [items])

  const nextPage = () => {
    if (!nextCursor || loading) return
    setNavigation(current => ({index: current.index + 1, cursors: [...current.cursors.slice(0, current.index + 1), nextCursor]}))
  }
  const isRowActionable = useCallback((item: Item) => !activeAdminMode && item.personalStatus === 'pending' && item.actionability === 'available', [activeAdminMode])
  const detailActionLabel = useCallback((item: Item) => isRowActionable(item) ? '进行审批' : '查看详情', [isRowActionable])
  const detailActionClass = useCallback((item: Item) => isRowActionable(item)
    ? 'inline-flex items-center rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-foreground shadow-sm transition-colors hover:bg-primary/90 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary'
    : 'inline-flex items-center rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary', [isRowActionable])
  const previousPage = () => {
    if (loading) return
    setNavigation(current => ({...current, index: Math.max(0, current.index - 1)}))
  }

  return <section className="mx-auto max-w-7xl space-y-5 p-6" aria-label="审批工作台">
    <header><h1 className="text-xl font-semibold text-slate-950">工作台</h1><p className="mt-1 text-sm text-slate-500">查看并处理审批任务{activeAdminMode ? (filters.empId ? ` · 当前查看工号 ${filters.empId}` : ' · 管理员模式：全部人员') : ' · 我的审批'}</p></header>
    <div className="flex gap-2 border-b border-slate-200" aria-label="审批分类"><button type="button" aria-pressed={tab === 'pending'} onClick={() => changeTab('pending')} className={`px-4 py-2 text-sm ${tab === 'pending' ? 'border-b-2 border-primary font-semibold text-primary' : 'text-slate-500'}`}>待审批</button><button type="button" aria-pressed={tab === 'handled'} onClick={() => changeTab('handled')} className={`px-4 py-2 text-sm ${tab === 'handled' ? 'border-b-2 border-primary font-semibold text-primary' : 'text-slate-500'}`}>已办</button></div>
    <form className="flex flex-wrap items-end gap-3 rounded-xl border border-slate-200 bg-white p-4" onSubmit={e => {e.preventDefault(); setFilters({...draft}); resetPage(); setRevision(n => n + 1)}}>
      <label className="text-xs text-slate-600">工作流 ID<input value={draft.workflowId} onChange={e => setDraft({...draft, workflowId: e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
      {isAdmin && <div className="text-xs text-slate-600"><label htmlFor="approval-workbench-employee">审批人工号</label><div className="mt-1 flex gap-2"><input id="approval-workbench-employee" aria-label="审批人工号" value={activeAdminMode ? draft.empId : userId} onChange={e => setDraft({...draft, empId: e.target.value})} disabled={!activeAdminMode} placeholder={activeAdminMode ? '留空查询全部人员' : ''} className="min-w-0 rounded-md border border-slate-300 px-3 py-2 text-sm disabled:bg-slate-50 disabled:text-slate-500"/><button type="button" aria-pressed={activeAdminMode} onClick={toggleAdmin} className={`shrink-0 rounded-md border px-3 py-2 text-xs font-medium ${activeAdminMode ? 'border-primary bg-primary text-primary-foreground' : 'border-slate-300 bg-white text-slate-700'}`}>{activeAdminMode ? '关闭管理员' : '启用管理员'}</button></div></div>}
      <label className="text-xs text-slate-600">创建日期从<input type="date" value={draft.from} onChange={e => setDraft({...draft, from: e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
      <label className="text-xs text-slate-600">创建日期至<input type="date" value={draft.to} onChange={e => setDraft({...draft, to: e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm" /></label>
      {tab === 'handled' && <label className="text-xs text-slate-600">处理结果<select value={draft.decision} onChange={e => setDraft({...draft, decision: e.target.value})} className="mt-1 block rounded-md border border-slate-300 px-3 py-2 text-sm"><option value="handled">全部已办</option><option value="approved">已同意</option><option value="rejected">已驳回</option></select></label>}
      <button type="submit" className="rounded-md bg-primary px-4 py-2 text-sm text-primary-foreground">查询</button><button type="button" onClick={() => {setDraft({...empty}); setFilters({...empty}); resetPage(); setRevision(n => n + 1)}} className="rounded-md border border-slate-300 px-4 py-2 text-sm">重置</button>
    </form>
    {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">{error}</div>}
    <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white"><table className="w-full text-left text-sm"><thead className="bg-slate-50 text-xs text-slate-500"><tr>{['工作流', '审批内容', '审批人工号', '创建时间', '个人状态', '操作'].map(h => <th className="px-4 py-3 font-medium" key={h}>{h}</th>)}</tr></thead><tbody>{items.map(item => <tr key={item.cardId} className="border-t border-slate-100"><td className="px-4 py-3"><div>{item.workflowTitle || '未知工作流'}</div><div className="text-xs text-slate-400">{item.workflowId}</div></td><td className="px-4 py-3">{item.message || '—'}</td><td className="px-4 py-3">{item.approverIds.join('、')}</td><td className="px-4 py-3">{new Date(item.createdAt).toLocaleString()}</td><td className="px-4 py-3">{item.matchedApprovers?.length ? item.matchedApprovers.map(person => <div key={person.empId}>{person.empId}：{statusLabel(person.personalStatus, item.actionability)}</div>) : statusLabel(item.personalStatus, item.actionability)}</td><td className="px-4 py-3"><div className="flex flex-wrap gap-2"><button type="button" aria-label="复制链接" title="复制链接" onClick={() => void copyApprovalLink(item)} className="inline-flex items-center gap-1.5 rounded-md border border-slate-300 bg-white px-2.5 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:border-slate-400 hover:bg-slate-50 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-primary">复制链接</button><button type="button" onClick={() => setSelected(item)} className={detailActionClass(item)}>{detailActionLabel(item)}</button></div>{copyStates[item.cardId] && <span role="status" className={copyStates[item.cardId] === 'copied' ? 'sr-only' : 'mt-1 block text-xs text-red-700'}>{copyStates[item.cardId] === 'copied' ? '已复制' : '复制失败，请手动复制链接'}</span>}</td></tr>)}</tbody></table>{!loading && !error && items.length === 0 && <p className="py-12 text-center text-sm text-slate-400">当前没有符合条件的审批</p>}</div>
    <div className="flex flex-wrap items-center justify-between gap-3 text-sm text-slate-600" aria-label="审批分页">
      <label>每页条数 <select aria-label="每页条数" value={pageSize} onChange={e => {setPageSize(Number(e.target.value)); resetPage()}} className="rounded-md border border-slate-300 bg-white px-2 py-1">{[10,20,50,100].map(size => <option key={size} value={size}>{size}</option>)}</select></label>
      <div className="flex items-center gap-3"><button type="button" disabled={loading || navigation.index === 0} onClick={previousPage} className="rounded-md border border-slate-300 px-3 py-1.5 disabled:opacity-50">上一页</button><span aria-live="polite">第 {navigation.index + 1} 页</span><button type="button" disabled={loading || !nextCursor} onClick={nextPage} className="rounded-md border border-slate-300 px-3 py-1.5 disabled:opacity-50">下一页</button></div>
    </div>
    {loading && <p role="status" className="text-sm text-slate-500">加载中…</p>}
    {selected && <ApprovalSlidePanel workbench card={{id: selected.cardId}} run={{flow_id: selected.flowId, workflow_id: selected.workflowId, workflow_title: selected.workflowTitle}} onClose={() => setSelected(null)} onResolved={() => {resetPage(); setRevision(n => n + 1)}} />}
  </section>
}
