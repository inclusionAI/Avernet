import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { MAX_ITEMS, MAX_MODEL_INPUT_BYTES, type RepairItem } from '../contracts/repair-batch.js';
import { validateWorkflowPatchProposal } from '@avernet/clawevolve/server/services/evolution/contracts';
import type { IssueAggregationRepository } from '@avernet/clawevolve/server/repositories/issue-aggregation-repository';
import { selectionConflicts } from '../contracts/repair-selection.js';
import { digestRepairJson } from '../contracts/repair-batch.js';
import type { RepairBaseline } from '../contracts/repair-batch.js';
import type { AutomationWorkbench } from '../contracts/workflow-automation.js';
import type { RepairInboxItem } from '../contracts/repair-workbench.js';
async function legacyAutomaticScope(workbench: AutomationWorkbench, issues: IssueAggregationRepository, workflowId: string, baseline: RepairBaseline) {
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


/** Closed-open natural day. Historical unresolved evidence is retained before untilMs. */
export function previousShanghaiDay(now: number) {
  const untilMs = Math.floor((now + 8 * 3600000) / 86400000) * 86400000 - 8 * 3600000;
  return { fromMs: untilMs - 86400000, untilMs };
}
export type AutomaticScope = { items: RepairInboxItem[]; inputDigest: string; fingerprint: string;
  batches?: string[][]; deferred?: Record<string, string>; revisions?: Record<string, string>; window?: ReturnType<typeof previousShanghaiDay> };
export function automaticItemDigest(item: RepairItem) {
  return digestRepairJson({ id: item.itemId, revision: item.contentRevision, proposal: item.proposal, sources: item.sources, context: item.context ?? null });
}
export async function selectAutomaticScope(workbench: AutomationWorkbench, issues: IssueAggregationRepository,
  workflowId: string, baseline: RepairBaseline, scope?: { db: IDatabase; minimumImpact: 'medium' | 'high'; evidenceCutoffMs: number }): Promise<AutomaticScope | null> {
  if (!scope) return legacyAutomaticScope(workbench, issues, workflowId, baseline);
  const window = previousShanghaiDay(scope.evidenceCutoffMs);
  const deferred: Record<string, string> = {}, revisions: Record<string, string> = {};
  const items: RepairInboxItem[] = [];
  let inputDigest = '', page = 1, pageSize = 100;
  for (;;) {
    let result: Awaited<ReturnType<AutomationWorkbench['candidates']>>;
    try { result = await workbench.candidates(workflowId, { state: 'pending', includeHistorical: true, page, pageSize }); }
    catch (error) {
      if ((error as { code?: string }).code !== 'PAYLOAD_TOO_LARGE' || pageSize === 1) throw error;
      // Response byte protection is a transport bound, never a candidate count limit.
      pageSize = Math.max(1, Math.floor(pageSize / 2)); page = 1; inputDigest = ''; items.length = 0;
      for (const id of Object.keys(deferred)) delete deferred[id];
      for (const id of Object.keys(revisions)) delete revisions[id];
      continue;
    }
    if (!result.capabilities.generation) return null;
    if (inputDigest && inputDigest !== result.inputDigest) throw new Error('SOURCE_CHANGED');
    inputDigest = result.inputDigest;
    for (const item of result.items.length ? await workbench.items(workflowId, result.items.map(item => item.itemId)) : []) {
      let reason: string | null = !item.sourceAvailable ? 'SOURCE_UNAVAILABLE' : null;
      const diagnoses = (Array.isArray(item.context?.diagnoses) ? item.context.diagnoses : []) as Array<Record<string, unknown>>;
      const eligible = diagnoses.filter(d => typeof d.completedAtMs === 'number' && d.completedAtMs <= scope.evidenceCutoffMs
        && ['low', 'medium', 'high', 'critical'].indexOf(String(d.severity)) >= (scope.minimumImpact === 'high' ? 2 : 1));
      if (!reason && !eligible.length) reason = 'IMPACT_NOT_ESTABLISHED';
      const flows = [...new Set(eligible.flatMap(d => [d.flowId, ...(Array.isArray(d.flowIds) ? d.flowIds : [])])
        .filter((id): id is string => typeof id === 'string' && !!id))];
      let grounded = false;
      for (let offset = 0; offset < flows.length && !grounded; offset += 100) {
        const batch = flows.slice(offset, offset + 100);
        const rows = await scope.db.query<{ started_at: number; completed_at: number | null }>(
          `SELECT started_at, completed_at FROM flow_runs WHERE workflow_id = ? AND flow_id IN (${batch.map(() => '?').join(',')})`, [workflowId, ...batch]);
        grounded = rows.some(row => {
          const started = Number(row.started_at), completed = Number(row.completed_at);
          const startMs = started < 1e12 ? started * 1000 : started;
          const endMs = completed < 1e12 ? completed * 1000 : completed;
          return started > 0 && completed > 0 && startMs < window.untilMs && endMs <= scope.evidenceCutoffMs;
        });
      }
      if (!reason && !grounded) reason = 'RUN_TIME_NOT_IN_SCOPE';
      if (!reason) try {
        const proposal = validateWorkflowPatchProposal(item.proposal);
        if (proposal.workflowId !== workflowId || proposal.baseSpecDigest !== baseline.specDigest) reason = 'BASELINE_REASSESSMENT_REQUIRED';
      } catch { reason = 'PROPOSAL_REASSESSMENT_REQUIRED'; }
      if (reason) deferred[item.itemId] = reason;
      else { items.push(item); revisions[item.itemId] = automaticItemDigest(item); }
    }
    if (page >= result.page.totalPages) break;
    page++;
  }
  const conflicts = new Set(selectionConflicts(items).flatMap(conflict => conflict.itemIds));
  const batches: string[][] = [];
  let batch: string[] = [], bytes = 0;
  const maxBytes = MAX_MODEL_INPUT_BYTES - 64 * 1024;
  for (const item of items) {
    if (conflicts.has(item.itemId)) { deferred[item.itemId] = 'CONFLICT_REQUIRES_CHOICE'; continue; }
    const size = Buffer.byteLength(JSON.stringify(item));
    if (size > maxBytes) { deferred[item.itemId] = 'EVIDENCE_REQUIRES_REDUCTION'; continue; }
    if (batch.length >= MAX_ITEMS || bytes + size > maxBytes) { batches.push(batch); batch = []; bytes = 0; }
    batch.push(item.itemId); bytes += size;
  }
  if (batch.length) batches.push(batch);
  const selected = items.filter(item => !deferred[item.itemId]);
  const fingerprint = digestRepairJson({ baseline, minimumImpact: scope.minimumImpact, items: selected.map(item => revisions[item.itemId]), deferred });
  return { items: selected, inputDigest, fingerprint, batches, deferred, revisions, window };
}
