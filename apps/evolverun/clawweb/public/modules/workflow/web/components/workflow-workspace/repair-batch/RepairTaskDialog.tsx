import { useEffect, useRef, useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import type { RepairCandidatesResponse, RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import RepairTaskPanel from './RepairTaskPanel'
import { primary, RepairDialog } from './repair-view'

const message = (error: unknown) => error instanceof Error ? error.message : String(error)
const frozenItemTitle = (item: RepairTaskDetail['latestAttempt']['input']['items'][number]) =>
  typeof item.proposal?.summary === 'string' ? item.proposal.summary : item.instruction || item.itemId

export default function RepairTaskDialog({ workflowId, taskId, includeHistorical, canEdit, onClose, onChanged }: {
  workflowId: string; taskId: string; includeHistorical: boolean; canEdit: boolean;
  onClose: () => void; onChanged: () => void;
}) {
  const [detail, setDetail] = useState<RepairTaskDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [refresh, setRefresh] = useState(0)
  const [mode, setMode] = useState<'detail' | 'feedback' | 'cancel'>('detail')
  const [instructions, setInstructions] = useState('')
  const [feedback, setFeedback] = useState('')
  const [feedbackItemIds, setFeedbackItemIds] = useState<string[]>([])
  const [feedbackCandidates, setFeedbackCandidates] = useState<RepairCandidatesResponse | null>(null)
  const [feedbackCandidatesLoading, setFeedbackCandidatesLoading] = useState(false)
  const [feedbackCandidatesError, setFeedbackCandidatesError] = useState('')
  const [feedbackPage, setFeedbackPage] = useState(1)
  const [dispatchError, setDispatchError] = useState('')
  const requestId = useRef('')
  const requestPayloadKey = useRef('')
  const requestSequence = useRef(0)
  const activeTask = useRef<{ taskId: string; active: boolean } | null>(null)

  useEffect(() => {
    let current = true
    let pollTimer: ReturnType<typeof setTimeout> | undefined
    const schedulePoll = () => {
      pollTimer = setTimeout(() => { if (current) setRefresh(value => value + 1) }, 2_000)
    }
    setLoading(detail == null); setError('')
    repairBatches.task(taskId).then(result => {
      if (!current) return
      if (result.workflowId !== workflowId) { activeTask.current = { taskId, active: false }; setError('此任务不属于当前工作流'); return }
      setDetail(result)
      const active = ['drafting', 'publishing'].includes(result.latestAttempt.phase)
      activeTask.current = { taskId, active }
      if (active) schedulePoll()
    }).catch(reason => {
      if (!current) return
      setError(message(reason))
      if (activeTask.current?.taskId !== taskId || activeTask.current.active) schedulePoll()
    })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false; if (pollTimer) clearTimeout(pollTimer) }
  }, [workflowId, taskId, refresh])

  useEffect(() => {
    if (mode !== 'feedback') return
    let current = true
    setFeedbackCandidatesLoading(true)
    setFeedbackCandidatesError('')
    repairBatches.candidates(workflowId, { state: 'pending', page: feedbackPage, pageSize: 20, includeHistorical })
      .then(result => { if (current) setFeedbackCandidates(result) })
      .catch(reason => { if (current) setFeedbackCandidatesError(message(reason)) })
      .finally(() => { if (current) setFeedbackCandidatesLoading(false) })
    return () => { current = false }
  }, [mode, workflowId, feedbackPage, includeHistorical])

  const reload = () => { setRefresh(value => value + 1); onChanged() }
  const retryDispatch = async () => {
    if (!detail || busy) return
    setBusy(true); setDispatchError('')
    try { await repairBatches.retryDispatch(taskId, detail.latestAttempt.revision); reload() }
    catch (reason) { setDispatchError(message(reason)) }
    finally { setBusy(false) }
  }
  const cancel = async () => {
    if (!detail || busy) return
    setBusy(true); setError('')
    try { await repairBatches.cancel(taskId, detail.latestAttempt.revision); setMode('detail'); reload() }
    catch (reason) { setError(message(reason)) }
    finally { setBusy(false) }
  }
  const revise = async () => {
    if (!detail || !feedbackCandidates || !feedback.trim() || !feedbackItemIds.length || busy) return
    const revisionInput = {
      workflowId, inputDigest: feedbackCandidates.inputDigest, includeHistorical: feedbackCandidates.includeHistorical,
      itemIds: feedbackItemIds, instructions,
      expectedAttemptRevision: detail.latestAttempt.revision,
      parentCandidateCommit: typeof detail.latestSuccessful?.draft?.candidateCommit === 'string' ? detail.latestSuccessful.draft.candidateCommit : null,
      feedback: feedback.trim(),
    }
    const payloadKey = JSON.stringify(revisionInput)
    if (!requestId.current || requestPayloadKey.current !== payloadKey) {
      requestSequence.current += 1
      requestId.current = globalThis.crypto?.randomUUID?.() ?? `repair-feedback-${Date.now()}-${requestSequence.current}`
      requestPayloadKey.current = payloadKey
    }
    const payload = { ...revisionInput, requestId: requestId.current }
    if (new TextEncoder().encode(JSON.stringify(payload)).length > feedbackCandidates.limits.maxRequestBytes) {
      setError('请求内容过大，请缩短说明或减少选择。')
      return
    }
    setBusy(true); setError('')
    try {
      await repairBatches.revise(taskId, payload)
      requestId.current = ''; requestPayloadKey.current = ''; setMode('detail'); setFeedback(''); reload()
    } catch (reason) { setError(`修订请求状态未确认，请刷新任务后再决定是否重试：${message(reason)}`) }
    finally { setBusy(false) }
  }

  if (mode === 'feedback' && detail) {
    const frozenItems = detail.latestAttempt.input.items
    const frozenIds = new Set(frozenItems.map(item => item.itemId))
    const selectableItems = [...frozenItems, ...(feedbackCandidates?.items ?? []).filter(item => !frozenIds.has(item.itemId))]
    const maxItems = feedbackCandidates?.limits.maxItems ?? 100
    return <RepairDialog title="反馈并生成下一版" busy={busy} onClose={() => setMode('detail')}>
    <p className="text-xs leading-5 text-slate-500">重新确认本轮处理项后生成新的可审阅 Pack 候选，不会应用或部署。</p>
    <fieldset className="mt-4 rounded-lg border border-slate-200 p-3">
      <legend className="px-1 text-xs font-medium text-slate-700">确认本轮处理项</legend>
      {feedbackCandidatesLoading && <p role="status" className="mt-1 text-xs text-slate-500">加载最新待处理项…</p>}
      {feedbackCandidatesError && <p role="alert" className="mt-1 text-xs text-red-600">待处理项加载失败：{feedbackCandidatesError}</p>}
      <div className="mt-1 space-y-2">{selectableItems.map(item => {
        const title = frozenItemTitle(item)
        return <label key={item.itemId} className="flex items-start gap-2 text-xs text-slate-600">
          <input type="checkbox" aria-label={`选择修订项 ${title}`} checked={feedbackItemIds.includes(item.itemId)}
            disabled={busy || (!feedbackItemIds.includes(item.itemId) && feedbackItemIds.length >= maxItems)}
            onChange={() => setFeedbackItemIds(current => current.includes(item.itemId)
              ? current.filter(id => id !== item.itemId) : [...current, item.itemId])}
            className="mt-0.5 h-4 w-4 rounded border-slate-300 text-blue-600" />
          <span>{title}{!frozenIds.has(item.itemId) && <span className="ml-1 text-blue-600">新待处理</span>}</span>
        </label>
      })}</div>
      {feedbackCandidates && feedbackCandidates.page.totalPages > 1 && <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
        <button type="button" disabled={feedbackPage <= 1 || feedbackCandidatesLoading} onClick={() => setFeedbackPage(page => page - 1)} className="underline disabled:no-underline disabled:opacity-50">上一页</button>
        <span>待处理项第 {feedbackCandidates.page.page}/{feedbackCandidates.page.totalPages} 页</span>
        <button type="button" disabled={feedbackPage >= feedbackCandidates.page.totalPages || feedbackCandidatesLoading} onClick={() => setFeedbackPage(page => page + 1)} className="underline disabled:no-underline disabled:opacity-50">下一页</button>
      </div>}
    </fieldset>
    <label className="mt-4 block text-xs font-medium text-slate-700">修复要求
      <textarea aria-label="修复要求" rows={4} maxLength={20_000} value={instructions} disabled={busy}
        onChange={event => setInstructions(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 p-3 font-normal leading-5" />
    </label>
    <label className="mt-4 block text-xs font-medium text-slate-700">修改反馈
      <textarea aria-label="修改反馈" rows={5} maxLength={20_000} value={feedback} disabled={busy}
        onChange={event => setFeedback(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 p-3 font-normal leading-5" />
    </label>
    {error && <p role="alert" className="mt-3 text-xs text-red-600">{error}</p>}
    {!feedbackItemIds.length && <p role="alert" className="mt-3 text-xs text-amber-700">请至少保留一个处理项。</p>}
    <button type="button" className={`${primary} mt-4`} disabled={busy || feedbackCandidatesLoading || !!feedbackCandidatesError || !feedbackCandidates || !feedback.trim() || !feedbackItemIds.length} onClick={() => void revise()}>{busy ? '提交中…' : '确认生成下一版'}</button>
  </RepairDialog>
  }

  if (mode === 'cancel' && detail) return <RepairDialog title="取消修复任务" busy={busy} onClose={() => setMode('detail')}>
    <p className="text-xs leading-5 text-slate-500">取消任务会保留候选稿与历史记录，并释放处理项供后续重新选择。</p>
    {error && <p role="alert" className="mt-3 text-xs text-red-600">{error}</p>}
    <button type="button" className={`${primary} mt-4`} disabled={busy} onClick={() => void cancel()}>{busy ? '取消中…' : '确认取消任务'}</button>
  </RepairDialog>

  return <RepairDialog title="修复任务详情" busy={busy} onClose={onClose}>
    {loading && <p role="status" className="text-xs text-slate-500">加载修复任务…</p>}
    {error && <p role="alert" className="text-xs text-red-600">任务读取失败：{error}</p>}
    {detail && <RepairTaskPanel detail={detail} canEdit={canEdit} busy={busy} dispatchError={dispatchError}
      onFeedback={() => {
        requestId.current = ''
        requestPayloadKey.current = ''
        setInstructions(detail.latestAttempt.input.instructions)
        setFeedbackItemIds(detail.latestAttempt.input.items.map(item => item.itemId))
        setFeedbackCandidates(null)
        setFeedbackCandidatesError('')
        setFeedbackPage(1)
        setError('')
        setMode('feedback')
      }}
      onCancel={() => { setError(''); setMode('cancel') }} onRetryDispatch={() => void retryDispatch()} />}
  </RepairDialog>
}
