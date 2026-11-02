import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { readWorkflowDocument } from '../api/workflowDocument'
import { readDatabaseWorkflowDraft } from '../api/editorWorkflowSource'
import { useEditorStore } from './store'
import { normalizationChanges } from './yamlDraft'

export type WorkflowDocumentSaveOptions = {
  packId?: string
  expectedActiveDeployNumber?: number
  identityWrite?: { newPackId: string; onStart: () => void; onResponse: (packId: string) => void }
}
const savesInFlight = new Set<string>()

/** Optimistic read check only: the existing DB endpoint has no atomic CAS. */
async function verifyDatabaseBaseline(id: string, baseline: WorkflowSpec | null, isCurrent: () => boolean) {
  let before: WorkflowSpec | null
  try { before = await readDatabaseWorkflowDraft(id) }
  catch { throw new Error('无法核对数据库草稿，未发送保存；本地草稿仍保留。') }
  if (normalizationChanges(baseline, before).length) throw new Error('数据库草稿自读取后已变化；未发送保存，请先核对并合并，本地草稿仍保留。')
  if (!isCurrent()) throw new Error('核对数据库期间编辑内容或会话已变化；未发送保存，本地草稿仍保留。')
}

/** The save response has no document. Verify the DB before and after writing;
 * never infer success from normalization or a Git/Pack version response. */
export async function saveVerifiedWorkflowDocument(id: string, baseline: WorkflowSpec | null, requestSpec: WorkflowSpec,
  isCurrent: () => boolean, options: WorkflowDocumentSaveOptions = {}): Promise<WorkflowSpec> {
  if (!id || requestSpec.id !== id || baseline !== null && baseline.id !== id) {
    throw new Error('数据库保存文档身份不匹配；未发送保存，本地草稿仍保留。')
  }
  if (baseline === null && (!Number.isSafeInteger(options.expectedActiveDeployNumber) || (options.expectedActiveDeployNumber ?? 0) <= 0)) {
    throw new Error('数据库保存缺少生效版本基线；未发送保存，本地草稿仍保留。')
  }
  const request = structuredClone(requestSpec)
  await verifyDatabaseBaseline(id, structuredClone(baseline), isCurrent)
  if (!isCurrent()) throw new Error('核对数据库期间编辑内容或会话已变化；未发送保存，本地草稿仍保留。')
  const { packId, identityWrite } = options
  let result: { ok?: boolean; saved?: boolean; workflowId?: string; packId?: string }
  try {
    identityWrite?.onStart()
    result = await fetchJson('/api/auto-heal/save', {
      method: 'POST', headers: { 'X-Pack-Version-CSRF': '1' },
      // No createOnly, sourceCommit or expectedSavedHead: even an absent draft
      // is persisted through the DB-only path for an existing workflow. The
      // absent-only precondition prevents overwriting a concurrently inserted draft.
      body: new Blob([JSON.stringify({ workflowId: id, spec: request, ...(baseline === null ? { expectedDraftAbsent: true, expectedActiveDeployNumber: options.expectedActiveDeployNumber! } : {}), ...(identityWrite ? { packId: identityWrite.newPackId } : {}) })], { type: 'application/json' }),
    })
  } catch (cause) {
    const detail = cause instanceof Error ? `（${cause.message}）` : ''
    throw new Error(`数据库保存失败或结果未知${detail}；本地草稿仍保留，请先核对数据库，不要盲目重试。`)
  }
  if (!result || result.ok !== true || result.saved !== true || result.workflowId !== id
    || typeof result.packId !== 'string' || !result.packId) {
    throw new Error('数据库保存响应无法验证，结果未知；本地草稿仍保留，请核对数据库后再试。')
  }
  identityWrite?.onResponse(result.packId)
  if (packId && result.packId !== packId) throw new Error('数据库已报告保存，但返回的 Pack 身份与预期不一致；本地草稿仍保留，请核对。')
  let stored: WorkflowSpec
  try { stored = await readWorkflowDocument(id) }
  catch { throw new Error('数据库已报告保存成功，但无法重新读取核对；本地草稿仍保留，请先核对数据库。') }
  const changed = normalizationChanges(request, stored)
  if (changed.length) throw new Error(`数据库已报告保存成功，但保存内容与请求不同（字段：${changed.slice(0, 5).join('、')}${changed.length > 5 ? '等' : ''}）；本地草稿仍保留，请核对规范化或并发更改。`)
  return stored
}

/** Persist only the applied canvas document. Pack uploads, locks and file drafts
 * belong to another operation and are deliberately not consulted here. */
export async function saveWorkflowCanvasDocument(id: string): Promise<{ dbSaved: boolean; commit: null }> {
  const live = useEditorStore.getState()
  if (Object.keys(live.pendingEdits).length) throw new Error('存在未应用的配置草稿；请先应用或放弃，再保存。')
  if (live.validationErrors.length) throw new Error(`保存前请修复验证错误：${live.validationErrors[0].message}`)
  if (!live.spec || live.spec.id !== id || !live._savedSpec
    || live._databaseSpec !== null && live._databaseSpec.id !== id
    || live._databaseSpec === null && (!Number.isSafeInteger(live._activeDeployNumber) || (live._activeDeployNumber ?? 0) <= 0)) {
    throw new Error('缺少现有工作流的数据库文档或生效版本基线；本地草稿仍保留。')
  }
  const key = JSON.stringify([live.sessionId, id])
  if (savesInFlight.has(key)) throw new Error('当前工作流正在保存，请等待完成；本地草稿仍保留。')
  const request = structuredClone(live.spec), baseline = structuredClone(live._databaseSpec)
  const isCurrent = () => {
    const now = useEditorStore.getState()
    return now.sessionId === live.sessionId && now.spec?.id === id
      && !normalizationChanges(request, now.spec).length && !normalizationChanges(baseline, now._databaseSpec).length
      && !Object.keys(now.pendingEdits).length && !now.validationErrors.length
  }
  savesInFlight.add(key)
  try {
    const dbSaved = baseline === null || normalizationChanges(request, baseline).length > 0
    let stored: WorkflowSpec
    if (dbSaved) stored = await saveVerifiedWorkflowDocument(id, baseline, request, isCurrent,
      { expectedActiveDeployNumber: live._activeDeployNumber ?? undefined })
    else { await verifyDatabaseBaseline(id, baseline, isCurrent); stored = baseline! }
    const latest = useEditorStore.getState()
    // A POST may finish after navigation. Never write even an error into the new
    // session; same-session edits made after POST stay dirty via completeSave.
    if (latest.sessionId === live.sessionId && latest.spec?.id === id && !latest.completeSave(stored, request, live.sessionId)) {
      throw new Error('数据库文档已核对，但当前编辑器无法确认保存基线；本地草稿仍保留，请核对。')
    }
    return { dbSaved, commit: null }
  } finally { savesInFlight.delete(key) }
}
