import { useEffect, useRef, useState } from 'react'
import { repairBatches } from '../../../api/repair-batches'
import type { RepairTaskDetail } from '../../../../server/contracts/repair-workbench'
import RepairTaskPanel from './RepairTaskPanel'
import { primary, RepairDialog } from './repair-view'

const message = (error: unknown) => error instanceof Error ? error.message : String(error)

export default function RepairTaskDialog({ workflowId, taskId, inputDigest, includeHistorical, canEdit, onClose, onChanged }: {
  workflowId: string; taskId: string; inputDigest: string; includeHistorical: boolean; canEdit: boolean;
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
  const [dispatchError, setDispatchError] = useState('')
  const requestId = useRef('')

  useEffect(() => {
    let current = true
    setLoading(true); setError('')
    repairBatches.task(taskId).then(result => {
      if (!current) return
      if (result.workflowId !== workflowId) throw new Error('此任务不属于当前工作流')
      setDetail(result)
    }).catch(reason => { if (current) setError(message(reason)) })
      .finally(() => { if (current) setLoading(false) })
    return () => { current = false }
  }, [workflowId, taskId, refresh])

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
    if (!detail || !feedback.trim() || busy) return
    if (!requestId.current) requestId.current = globalThis.crypto?.randomUUID?.() ?? `repair-feedback-${Date.now()}`
    setBusy(true); setError('')
    try {
      await repairBatches.revise(taskId, {
        workflowId, inputDigest, includeHistorical, requestId: requestId.current,
        itemIds: detail.latestAttempt.input.items.map(item => item.itemId), instructions,
        expectedAttemptRevision: detail.latestAttempt.revision,
        parentCandidateCommit: typeof detail.latestSuccessful?.draft?.candidateCommit === 'string' ? detail.latestSuccessful.draft.candidateCommit : null,
        feedback: feedback.trim(),
      })
      requestId.current = ''; setMode('detail'); setFeedback(''); reload()
    } catch (reason) { setError(`修订请求状态未确认，请刷新任务后再决定是否重试：${message(reason)}`) }
    finally { setBusy(false) }
  }

  if (mode === 'feedback' && detail) return <RepairDialog title="反馈并生成下一版" busy={busy} onClose={() => setMode('detail')}>
    <p className="text-xs leading-5 text-slate-500">沿用任务冻结的 {detail.latestAttempt.input.items.length} 个处理项，只生成新的可审阅 Pack 候选，不会应用或部署。</p>
    <label className="mt-4 block text-xs font-medium text-slate-700">修复要求
      <textarea aria-label="修复要求" rows={4} maxLength={20_000} value={instructions} disabled={busy}
        onChange={event => setInstructions(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 p-3 font-normal leading-5" />
    </label>
    <label className="mt-4 block text-xs font-medium text-slate-700">修改反馈
      <textarea aria-label="修改反馈" rows={5} maxLength={20_000} value={feedback} disabled={busy}
        onChange={event => setFeedback(event.target.value)} className="mt-2 w-full rounded-lg border border-slate-200 p-3 font-normal leading-5" />
    </label>
    {error && <p role="alert" className="mt-3 text-xs text-red-600">{error}</p>}
    <button type="button" className={`${primary} mt-4`} disabled={busy || !feedback.trim()} onClick={() => void revise()}>{busy ? '提交中…' : '确认生成下一版'}</button>
  </RepairDialog>

  if (mode === 'cancel' && detail) return <RepairDialog title="取消修复任务" busy={busy} onClose={() => setMode('detail')}>
    <p className="text-xs leading-5 text-slate-500">取消任务会保留候选稿与历史记录，并释放处理项供后续重新选择。</p>
    {error && <p role="alert" className="mt-3 text-xs text-red-600">{error}</p>}
    <button type="button" className={`${primary} mt-4`} disabled={busy} onClick={() => void cancel()}>{busy ? '取消中…' : '确认取消任务'}</button>
  </RepairDialog>

  return <RepairDialog title="修复任务详情" busy={busy} onClose={onClose}>
    {loading && <p role="status" className="text-xs text-slate-500">加载修复任务…</p>}
    {error && <p role="alert" className="text-xs text-red-600">任务读取失败：{error}</p>}
    {detail && <RepairTaskPanel detail={detail} canEdit={canEdit} busy={busy} dispatchError={dispatchError}
      onFeedback={() => { setInstructions(detail.latestAttempt.input.instructions); setError(''); setMode('feedback') }}
      onCancel={() => { setError(''); setMode('cancel') }} onRetryDispatch={() => void retryDispatch()} />}
  </RepairDialog>
}
