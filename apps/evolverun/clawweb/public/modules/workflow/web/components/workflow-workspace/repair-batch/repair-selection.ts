import type { RepairInboxItem } from '../../../../server/contracts/repair-workbench'

function canonical(value: unknown): string {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']'
  if (value && typeof value === 'object') return '{' + Object.entries(value).sort(([a], [b]) => a.localeCompare(b))
    .map(([key, entry]) => JSON.stringify(key) + ':' + canonical(entry)).join(',') + '}'
  return JSON.stringify(value) ?? 'undefined'
}

/** A conservative preview of structural conflicts, not a replacement for Pack validation. */
export function selectionConflicts(items: RepairInboxItem[]) {
  const operations = items.flatMap(item => (Array.isArray(item.proposal?.operations) ? item.proposal.operations : [])
    .flatMap((raw: unknown) => {
      if (!raw || typeof raw !== 'object') return []
      const operation = raw as Record<string, unknown>
      if (typeof operation.path !== 'string' || operation.op === 'test') return []
      return [{ itemId: item.itemId, nodeId: String(operation.nodeId ?? '工作流'),
        path: operation.path.startsWith('/') ? operation.path : '/' + operation.path.replaceAll('.', '/'),
        identity: canonical({ op: operation.op ?? 'replace', value: operation.value }) }]
    }))
  const conflicts: Array<{ itemIds: string[]; nodeId: string; path: string }> = []
  const seen = new Set<string>()
  for (let i = 0; i < operations.length; i++) for (let j = i + 1; j < operations.length; j++) {
    const left = operations[i], right = operations[j]
    if (left.itemId === right.itemId || left.nodeId !== right.nodeId) continue
    const overlaps = left.path === right.path || left.path.startsWith(right.path + '/') || right.path.startsWith(left.path + '/')
    if (!overlaps || left.path === right.path && left.identity === right.identity) continue
    const itemIds = [left.itemId, right.itemId].sort()
    const key = JSON.stringify(itemIds)
    if (seen.has(key)) continue
    seen.add(key)
    conflicts.push({ itemIds, nodeId: left.nodeId, path: left.path.length <= right.path.length ? left.path : right.path })
  }
  return conflicts
}
