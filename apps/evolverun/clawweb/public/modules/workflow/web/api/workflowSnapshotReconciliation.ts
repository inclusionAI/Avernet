import type { Change, Version } from '../editor/packVersionSession'
import { assertLosslessNumbers } from '../editor/losslessNumbers'
import { serializeUnifiedSpec } from './workflowUnifiedSave'
import { containsUnsafeReviewText, reconciliationHasBinaryFiles, validateReconciliationFiles } from './workflowReconciliationFiles'
import type { ReconciliationFile } from './workflowReconciliationFiles'

/** All values must come from the same guarded editor read, not a reconstructed DB document.
 * revision must change monotonically on *any* relevant editor/session change, even if undone. */
export type ReconciliationContext = {
  workflowId: string; packId: string; sourceVersion: Version; sourceCommit: string
  expectedSavedHead: string | null; expectedDraftToken: string; workflowPath: string
  dbSpecJson: string; gitYaml: string; candidateSpecJson: string; changes: Change[]; filesBefore: ReconciliationFile[]; filesAfter: ReconciliationFile[]; revision: string
}
export type ReconciliationReview = Readonly<ReconciliationContext>
export type ReconciliationApproval = Readonly<{ specJson: string }>
const reviews = new WeakMap<ReconciliationReview, { encoded: string; owner: ReconciliationOwner; generation: number }>()
const approvals = new WeakMap<ReconciliationApproval, { review: ReconciliationReview; context: string; specJson: string; generation: number }>()
const id = /^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$/
const oid = /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/
const hashPattern = /^[a-f0-9]{64}$/

function document(text: string, workflowId: string): void {
  if (typeof text !== 'string') throw new Error('工作流文档无效；未确认。')
  if (containsUnsafeReviewText(text)) throw new Error('最终文档含不可安全显示的字符；未确认。')
  assertLosslessNumbers(text)
  const value: unknown = JSON.parse(text)
  if (!value || typeof value !== 'object' || Array.isArray(value) || (value as { id?: unknown }).id !== workflowId) {
    throw new Error('最终文档的工作流身份不匹配；未确认。')
  }
  serializeUnifiedSpec(value)
}
function encode(context: ReconciliationContext): string {
  if (!context || !id.test(context.workflowId) || !id.test(context.packId)
    || context.workflowId.includes('..') || context.packId.includes('..')
    || !oid.test(context.sourceCommit) || !hashPattern.test(context.expectedDraftToken)
    || (context.sourceVersion !== 'draft' && context.sourceVersion !== 'current')
    || !(context.expectedSavedHead === null || oid.test(context.expectedSavedHead))
    || (context.sourceVersion === 'draft' && context.expectedSavedHead !== context.sourceCommit)
    || (context.sourceVersion === 'current' && context.expectedSavedHead !== null && context.expectedSavedHead !== context.sourceCommit)
    || typeof context.workflowPath !== 'string' || !context.workflowPath || typeof context.gitYaml !== 'string'
    || typeof context.dbSpecJson !== 'string' || typeof context.candidateSpecJson !== 'string'
    || typeof context.revision !== 'string' || !context.revision || !Array.isArray(context.changes)) {
    throw new Error('合并审阅基线无效；未确认。')
  }
  validateReconciliationFiles(context.filesBefore, context.filesAfter, context.changes, context.workflowPath, context.gitYaml)
  // Include both complete file snapshots and all proposed changes.
  return serializeUnifiedSpec({ workflowId: context.workflowId, packId: context.packId, sourceVersion: context.sourceVersion,
    sourceCommit: context.sourceCommit, expectedSavedHead: context.expectedSavedHead, expectedDraftToken: context.expectedDraftToken,
    workflowPath: context.workflowPath, dbSpecJson: context.dbSpecJson, gitYaml: context.gitYaml,
    candidateSpecJson: context.candidateSpecJson, changes: context.changes, filesBefore: context.filesBefore, filesAfter: context.filesAfter, revision: context.revision })
}

/** A caller-owned lifetime. Keep one owner per editor review surface. Closing,
 * opening a replacement, or editing invalidates every earlier approval synchronously. */
export type ReconciliationOwner = Readonly<{
  open: (context: ReconciliationContext) => ReconciliationReview
  edited: () => void
  close: () => void
}>
export function createWorkflowReconciliationOwner(): ReconciliationOwner {
  let active: ReconciliationReview | undefined
  const owner: ReconciliationOwner = Object.freeze({
    open(context: ReconciliationContext) {
      if (active) invalidateWorkflowReconciliation(active)
      active = undefined
      const encoded = encode(context)
      const copy = JSON.parse(encoded) as ReconciliationContext
      for (const item of [...copy.changes, ...copy.filesBefore, ...copy.filesAfter]) Object.freeze(item)
      Object.freeze(copy.changes); Object.freeze(copy.filesBefore); Object.freeze(copy.filesAfter)
      const review = Object.freeze(copy)
      reviews.set(review, { encoded, owner, generation: 0 })
      active = review
      return review
    },
    edited() { if (active) revokeWorkflowReconciliationApprovals(active) },
    close() { if (active) invalidateWorkflowReconciliation(active); active = undefined },
  })
  return owner
}

/** Observing a mismatch permanently ends this review, even if values are restored. */
export function workflowReconciliationIsCurrent(review: ReconciliationReview, current: ReconciliationContext): boolean {
  const state = reviews.get(review)
  if (!state) return false
  try {
    if (state.encoded === encode(current) && state.encoded === encode(review)) return true
  } catch { /* An invalid context is also an observed mismatch. */ }
  invalidateWorkflowReconciliation(review)
  return false
}

/** Editing review fields must revoke earlier approvals, even if the value is restored. */
export function revokeWorkflowReconciliationApprovals(review: ReconciliationReview): void {
  const state = reviews.get(review)
  if (state) state.generation++
}

/** Call only after explicit UI confirmation and review of the complete documents. */
export function confirmWorkflowReconciliation(review: ReconciliationReview, current: ReconciliationContext, specJson: string): ReconciliationApproval {
  if (!workflowReconciliationIsCurrent(review, current)) throw new Error('审阅后基线或草稿已变化；请重新核对。')
  if (containsUnsafeReviewText(review.dbSpecJson) || containsUnsafeReviewText(review.candidateSpecJson)
    || containsUnsafeReviewText(review.gitYaml)) throw new Error('审阅文档含不可安全显示的字符；不能确认。')
  if (reconciliationHasBinaryFiles(review.filesBefore, review.filesAfter)) throw new Error('存在二进制或无法安全展示的文件；不能确认已审阅。')
  document(specJson, review.workflowId)
  const state = reviews.get(review)!
  state.generation++ // only the most recently confirmed selection remains valid
  const approval = Object.freeze({ specJson })
  approvals.set(approval, { review, context: state.encoded, specJson, generation: state.generation })
  return approval
}

export function invalidateWorkflowReconciliation(review: ReconciliationReview): void {
  reviews.delete(review)
}

/** Immediately before POST, compare freshly observed state and still send original CAS tokens. */
export function assertWorkflowReconciliation(approval: ReconciliationApproval, current: ReconciliationContext, specJson: string): void {
  const proof = approval && approvals.get(approval)
  const state = proof && reviews.get(proof.review)
  if (!proof || !state) throw new Error('合并确认缺失或确认后内容已变化；未发送保存。')
  const currentReview = workflowReconciliationIsCurrent(proof.review, current)
  if (!currentReview || proof.generation !== state.generation || proof.specJson !== specJson || proof.context !== state.encoded) throw new Error('合并确认缺失或确认后内容已变化；未发送保存。')
}
