import { stringify } from 'yaml'
// This validator has no Node dependencies: its workflow import re-exports the
// browser-safe shared normalization contract. Reuse the server's exact graph
// validation instead of maintaining a second approximation in the editor.
import { validateSpec as validateServerSpec } from '../../server/validators/workflow-validator'
import { validateWorkflowSemantics } from '../../shared/workflow'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { assertLosslessNumbers } from '../editor/losslessNumbers'
import { serializeUnifiedSpec } from './workflowUnifiedSave'

const MAX_BYTES = 1024 * 1024, MAX_NODES = 512, MAX_LOOP_DEPTH = 8, MAX_VALUES = 100_000, MAX_DEPTH = 100
const invalidUnicode = /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u
const record = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value)
const fail = (message: string): never => { throw new Error(`最终文档验证失败：${message}；未发送保存，本地草稿仍保留。`) }

/** Match workflow-snapshot-projection's bounded raw-node checks before the
 * server normalizer can discard malformed fields. The server remains the
 * authoritative validator and revalidates every request. */
export function validateWorkflowSnapshotRequest(text: string, workflowId: string): WorkflowSpec {
  if (typeof text !== 'string' || invalidUnicode.test(text) || new TextEncoder().encode(text).length > MAX_BYTES) return fail('JSON 无效或超过大小限制')
  let value: unknown
  try { assertLosslessNumbers(text); value = JSON.parse(text); serializeUnifiedSpec(value) }
  catch { return fail('JSON 无效、存在重复键或无法无损表示') }
  if (!record(value) || !workflowId || workflowId.trim() !== workflowId || value.id !== workflowId) return fail('工作流身份不匹配')
  const pendingValues: Array<{ value: unknown; depth: number }> = [{ value, depth: 0 }]
  let values = 0
  while (pendingValues.length) {
    const item = pendingValues.pop()!
    if (++values > MAX_VALUES || item.depth > MAX_DEPTH) return fail('文档值数量或嵌套深度超过限制')
    if (typeof item.value === 'string') { if (invalidUnicode.test(item.value)) return fail('包含无效 Unicode'); continue }
    if (!item.value || typeof item.value !== 'object') continue
    for (const [key, child] of Object.entries(item.value)) {
      if (invalidUnicode.test(key)) return fail('字段名包含无效 Unicode')
      pendingValues.push({ value: child, depth: item.depth + 1 })
    }
  }
  const pendingNodes: Array<{ nodes: unknown[]; depth: number }> = []
  let count = 0
  const enqueue = (nodes: unknown, depth: number, nonempty: boolean) => {
    if (!Array.isArray(nodes) || nonempty && !nodes.length) return fail('循环或模板 body 必须是非空节点数组')
    if (depth > MAX_LOOP_DEPTH) return fail('循环嵌套深度超过限制')
    count += nodes.length
    if (count > MAX_NODES) return fail('节点总数超过限制')
    pendingNodes.push({ nodes, depth })
  }
  enqueue(value.nodes, 0, false)
  if (Object.hasOwn(value, 'nodeTemplates')) {
    if (!record(value.nodeTemplates)) return fail('nodeTemplates 必须是对象')
    for (const template of Object.values(value.nodeTemplates)) {
      if (!record(template)) return fail('节点模板必须是对象')
      enqueue(template.body, 0, true)
    }
  }
  while (pendingNodes.length) {
    const { nodes, depth } = pendingNodes.pop()!
    for (const node of nodes) {
      if (!record(node)) return fail('节点必须是对象')
      if (Object.hasOwn(node, 'dependsOn') && (!Array.isArray(node.dependsOn) || node.dependsOn.some(dep => typeof dep !== 'string'))) return fail('dependsOn 必须是字符串数组')
      if (Object.hasOwn(node, 'triggerRule') && node.triggerRule !== 'all_success' && node.triggerRule !== 'one_success' && node.triggerRule !== 'all_done') return fail('triggerRule 无效')
      if (typeof node.id !== 'string' || !node.id.trim()) return fail('节点 id 无效')
      const executor = record(node.executor) ? node.executor : null
      if (executor?.type === 'loop-group') enqueue(executor.body, depth + 1, true)
      else if (node.type === 'loop-group') enqueue(node.body, depth + 1, true)
      else if (!executor || typeof executor.type !== 'string' || !executor.type.trim()) return fail('节点 executor.type 无效')
    }
  }
  const validation = validateServerSpec(value)
  if (!validation.valid || !validation.normalizedSpec || validation.normalizedSpec.id !== workflowId) return fail(validation.issues[0]?.message ?? '工作流结构无效')
  try { validateWorkflowSemantics(validation.normalizedSpec) } catch { return fail('工作流语义无效') }
  const yaml = stringify(value, { sortMapEntries: true, lineWidth: 0 })
  if (new TextEncoder().encode(yaml).length > MAX_BYTES) return fail('生成的 YAML 超过大小限制')
  return value as unknown as WorkflowSpec
}
