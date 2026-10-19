import type { RepairInboxItem, RepairSelectionState } from '../../../../server/contracts/repair-workbench'

export function selectionProof(ids: string[], items: Record<string, RepairInboxItem>) {
  return ids.every(id => /^[a-f0-9]{64}$/.test(items[id]?.selectionDigest ?? ''))
    ? Object.fromEntries(ids.map(id => [id, items[id].selectionDigest!])) : undefined
}
export function reviewedSelection(ids: string[], previous: Record<string, RepairInboxItem>, fresh: RepairSelectionState, confirmPlans = false) {
  // Regrouping/background analysis never revokes a user's manual draft goals.
  // Update only selected originals; do not silently include new plan members.
  const latest = Object.fromEntries(fresh.items.map(item => [item.itemId, item]))
  const current = Object.fromEntries(ids.flatMap(id => {
    const item = latest[id] ?? previous[id]
    if (!item) return []
    const { aggregation: _plan, ...original } = item
    return [[id, original]]
  })) as Record<string, RepairInboxItem>
  return { retained: [...ids], current, planReview: false, removalNotice: '', planNotice: '', notice: '' }
}
