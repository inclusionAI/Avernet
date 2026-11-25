import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

/** Validate and retain only the static topology returned by the run snapshot endpoint. */
export function parseRunSnapshot(value: unknown): WorkflowNode[] {
  if (!isRecord(value) || !Array.isArray(value.nodes)) throw new Error('Invalid run snapshot')
  const ids = new Set<string>()
  return value.nodes.map((node: unknown) => {
    if (!isRecord(node) || typeof node.id !== 'string' || !node.id.trim() || ids.has(node.id)
      || typeof node.title !== 'string' || !Array.isArray(node.dependsOn)
      || !node.dependsOn.every((id: unknown) => typeof id === 'string' && !!id.trim())
      || !isRecord(node.executor) || typeof node.executor.type !== 'string' || !node.executor.type.trim()) {
      throw new Error('Invalid run snapshot node')
    }
    ids.add(node.id)
    return { id: node.id, title: node.title, dependsOn: node.dependsOn as string[], executor: { type: node.executor.type } }
  })
}
