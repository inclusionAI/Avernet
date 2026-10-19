import { workflowSnapshotKey } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot'
import { itemSnapshotKey } from '../../../../server/contracts/repair-version-scope'
import type { RepairInboxItem, RepairSelectionState } from '../../../../server/contracts/repair-workbench'
import { exclusion, itemTitle } from './repair-view'
import { selectionConflicts } from '../../../../server/contracts/repair-selection'

export function selectionProof(ids: string[], items: Record<string, RepairInboxItem>) {
  return ids.every(id => /^[a-f0-9]{64}$/.test(items[id]?.selectionDigest ?? ''))
    ? Object.fromEntries(ids.map(id => [id, items[id].selectionDigest!])) : undefined
}
const content = (item: RepairInboxItem) => item.selectionDigest ?? JSON.stringify([item.contentRevision, item.proposalKey, item.proposal, item.instruction])
const planContent = (plan: NonNullable<RepairInboxItem['aggregation']> | NonNullable<RepairSelectionState['plans']>[number]) => JSON.stringify([plan.itemIds, plan.title, plan.changes, plan.relation, plan.causes, plan.unknowns, plan.conflicts])
export function reviewedSelection(ids: string[], previous: Record<string, RepairInboxItem>, fresh: RepairSelectionState, confirmPlans = false) {
  const targetKey = fresh.activeSnapshot?.status === 'resolved' ? workflowSnapshotKey(fresh.activeSnapshot.snapshot) : undefined
  const historical = !!targetKey && fresh.items.some(item => ids.includes(item.itemId) && itemSnapshotKey(item) && itemSnapshotKey(item) !== targetKey)
  const originals = Object.fromEntries(fresh.items.map(item => [item.itemId, item]))
  const current = { ...originals }
  const removed: string[] = []
  let planReview = false
  const retained = ids.filter(id => {
    const item = current[id]
    const reason = !item ? '原始来源已不可用' : exclusion(item, undefined, targetKey) || (previous[id] && content(previous[id]) !== content(item) ? '建议内容已变化，请审阅后重新勾选' : '')
    if (reason) removed.push(`${previous[id] ? itemTitle(previous[id]) : id}：${reason}`)
    return !reason
  })
  for (const id of retained) {
    if (retained.length === 1 || historical) { const { aggregation: _plan, ...original } = current[id]; current[id] = original; continue }
    const old = previous[id]?.aggregation, plan = fresh.plans?.find(plan => plan.itemIds.includes(id))
    if (!old && !plan) continue
    const unchanged = old && plan && planContent(old) === planContent(plan)
      && (old.fingerprint === plan.fingerprint && old.id === plan.id || old.relation === 'exact' && plan.relation === 'exact')
    const complete = plan?.itemIds.every(member => retained.includes(member))
    if (plan?.relation === 'exact' && !complete && (confirmPlans || !old)) {
      // Exact grouping is optional presentation of identical edits. Keep the
      // reviewed raw originals; do not import unselected members or their counts.
      const { aggregation: _plan, ...original } = current[id]; current[id] = original; continue
    }
    const independent = plan?.relation === 'exact' && plan.itemIds.length === 1
      && !selectionConflicts(retained.map(member => originals[member])).some(conflict => conflict.itemIds.includes(id))
    if (plan && complete && (unchanged || confirmPlans) && (independent || !['queued', 'organizing', 'retryable', 'unavailable', 'uncertain'].includes(plan.status))) {
      current[id] = { ...current[id], aggregation: { ...plan, originals: plan.itemIds.map(member => originals[member]), flowIds: [] } }
    } else {
      planReview = true
      if (old) current[id] = { ...current[id], aggregation: { ...old, status: 'uncertain' } }
    }
  }
  const removalNotice = removed.length ? `${removed.join('；')}。其余 ${retained.length} 条选择与文字要求已保留。` : ''
  const planNotice = planReview ? ' 整合方案已变化或尚待整理，请查看当前方案后确认已选原始范围；未选成员不会自动纳入。' : ''
  return { retained, current, planReview, removalNotice, planNotice, notice: (removalNotice || '已逐项核对，原选择与文字要求均已保留；新增建议不会自动纳入。') + planNotice }
}
