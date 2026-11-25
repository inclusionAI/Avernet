import type { WorkflowNode, WorkflowSpec } from '@avernet/clawweb-shared/web/types'
import { getResultBranchIds, getResultBranches, hasResultBranches, isRecord } from './contracts'

export interface Position { x: number; y: number }
export interface ValidationError {
  type: 'cycle' | 'missing-field' | 'missing-config' | 'invalid-structure' | 'duplicate-id' | 'invalid-edge' | 'missing-reference' | 'branch-conflict'
  nodeId?: string
  message: string
}

export function dependencies(node: WorkflowNode): string[] {
  return Array.isArray(node.dependsOn) ? node.dependsOn.filter((id) => typeof id === 'string') : []
}

function indexGraph(nodes: WorkflowNode[]) {
  const byId = new Map(nodes.filter((node) => isRecord(node) && typeof node.id === 'string').map((node) => [node.id, node]))
  const children = new Map([...byId.keys()].map((id) => [id, [] as string[]]))
  const indegree = new Map([...byId.keys()].map((id) => [id, 0]))
  for (const node of byId.values()) {
    for (const dep of new Set(dependencies(node))) {
      if (!byId.has(dep)) continue
      children.get(dep)!.push(node.id)
      indegree.set(node.id, indegree.get(node.id)! + 1)
    }
  }
  return { byId, children, indegree }
}

/** Branch lookup may pass through ordinary dependencies, but a branch-producing
 * node is a new runtime origin: its ancestors do not gate its descendants.
 * Traverse once per named branch, iteratively, so cyclic imports stay bounded. */
function branchReachability(nodes: WorkflowNode[]): Map<string, Set<string>> {
  const { byId, children } = indexGraph(nodes)
  const origins = new Map<string, string[]>()
  for (const node of byId.values()) {
    for (const branchId of getResultBranchIds(node)) {
      const sources = origins.get(branchId) ?? []
      sources.push(node.id)
      origins.set(branchId, sources)
    }
  }
  const reachable = new Map<string, Set<string>>()
  for (const [branchId, sources] of origins) {
    const queue: string[] = []
    for (const source of sources) {
      for (const child of children.get(source)!) queue.push(child)
    }
    const visited = new Set<string>()
    for (let cursor = 0; cursor < queue.length; cursor++) {
      const id = queue[cursor]
      if (visited.has(id)) continue
      visited.add(id)
      if (hasResultBranches(byId.get(id)!)) continue
      for (const child of children.get(id)!) if (!visited.has(child)) queue.push(child)
    }
    reachable.set(branchId, visited)
  }
  return reachable
}

/** Kahn traversal: dangling references and cyclic leftovers never recurse. */
function topologicalLevels(nodes: WorkflowNode[]) {
  const { byId, children, indegree } = indexGraph(nodes)
  const queue = [...byId.keys()].filter((id) => indegree.get(id) === 0)
  const levels = new Map(queue.map((id) => [id, 0]))
  for (let cursor = 0; cursor < queue.length; cursor++) {
    const id = queue[cursor]
    for (const child of children.get(id)!) {
      levels.set(child, Math.max(levels.get(child) ?? 0, levels.get(id)! + 1))
      indegree.set(child, indegree.get(child)! - 1)
      if (indegree.get(child) === 0) queue.push(child)
    }
  }
  return { byId, levels, unresolved: [...byId.keys()].filter((id) => indegree.get(id)! > 0) }
}

export function computeDagLayout(nodes: WorkflowNode[]): Map<string, Position> {
  const { byId, levels, unresolved } = topologicalLevels(nodes)
  let fallback = 0
  for (const level of levels.values()) fallback = Math.max(fallback, level + 1)
  for (const id of unresolved) levels.set(id, fallback)
  const counts = new Map<number, number>()
  for (const id of byId.keys()) {
    const level = levels.get(id) ?? 0
    counts.set(level, (counts.get(level) ?? 0) + 1)
  }
  const maxWidth = Math.max(1, ...counts.values())
  const rows = new Map<number, number>()
  const result = new Map<string, Position>()
  for (const id of byId.keys()) {
    const level = levels.get(id) ?? 0
    const column = rows.get(level) ?? 0
    // Top-to-bottom matches the approved prototype; parallel siblings share a row.
    result.set(id, { x: 80 + (maxWidth - counts.get(level)!) * 160 + column * 320, y: 80 + level * 240 })
    rows.set(level, column + 1)
  }
  return result
}

/** Only known references are interpreted; arbitrary strings/extension fields are not rewritten. */
export function findNodeReferences(spec: WorkflowSpec, targetId: string): string[] {
  const refs = new Set<string>()
  const escaped = targetId.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
  const template = new RegExp(`\\{\\{[^{}]*?\\b(?:nodeOutput|nodes)\\s*(?:\\.\\s*${escaped}(?=[.\\s}\\[])|\\[\\s*["']${escaped}["']\\s*\\])`)
  const conditionKey = new RegExp(`^(?:nodeOutput|nodes)\\.${escaped}(?:\\.|$)`)
  const stack: Array<{ value: unknown; path: string; keys: string[] }> = [{ value: spec, path: 'spec', keys: [] }]
  const seen = new Set<object>()
  while (stack.length) {
    const { value, path, keys } = stack.pop()!
    if (typeof value === 'string') {
      if (template.test(value)) refs.add(path)
      // Exact schema paths only: match.target and arbitrary tool args named
      // target are data, not routing references. Array indexes are absent in keys.
      const knownTarget = /^nodes(?:\.executor\.body)*\.(?:onFeedback\.target|executor\.(?:(?:onFeedback|onRevise)\.target|actions\.revise\.target)|onResult\.(?:target|(?:then|else)\.(?:rerun\.target|wait\.actions\.revise\.target)))$/.test(keys.join('.'))
      if (knownTarget && value === targetId) refs.add(path)
      continue
    }
    if (value === null || typeof value !== 'object' || seen.has(value)) continue
    seen.add(value)
    if (Array.isArray(value)) {
      value.forEach((item, i) => {
        if (path === 'spec.nodes' && isRecord(item) && item.id === targetId) return
        const label = path === 'spec.nodes' && isRecord(item) ? String(item.id) : String(i)
        stack.push({ value: item, path: `${path}[${label}]`, keys })
      })
    } else {
      for (const [key, child] of Object.entries(value)) {
        const nextPath = `${path}.${key}`
        if (conditionKey.test(key)) refs.add(nextPath)
        stack.push({ value: child, path: nextPath, keys: [...keys, key] })
      }
    }
  }
  const removed = spec.nodes.find((node) => node.id === targetId)
  const branchIds = removed ? getResultBranchIds(removed) : []
  for (const node of spec.nodes) {
    if (node.id !== targetId && node.branchId && branchIds.includes(node.branchId)) {
      refs.add(`spec.nodes[${node.id}].branchId (${node.branchId})`)
    }
  }
  return [...refs].sort()
}

export function canConnect(spec: WorkflowSpec, sourceId: string, targetId: string, branchId?: string): string | null {
  const { byId, children } = indexGraph(spec.nodes)
  const source = byId.get(sourceId)
  const target = byId.get(targetId)
  if (!source || !target) return '连线端点不存在 (missing node)'
  if (sourceId === targetId) return '不能连接节点自身 (self edge)'
  if (dependencies(target).includes(sourceId)) return '依赖边已存在 (duplicate edge)'
  const queue = [targetId]
  const seen = new Set(queue)
  for (let cursor = 0; cursor < queue.length; cursor++) {
    for (const child of children.get(queue[cursor]) ?? []) {
      if (child === sourceId) return '连线将产生依赖环 (cycle)'
      if (!seen.has(child)) { seen.add(child); queue.push(child) }
    }
  }
  // evaluateOnResult dispatches a matched branch regardless of its complete flag.
  // Preserve that metadata; it does not constrain graph connections.
  const branchIds = getResultBranchIds(source)
  if (branchId !== undefined) {
    if (!branchIds.includes(branchId)) return `分支端口不存在: ${branchId}`
    if (target.branchId !== undefined && target.branchId !== branchId) return `节点已有不兼容的 branchId: ${target.branchId}`
    for (const dep of dependencies(target)) {
      const parent = byId.get(dep)
      const routes = parent ? getResultBranchIds(parent) : []
      if (parent && hasResultBranches(parent) && !routes.includes(branchId)) {
        return `已有上游 ${dep} 的分支归属与 ${branchId} 冲突`
      }
    }
  } else if (branchIds.length) {
    return '请从明确的分支端口连线'
  }
  return null
}

/** Check each added edge independently of preexisting validation errors. An
 * imported cyclic component must not hide a new cycle elsewhere in the graph. */
export function graphMutationError(before: WorkflowSpec, after: WorkflowSpec): string | null {
  const previous = new Map(before.nodes.map((node) => [node.id, node]))
  const next = new Map(after.nodes.map((node) => [node.id, node]))
  const previousOrigins = branchReachability(before.nodes)
  const nextOrigins = branchReachability(after.nodes)
  for (const node of after.nodes) {
    if (node.branchId && previous.get(node.id)?.branchId === node.branchId
      && previousOrigins.get(node.branchId)?.has(node.id) && !nextOrigins.get(node.branchId)?.has(node.id)) {
      return `节点 ${node.id} 的分支 ${node.branchId} 将失去最后一条有效来源路径，不能断开依赖`
    }
  }
  for (const node of after.nodes) {
    const old = previous.get(node.id)
    const oldDeps = old ? dependencies(old) : []
    const newDeps = dependencies(node)
    for (const dep of new Set(newDeps)) {
      if (newDeps.filter((id) => id === dep).length <= oldDeps.filter((id) => id === dep).length) continue
      const oneRemoved = [...newDeps]
      oneRemoved.splice(oneRemoved.indexOf(dep), 1)
      const withoutEdge = { ...after, nodes: after.nodes.map((item) => item.id === node.id ? { ...item, dependsOn: oneRemoved } : item) }
      const source = next.get(dep)
      const branchId = source && getResultBranchIds(source).length ? node.branchId : undefined
      const error = canConnect(withoutEdge, dep, node.id, branchId)
      if (error) return error
    }
  }
  return null
}

export function validateSpec(spec: WorkflowSpec): ValidationError[] {
  const errors: ValidationError[] = []
  if (!isRecord(spec)) return [{ type: 'invalid-structure', message: 'Workflow 必须是对象' }]
  if (typeof spec.id !== 'string' || !spec.id.trim()) errors.push({ type: 'missing-field', message: 'Workflow id is required' })
  if (typeof spec.title !== 'string' || !spec.title.trim()) errors.push({ type: 'missing-field', message: 'Workflow title is required' })
  if (!Array.isArray(spec.nodes) || spec.nodes.some((node) => !isRecord(node))) {
    return [...errors, { type: 'invalid-structure', message: 'Workflow nodes 必须是节点对象数组' }]
  }
  const ids = new Set<string>()
  const branchOwners = new Map<string, string>()
  for (const node of spec.nodes) {
    if (typeof node.id !== 'string' || !node.id.trim()) errors.push({ type: 'missing-field', message: 'Node missing id' })
    if (ids.has(node.id)) errors.push({ type: 'duplicate-id', nodeId: node.id, message: `重复节点 ID: ${node.id}` })
    ids.add(node.id)
    if (typeof node.phase !== 'string' || !node.phase.trim()) errors.push({ type: 'missing-field', nodeId: node.id, message: `Node "${node.id}" missing phase` })
    if (!isRecord(node.executor) || typeof node.executor.type !== 'string' || !node.executor.type.trim()) {
      errors.push({ type: 'missing-config', nodeId: node.id, message: `Node "${node.id}" missing executor type` })
    }
    if (node.dependsOn !== undefined && (!Array.isArray(node.dependsOn) || node.dependsOn.some((dep) => typeof dep !== 'string'))) {
      errors.push({ type: 'invalid-structure', nodeId: node.id, message: `Node "${node.id}" dependsOn 必须是字符串数组` })
    }
    for (const branchId of getResultBranchIds(node)) {
      if (!branchId.trim() || branchOwners.has(branchId)) {
        errors.push({ type: 'branch-conflict', nodeId: node.id, message: `分支 ID 为空或重复: ${branchId}` })
      }
      branchOwners.set(branchId, node.id)
    }
    const branches = getResultBranches(node)
    for (const [i, branch] of (branches ?? []).entries()) {
      if (!Object.keys(branch.match).length && i < branches!.length - 1) {
        errors.push({ type: 'branch-conflict', nodeId: node.id, message: `兜底分支 ${branch.branchId} 必须放在最后` })
      }
    }
  }
  const byId = new Map(spec.nodes.map((node) => [node.id, node]))
  const reachable = branchReachability(spec.nodes)
  for (const node of spec.nodes) {
    if (node.branchId && reachable.has(node.branchId) && !reachable.get(node.branchId)!.has(node.id)) {
      errors.push({ type: 'branch-conflict', nodeId: node.id, message: `节点 ${node.id} 的分支 ${node.branchId} 缺少有效来源路径` })
    }
    const deps = dependencies(node)
    for (const dep of new Set(deps)) {
      if (!ids.has(dep)) errors.push({ type: 'missing-reference', nodeId: node.id, message: `Node "${node.id}" 依赖不存在: ${dep}` })
      if (dep === node.id || deps.filter((id) => id === dep).length > 1) errors.push({ type: 'invalid-edge', nodeId: node.id, message: `Node "${node.id}" 自环或重复依赖: ${dep}` })
      const parent = byId.get(dep)
      if (node.branchId && parent && hasResultBranches(parent) && !getResultBranchIds(parent).includes(node.branchId)) {
        errors.push({ type: 'branch-conflict', nodeId: node.id, message: `节点 ${node.id} 的 branchId ${node.branchId} 与上游 ${dep} 冲突` })
      }
    }
  }
  if (topologicalLevels(spec.nodes).unresolved.length) errors.push({ type: 'cycle', message: 'Dependency cycle detected' })
  return errors
}

/** Prevent a branch edit/removal from stranding any existing branch membership. */
export function branchEditError(before: WorkflowSpec, after: WorkflowSpec): string | null {
  for (const parent of before.nodes) {
    const oldBranches = getResultBranchIds(parent)
    if (!oldBranches?.length) continue
    const next = after.nodes.find((node) => node.id === parent.id)
    const newBranches = next ? getResultBranchIds(next) : []
    for (const branchId of oldBranches) {
      if (newBranches.includes(branchId)) continue
      const users = after.nodes.filter((node) => node.id !== parent.id && node.branchId === branchId)
      if (users.length) return `分支 ${branchId} 仍被节点 ${users.map((node) => node.id).join(', ')} 引用，不能删除或改名`
    }
  }
  return null
}
