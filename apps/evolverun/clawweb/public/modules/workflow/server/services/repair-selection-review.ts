import { digestRepairJson, RepairBatchError, type RepairItem, type RepairBaseline } from '../contracts/repair-batch.js';
import type { RepairInboxItem, RepairSelectionRequest } from '../contracts/repair-workbench.js';

export function selectedContentDigest(item: RepairItem): string {
  return digestRepairJson({ itemId: item.itemId, groupKey: item.groupKey, proposalKey: item.proposalKey,
    contentRevision: item.contentRevision, previousItemId: item.previousItemId, proposal: item.proposal, instruction: item.instruction });
}
export function needsReevaluation(item: RepairItem, baseline: RepairBaseline): boolean {
  return ((Array.isArray(item.proposal?.operations) && item.proposal.operations.length > 0)
    || (Array.isArray(item.proposal?.patches) && item.proposal.patches.length > 0)) && item.proposal?.baseSpecDigest !== (baseline.proposalSpecDigest ?? baseline.specDigest);
}
export function assertProposalBaseline(items: RepairItem[], baseline: RepairBaseline): void {
  const incompatible = items.filter(item => needsReevaluation(item, baseline));
  if (!incompatible.length) return;
  const missing = incompatible.some(item => typeof item.proposal?.baseSpecDigest !== 'string' || !/^[a-f0-9]{64}$/.test(item.proposal.baseSpecDigest));
  throw new RepairBatchError('SOURCE_CHANGED', missing ? '原建议缺少可核对的配置摘要，无法确认基线' : '原建议基线已变化，请核对配置差异',
    { reasonCode: missing ? 'BASELINE_IDENTITY_UNKNOWN' : 'PROPOSAL_BASELINE_CHANGED', itemIds: incompatible.map(item => item.itemId) });
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
