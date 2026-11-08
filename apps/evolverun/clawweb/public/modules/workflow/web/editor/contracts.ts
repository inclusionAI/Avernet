import type { WorkflowNode } from '@avernet/clawweb-shared/web/types'

export interface ResultBranch {
  branchId: string
  match: Record<string, unknown>
  complete?: boolean
  [key: string]: unknown
}

export function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function isResultBranch(value: unknown): value is ResultBranch {
  return isRecord(value) && typeof value.branchId === 'string' && isRecord(value.match)
    && (value.complete === undefined || typeof value.complete === 'boolean')
}

/** null means compatibility/raw mode, not an empty routing configuration.
 * Graph safety must use getResultBranchIds instead: valid anonymous branches
 * can make the form adapter unavailable without erasing named references. */
export function getResultBranches(node: WorkflowNode): ResultBranch[] | null {
  const routing: unknown = node.onResult
  if (routing === undefined) return []
  if (!isRecord(routing) || ['if', 'then', 'else'].some((key) => key in routing)) return null
  if (!Array.isArray(routing.branches) || !routing.branches.every(isResultBranch)) return null
  return routing.branches
}

/** Reference extraction is deliberately independent of form editability.
 * A missing ID is valid runtime metadata; never invent an ID or drop that entry.
 * Named references remain protective even alongside unsupported sibling fields. */
export function getResultBranchIds(node: WorkflowNode): string[] {
  const routing: unknown = node.onResult
  if (!isRecord(routing) || !Array.isArray(routing.branches)) return []
  return routing.branches.flatMap(branch =>
    isRecord(branch) && typeof branch.branchId === 'string' ? [branch.branchId] : [],
  )
}

/** Runtime branch origins stop inherited branch lookup, including anonymous-only origins. */
export function hasResultBranches(node: WorkflowNode): boolean {
  const routing: unknown = node.onResult
  return isRecord(routing) && Array.isArray(routing.branches) && routing.branches.length > 0
}

/** The shared DTO still describes the legacy array. Keep the runtime cast here. */
export function withResultBranches(node: WorkflowNode, branches: ResultBranch[]): Partial<WorkflowNode> {
  if (getResultBranches(node) === null) {
    throw new Error('旧式或未知 onResult 必须通过完整配置编辑，不能混写 branches')
  }
  const routing: unknown = node.onResult
  return {
    onResult: structuredClone({ ...(isRecord(routing) ? routing : {}), branches }) as unknown as WorkflowNode['onResult'],
  }
}

export function getEffectiveTriggerRule(node: WorkflowNode): 'all_success' | 'one_success' | 'all_done' {
  if (node.triggerRule === 'all_success' || node.triggerRule === 'one_success' || node.triggerRule === 'all_done') {
    return node.triggerRule
  }
  return node.join === 'any' ? 'one_success' : 'all_success'
}
