import { validateWorkflowPatchProposal } from '@avernet/clawevolve/server/services/evolution/contracts';
import type { IssueAggregationRepository } from '@avernet/clawevolve/server/repositories/issue-aggregation-repository';
import { selectionConflicts } from '../contracts/repair-selection.js';
import { digestRepairJson } from '../contracts/repair-batch.js';
import type { RepairBaseline } from '../contracts/repair-batch.js';
import type { AutomationWorkbench } from '../contracts/workflow-automation.js';
import type { RepairInboxItem } from '../contracts/repair-workbench.js';
export async function selectAutomaticScope(workbench: AutomationWorkbench, issues: IssueAggregationRepository, workflowId: string, baseline: RepairBaseline) {
  const ranked = await issues.listPage(workflowId, { page: 1, pageSize: 20, sort: 'priority' });
  const candidates: RepairInboxItem[] = [];
  let inputDigest = '';
  for (const group of ranked.groups) {
    if (!group.priority?.severity) continue;
    const page = await workbench.candidates(workflowId, { state: 'pending', signature: group.signature, pageSize: 100 });
    if (!page.capabilities.generation) return null;
    if (inputDigest && inputDigest !== page.inputDigest) return null;
    inputDigest = page.inputDigest;
    for (const item of page.items) {
      if (item.state !== 'pending' || !item.sourceAvailable || (!Array.isArray(item.context?.diagnoses) || !item.context.diagnoses.length) || JSON.stringify(item).length > 32000) continue;
      try {
        const proposal = validateWorkflowPatchProposal(item.proposal);
        if (proposal.workflowId !== workflowId || proposal.baseSpecDigest !== baseline.specDigest) continue;
        candidates.push(item);
      } catch { /* Unstructured, stale or ungrounded proposals remain available for manual selection. */ }
    }
    if (candidates.length >= 100) break;
  }
  const shortlist = [...new Map(candidates.slice(0, 100).map(item => [item.itemId, item])).values()];
  const ambiguous = new Set(selectionConflicts(shortlist).flatMap(conflict => conflict.itemIds));
  const items: RepairInboxItem[] = [];
  let bytes = 0;
  for (const item of shortlist) {
    if (ambiguous.has(item.itemId)) continue;
    const size = Buffer.byteLength(JSON.stringify(item));
    if (bytes + size > 100000) continue;
    items.push(item); bytes += size;
    if (items.length === 5) break;
  }
  if (!items.length) return null;
  const fingerprint = digestRepairJson({ baseline: baseline.packCommit, items: items.map(item => ({ id: item.itemId,
    revision: item.contentRevision, proposal: item.proposalKey, recurrence: item.context?.recurrence ?? null,
    flows: [...new Set(item.sources.flatMap(source => source.kind === 'diagnosis_candidate' ? [source.flowId] : []))].sort() })) });
  return { items, inputDigest, fingerprint };
}
