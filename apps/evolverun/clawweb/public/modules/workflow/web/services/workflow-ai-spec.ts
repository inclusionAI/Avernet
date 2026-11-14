import { parseDocument, stringify } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

export type WorkflowAiChange = {
  kind: 'add' | 'update' | 'remove'
  nodeId: string
  title: string
  reason: string
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function assertJson(value: unknown, ancestors = new Set<object>(), depth = 0): void {
  if (depth > 80) throw new Error('AI YAML 结构嵌套过深')
  if (typeof value === 'number' && !Number.isFinite(value)) throw new Error('AI YAML 包含非有限数值')
  if (value === null || typeof value !== 'object') return
  if (ancestors.has(value)) throw new Error('AI YAML 包含循环引用')
  ancestors.add(value)
  for (const nested of Object.values(value)) assertJson(nested, ancestors, depth + 1)
  ancestors.delete(value)
}

function canonical(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(canonical)
  if (record(value)) {
    return Object.fromEntries(Object.entries(value).filter(([, item]) => item !== undefined)
      .sort(([a], [b]) => a.localeCompare(b)).map(([key, item]) => [key, canonical(item)]))
  }
  return value
}

/** Exact document identity; includes editor positions to guard against stale overwrites. */
export function workflowSpecSignature(spec: WorkflowSpec): string {
  return JSON.stringify(canonical(spec))
}

export function withoutLayout(spec: WorkflowSpec): WorkflowSpec {
  return {
    ...spec,
    nodes: spec.nodes.map(({ _x, _y, ...node }) => node),
  }
}

export function restoreLayout(spec: WorkflowSpec, source: WorkflowSpec): WorkflowSpec {
  return {
    ...spec,
    nodes: withoutLayout(spec).nodes.map(node => {
      const previous = source.nodes.find(item => item.id === node.id)
      return {
        ...node,
        ...(previous?._x !== undefined ? { _x: previous._x } : {}),
        ...(previous?._y !== undefined ? { _y: previous._y } : {}),
      }
    }),
  }
}

/** Parse untrusted model output, not a markdown extraction or a guessed template. */
export function parseWorkflowAiYaml(yaml: string, source: WorkflowSpec): WorkflowSpec {
  if (typeof yaml !== 'string' || !yaml.trim()) throw new Error('生成结果没有 YAML，请重新描述需求')
  if (new TextEncoder().encode(yaml).byteLength > 100_000) throw new Error('生成的 YAML 超过 100000 UTF-8 字节限制')
  const document = parseDocument(yaml, { uniqueKeys: true })
  if (document.errors.length) throw new Error(`YAML 解析失败：${document.errors[0].message}`)
  const value: unknown = document.toJS({ maxAliasCount: 100 })
  assertJson(value)
  assertWorkflowAiSpec(value, source)
  return restoreLayout(value, source)
}

export function assertWorkflowAiSpec(value: unknown, source: WorkflowSpec): asserts value is WorkflowSpec {
  if (!record(value) || !Array.isArray(value.nodes) || typeof value.title !== 'string' || !value.title.trim()) {
    throw new Error('AI YAML 结构必须包含 id、version、title 和 nodes 数组')
  }
  if (value.id !== source.id) throw new Error('AI 不得修改当前工作流 ID，请在新建工作流中创建独立流程')
  if (value.version !== source.version) throw new Error('AI 不得自动修改工作流版本')
  if (!value.nodes.length) throw new Error('AI YAML 至少需要一个节点')
  const ids = new Set<string>()
  for (const node of value.nodes) {
    if (!record(node) || typeof node.id !== 'string' || !node.id.trim() || !record(node.executor)
      || typeof node.executor.type !== 'string' || !node.executor.type.trim()) {
      throw new Error('AI YAML 每个节点必须有有效 id 和 executor.type')
    }
    if (ids.has(node.id)) throw new Error(`AI YAML 出现重复节点 ID：${node.id}`)
    ids.add(node.id)
    if (node.dependsOn !== undefined && (!Array.isArray(node.dependsOn) || node.dependsOn.some(id => typeof id !== 'string'))) {
      throw new Error(`节点 ${node.id} 的 dependsOn 必须是字符串数组`)
    }
  }
  const nodes = value.nodes as WorkflowSpec['nodes']
  for (const node of nodes) {
    for (const dependency of node.dependsOn ?? []) {
      if (!ids.has(dependency)) throw new Error(`节点 ${node.id} 的依赖 ${dependency} 不存在`)
    }
    // Only inspect the editor's known target-array form. Runtime object branches
    // (if/then/else/branches and extensions) stay opaque for backend validation.
    if (Array.isArray(node.onResult)) {
      if (node.onResult.some(branch => !record(branch) || typeof branch.target !== 'string' || !ids.has(branch.target))) {
        throw new Error(`节点 ${node.id} 的结果分支引用了不存在的节点`)
      }
    }
  }
  const visiting = new Set<string>()
  const visited = new Set<string>()
  const byId = new Map(nodes.map(node => [node.id, node]))
  function visit(id: string) {
    if (visiting.has(id)) throw new Error(`AI YAML 依赖存在环：${id}`)
    if (visited.has(id)) return
    visiting.add(id)
    for (const dependency of byId.get(id)?.dependsOn ?? []) visit(dependency)
    visiting.delete(id)
    visited.add(id)
  }
  for (const id of ids) visit(id)
}

export function describeWorkflowAiChanges(source: WorkflowSpec, next: WorkflowSpec) {
  const before = withoutLayout(source)
  const after = withoutLayout(next)
  const changes: WorkflowAiChange[] = []
  const oldNodes = new Map(before.nodes.map(node => [node.id, node]))
  const newNodes = new Map(after.nodes.map(node => [node.id, node]))
  for (const node of after.nodes) {
    const previous = oldNodes.get(node.id)
    if (!previous) changes.push({ kind: 'add', nodeId: node.id, title: node.title || node.id, reason: 'AI 建议新增节点；请核对配置与连线' })
    else if (JSON.stringify(canonical(previous)) !== JSON.stringify(canonical(node))) {
      changes.push({ kind: 'update', nodeId: node.id, title: node.title || node.id, reason: '节点配置或依赖发生变更，请审阅前后差异' })
    }
  }
  const removedNodeIds = before.nodes.filter(node => !newNodes.has(node.id)).map(node => node.id)
  for (const nodeId of removedNodeIds) {
    changes.push({ kind: 'remove', nodeId, title: oldNodes.get(nodeId)!.title || nodeId, reason: 'AI 建议删除已有节点，需要明确确认' })
  }
  const metadataChanges = [...new Set([...Object.keys(before), ...Object.keys(after)])]
    .filter(key => key !== 'nodes' && JSON.stringify(canonical(before[key])) !== JSON.stringify(canonical(after[key])))
  return { changes, removedNodeIds, metadataChanges }
}

export function workflowAiYaml(spec: WorkflowSpec): string {
  return stringify(withoutLayout(spec), { lineWidth: 0 })
}
