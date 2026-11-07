import { useEffect, useLayoutEffect, useRef, useState, type Dispatch, type MutableRefObject, type SetStateAction } from 'react'
import { invalidateWorkflowReconciliation, workflowReconciliationIsCurrent, type ReconciliationContext, type ReconciliationReview } from '../api/workflowSnapshotReconciliation'
import { UNIFIED_SAVE_TIMEOUT_MS } from '../api/workflowUnifiedSave'
import { useEditorStore } from '../editor/store'
import type { usePackCanvasSession } from '../editor/usePackCanvasSession'
import { supportsEditorReconciliation, type EditorReconciliationContract } from './editorReconciliationContract'

type Message = { type: 'success' | 'error'; text: string } | null
function disposeReview(review: ReconciliationReview, adapter: EditorReconciliationContract | null) {
  try { adapter?.cancelReconciliation(review) } catch { /* Cleanup must not hide the preparation error. */ }
  finally { invalidateWorkflowReconciliation(review) }
}

/** Presentation lifetime only; the adapter still owns all pre-save validation. */
export function useEditorMergeReview(
  workflowId: string | null, sessionId: number, packCanvas: ReturnType<typeof usePackCanvasSession>,
  inFlight: MutableRefObject<boolean>, setSaving: Dispatch<SetStateAction<boolean>>,
  setMessage: Dispatch<SetStateAction<Message>>, formatError: (error: unknown) => string,
) {
  const [review, setReview] = useState<{ workflowId: string; sessionId: number; review: ReconciliationReview } | null>(null)
  const [intent, setIntent] = useState<{ workflowId: string; sessionId: number } | null>(null)
  const [preparing, setPreparing] = useState(false)
  const request = useRef(0), selected = useRef(workflowId)
  const activeRequest = useRef<{ cancel: () => void } | null>(null)
  selected.current = workflowId
  const adapter = supportsEditorReconciliation(packCanvas) ? packCanvas : null
  const visible = review?.workflowId === workflowId && review.sessionId === sessionId ? review : null
  const wantsReview = intent?.workflowId === workflowId && intent.sessionId === sessionId
  let current: ReconciliationContext | null = null
  if (visible && adapter) {
    try { current = adapter.currentReconciliation(visible.review) } catch { invalidateWorkflowReconciliation(visible.review) }
  }
  useLayoutEffect(() => {
    request.current++
    return () => { request.current++; activeRequest.current?.cancel() }
  }, [workflowId, sessionId])
  useEffect(() => {
    if (!review || adapter) return
    invalidateWorkflowReconciliation(review.review)
    setReview(null)
  }, [adapter, review])
  useEffect(() => () => { if (review) invalidateWorkflowReconciliation(review.review) }, [review])
  const clear = () => { if (visible) invalidateWorkflowReconciliation(visible.review); setReview(null); setIntent(null) }
  const cancel = () => { if (visible) { disposeReview(visible.review, adapter); setReview(null) } }
  const open = async () => {
    if (!adapter || !workflowId || inFlight.current) return
    const id = workflowId, session = useEditorStore.getState().sessionId, token = ++request.current
    const isCurrent = () => token === request.current && selected.current === id && useEditorStore.getState().sessionId === session
    let accepting = true, timer: ReturnType<typeof setTimeout> | undefined
    let interrupt!: (error: Error) => void
    const interrupted = new Promise<never>((_, reject) => { interrupt = reject })
    // Only this preparation may release its lock. A late finally must never
    // unlock a replacement preparation or an ordinary save in a new session.
    const finish = () => {
      if (activeRequest.current !== operation) return
      clearTimeout(timer)
      activeRequest.current = null
      inFlight.current = false; setSaving(false); setPreparing(false)
    }
    const operation = { cancel: () => {
      if (activeRequest.current !== operation) return
      accepting = false
      finish()
      interrupt(new Error('审阅准备已取消；未发送保存。'))
    } }
    activeRequest.current = operation
    setIntent({ workflowId: id, sessionId: session }); setPreparing(true)
    inFlight.current = true; setSaving(true)
    let prepared: ReconciliationReview | null = null
    try {
      // Observe the original promise even after the race ends: a late review
      // is a capability to dispose, not a result to adopt in another request.
      const pending = adapter.prepareReconciliation().then(candidate => {
        if (!accepting || activeRequest.current !== operation || !isCurrent()) {
          disposeReview(candidate, adapter)
          throw new Error('审阅准备期间工作流或基线已变化；未发送保存。')
        }
        prepared = candidate
        return candidate
      })
      timer = setTimeout(() => {
        accepting = false
        interrupt(new Error('差异审阅准备超时；未发送保存，本地草稿仍保留。'))
      }, UNIFIED_SAVE_TIMEOUT_MS)
      prepared = await Promise.race([pending, interrupted])
      if (!isCurrent() || prepared.workflowId !== id || !workflowReconciliationIsCurrent(prepared, adapter.currentReconciliation(prepared))) {
        throw new Error('审阅准备期间工作流或基线已变化；未发送保存。')
      }
      setReview({ workflowId: id, sessionId: session, review: prepared }); prepared = null; setMessage(null)
    } catch (cause) {
      if (prepared) disposeReview(prepared, adapter)
      if (activeRequest.current === operation && isCurrent()) setMessage({ type: 'error', text: `无法准备审阅：${formatError(cause)}` })
    } finally { accepting = false; finish() }
  }
  return { mergeAdapter: adapter, visibleMerge: visible, mergeCurrent: current, wantsReview, preparing, openMergeReview: open, cancelMergeReview: cancel, clearMergeReview: clear }
}
