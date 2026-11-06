import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { originalDraftPackId, readDatabaseWorkflowDraft, verifyOriginalActiveDraftBaseline, type ActiveDraftBaseline } from '../api/editorWorkflowSource'
import { writeEditorFirstDraft } from '../api/editorFirstDraft'
import { editorDraftDocument, isEditorDraftAcknowledged, isEditorDraftConflict, readEditorDraftCas, validateEditorDraftReceipt, writeEditorDraftCas } from '../api/editorDraftCas'
import { useEditorStore } from './store'
import { normalizationChanges, parseWorkflowYaml } from './yamlDraft'
import { abandonEditorWorkflowWrite, assertEditorWorkflowReadable, beginEditorWorkflowWrite, finishEditorWorkflowWrite } from './workflowWriteGuard'

export type WorkflowDocumentSaveOptions = {
  packId?: string
  expectedRawPackId?: string | null
  expectedActiveDeployNumber?: number
  activeBaseline?: ActiveDraftBaseline
  identityWrite?: { newPackId: string; onStart: () => void; onResponse: (packId: string) => void }
}
/** A write may have reached the server, but the saved document is not verified. */
export class WorkflowDocumentAcknowledgedError extends Error {
  constructor(readonly workflowId: string, message: string) {
    super(message)
    this.name = 'WorkflowDocumentAcknowledgedError'
  }
}
/** Transport failures after dispatch cannot prove whether the server committed.
 * Extend the common possibly-written category so callers preserve its recovery path. */
export class WorkflowDocumentUnknownWriteError extends WorkflowDocumentAcknowledgedError {
  constructor(workflowId: string, message: string) {
    super(workflowId, message)
    this.name = 'WorkflowDocumentUnknownWriteError'
  }
}
const savesInFlight = new Set<string>()
class PrewriteConflict extends Error {}

async function guardedWrite<T>(id: string, write: () => Promise<T>): Promise<T> {
  const owner = beginEditorWorkflowWrite(id)
  try {
    const result = await write()
    try { finishEditorWorkflowWrite(id, owner) }
    catch { throw new WorkflowDocumentAcknowledgedError(id, '数据库已响应，但无法安全清除写入标记；请人工核对，本地草稿仍保留。') }
    return result
  } catch (cause) {
    if (cause instanceof PrewriteConflict) {
      try { finishEditorWorkflowWrite(id, owner) }
      catch { throw new Error('保存冲突，但写入状态标记无法清除；请人工核对。') }
    } else abandonEditorWorkflowWrite(id, owner)
    throw cause
  }
}

/** Existing drafts must be checked using the same row that supplies the CAS token. */
export async function verifyDatabaseBaseline(id: string, baseline: WorkflowSpec | null, isCurrent: () => boolean, expectedRawPackId: string | null | undefined) {
  if (baseline === null || expectedRawPackId === undefined) throw new Error('缺少可信的原始数据库 Pack 身份基线；未发送保存，本地草稿仍保留。')
  let before: WorkflowSpec
  let row: Awaited<ReturnType<typeof readEditorDraftCas>>
  try { row = await readEditorDraftCas(id); before = editorDraftDocument(row.specJson, id, baseline) }
  catch { throw new Error('无法核对数据库草稿（无效或含额外字段的历史包装会拒绝）；未发送保存，本地草稿仍保留。') }
  if (row.packId !== expectedRawPackId) throw new Error('数据库草稿 Pack 身份自读取后已变化；未发送保存，本地草稿仍保留。')
  if (normalizationChanges(baseline, before).length) throw new Error('数据库草稿自读取后已变化；未发送保存，请先核对并合并，本地草稿仍保留。')
  if (!isCurrent()) throw new Error('核对数据库期间编辑内容或会话已变化；未发送保存，本地草稿仍保留。')
  return row
}

/** Observation only: never substitute a fresh active identity for the original. */
async function verifyActiveBaseline(id: string, baseline: ActiveDraftBaseline, isCurrent: () => boolean) {
  try { await verifyOriginalActiveDraftBaseline(id, baseline) }
  catch { throw new Error('无法核对原始生效版本或草稿状态；未发送保存，本地草稿仍保留。') }
  if (!isCurrent()) throw new Error('核对数据库期间编辑内容或会话已变化；未发送保存，本地草稿仍保留。')
}

/** Existing drafts use same-row CAS. First drafts require a matching active
 * snapshot token, and the server atomically checks it before insertion. */
export async function saveVerifiedWorkflowDocument(id: string, baseline: WorkflowSpec | null, requestSpec: WorkflowSpec,
  isCurrent: () => boolean, options: WorkflowDocumentSaveOptions = {}): Promise<WorkflowSpec> {
  assertEditorWorkflowReadable(id)
  if (!id || requestSpec.id !== id || baseline !== null && baseline.id !== id) {
    throw new Error('数据库保存文档身份不匹配；未发送保存，本地草稿仍保留。')
  }
  if (options.identityWrite) {
    throw new Error('暂不支持通过编辑器保存更改 Pack 身份；未发送保存，本地草稿仍保留。')
  }
  if (baseline === null && (!Number.isSafeInteger(options.expectedActiveDeployNumber) || (options.expectedActiveDeployNumber ?? 0) <= 0)) {
    throw new Error('数据库保存缺少生效版本基线；未发送保存，本地草稿仍保留。')
  }
  const expectedRawPackId = options.expectedRawPackId !== undefined ? options.expectedRawPackId : originalDraftPackId(baseline)
  const request = structuredClone(requestSpec)
  if (baseline === null) {
    const active = options.activeBaseline ? structuredClone(options.activeBaseline) : undefined
    if (!active || active.workflowId !== id || active.packId !== expectedRawPackId
      || active.deployNumber !== options.expectedActiveDeployNumber || !/^[a-f0-9]{64}$/.test(active.activeToken)
      || options.packId !== undefined && options.packId !== active.packId) {
      throw new Error('缺少可信的原始生效版本 Pack 和 token 基线；未发送保存，本地草稿仍保留。')
    }
    await verifyActiveBaseline(id, active, isCurrent)
    return guardedWrite(id, async () => {
      let result: Awaited<ReturnType<typeof writeEditorFirstDraft>>
      try {
        result = await writeEditorFirstDraft({ workflowId: id, spec: request, packId: active.packId, expectedDraftAbsent: true,
          expectedActiveDeployNumber: active.deployNumber, expectedActiveToken: active.activeToken })
      } catch {
        throw new WorkflowDocumentUnknownWriteError(id, '首次数据库保存失败或结果未知；本地草稿仍保留，请先核对数据库，不要盲目重试。')
      }
      if (result.kind === 'conflict') throw new PrewriteConflict('首次数据库草稿保存冲突；本地草稿仍保留，请先核对并合并。')
      const response = result.body
      try {
        if (!response || typeof response !== 'object' || !('ok' in response) || response.ok !== true || !('saved' in response) || response.saved !== true || !('workflowId' in response) || response.workflowId !== id || !('packId' in response) || response.packId !== active.packId) throw new Error('创建回执身份不匹配')
        const stored = await readDatabaseWorkflowDraft(id)
        if (!stored || originalDraftPackId(stored) !== active.packId || normalizationChanges(request, stored).length) throw new Error('数据库文档或 Pack 身份不匹配')
        return stored
      } catch { throw new WorkflowDocumentAcknowledgedError(id, '数据库可能已写入，但首次草稿回执无法验证；本地草稿仍保留，请先核对数据库，勿盲目重试。') }
    })
  }
  const row = await verifyDatabaseBaseline(id, structuredClone(baseline), isCurrent, expectedRawPackId)
  if (!isCurrent()) throw new Error('核对数据库期间编辑内容或会话已变化；未发送保存，本地草稿仍保留。')
  const { packId } = options
  // A NULL database binding addresses the workflow's own Pack without
  // changing the stored binding. The CAS request must still omit packId.
  if (packId !== undefined && packId !== (row.packId ?? id)) {
    throw new Error('数据库草稿 Pack 身份与预期不一致；未发送保存，本地草稿仍保留。')
  }
  return guardedWrite(id, async () => {
  let response: unknown
  try {
    response = await writeEditorDraftCas(id, row.draftToken, request)
  } catch (cause) {
    if (isEditorDraftConflict(cause)) {
      throw new PrewriteConflict('数据库草稿保存冲突；本地草稿仍保留，请先核对并合并。')
    }
    if (isEditorDraftAcknowledged(cause)) {
      throw new WorkflowDocumentAcknowledgedError(id, '数据库可能已写入，但保存响应不是有效 JSON；本地草稿仍保留，请先核对数据库，勿盲目重试。')
    }
    throw new WorkflowDocumentUnknownWriteError(id, '数据库保存失败或结果未知；本地草稿仍保留，请先核对数据库，不要盲目重试。')
  }
  try {
    const receipt = validateEditorDraftReceipt(response, id)
    if (receipt.specJson !== row.specJson && receipt.draftToken === row.draftToken) throw new Error('数据库保存响应 token 未更新')
    const expectedPackId = row.packId
    if (receipt.packId !== expectedPackId || (packId !== undefined && (receipt.packId ?? id) !== packId)) throw new Error('数据库保存响应 Pack 身份与预期不一致')
    // A saved spec is a raw document, not a legacy read projection: never
    // unwrap an authored content field in the write acknowledgement.
    JSON.parse(receipt.specJson) // The receipt contract requires JSON, not arbitrary YAML.
    const stored = parseWorkflowYaml(receipt.specJson)
    if (stored.id !== id || normalizationChanges(request, stored).length) throw new Error('数据库保存内容与请求不同')
    return stored
  } catch (cause) {
    const detail = cause instanceof Error ? cause.message : '数据库保存响应无法验证'
    throw new WorkflowDocumentAcknowledgedError(id, `数据库可能已写入，但回执无法验证（${detail}）；本地草稿仍保留，请先核对数据库，勿盲目重试。`)
  }
  })
}

/** Persist only the applied canvas document. Pack uploads, locks and file drafts
 * belong to another operation and are deliberately not consulted here. */
export async function saveWorkflowCanvasDocument(id: string): Promise<{ dbSaved: boolean; commit: null }> {
  assertEditorWorkflowReadable(id)
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
  const request = structuredClone(live.spec), baseline = structuredClone(live._databaseSpec), activeBaseline = structuredClone(live._activeBaseline)
  const isCurrent = () => {
    const now = useEditorStore.getState()
    return now.sessionId === live.sessionId && now.spec?.id === id
      && !normalizationChanges(request, now.spec).length && !normalizationChanges(baseline, now._databaseSpec).length
      && now._databasePackId === live._databasePackId && !normalizationChanges(now._activeBaseline, activeBaseline).length
      && !Object.keys(now.pendingEdits).length && !now.validationErrors.length
  }
  savesInFlight.add(key)
  try {
    const dbSaved = baseline === null || normalizationChanges(request, baseline).length > 0
    let stored: WorkflowSpec
    if (dbSaved) stored = await saveVerifiedWorkflowDocument(id, baseline, request, isCurrent,
      { expectedActiveDeployNumber: live._activeDeployNumber ?? undefined, expectedRawPackId: live._databasePackId, activeBaseline })
    else { await verifyDatabaseBaseline(id, baseline, isCurrent, live._databasePackId); stored = baseline! }
    const latest = useEditorStore.getState()
    // A POST may finish after navigation. Never write even an error into the new
    // session; same-session edits made after POST stay dirty via completeSave.
    if (latest.sessionId === live.sessionId && latest.spec?.id === id && !latest.completeSave(stored, request, live.sessionId, baseline === null ? stored : undefined, baseline === null ? activeBaseline : undefined)) {
      throw new Error('数据库文档已核对，但当前编辑器无法确认保存基线；本地草稿仍保留，请核对。')
    }
    return { dbSaved, commit: null }
  } finally { savesInFlight.delete(key) }
}
