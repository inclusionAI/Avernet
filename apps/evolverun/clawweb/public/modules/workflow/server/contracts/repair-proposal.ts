import { selectionConflicts } from './repair-selection.js';
import type { RepairInboxItem } from './repair-workbench.js';
export type ProposalRelation = 'exact' | 'equivalent' | 'complementary' | 'uncertain';
export type ProposalConflict = { itemIds: string[]; nodeId: string; path: string; reason: string };
export type ProposalPlan = { itemIds: string[]; title: string; changes: string[]; relation: ProposalRelation };
export type ProposalCause = { title: string; certainty: 'supported'|'hypothesis'|'unknown'; itemIds: string[]; evidenceIds: string[] };
export type ProposalGrouping = { plans: ProposalPlan[]; conflicts: ProposalConflict[]; causes?: ProposalCause[]; unknowns?: string[] };
export type ProposalAggregation = {
  id: string; fingerprint: string; itemIds: string[]; title: string; changes: string[]; relation: ProposalRelation;
  status: 'ready' | 'queued' | 'organizing' | 'retryable' | 'unavailable' | 'uncertain' | 'stale';
  causes?: ProposalCause[]; unknowns?: string[];
  flowIds: string[]; runCount?: number; originals: RepairInboxItem[]; conflicts: ProposalConflict[];
};
/** Expand the displayed selection into original identities without selecting hidden alternatives. */
export function proposalMembers(item: RepairInboxItem): RepairInboxItem[] {
  return item.aggregation?.originals ?? [item];
}
export function proposalChecked(item: RepairInboxItem, selected: readonly string[]): boolean {
  return proposalMembers(item).every(member => selected.includes(member.itemId));
}
export function proposalSelection(items: RepairInboxItem[]): RepairInboxItem[] {
  const seen = new Set<string>();
  return items.flatMap(item => {
    const id = item.aggregation?.id ?? item.itemId;
    if (seen.has(id)) return [];
    seen.add(id); return [item];
  });
}

export function expandProposalCards(items: RepairInboxItem[]): RepairInboxItem[] {
  return items.flatMap(item => proposalMembers(item).map(member => ({ ...member, ...(item.aggregation ? { aggregation: item.aggregation } : {}) })));
}
export function proposalConflicts(items: RepairInboxItem[]): ProposalConflict[] {
  const ids = new Set(items.map(i => i.itemId));
  const explicit = items.flatMap(i => i.aggregation?.conflicts ?? []).filter(c => c.itemIds.every(id => ids.has(id)));
  const legacy = selectionConflicts(items.filter(i => !i.aggregation)).map(c => ({ ...c, reason: '所选修改要求不同取值' }));
  return [...new Map([...explicit, ...legacy].map(c => [JSON.stringify(c.itemIds), c])).values()];
}
export function proposalPending(items: RepairInboxItem[]): boolean {
  const ids = new Set(items.map(item => item.itemId));
  if (items.length > 1 && items.some(item => item.readSummary && !item.readSummary.conflictsComplete
    && !(item.aggregation?.status === 'ready' && item.aggregation.relation !== 'uncertain' && item.aggregation.itemIds.every(id => ids.has(id))))) return true;
  if (items.some(item => item.aggregation?.status === 'ready' && item.aggregation.relation !== 'exact' && item.aggregation.itemIds.some(id => !ids.has(id)))) return true;
  return selectionConflicts(items).some(c => {
    const pair = items.filter(i => c.itemIds.includes(i.itemId));
    return pair.some(i => i.aggregation) && !(pair.length === 2 && pair[0].aggregation?.id === pair[1].aggregation?.id
      && pair.every(i => i.aggregation?.status === 'ready' && i.aggregation.relation !== 'uncertain'));
  }) && !proposalConflicts(items).length;
}
