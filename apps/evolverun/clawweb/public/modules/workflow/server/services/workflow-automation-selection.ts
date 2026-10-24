import { workflowSnapshotKey } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import { itemSnapshotKey } from '../contracts/repair-version-scope.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import { MAX_ITEMS, MAX_MODEL_INPUT_BYTES, type RepairItem } from '../contracts/repair-batch.js';
import type { IssueAggregationRepository } from '@avernet/clawevolve/server/repositories/issue-aggregation-repository';
import { proposalMembers, expandProposalCards, proposalConflicts, proposalPending } from '../contracts/repair-proposal.js';
import { digestRepairJson } from '../contracts/repair-batch.js';
import type { RepairBaseline } from '../contracts/repair-batch.js';
import type { AutomationWorkbench } from '../contracts/workflow-automation.js';
import type { RepairInboxItem } from '../contracts/repair-workbench.js';
export type AutomaticPlanningBaseline = Pick<RepairBaseline, 'activeDeployNumber' | 'specDigest' | 'proposalSpecDigest'>;
/** Text goals may omit baseline metadata; concrete changes must satisfy generation's baseline guard. */
function matchesProposalBaseline(item: RepairItem, workflowId: string, baseline: AutomaticPlanningBaseline) {
  const structured = [item.proposal?.operations, item.proposal?.patches].some(value => Array.isArray(value) && value.length > 0);
  return (item.proposal?.workflowId == null || item.proposal.workflowId === workflowId)
    && (!structured && item.proposal?.baseSpecDigest == null
      || item.proposal?.baseSpecDigest === (baseline.proposalSpecDigest ?? baseline.specDigest));
}
async function legacyAutomaticScope(workbench: AutomationWorkbench, issues: IssueAggregationRepository, workflowId: string, baseline: AutomaticPlanningBaseline) {
  const header=await workbench.candidates(workflowId,{state:'pending',pageSize:1,versionScope:'current'});
  if(header.activeSnapshot!==undefined&&(header.activeSnapshot?.status!=='resolved'||header.activeSnapshot.snapshot.kind!=='deployment'||header.activeSnapshot.snapshot.deployNumber!==baseline.activeDeployNumber))return null;
  const ranked = await issues.listPage(workflowId, { page: 1, pageSize: 20, sort: 'priority',...(header.activeSnapshot!==undefined?{versionScope:'current' as const}:{}) });
  const candidates: RepairInboxItem[] = [];
  let inputDigest = '';
  let targetSnapshotKey:string|undefined;
  for (const group of ranked.groups) {
    if (!group.priority?.severity) continue;
    let number = 1;
    let scopeDigest: string | undefined, totalPages: number | undefined;
    for (;;) {
      const page = await workbench.candidates(workflowId, { state: 'pending', signature: group.signature, page: number, pageSize: 100, rawItems: true,versionScope:'current' });
      if (!page.capabilities.generation) return null;
      if(page.activeSnapshot!==undefined){if(page.activeSnapshot?.status!=='resolved'||page.activeSnapshot.snapshot.kind!=='deployment'||page.activeSnapshot.snapshot.deployNumber!==baseline.activeDeployNumber)return null;targetSnapshotKey=workflowSnapshotKey(page.activeSnapshot.snapshot);}
      if (inputDigest && inputDigest !== page.inputDigest) return null;
      if (number > 1 && (scopeDigest !== page.page.snapshotDigest || totalPages !== page.page.totalPages)) return null;
      scopeDigest = page.page.snapshotDigest; totalPages = page.page.totalPages;
      inputDigest = page.inputDigest;
      for (const item of expandProposalCards(page.items)) {
        const { aggregation: _projection, ...raw } = item;
        if (targetSnapshotKey&&itemSnapshotKey(item)!==targetSnapshotKey)continue;
        if (item.state !== 'pending' || !item.sourceAvailable || (!Array.isArray(item.context?.diagnoses) || !item.context.diagnoses.length) || Buffer.byteLength(JSON.stringify(raw)) > 32000) continue;
        if (matchesProposalBaseline(item, workflowId, baseline)) candidates.push(item);
      }
      if (number >= page.page.totalPages) break;
      number++;
    }
  }
  const shortlist = [...new Map(candidates.map(item => [item.itemId, item])).values()];
  const ambiguous = new Set(proposalConflicts(shortlist).flatMap(conflict => conflict.itemIds));
  const items: RepairInboxItem[] = [];
  const units = new Map<string, RepairInboxItem[]>();
  for (const item of shortlist) {
    const key = item.aggregation?.id ?? item.itemId;
    const unit = units.get(key) ?? []; unit.push(item); units.set(key, unit);
  }
  let bytes = 0, selectedUnits = 0;
  for (const unit of units.values()) {
    const ids = new Set(unit.map(item => item.itemId));
    if (unit.some(item => ambiguous.has(item.itemId) || item.aggregation?.itemIds.some(id => !ids.has(id))) || proposalPending(unit)) continue;
    const size = unit.reduce((sum,item) => { const {aggregation:_projection,...raw}=item; return sum + Buffer.byteLength(JSON.stringify(raw)); },0);
    if (bytes + size > 100000 || items.length + unit.length > MAX_ITEMS) continue;
    items.push(...unit); bytes += size; selectedUnits++;
    // Legacy scope selects up to five displayed choices; their original members
    // remain atomic and still obey the shared original-item and byte limits.
    if (selectedUnits === 5) break;
  }
  if (!items.length) return null;
  const fingerprint = digestRepairJson({ baseline: [baseline.activeDeployNumber, baseline.proposalSpecDigest ?? baseline.specDigest], items: items.map(item => ({ id: item.itemId,
    revision: item.contentRevision, proposal: item.proposalKey, recurrence: item.context?.recurrence ?? null,
    flows: [...new Set(item.sources.flatMap(source => source.kind === 'diagnosis_candidate' ? [source.flowId] : []))].sort() })) });
  return { items, inputDigest, fingerprint,...(targetSnapshotKey?{targetSnapshotKey}:{}) };
}


/** Closed-open natural day. Historical unresolved evidence is retained before untilMs. */
export function previousShanghaiDay(now: number) {
  const untilMs = Math.floor((now + 8 * 3600000) / 86400000) * 86400000 - 8 * 3600000;
  return { fromMs: untilMs - 86400000, untilMs };
}
export type AutomaticScope = { targetSnapshotKey?:string; items: RepairInboxItem[]; inputDigest: string; fingerprint: string;
  batches?: string[][]; deferred?: Record<string, string>; revisions?: Record<string, string>; window?: ReturnType<typeof previousShanghaiDay> };
export function automaticItemDigest(item: RepairItem) {
  return digestRepairJson({ id: item.itemId, revision: item.contentRevision, proposal: item.proposal, sources: item.sources, context: item.context ?? null });
}
export async function selectAutomaticScope(workbench: AutomationWorkbench, issues: IssueAggregationRepository,
  workflowId: string, baseline: AutomaticPlanningBaseline, scope?: { db: IDatabase; minimumImpact: 'medium' | 'high'; evidenceCutoffMs: number }): Promise<AutomaticScope | null> {
  if (!scope) return legacyAutomaticScope(workbench, issues, workflowId, baseline);
  const window = previousShanghaiDay(scope.evidenceCutoffMs);
  const deferred: Record<string, string> = {}, revisions: Record<string, string> = {};
  const items: RepairInboxItem[] = [];
  let inputDigest = '', page = 1, pageSize = 100;
  let targetSnapshotKey:string|undefined;
  for (;;) {
    let result: Awaited<ReturnType<AutomationWorkbench['candidates']>>;
    try { result = await workbench.candidates(workflowId, { state: 'pending', includeHistorical: true, page, pageSize,versionScope:'current' }); }
    catch (error) {
      if ((error as { code?: string }).code !== 'PAYLOAD_TOO_LARGE' || pageSize === 1) throw error;
      // Response byte protection is a transport bound, never a candidate count limit.
      pageSize = Math.max(1, Math.floor(pageSize / 2)); page = 1; inputDigest = ''; items.length = 0;
      for (const id of Object.keys(deferred)) delete deferred[id];
      for (const id of Object.keys(revisions)) delete revisions[id];
      continue;
    }
    if (!result.capabilities.generation) return null;
    if(result.activeSnapshot!==undefined){
      if(result.activeSnapshot?.status!=='resolved'||result.activeSnapshot.snapshot.kind!=='deployment'||result.activeSnapshot.snapshot.deployNumber!==baseline.activeDeployNumber)return null;
      const key=workflowSnapshotKey(result.activeSnapshot.snapshot);if(targetSnapshotKey&&key!==targetSnapshotKey)throw new Error('TARGET_VERSION_CHANGED');targetSnapshotKey=key;
    }
    if (inputDigest && inputDigest !== result.inputDigest) throw new Error('SOURCE_CHANGED');
    inputDigest = result.inputDigest;
    const projected = expandProposalCards(result.items);
    const members = [...new Set(result.items.flatMap(card => proposalMembers(card).map(item => item.itemId)))];
    for (const raw of members.length ? await workbench.items(workflowId, members) : []) {
      const item = { ...raw, ...(projected.find(p => p.itemId === raw.itemId)?.aggregation ? { aggregation: projected.find(p => p.itemId === raw.itemId)!.aggregation } : {}) };
      let reason: string | null = targetSnapshotKey&&itemSnapshotKey(item)!==targetSnapshotKey?'SOURCE_VERSION_MISMATCH':!item.sourceAvailable ? 'SOURCE_UNAVAILABLE' : null;
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
      if (!reason && !matchesProposalBaseline(item, workflowId, baseline)) reason = 'BASELINE_REASSESSMENT_REQUIRED';
      if (reason) deferred[item.itemId] = reason;
      else { items.push(item); revisions[item.itemId] = automaticItemDigest(item); }
    }
    if (page >= result.page.totalPages) break;
    page++;
  }
  for (const item of items) if (item.aggregation && item.aggregation.itemIds.some(id => !items.some(i => i.itemId === id))) deferred[item.itemId] = 'PLAN_SCOPE_INCOMPLETE';
  const conflicts = new Set(proposalConflicts(items).flatMap(conflict => conflict.itemIds));
  const batches: string[][] = [];
  let batch: string[] = [], bytes = 0;
  const maxBytes = MAX_MODEL_INPUT_BYTES - 64 * 1024;
  const units = new Map<string, RepairInboxItem[]>();
  for (const item of items) {
    const key=item.aggregation?.id??item.itemId;
    const unit=units.get(key)??[];unit.push(item);units.set(key,unit);
  }
  for (const unit of units.values()) {
    let reason=unit.map(item=>deferred[item.itemId]).find(Boolean);
    if (!reason && unit.some(item=>conflicts.has(item.itemId))) reason='CONFLICT_REQUIRES_CHOICE';
    if (!reason && proposalPending(unit)) reason='PROPOSAL_INTEGRATION_PENDING';
    const size=unit.reduce((sum,item)=>{const {aggregation:_projection,...raw}=item;return sum+Buffer.byteLength(JSON.stringify(raw));},0);
    if (!reason && (size>maxBytes || unit.length>MAX_ITEMS)) reason='EVIDENCE_REQUIRES_REDUCTION';
    if(reason){for(const item of unit)deferred[item.itemId]=reason;continue;}
    if(batch.length+unit.length>MAX_ITEMS||bytes+size>maxBytes){batches.push(batch);batch=[];bytes=0;}
    batch.push(...unit.map(item=>item.itemId));bytes+=size;
  }
  if (batch.length) batches.push(batch);
  const selected = items.filter(item => !deferred[item.itemId]);
  const fingerprint = digestRepairJson({ baseline, minimumImpact: scope.minimumImpact, items: selected.map(item => revisions[item.itemId]), deferred });
  return { items: selected, inputDigest, fingerprint, batches, deferred, revisions, window,...(targetSnapshotKey?{targetSnapshotKey}:{}) };
}
