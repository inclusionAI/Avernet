import { api } from '@avernet/clawweb-shared/web/api/client'
import { getClientUser } from '@avernet/clawweb-shared/web/hooks/useClientUser'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { parseWorkflowYaml, normalizationChanges } from '../editor/yamlDraft'

function record(value: unknown): value is Record<string, unknown> {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function comparable(value: Record<string, unknown>) {
  const { content: _content, version: _version, updatedAt: _updatedAt, facade: _facade, ...fields } = value
  return { ...fields, nodes: Array.isArray(fields.nodes) ? fields.nodes.map(node => {
    if (!record(node)) return node
    const { _x, _y, ...execution } = node
    return execution
  }) : fields.nodes }
}
function withDatabaseLayout(source: WorkflowSpec, response: Record<string, unknown>): WorkflowSpec {
  const nodes = Array.isArray(response.nodes) ? response.nodes : []
  return { ...source, nodes: source.nodes.map(node => {
    const dbNode = nodes.find(value => record(value) && value.id === node.id)
    return record(dbNode) && Number.isFinite(dbNode._x) && Number.isFinite(dbNode._y)
      ? { ...node, _x: dbNode._x, _y: dbNode._y } : node
  }) }
}
function verifyProjection(response: Record<string, unknown>, source: WorkflowSpec) {
  // Legacy wrappers have no flattened execution fields to compare. New projections
  // must not use content to hide a contradictory DB node/config/custom field.
  if (Array.isArray(response.nodes) && normalizationChanges(comparable(response), comparable(source)).length) {
    throw new Error('数据库工作流投影与原文执行内容不一致；未替换草稿')
  }
}

/** Existing candidateProjection convention: the stored outer fields are a
 * normalized copy of its content YAML. Check ALL source fields, including the
 * unoverwritten version, before treating content as this reserved wrapper shape.
 * Exact user-authored lookalikes are intrinsically ambiguous in the legacy API. */
function sourceDocument(exported: WorkflowSpec): WorkflowSpec {
  if (typeof exported.content !== 'string') return exported
  let inner: WorkflowSpec
  try { inner = parseWorkflowYaml(exported.content) } catch { return exported }
  if (inner.id !== exported.id) return exported
  const projection = (spec: WorkflowSpec) => {
    const { content: _content, ...fields } = spec
    return { ...fields, version: String(spec.version ?? '1'),
      nodes: spec.nodes.map(node => { const { _x, _y, ...execution } = node; return execution }) }
  }
  return normalizationChanges(projection(exported), projection(inner)).length === 0 ? inner : exported
}

/** Only this API-envelope boundary unwraps a confirmed projection, once. */
export function normalizeWorkflowDocument(response: unknown, id: string, exported?: WorkflowSpec): WorkflowSpec {
  if (!record(response) || response.id !== id) throw new Error('工作流响应身份不匹配')
  if (!exported && typeof response.content === 'string') throw new Error('需要未覆盖的数据库原文来区分 content 投影与用户字段')
  const source = exported ? sourceDocument(exported) : structuredClone(response) as unknown as WorkflowSpec
  if (source.id !== id) throw new Error('工作流内容身份不匹配')
  if (!Array.isArray(source.nodes)) throw new Error('工作流响应结构无效')
  verifyProjection(response, source)
  return withDatabaseLayout(source, response)
}

/** Older GETs have lost their source version/facade. Recover a COMPLETE original
 * DB document from export; never stitch its version onto GET nodes or guess from Git. */
export async function readWorkflowDocument(id: string): Promise<WorkflowSpec> {
  const response: unknown = await api.workflows.get(id)
  if (!record(response) || response.id !== id) throw new Error('工作流响应身份不匹配')
  if (typeof response.content !== 'string' && typeof response.version !== 'number'
    && !Object.hasOwn(response, 'updatedAt') && !Object.hasOwn(response, 'facade')) {
    return normalizeWorkflowDocument(response, id)
  }
  const user = getClientUser()
  const headers: Record<string, string> = { Accept: 'application/x-yaml' }
  if (user) { headers['X-User-Id'] = user.userId; headers['X-User-Name'] = encodeURIComponent(user.nickName) }
  const exported = await fetch(`/api/workflows/${encodeURIComponent(id)}/export`, { cache: 'no-store', headers })
  if (!exported.ok) throw new Error('无法读取未覆盖的数据库工作流原文；未替换当前草稿')
  return normalizeWorkflowDocument(response, id, parseWorkflowYaml(await exported.text()))
}
