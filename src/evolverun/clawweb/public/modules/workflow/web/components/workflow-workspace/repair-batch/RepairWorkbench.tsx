import { useCallback, useEffect, useRef, useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import type { RepairCandidatesResponse, RepairInboxItem, RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import RepairItems from './RepairItems'
import RepairTaskPanel from './RepairTaskPanel'
import { button, exclusion, phases, primary, RepairDialog } from './repair-view'

type Filter = 'pending' | 'processing' | 'awaiting_verification' | 'closed' | 'no_action' | 'all'
const filters: Record<Filter, string> = { pending: '待处理', processing: '处理中', awaiting_verification: '待验证', closed: '已关闭', no_action: '暂不处理', all: '全部' }
const message = (error: unknown) => error instanceof Error ? error.message : String(error)
const matches = (item: RepairInboxItem, filter: Filter) => filter === 'all' || (filter === 'closed' ? ['verified', 'ineffective'].includes(item.state) : item.state === filter)
function readView(workflowId: string): { filter: Filter; taskId: string } {
  try {
    const saved = JSON.parse(sessionStorage.getItem(`workflow-repair:${workflowId}`) ?? '{}')
    return { filter: saved.filter in filters ? saved.filter : 'pending', taskId: typeof saved.taskId === 'string' ? saved.taskId : '' }
  } catch { return { filter: 'pending', taskId: '' } }
}

export default function RepairWorkbench({ workflowId }: { workflowId: string }) {
  return <Workbench key={workflowId} workflowId={workflowId} />
}

function Workbench({ workflowId }: { workflowId: string }) {
  const [view, setView] = useState(() => readView(workflowId))
  const [data, setData] = useState<RepairCandidatesResponse | null>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState('')
  const [detail, setDetail] = useState<RepairTaskDetail | null>(null)
  const [taskError, setTaskError] = useState('')
  const [taskLoading, setTaskLoading] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const initialized = useRef(false)
  const [form, setForm] = useState<'generate' | 'feedback' | null>(null)
  const [formSelection, setFormSelection] = useState<string[]>([])
  const [instructions, setInstructions] = useState('')
  const [feedback, setFeedback] = useState('')
  const [busy, setBusy] = useState(false)
  const [actionError, setActionError] = useState('')
  const [dispatchError, setDispatchError] = useState('')
  const [disposition, setDisposition] = useState<{ item: RepairInboxItem; action: 'no_action' | 'restore' } | null>(null)
  const [reason, setReason] = useState('')
  const [cancelOpen, setCancelOpen] = useState(false)
  const request = useRef({ payload: '', id: '' })
  const reload = useCallback(() => setRefresh(value => value + 1), [])

  useEffect(() => {
    try { sessionStorage.setItem(`workflow-repair:${workflowId}`, JSON.stringify(view)) } catch { /* Storage can be disabled. */ }
  }, [workflowId, view])
  useEffect(() => {
    let current = true
    const load = async () => {
      setLoading(true)
      try {
        const result = await repairBatches.candidates(workflowId)
        if (!current) return
        setData(result); setLoadError('')
        const eligible = result.items.filter(item => !exclusion(item)).map(item => item.itemId)
        const limit = Math.min(100, result.limits.maxItems)
        const wasInitialized = initialized.current
        setSelected(previous => wasInitialized ? previous.filter(id => eligible.includes(id)).slice(0, limit) : eligible.slice(0, limit))
        initialized.current = true
      } catch (error) { if (current) setLoadError(message(error)) }
      finally { if (current) setLoading(false) }
    }
    void load()
    return () => { current = false }
  }, [workflowId, refresh])
  useEffect(() => { const timer = window.setInterval(reload, 15_000); return () => window.clearInterval(timer) }, [reload])
  useEffect(() => {
    if (!view.taskId) { setDetail(null); setTaskError(''); return }
    let current = true; setTaskLoading(true); setTaskError('')
    repairBatches.task(view.taskId).then(result => {
      if (!current) return
      if (result.workflowId !== workflowId) { setDetail(null); setTaskError('此任务不属于当前工作流'); return }
      setDetail(result)
    }).catch(error => { if (current) setTaskError(message(error)) }).finally(() => { if (current) setTaskLoading(false) })
    return () => { current = false }
  }, [workflowId, view.taskId, refresh])

  const limit = Math.min(100, data?.limits.maxItems ?? 100)
  const canEdit = data?.canEdit === true
  const visibleDetail = detail?.taskId === view.taskId ? detail : null
  const activeTask = data?.tasks.find(task => ['drafting', 'review', 'blocked', 'failed', 'publishing'].includes(task.phase))
  const toggle = (ids: string[], id: string) => ids.includes(id) ? ids.filter(value => value !== id) : ids.length < limit ? [...ids, id] : ids
  const openTask = (taskId: string) => { setDetail(null); setView(previous => ({ ...previous, taskId })); setActionError(''); setDispatchError('') }
  const openForm = (mode: 'generate' | 'feedback') => {
    if (!data) return
    setForm(mode); setActionError(''); setFeedback('')
    setInstructions(mode === 'feedback' ? visibleDetail?.latestAttempt.input.instructions ?? '' : '')
    const eligible = data.items.filter(item => !exclusion(item, mode === 'feedback' ? view.taskId : undefined)).map(item => item.itemId)
    const chosen = mode === 'feedback' ? visibleDetail?.latestAttempt.input.items.map(item => item.itemId) ?? [] : selected
    setFormSelection(chosen.filter(id => eligible.includes(id)).slice(0, limit))
  }
  const requestId = (payload: unknown) => {
    const serialized = JSON.stringify(payload)
    if (request.current.payload !== serialized) request.current = { payload: serialized, id: crypto.randomUUID() }
    return request.current.id
  }
  const submit = async () => {
    if (!data || !form || !canEdit || !data.capabilities.generation || busy) return
    const eligible = new Set(data.items.filter(item => !exclusion(item, form === 'feedback' ? view.taskId : undefined)).map(item => item.itemId))
    if (!formSelection.length || formSelection.some(id => !eligible.has(id))) { setActionError('所选建议状态已变化，请重新选择后提交。'); return }
    const selection = { workflowId, inputDigest: data.inputDigest, itemIds: formSelection, instructions }
    const payload = form === 'feedback' && visibleDetail ? { ...selection, expectedAttemptRevision: visibleDetail.latestAttempt.revision,
      parentCandidateCommit: typeof visibleDetail.latestSuccessful?.draft?.candidateCommit === 'string' ? visibleDetail.latestSuccessful.draft.candidateCommit : null, feedback } : selection
    const withId = { ...payload, requestId: requestId(payload) }
    if (new TextEncoder().encode(JSON.stringify(withId)).length > data.limits.maxRequestBytes) { setActionError('请求内容过大，请缩短说明或减少选择。'); return }
    setBusy(true); setActionError('')
    try {
      const result = form === 'feedback' && 'feedback' in withId ? await repairBatches.revise(view.taskId, withId) : await repairBatches.create(withId)
      setForm(null); openTask(result.taskId); reload()
    } catch (error) { setActionError(message(error)); reload() }
    finally { setBusy(false) }
  }
  const submitDisposition = async () => {
    if (!data || !disposition || !reason.trim() || !canEdit || busy) return
    const payload = { workflowId, inputDigest: data.inputDigest, expectedStateVersion: disposition.item.stateVersion,
      contentRevision: disposition.item.contentRevision, action: disposition.action, reason: reason.trim() }
    setBusy(true); setActionError('')
    try { await repairBatches.disposition(disposition.item.itemId, { ...payload, requestId: requestId({ ...payload, itemId: disposition.item.itemId }) }); setDisposition(null); reload() }
    catch (error) { setActionError(message(error)); reload() }
    finally { setBusy(false) }
  }
  const retryDispatch = async () => {
    if (!visibleDetail || !canEdit || !visibleDetail.capabilities.generation || !data?.capabilities.generation || busy
      || visibleDetail.latestAttempt.phase !== 'drafting' || !['created', 'dispatch_failed'].includes(visibleDetail.execution?.status ?? '')) return
    setBusy(true); setDispatchError('')
    try { await repairBatches.retryDispatch(view.taskId, visibleDetail.latestAttempt.revision); reload() }
    catch (error) { setDispatchError(message(error)); reload() }
    finally { setBusy(false) }
  }
  const cancel = async () => {
    if (!visibleDetail || !canEdit || busy) return
    setBusy(true); setActionError('')
    try { await repairBatches.cancel(view.taskId, visibleDetail.latestAttempt.revision); setCancelOpen(false); reload() }
    catch (error) { setActionError(message(error)) }
    finally { setBusy(false) }
  }

  return <section aria-label="修复收件箱" className="mb-5 rounded-xl border border-slate-200 bg-slate-50/50 p-4 sm:p-5">
    <header className="flex flex-wrap items-start justify-between gap-3"><div><h2 className="text-base font-semibold text-slate-900">修复收件箱</h2><p className="mt-1 text-xs leading-5 text-slate-500">跨运行汇总问题与建议，选择后生成可审阅的修复候选稿。</p></div>
      <button type="button" className={button} disabled={loading} onClick={reload}>刷新</button>
    </header>
    {loading && <p role="status" className="mt-3 text-xs text-slate-500">加载修复收件箱…</p>}
    {loadError && <p role="alert" className="mt-3 text-xs text-red-600">收件箱读取失败：{loadError} <button type="button" className={button} onClick={reload}>重试</button></p>}
    {data && <>
      {!canEdit && <p className="mt-3 text-xs text-slate-500">当前账号为只读权限。</p>}
      {!data.capabilities.generation && <p className="mt-3 rounded-lg border border-amber-200 bg-amber-50 p-3 text-xs text-amber-800">{data.capabilities.reason || '修复生成服务暂不可用'}</p>}
      {activeTask && <div className="mt-3 flex flex-wrap items-center justify-between gap-2 rounded-lg bg-blue-50 p-3 text-xs text-blue-800"><span>进行中修复：{activeTask.taskId} · v{activeTask.revision} · {phases[activeTask.phase]}</span><button className={button} onClick={() => openTask(activeTask.taskId)}>查看进行中任务</button></div>}
      <nav aria-label="修复状态筛选" className="mt-4 flex flex-wrap gap-2">{Object.entries(filters).map(([key, label]) => <button type="button" key={key} aria-pressed={view.filter === key}
        className={`${button} ${view.filter === key ? 'border-blue-200 bg-blue-50 text-blue-700' : 'bg-white text-slate-600'}`}
        onClick={() => setView(previous => ({ ...previous, filter: key as Filter }))}>{label} {data.items.filter(item => matches(item, key as Filter)).length}</button>)}</nav>
      <div className="mt-4 flex flex-wrap items-center justify-between gap-3"><p className="text-xs text-slate-600">已选择 {selected.length} 项 · 最多 {limit} 项 · {data.items.filter(item => !!exclusion(item)).length} 项不参与本次生成</p>
        <div className="flex flex-wrap gap-2"><button className={button} disabled={!canEdit} onClick={() => setSelected(data.items.filter(item => !exclusion(item)).slice(0, limit).map(item => item.itemId))}>选择可处理项</button>
          <button className={button} disabled={!canEdit || !selected.length} onClick={() => setSelected([])}>清空选择</button>
          <button type="button" className={primary} disabled={!canEdit || !data.capabilities.generation || !selected.length || !!activeTask} onClick={() => openForm('generate')}>生成修复候选稿</button></div>
      </div>
      {data.items.some(item => matches(item, view.filter)) ? <RepairItems items={data.items.filter(item => matches(item, view.filter))} allItems={data.items}
        selected={selected} onToggle={id => setSelected(previous => toggle(previous, id))} canEdit={canEdit} limit={limit}
        onDisposition={(item, action) => { setDisposition({ item, action }); setReason(''); setActionError('') }} /> : <p className="py-8 text-center text-xs text-slate-500">当前筛选下没有修复项。</p>}
      {!!data.tasks.length && <details className="mt-3" open><summary className="cursor-pointer text-xs font-medium text-slate-700">修复任务记录 · {data.tasks.length}</summary><div className="mt-2 flex flex-wrap gap-2">{data.tasks.map(task => <button key={task.taskId} type="button" className={`${button} bg-white`} aria-label={`打开任务 ${task.taskId}`} onClick={() => openTask(task.taskId)}>{task.taskId} · v{task.revision} · {phases[task.phase] ?? task.phase} · {task.itemCount} 项</button>)}</div></details>}
    </>}
    {view.taskId && <div className="mt-3"><button type="button" className={button} onClick={() => setView(previous => ({ ...previous, taskId: '' }))}>收起任务详情</button>
      {taskLoading && <p role="status" className="mt-2 text-xs">加载修复任务…</p>}
      {taskError && <p role="alert" className="mt-2 text-xs text-red-600">任务读取失败：{taskError} <button className={button} onClick={reload}>重试任务</button></p>}
    </div>}
    {visibleDetail && <RepairTaskPanel key={`${visibleDetail.taskId}:${visibleDetail.latestSuccessful?.revision ?? 0}`} detail={visibleDetail} canEdit={canEdit} busy={busy}
      onFeedback={() => openForm('feedback')} onCancel={() => { setCancelOpen(true); setActionError('') }} onRetryDispatch={() => void retryDispatch()} dispatchError={dispatchError} />}
    {form && data && <RepairDialog title={form === 'feedback' ? '反馈并生成下一版' : '生成修复候选稿'} busy={busy} onClose={() => setForm(null)}>
      <p className="text-xs leading-5 text-slate-500">已选择 {formSelection.length} 项 · 可调整本次选择。只生成候选稿，不会应用或部署。</p>
      <div className="my-3 max-h-64 overflow-auto"><RepairItems items={data.items} selected={formSelection} onToggle={id => setFormSelection(previous => toggle(previous, id))} canEdit={canEdit && !busy} limit={limit} taskId={form === 'feedback' ? view.taskId : undefined} /></div>
      <label className="block text-xs font-medium">修复说明<textarea aria-label="修复说明" maxLength={20000} disabled={busy} className="mt-1 block min-h-24 w-full rounded-lg border border-slate-200 p-3 font-normal" value={instructions} onChange={event => setInstructions(event.target.value)} /></label>
      {form === 'feedback' && <label className="mt-3 block text-xs font-medium">修改反馈<textarea aria-label="修改反馈" maxLength={20000} disabled={busy} className="mt-1 block min-h-24 w-full rounded-lg border border-slate-200 p-3 font-normal" value={feedback} onChange={event => setFeedback(event.target.value)} /></label>}
      {actionError && <p role="alert" className="mt-3 text-xs text-red-600">{actionError}</p>}
      <button type="button" className={`${primary} mt-4`} disabled={busy || !canEdit || !data.capabilities.generation || !formSelection.length || (form === 'feedback' && !feedback.trim())} onClick={() => void submit()}>{busy ? '正在提交…' : form === 'feedback' ? '确认生成下一版' : '确认生成'}</button>
    </RepairDialog>}
    {disposition && <RepairDialog title={disposition.action === 'restore' ? '恢复待处理' : '暂不处理'} busy={busy} onClose={() => setDisposition(null)}>
      <p className="text-xs text-slate-500">仅处置这条建议的当前内容版本，原始问题与历史记录会保留。</p>
      <label className="mt-3 block text-xs font-medium">处置原因<textarea aria-label="处置原因" maxLength={2000} className="mt-1 block min-h-24 w-full rounded-lg border border-slate-200 p-3" value={reason} onChange={event => setReason(event.target.value)} disabled={busy} /></label>
      {actionError && <p role="alert" className="mt-3 text-xs text-red-600">{actionError}</p>}
      <button type="button" className={`${primary} mt-4`} disabled={busy || !canEdit || !reason.trim()} onClick={() => void submitDisposition()}>确认处置</button>
    </RepairDialog>}
    {cancelOpen && <RepairDialog title="取消修复任务" busy={busy} onClose={() => setCancelOpen(false)}><p className="text-xs">取消任务，保留候选稿与历史记录，释放处理项以便重新选择。</p>
      {actionError && <p role="alert" className="mt-3 text-xs text-red-600">{actionError}</p>}
      <button className={`${primary} mt-4`} disabled={busy || !canEdit} onClick={() => void cancel()}>确认取消任务</button>
    </RepairDialog>}
  </section>
}
