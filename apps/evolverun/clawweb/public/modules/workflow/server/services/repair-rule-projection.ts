import { digestRepairJson, MAX_ITEMS, repairEvidenceFlows } from '../contracts/repair-batch.js';
import { itemSnapshot } from '../contracts/repair-version-scope.js';
import type { RepairInboxItem } from '../contracts/repair-workbench.js';

/** Display-only exact folding. Original identities, constraints and evidence remain authoritative. */
export function presentExactProposals(items: RepairInboxItem[]): RepairInboxItem[] {
  const buckets = new Map<string, RepairInboxItem[]>();
  for (const item of items) {
    const foldable = item.sourceAvailable && item.state === 'pending' && (item.proposal || item.instruction.trim());
    const key = foldable ? digestRepairJson({ version: itemSnapshot(item) ?? null,
      signature: item.context?.signature ?? null, nodeId: item.context?.nodeId ?? null,
      failureMode: item.context?.failureMode ?? null, proposal: item.proposal, instruction: item.instruction }) : item.itemId;
    const bucket = buckets.get(key) ?? []; bucket.push(item); buckets.set(key, bucket);
  }
  const cards: RepairInboxItem[] = [];
  for (const [key, bucket] of buckets) {
    // Keep every displayed choice within the existing original-item selection cap.
    for (let start = 0; start < bucket.length; start += MAX_ITEMS) {
      const originals = bucket.slice(start, start + MAX_ITEMS);
      if (originals.length === 1) { cards.push(originals[0]); continue; }
      const itemIds = originals.map(item => item.itemId);
      const id = digestRepairJson(['exact-display/v1', key, itemIds]);
      const first = originals[0];
      cards.push({ ...first, aggregation: { id, fingerprint: id, itemIds,
        title: String(first.proposal?.summary || first.instruction).split('\n')[0].slice(0, 96),
        changes: [], relation: 'exact', status: 'ready', originals, conflicts: [],
        flowIds: [...new Set(originals.flatMap(repairEvidenceFlows))].sort() } });
    }
  }
  return cards;
}
