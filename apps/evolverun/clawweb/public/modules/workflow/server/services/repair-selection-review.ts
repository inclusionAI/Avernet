import { digestRepairJson, RepairBatchError, type RepairItem, type RepairBaseline, type RepairBatchInput } from '../contracts/repair-batch.js';
import type { RepairInboxItem, RepairSelectionRequest } from '../contracts/repair-workbench.js';
import { selectionConflicts } from '../contracts/repair-selection.js';

export function selectedContentDigest(item: RepairItem): string {
  return digestRepairJson({ itemId: item.itemId, groupKey: item.groupKey, proposalKey: item.proposalKey,
    contentRevision: item.contentRevision, previousItemId: item.previousItemId, proposal: item.proposal, instruction: item.instruction });
}
export function needsReevaluation(item: RepairItem, baseline: RepairBaseline): boolean {
  return ((Array.isArray(item.proposal?.operations) && item.proposal.operations.length > 0)
    || (Array.isArray(item.proposal?.patches) && item.proposal.patches.length > 0)) && item.proposal?.baseSpecDigest !== baseline.specDigest;
}
export function reevaluationPlan(items: RepairItem[], baseline: RepairBaseline): RepairBatchInput['integration'] {
  if (!items.some(item => needsReevaluation(item, baseline))) return undefined;
  if (selectionConflicts(items).length) throw new RepairBatchError('INVALID_INPUT', '重叠旧建议需要整合或取舍，不能逐项覆盖', { reasonCode: 'PLAN_CONFLICT' });
  return { schemaVersion: 'workflow-proposal-plan/v1', baselineSpecDigest: baseline.specDigest,
    plans: items.map(item => ({ id: digestRepairJson(['reevaluate', item.itemId, baseline.specDigest]), originalItemIds: [item.itemId], relation: 'exact',
      goals: ['基于当前 Pack 重新评估原建议目标；旧 operations/patches 仅作证据，不直接套用。已满足或不再适用的目标须逐项说明。'] })) };
}
export function reviewSelected(request: RepairSelectionRequest, items: RepairInboxItem[], inputDigest: string, taskId: string | null) {
  if (!request.selection) {
    if (inputDigest !== request.inputDigest) throw new RepairBatchError('SOURCE_CHANGED', '列表来源已更新，正在核对本次选择', { reasonCode: 'SNAPSHOT_CHANGED' });
    return;
  }
  const issues = request.itemIds.flatMap(itemId => {
    const item = items.find(row => row.itemId === itemId);
    const reasonCode = !item || !item.sourceAvailable && !(taskId && item.activeTaskId === taskId) ? 'SOURCE_UNAVAILABLE' : selectedContentDigest(item) !== request.selection![itemId]
      ? 'CONTENT_CHANGED' : item.state !== 'pending' && !(taskId && item.state === 'processing' && item.activeTaskId === taskId) ? item.state.toUpperCase() : '';
    return reasonCode ? [{ itemId, reasonCode }] : [];
  });
  if (issues.length) throw new RepairBatchError('SOURCE_CHANGED', '部分已选建议需要重新确认，其余选择与要求保留', { reasonCode: 'SELECTED_ITEMS_CHANGED', items: issues });
}
