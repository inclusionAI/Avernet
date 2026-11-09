import { fetchJson } from '@avernet/clawweb-shared/web/api/client'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { normalizeWorkflowSpec } from '../../shared/workflow'
import { normalizationChanges, parseWorkflowYaml } from '../editor/yamlDraft'
import { assertLosslessNumbers } from '../editor/losslessNumbers'

export type EditorDraftCas = { workflowId: string; packId: string | null; specJson: string; draftToken: string }
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)

/** The token and document always come from the same unmodified database row. */
export function validateEditorDraftReceipt(value: unknown, id: string): EditorDraftCas {
  if (!record(value) || value.workflowId !== id || !(value.packId === null || typeof value.packId === 'string' && value.packId.length > 0)
    || typeof value.specJson !== 'string' || typeof value.draftToken !== 'string' || !/^[a-f0-9]{64}$/.test(value.draftToken)) {
    throw new Error('数据库草稿响应身份或结构无效')
  }
  return value as EditorDraftCas
}

/** Only a content-only legacy envelope is unwrapped without a projection.
 * Every complete document is returned raw; its content may be user-authored.
 * The caller compares the entire result with its own baseline before writing. */
export function editorDraftDocument(text: string, id: string, _baseline?: WorkflowSpec): WorkflowSpec {
  assertLosslessNumbers(text)
  const value: unknown = JSON.parse(text)
  if (!record(value)) throw new Error('数据库草稿结构无效')
  if (Object.hasOwn(value, 'id') && value.id !== id) throw new Error('数据库草稿身份不匹配')
  if (!Object.hasOwn(value, 'nodes') && typeof value.content === 'string'
    && Object.keys(value).every(key => key === 'id' || key === 'content')) {
    // Validate other legacy normalization changes, but retain the source document.
    // In particular, normalizing version would hide concurrent type/value changes.
    const source: unknown = parseWorkflowYaml(value.content)
    const inner = normalizeWorkflowSpec(source) as unknown as WorkflowSpec
    if (!record(source)) throw new Error('数据库草稿内容结构无效')
    const { version: _sourceVersion, ...sourceFields } = source
    const { version: _innerVersion, ...innerFields } = inner
    if (normalizationChanges(sourceFields, innerFields).length) throw new Error('数据库草稿归一化会改变原始字段')
    if (source.id !== id) throw new Error('数据库草稿内容身份不匹配')
    return source as unknown as WorkflowSpec
  }
  const raw = parseWorkflowYaml(text)
  if (raw.id !== id) throw new Error('数据库草稿内容身份不匹配')
  return raw
}

export async function readEditorDraftCas(id: string): Promise<EditorDraftCas> {
  const result: unknown = await fetchJson(`/api/workflows/${encodeURIComponent(id)}/editor-draft`, {
    method: 'GET', headers: { 'X-Pack-Version-CSRF': '1' }, cache: 'no-store',
  })
  return validateEditorDraftReceipt(result, id)
}

// Only actual responses observed here can prove a pre-write conflict or a 2xx
// acknowledgement. Arbitrary exceptions from fetch are never evidence.
const conflicts = new WeakSet<object>()
const acknowledged = new WeakSet<object>()
export const isEditorDraftConflict = (value: unknown): boolean => !!value && typeof value === 'object' && conflicts.has(value)
export const isEditorDraftAcknowledged = (value: unknown): boolean => !!value && typeof value === 'object' && acknowledged.has(value)

/** Return the untrusted body for the caller to validate after the write. */
export async function writeEditorDraftCas(id: string, expectedDraftToken: string, spec: WorkflowSpec, packId?: string): Promise<unknown> {
  const user = getClientUser()
  const headers: Record<string, string> = { 'Content-Type': 'application/json', 'X-Pack-Version-CSRF': '1' }
  if (user) { headers['X-User-Id'] = user.userId; headers['X-User-Name'] = encodeURIComponent(user.nickName) }
  const response = await fetch(`/api/workflows/${encodeURIComponent(id)}/editor-draft`, {
    method: 'PUT', headers, cache: 'no-store',
    body: new Blob([JSON.stringify({ expectedDraftToken, spec, ...(packId !== undefined ? { packId } : {}) })], { type: 'application/json' }),
  })
  if (!response.ok) {
    const error = new Error('数据库 CAS 请求失败')
    try {
      const text = await response.text()
      if (response instanceof Response && response.status === 409 && text.length <= 8192) {
        const body: unknown = JSON.parse(text)
        if (record(body) && body.code === 'DRAFT_CONFLICT') conflicts.add(error)
      }
    } catch { /* Unreadable or malformed responses are unknown. */ }
    throw error
  }
  try { return await response.json() as unknown }
  catch {
    const error = new Error('数据库保存响应不是有效 JSON')
    if (response instanceof Response) acknowledged.add(error)
    throw error
  }
}
