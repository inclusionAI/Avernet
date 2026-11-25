import type { ReconciliationApproval, ReconciliationContext, ReconciliationReview } from '../api/workflowSnapshotReconciliation'
import type { PackCanvasSaveResult } from '../editor/usePackCanvasSession'

/**
 * Page/hook boundary proposal. The hook owns the review lifetime and MUST
 * re-read/check the exact DB token, Git head, Pack files, editor lifetime and
 * monotonic revision immediately before dispatch; it also owns the existing
 * write guard, timeout and postflight. No page-side check grants permission.
 *
 * This is optional until the in-progress hook implementation adopts it. The
 * page must not fall back to an ordinary save when the contract is absent.
 */
export type EditorReconciliationContract = {
  prepareReconciliation: () => Promise<ReconciliationReview>
  currentReconciliation: (review: ReconciliationReview) => ReconciliationContext
  saveReconciled: (approval: ReconciliationApproval) => Promise<PackCanvasSaveResult>
  cancelReconciliation: (review: ReconciliationReview) => void
}

export function supportsEditorReconciliation(value: unknown): value is EditorReconciliationContract {
  if (!value || typeof value !== 'object') return false
  const candidate = value as Partial<EditorReconciliationContract>
  return typeof candidate.prepareReconciliation === 'function' && typeof candidate.currentReconciliation === 'function'
    && typeof candidate.saveReconciled === 'function' && typeof candidate.cancelReconciliation === 'function'
}
