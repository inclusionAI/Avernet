import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { readEditorDraftCas } from '../api/editorDraftCas'
import { UNIFIED_SAVE_TIMEOUT_MS } from '../api/workflowUnifiedSave'
import type { ReconciliationReview } from '../api/workflowSnapshotReconciliation'
import { diff, validateSnapshot } from '../editor/packVersionSession'

/** Re-read both sources immediately before replacing any local editor state. */
export async function revalidateGitComparison(review: ReconciliationReview): Promise<void> {
  let timer: ReturnType<typeof setTimeout> | undefined
  try {
    const [row, raw] = await Promise.race([
      Promise.all([
        readEditorDraftCas(review.workflowId),
        fetchJson<unknown>(`/api/workflows/${encodeURIComponent(review.workflowId)}/pack-version?version=${review.sourceVersion}`, { cache: 'no-store' }),
      ]),
      new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new Error('读取对比基线超时。')), UNIFIED_SAVE_TIMEOUT_MS) }),
    ])
    const snapshot = validateSnapshot(raw, review.workflowId, review.sourceVersion)
    if (row.draftToken !== review.expectedDraftToken || row.specJson !== review.dbSpecJson
      || (row.packId ?? review.workflowId) !== review.packId || snapshot.packId !== review.packId
      || snapshot.baseCommit !== review.sourceCommit || snapshot.draftHead !== review.expectedSavedHead
      || diff(review.filesBefore, snapshot.versionFiles).length) throw new Error('数据库或 Git 基线已变化，请重新查看差异。')
  } finally { clearTimeout(timer) }
}
