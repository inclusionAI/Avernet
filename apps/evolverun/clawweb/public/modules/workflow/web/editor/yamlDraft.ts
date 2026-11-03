import { parse } from 'yaml'
import type { WorkflowSpec } from '@avernet/clawweb-shared/web/types'

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** A structural input boundary, not a replacement for the runtime validator. */
export function parseWorkflowYaml(text: string): WorkflowSpec {
  const value: unknown = parse(text, { uniqueKeys: true, maxAliasCount: 100 })
  if (!record(value) || typeof value.id !== 'string' || !value.id.trim()
    || typeof value.title !== 'string' || !value.title.trim() || !Array.isArray(value.nodes)) {
    throw new Error('必须包含非空 id、title，以及 nodes 数组')
  }
  for (const [index, node] of value.nodes.entries()) {
    if (!record(node) || typeof node.id !== 'string' || !node.id.trim()
      || typeof node.title !== 'string' || !record(node.executor)
      || typeof node.executor.type !== 'string' || !node.executor.type.trim()) {
      throw new Error(`nodes[${index}] 必须包含 id、title 和 executor.type`)
    }
    if (node.dependsOn !== undefined && (!Array.isArray(node.dependsOn)
      || !node.dependsOn.every(id => typeof id === 'string'))) {
      throw new Error(`nodes[${index}].dependsOn 必须是字符串数组`)
    }
  }
  try { JSON.stringify(value) } catch { throw new Error('配置包含循环引用，不能保存为工作流 JSON') }
  // The legacy shared DTO requires a version; the server contract permits an absent one.
  return value as unknown as WorkflowSpec
}

/** Expose paths, never credentials or full values, before accepting server normalization. */
export function normalizationChanges(before: unknown, after: unknown, path = ''): string[] {
  if (Object.is(before, after)) return []
  if (Array.isArray(before) && Array.isArray(after)) {
    const changes: string[] = []
    for (let index = 0; index < Math.max(before.length, after.length); index++) {
      changes.push(...normalizationChanges(before[index], after[index], `${path}[${index}]`))
    }
    return changes
  }
  if (record(before) && record(after)) {
    return [...new Set([...Object.keys(before), ...Object.keys(after)])].flatMap(key =>
      normalizationChanges(before[key], after[key], path ? `${path}.${key}` : key),
    )
  }
  return [path || '(文档)']
}
