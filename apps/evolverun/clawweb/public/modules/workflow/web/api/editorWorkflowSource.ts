import { api } from '@avernet/clawweb-shared/web/api/client'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { parse } from 'yaml'
import { parseWorkflowYaml } from '../editor/yamlDraft'
import { normalizeWorkflowDocument, readWorkflowDocument } from './workflowDocument'

export type EditorWorkflowSource = { spec: WorkflowSpec; databaseSpec: WorkflowSpec | null; activeDeployNumber: number | null }

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

/** Only the exact GET contract means absence. An export/read/permission failure
 * must never authorize replacing a draft with a deployment snapshot. */
function missingDraft(cause: unknown, id: string): boolean {
  if (!record(cause) || cause.status !== 404 || typeof cause.body !== 'string') return false
  let body: unknown
  try { body = JSON.parse(cause.body) } catch { return false }
  if (!record(body) || (body.workflowId !== undefined && body.workflowId !== id)) return false
  if (body.code !== undefined) return body.code === 'WORKFLOW_NOT_FOUND'
  return body.error === 'Not Found' && body.message === `Workflow "${id}" not found`
}

/** null is a confirmed absent workflow_specs draft, not an unavailable service. */
export async function readDatabaseWorkflowDraft(id: string): Promise<WorkflowSpec | null> {
  let document: WorkflowSpec
  try { document = await readWorkflowDocument(id) }
  catch (cause) { if (missingDraft(cause, id)) return null; throw cause }
  return checkedDocument(document, id)
}

function checkedDocument(value: unknown, id: string): WorkflowSpec {
  const spec = parseWorkflowYaml(JSON.stringify(value))
  if (spec.id !== id) throw new Error('工作流内容身份不匹配；未替换草稿')
  const version: unknown = spec.version
  // Legacy null is source data; normalization must be reviewed, not done on read.
  if (!(version === undefined || version === null || typeof version === 'string' && version.trim()
    || typeof version === 'number' && Number.isFinite(version))) throw new Error('工作流源版本结构无效；未替换草稿')
  return spec
}

/** Parse the stored document, never a GET projection or pieces of another source.
 * Reuse the DB boundary's confirmed content-projection handling to retain source
 * versions and genuinely authored content/custom fields. */
function snapshotDocument(text: string, id: string): WorkflowSpec {
  const value: unknown = parse(text, { uniqueKeys: true, maxAliasCount: 100 })
  if (typeof value === 'string') return checkedDocument(parseWorkflowYaml(value), id)
  if (!record(value)) throw new Error('数据库生效快照结构无效；未替换草稿')
  if (Object.hasOwn(value, 'id') && value.id !== id) throw new Error('数据库生效快照内容身份不匹配；未替换草稿')
  if (!Object.hasOwn(value, 'nodes') && typeof value.content === 'string') {
    return checkedDocument(parseWorkflowYaml(value.content), id)
  }
  const exported = checkedDocument(value, id)
  return checkedDocument(normalizeWorkflowDocument(value, id, exported), id)
}

/** Draft first. Only confirmed absence permits falling back to the active DB
 * deployment; the latest history entry (or a Git tree) is never this source. */
export async function readEditorWorkflowSource(id: string): Promise<EditorWorkflowSource> {
  const databaseSpec = await readDatabaseWorkflowDraft(id)
  if (databaseSpec !== null) return { spec: structuredClone(databaseSpec), databaseSpec, activeDeployNumber: null }
  const history = await api.workflows.getHistory(id, 50, true)
  if (!record(history) || history.workflowId !== id || !Array.isArray(history.history)
    || !Object.hasOwn(history, 'active')) throw new Error('数据库工作流历史身份或结构不匹配；未替换草稿')
  const active = history.active
  if (active === null) throw new Error('数据库没有暂存稿或生效版本；未替换草稿')
  if (!record(active) || active.isActive !== true || !Number.isSafeInteger(active.deployNumber)
    || active.deployNumber <= 0 || !Number.isSafeInteger(active.version)) throw new Error('数据库生效版本结构无效；未替换草稿')
  const snapshot = await api.workflows.getDeploySnapshot(id, active.deployNumber)
  if (!record(snapshot) || snapshot.workflowId !== id || snapshot.deployNumber !== active.deployNumber
    || snapshot.version !== active.version || typeof snapshot.specJson !== 'string') {
    throw new Error('数据库生效快照身份或版本不匹配；未替换草稿')
  }
  return { spec: snapshotDocument(snapshot.specJson, id), databaseSpec: null, activeDeployNumber: active.deployNumber }
}
