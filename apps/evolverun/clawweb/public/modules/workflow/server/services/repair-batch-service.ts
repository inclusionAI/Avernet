import { RepairMaterialDownload } from '../repositories/repair-material-download.js';
import { RepairMaterialRepository } from '../repositories/repair-material-repository.js';
import { repairTaskPresentation } from '../contracts/repair-task-presentation.js';
import {historicalReference,reviewVersionSelection,freezeReference,historicalIntegration,manualIntegration} from './repair-historical-reference.js';
import { inVersionScope, itemSnapshotKey } from '../contracts/repair-version-scope.js';
import { workflowSnapshotKey } from '@avernet/clawweb-shared/server/contracts/workflow-snapshot';
import { inboxSummary, inboxPages } from './repair-inbox-projection.js';
import { prepareProposalEvidence } from './repair-proposal-evidence.js';
import { presentExactProposals } from './repair-rule-projection.js';
import type { ProposalCoordinator } from './repair-proposal-integration.js';
import { selectedContentDigest, reviewSelected, assertProposalBaseline } from './repair-selection-review.js';
import { automaticChain, isAutomaticBatch, retainedFeedbackCandidate } from '../contracts/repair-automatic-chain.js';
import { RepairAutomationDeniedError } from '../contracts/workflow-automation.js';
import { repairEvidenceFacts, repairEvidenceFlows, validateAutomationOrigin } from '../contracts/repair-batch.js';
import { randomUUID } from 'node:crypto';
import { logWorkflowBackground, startWorkflowBackgroundRun, withWorkflowBackgroundContext } from '@avernet/clawweb-shared/server/services/workflow-background-log';
import { repairStage } from '../observability/repair-diagnostics.js';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import {
  RepairBatchError, boundedRepairJson, canonicalRepairJson, digestRepairJson, validateRepairMaterialItem, validateRepairItemStructure, validateRepairModelInputs,
  MAX_ITEMS, MAX_REQUEST_BYTES, MAX_INSTRUCTIONS_CHARS, MAX_RESULT_BYTES, MAX_REPORT_BYTES,
  type RepairBatchInput, type RepairItem, type RepairRevision, type StoredRepairItem,
} from '../contracts/repair-batch.js';
import type {
  RepairWorkbenchService, RepairSourcePort, RepairExecutionPort, RepairSelectionRequest,
  RepairFeedbackRequest, RepairCandidatesResponse, RepairTaskDetail, RepairInboxItem, RepairCapabilities, RepairCandidatesQuery, RepairInboxFilter,
} from '../contracts/repair-workbench.js';
import { repairSignatureKey, RepairResultAbsentError } from '../contracts/repair-workbench.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';
import { RepairTaskRepository } from '../repositories/repair-task-repository.js';
import { REPAIR_ACTIVE_LOOKBACK_DAYS, structuredRepairProposalIdentity } from '../repositories/repair-source-adapter.js';

// One shared SQLite connection cannot overlap BEGIN calls. Database row locks remain
// authoritative across independent connections/processes; this queue only owns this connection.
const sqliteQueues = new WeakMap<IDatabase, Promise<unknown>>();
const MAX_INBOX_READ_BYTES = 4 * 1024 * 1024;
const MAX_TASK_READ_BYTES = 8 * 1024 * 1024;
function serial<T>(db: IDatabase, fn: () => Promise<T>): Promise<T> {
  if (db.dbType !== 'sqlite') return fn();
  const result = (sqliteQueues.get(db) ?? Promise.resolve()).catch(() => undefined).then(fn);
  sqliteQueues.set(db, result.then(() => undefined, () => undefined));
  return result;
}
function fail(code: ConstructorParameters<typeof RepairBatchError>[0], message: string, details: Record<string, unknown> = {}): never { throw new RepairBatchError(code, message, details); }
function requiredText(value: unknown, max: number, empty = false): value is string { return typeof value === 'string' && value.length <= max && (empty || value.trim().length > 0); }
function identity(value: unknown, max: number): value is string { return requiredText(value, max) && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(value); }
function safeInteger(value: unknown, min = 0): value is number { return Number.isSafeInteger(value) && Number(value) >= min; }
async function sourceRead<T>(read: () => Promise<T>): Promise<T> {
  try { return await read(); } catch (error) {
    if (error instanceof RepairBatchError) throw error;
    fail('CAPABILITY_UNAVAILABLE', '原始建议或证据读取失败，请重试核对；原选择与要求保留', { reasonCode: 'SOURCE_READ_UNAVAILABLE' });
  }
}
function selection(raw: RepairSelectionRequest): RepairSelectionRequest {
  boundedRepairJson(raw, MAX_REQUEST_BYTES);
  if (!raw || !identity(raw.workflowId, 190) || !requiredText(raw.requestId, 128)
    || !Array.isArray(raw.itemIds) || raw.itemIds.length < 1 || raw.itemIds.length > MAX_ITEMS
    || raw.itemIds.some(id => !requiredText(id, 64)) || new Set(raw.itemIds).size !== raw.itemIds.length
    || !/^[a-f0-9]{64}$/.test(raw.inputDigest) || !requiredText(raw.instructions, MAX_INSTRUCTIONS_CHARS, true)) fail('INVALID_INPUT', 'Invalid repair selection');
  if (raw.includeHistorical !== undefined && typeof raw.includeHistorical !== 'boolean') fail('INVALID_INPUT', 'Invalid history scope');
  if (raw.selection !== undefined && (!raw.selection || typeof raw.selection !== 'object' || Array.isArray(raw.selection)
    || Object.keys(raw.selection).length !== raw.itemIds.length || raw.itemIds.some(id => !Object.hasOwn(raw.selection!, id) || !/^[a-f0-9]{64}$/.test(raw.selection![id])))) fail('INVALID_INPUT', 'Invalid selected content identities');
  if(raw.targetSnapshotKey!==undefined&&!requiredText(raw.targetSnapshotKey,2048))fail('INVALID_INPUT','Invalid target snapshot');
  if(raw.historicalItemIds!==undefined&&(!Array.isArray(raw.historicalItemIds)||new Set(raw.historicalItemIds).size!==raw.historicalItemIds.length||raw.historicalItemIds.some(id=>!raw.itemIds.includes(id))))fail('INVALID_INPUT','Invalid historical reference selection');
  return { workflowId: raw.workflowId, itemIds: [...raw.itemIds].sort(), inputDigest: raw.inputDigest,
    ...(raw.selection ? { selection: { ...raw.selection } } : {}),
    ...(raw.targetSnapshotKey?{targetSnapshotKey:raw.targetSnapshotKey}:{}),...(raw.historicalItemIds?{historicalItemIds:[...raw.historicalItemIds].sort()}:{}),
    ...(raw.automation ? { automation: validateAutomationOrigin(raw.automation) } : {}),
    instructions: raw.instructions, requestId: raw.requestId, includeHistorical: raw.includeHistorical === true };
}
function identityKey(item: RepairItem, episodeKey: string): string {
  const structured = structuredRepairProposalIdentity(item.proposal);
  return canonicalRepairJson([itemSnapshotKey(item), item.groupKey, structured ?? { proposalKey: item.proposalKey }, episodeKey]);
}
const lifecyclePriority: Record<StoredRepairItem['state'], number> = {
  pending: 0, no_action: 2, verified: 2, ineffective: 2, awaiting_verification: 3, processing: 4,
};
function preferredStored(left: StoredRepairItem, right: StoredRepairItem): StoredRepairItem {
  const priority = lifecyclePriority[right.state] - lifecyclePriority[left.state];
  if (priority) return priority > 0 ? right : left;
  if (left.updatedAtMs !== right.updatedAtMs) return left.updatedAtMs > right.updatedAtMs ? left : right;
  return left.itemId.localeCompare(right.itemId) <= 0 ? left : right;
}
function inboxMatch(item: RepairInboxItem, state: RepairInboxFilter): boolean {
  return state === 'all' || (state === 'closed' ? item.state === 'verified' || item.state === 'ineffective' : item.state === state);
}
function itemFacets(item: RepairInboxItem) {
  const diagnoses = Array.isArray(item.context?.diagnoses) ? item.context.diagnoses as Record<string, unknown>[] : [];
  const suggestions = Array.isArray(item.context?.suggestions) ? item.context.suggestions as Record<string, unknown>[] : [];
  const node = item.context?.nodeId ?? diagnoses[0]?.nodeId ?? suggestions[0]?.nodeId;
  const mode = item.context?.failureMode ?? diagnoses[0]?.failureMode;
  return { node: typeof node === 'string' && node ? node : '未知节点', mode: typeof mode === 'string' ? mode : '' };
}
function matchesScope(item: RepairInboxItem, query: RepairCandidatesQuery): boolean {
  if (item.aggregation) return item.aggregation.originals.some(original => matchesScope(original, query));
  if (query.signature && item.context?.signature !== query.signature) return false;
  const { node, mode } = itemFacets(item);
  return (!query.nodeId || node === query.nodeId)
    && (!query.failureMode || mode === query.failureMode);
}
function sourceDigest(current: Awaited<ReturnType<RepairSourcePort['load']>>): string {
  return digestRepairJson(current.map(({ item, episodeKey, initialState }) => ({
    episodeKey, initialState: initialState ?? 'pending',
    item: { itemId: item.itemId, groupKey: item.groupKey, proposalKey: item.proposalKey,
      contentRevision: item.contentRevision, previousItemId: item.previousItemId, proposal: item.proposal,
      instruction: item.instruction, sources: item.sources },
  })));
}

function versionDigest(items:RepairInboxItem[]){return digestRepairJson([...items].sort((a,b)=>a.itemId.localeCompare(b.itemId)).map(item=>({id:item.itemId,content:selectedContentDigest(item),sources:item.sources})));}

export type TrustedRepairReport = {
  workflowId: string; taskId: string; revision: number; expectedStateVersion: number;
} & ({ status: 'succeeded' | 'blocked' | 'no_change'; draft: Record<string, unknown>; checks: Record<string, unknown> }
  | { status: 'failed'; error: Record<string, unknown> });

class WorkflowRepairWorkbench implements RepairWorkbenchService {
  private readonly readSnapshots = new Map<string, { version: string; at: number; value: Awaited<ReturnType<WorkflowRepairWorkbench['snapshot']>> }>();
  private readonly pendingReads = new Map<string, Promise<Awaited<ReturnType<WorkflowRepairWorkbench['snapshot']>>>>();
  private readGeneration = 0;
  private readonly statusFailures = new Map<string, { at: number; suppressed: number }>();
  constructor(private readonly db: IDatabase, private readonly sources: RepairSourcePort, private readonly execution?: RepairExecutionPort, private readonly proposals?: ProposalCoordinator,
    private readonly onCompleted?: (tx: IDatabase, revision: RepairRevision) => Promise<void>) {}
  private capabilities(): RepairCapabilities {
    return { generation: !!this.execution, diff: !!this.execution?.diff, publication: false,
      reason: this.execution ? null : 'Repair generation provider is unavailable' };
  }
  private tx<T>(fn: (tx: IDatabase) => Promise<T>): Promise<T> {
    const invalidate = () => { this.readGeneration++; this.readSnapshots.clear(); this.pendingReads.clear(); };
    invalidate();
    return serial(this.db, () => this.db.transaction(fn)).finally(invalidate);
  }
  private actor(actorId: string): void { if (!requiredText(actorId, 128)) fail('INVALID_INPUT', 'Actor identity is required'); }

  private async reviewVersionRequest(request: RepairSelectionRequest, items: RepairInboxItem[], previous: RepairTaskDetail | null, db: IDatabase) {
    if (!previous) return reviewVersionSelection(request, items, await this.sources.activeSnapshot?.(db, request.workflowId));
    const target = previous.latestAttempt.input.items[0]?.context?.targetSnapshot as import('@avernet/clawweb-shared/server/contracts/workflow-snapshot').WorkflowSnapshotRef | undefined;
    return reviewVersionSelection(request, items, target ? { status: 'resolved', snapshot: target } : undefined);
  }
  private async selectionDigest(request:RepairSelectionRequest,items:RepairInboxItem[],legacy:string,db:IDatabase){
    if(!this.sources.activeSnapshot)return legacy;
    const active=await this.sources.activeSnapshot(db,request.workflowId);
    const key=request.targetSnapshotKey??(active?.status==='resolved'?workflowSnapshotKey(active.snapshot):null);
    return versionDigest(request.historicalItemIds?.length?items:items.filter(item=>itemSnapshotKey(item)===key));
  }
  private async readSnapshot(workflowId: string, includeHistorical: boolean) {
    if (!identity(workflowId, 190)) fail('INVALID_INPUT', 'Invalid workflowId');
    const key = `${workflowId}:${includeHistorical}`;
    const pending = this.pendingReads.get(key);
    if (pending) return pending;
    const generation = this.readGeneration;
    const read = this.loadReadSnapshot(workflowId, includeHistorical, key, generation);
    this.pendingReads.set(key, read);
    try { return await read; }
    finally { if (this.pendingReads.get(key) === read) this.pendingReads.delete(key); }
  }
  private async loadReadSnapshot(workflowId: string, includeHistorical: boolean, key: string, generation: number) {
    const version = this.sources.readVersion ? await this.sources.readVersion(this.db, workflowId) : null;
    const cached = this.readSnapshots.get(key);
    if (version !== null && cached?.version === version && Date.now() - cached.at < 10_000) return cached.value;
    this.readSnapshots.delete(key);
    const value = await this.snapshot(this.db, workflowId, 'summary', { includeHistorical });
    // A small, version-checked read projection, never the input authority for writes.
    // TTL also bounds clock-based lookback changes and legacy second-resolution timestamps.
    if (version !== null && generation === this.readGeneration && Buffer.byteLength(JSON.stringify(value.items), 'utf8') <= 8 * 1024 * 1024) {
      if (this.readSnapshots.size >= 4) this.readSnapshots.delete(this.readSnapshots.keys().next().value!);
      this.readSnapshots.set(key, { version, at: Date.now(), value });
    }
    return value;
  }

  private async snapshot(db: IDatabase, workflowId: string, mode: 'summary' | 'full' = 'full', scope?: { itemIds?: readonly string[]; includeHistorical?: boolean }) {
    if (!identity(workflowId, 190)) fail('INVALID_INPUT', 'Invalid workflowId');
    if (!(await repairStage('workflow_lookup', () => db.query('SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?', [workflowId]))).length) fail('WORKFLOW_NOT_FOUND', 'Workflow does not exist');
    const repo = new RepairBatchRepository(db);
    const stored = await repairStage('stored_items', () => repo.listItems(workflowId));
    const byIdentity = new Map<string, StoredRepairItem>();
    const shadowed = new Set<string>();
    for (const item of stored) {
      const key = identityKey(item, item.episodeKey);
      const previous = byIdentity.get(key);
      if (!previous) { byIdentity.set(key, item); continue; }
      const preferred = preferredStored(previous, item);
      byIdentity.set(key, preferred);
      shadowed.add(preferred.itemId === previous.itemId ? item.itemId : previous.itemId);
    }
    const findStored = (item: RepairItem, episode: string) => {
      const exact = byIdentity.get(identityKey(item, episode));
      if (exact || !itemSnapshotKey(item)) return exact;
      const legacy = byIdentity.get(identityKey({...item,context:{...item.context,sourceSnapshot:undefined}}, episode));
      if (!legacy) return undefined;
      const originalFlows = repairEvidenceFlows(legacy), currentFlows = new Set(repairEvidenceFlows(item));
      return originalFlows.length && originalFlows.every(id=>currentFlows.has(id)) ? legacy : undefined;
    };
    let sourceScope = scope;
    if (mode === 'full' && scope?.itemIds?.length && !this.sources.hydrate) {
      const selected = new Set(scope.itemIds);
      const summaries = await repairStage('sources_summary', () => sourceRead(() => this.sources.load(db, workflowId, 'summary', { includeHistorical: scope.includeHistorical })));
      sourceScope = { ...scope, itemIds: summaries.filter(source => {
        const existing = findStored(source.item, source.episodeKey);
        return selected.has(existing?.itemId ?? source.item.itemId);
      }).map(source => source.item.itemId) };
    }
    const current = await repairStage(`sources_${mode}`, () => sourceRead(() => this.sources.load(db, workflowId, this.sources.hydrate ? 'summary' : mode, sourceScope)));
    // An automatic recurrence opens a new phase with a new proposal identity.
    // Do not carry the previously applied operations into another generation.
    const derived = new Set(current.map(source => source.item.itemId));
    const maximumEpisodes = current.length + stored.length + 1;
    for (let index = 0; index < current.length && index < maximumEpisodes; index++) {
      const source = current[index];
      const previous = findStored(source.item, source.episodeKey);
      if (previous?.state === 'no_action' && previous.disposition?.action === 'no_action') {
        const disposition = previous.disposition;
        const flows = repairEvidenceFlows(source.item);
        const facts = repairEvidenceFacts(source.item);
        const major = facts.severity >= 3;
        const fresh = disposition.evidenceFlowIds && (flows.some(id => !disposition.evidenceFlowIds!.includes(id))
          || facts.eventIds.some(id => !disposition.evidenceEventIds?.includes(id)) || facts.severity > (disposition.evidenceSeverity ?? 0));
        const expired = disposition.untilMs !== undefined && disposition.untilMs <= Date.now();
        if (expired || major && fresh) {
          const episodeKey = digestRepairJson(['resume-disposition', previous.itemId, expired ? disposition.untilMs : [flows, facts]]);
          const itemId = digestRepairJson([workflowId, previous.groupKey, previous.proposalKey, episodeKey]);
          if (!derived.has(itemId)) {
            derived.add(itemId);
            current.push({ episodeKey, initialState: 'pending', item: { ...source.item, itemId, previousItemId: previous.itemId,
              context: { ...source.item.context, dispositionResume: { reason: expired ? 'expired' : 'new_evidence', previousItemId: previous.itemId } } } });
          }
        }
      }
      const recurrence = previous?.context?.recurrence as { episodeKey?: string; flowIds?: string[]; reason?: string } | undefined;
      if (!previous || !recurrence?.episodeKey || previous.state !== 'ineffective' && recurrence.reason !== 'baseline_changed') continue;
      const proposalKey = digestRepairJson(['reassess', previous.itemId, recurrence.episodeKey]);
      const itemId = digestRepairJson([workflowId, previous.groupKey, proposalKey, recurrence.episodeKey]);
      if (derived.has(itemId)) continue;
      derived.add(itemId);
      current.push({ episodeKey: recurrence.episodeKey, initialState: 'pending', item: { ...source.item, itemId,
        previousItemId: previous.itemId, proposalKey, proposal: null,
        instruction: `${recurrence.reason === 'baseline_changed' ? '当前生效 Pack 已变化，请重新核对原修改的适用性。' : '原修改在生效版本明确复现。'}请依据新证据重新诊断并调整方案，不得原样重复已应用修改。关联运行：${(recurrence.flowIds ?? []).join(', ')}`,
        context: { ...source.item.context, recurrence } } });
    }
    current.forEach(source => validateRepairItemStructure(source.item));
    const ordered = [...current].sort((a, b) => a.item.itemId.localeCompare(b.item.itemId));
    const inputDigest = sourceDigest(ordered);
    const items = new Map<string, RepairInboxItem>(stored.filter(item => !shadowed.has(item.itemId))
      .map(item => [item.itemId, { ...item, sourceAvailable: false }]));
    const materializable = new Map<string, (typeof current)[number]>();
    for (const source of ordered) {
      const existing = findStored(source.item, source.episodeKey);
      const itemId = existing?.itemId ?? source.item.itemId;
      const sourceAvailable = !!itemSnapshotKey(source.item) || !source.initialState || source.initialState === 'pending';
      const refreshed = existing ? { ...source.item, itemId, proposalKey: existing.proposalKey,
        // Executable identity and persisted wording remain immutable; only current evidence/sources are refreshed.
        contentRevision: existing.contentRevision, previousItemId: existing.previousItemId,
        proposal: existing.proposal, instruction: existing.instruction } : source.item;
      const preview: RepairInboxItem = existing ? { ...existing, ...refreshed, sourceAvailable }
        : { ...source.item, itemId, workflowId, episodeKey: source.episodeKey, state: source.initialState ?? 'pending', stateVersion: 0,
          activeTaskId: null, activeRevision: null, disposition: null, updatedAtMs: 0, sourceAvailable };
      items.set(itemId, preview);
      if (sourceAvailable) materializable.set(itemId, { ...source, item: refreshed });
    }
    if (mode === 'full' && this.sources.hydrate) {
      const selected = [...items.values()].filter(item => !scope?.itemIds || scope.itemIds.includes(item.itemId));
      const hydrated = await sourceRead(() => this.sources.hydrate!(db, workflowId, selected));
      for (const item of hydrated) {
        items.set(item.itemId, { ...items.get(item.itemId)!, ...item });
        const source = materializable.get(item.itemId);
        if (source) materializable.set(item.itemId, { ...source, item });
      }
    }
    if (mode === 'full') for (const item of items.values()) {
      if (!scope?.itemIds || scope.itemIds.includes(item.itemId)) validateRepairMaterialItem(item);
    }
    return { items: [...items.values()].map((item): RepairInboxItem => ({ ...item, selectionDigest: selectedContentDigest(item) })), inputDigest, materializable };
  }
  async request(workflowId: string, requestId: string): Promise<RepairRevision | null> {
    if (!identity(workflowId, 190) || !requiredText(requestId, 128)) fail('INVALID_INPUT', 'Invalid request lookup');
    const rows = await this.db.query<{ task_id: string; revision: number }>(
      'SELECT task_id, revision FROM workflow_repair_revisions WHERE workflow_id = ? AND request_id = ?', [workflowId, requestId]);
    if (!rows.length) return null;
    return new RepairBatchRepository(this.db).getRevision(workflowId, rows[0].task_id, Number(rows[0].revision));
  }
  async selectionState(workflowId: string, itemIds: string[], includeHistorical: boolean) {
    if (!Array.isArray(itemIds) || !itemIds.length || itemIds.length > MAX_ITEMS || itemIds.some(id => !requiredText(id, 64)) || new Set(itemIds).size !== itemIds.length) fail('INVALID_INPUT', 'Invalid selected scope');
    return serial(this.db, async () => {
      const snapshot = await this.snapshot(this.db, workflowId, 'summary', { includeHistorical: true });
      const activeSnapshot = await this.sources.activeSnapshot?.(this.db, workflowId);
      const hasHistory = activeSnapshot?.status === 'resolved' && snapshot.items.some(item => itemIds.includes(item.itemId) && itemSnapshotKey(item) !== workflowSnapshotKey(activeSnapshot.snapshot));
      const digest = activeSnapshot === undefined ? snapshot.inputDigest : versionDigest(hasHistory ? snapshot.items : snapshot.items.filter(item => inVersionScope(item, activeSnapshot, 'current')));
      const cards = this.proposals && this.execution ? await this.proposals.present(workflowId, snapshot.items, this.sources.activeSnapshot?undefined:await this.execution.resolveBaseline(workflowId)) : presentExactProposals(snapshot.items);
      const result = { inputDigest: digest, ...(activeSnapshot !== undefined ? { activeSnapshot } : {}), items: snapshot.items.filter(item => itemIds.includes(item.itemId)).map(inboxSummary),
        missing: itemIds.filter(id => !snapshot.items.some(item => item.itemId === id)),
        plans: cards.filter(card => card.aggregation?.itemIds.some(id => itemIds.includes(id))).map(card => {
          const { originals: _originals, flowIds: _flows, ...plan } = inboxSummary(card).aggregation!;
          return plan;
        }) };
      boundedRepairJson(result, MAX_TASK_READ_BYTES);
      return result;
    });
  }
  async proposalSnapshot(workflowId: string) {
    if (!this.execution) fail('CAPABILITY_UNAVAILABLE', '基线读取不可用');
    return serial(this.db, async () => {
      const items=(await this.readSnapshot(workflowId,true)).items;
      if(this.sources.activeSnapshot){const activeSnapshot=await this.sources.activeSnapshot(this.db,workflowId);return {items:items.filter(item=>inVersionScope(item,activeSnapshot,'current')),activeSnapshot};}
      return {items,baseline:await this.execution!.resolveBaseline(workflowId)};
    });
  }
  async retryProposalIntegration(workflowId: string) { await this.proposals?.retry(workflowId); }
  async candidates(workflowId: string, query: RepairCandidatesQuery = {}): Promise<Omit<RepairCandidatesResponse, 'canEdit'>> {
    return serial(this.db, async () => {
      const state = query.state ?? 'all';
      const page = query.page ?? 1;
      const pageSize = query.pageSize ?? 20;
      if (!['pending', 'processing', 'awaiting_verification', 'closed', 'no_action', 'all'].includes(state)
        || !safeInteger(page, 1) || !safeInteger(pageSize, 1) || pageSize > 200) fail('INVALID_INPUT', 'Invalid inbox pagination');
      if (query.includeHistorical !== undefined && typeof query.includeHistorical !== 'boolean') fail('INVALID_INPUT', 'Invalid history scope');
      if (query.includeTasks !== undefined && typeof query.includeTasks !== 'boolean') fail('INVALID_INPUT', 'Invalid task metadata scope');
      for (const value of [query.nodeId, query.failureMode, query.signature]) {
        if (value !== undefined && !requiredText(value, 4096)) fail('INVALID_INPUT', 'Invalid repair scope');
      }
      const versionScope=query.versionScope??'current';
      if(!['current','historical','all'].includes(versionScope))fail('INVALID_INPUT','Invalid version scope');
      const activeSnapshot=await this.sources.activeSnapshot?.(this.db,workflowId);
      const includeHistorical = query.includeHistorical === true || versionScope!=='current';
      if (query.previewSignatures !== undefined && (!Array.isArray(query.previewSignatures)
        || query.previewSignatures.length > 50 || query.previewSignatures.some(value => !requiredText(value, 4096)))) {
        fail('INVALID_INPUT', 'Invalid issue preview scope');
      }
      const snapshot = await this.readSnapshot(workflowId, includeHistorical);
      if (query.snapshotKey !== undefined && !requiredText(query.snapshotKey, 2048)) fail('INVALID_INPUT', 'Invalid snapshot key');
      const versionItems=snapshot.items.filter(item=>inVersionScope(item,activeSnapshot,versionScope) && (query.snapshotKey === undefined || (itemSnapshotKey(item) ?? 'unresolved') === query.snapshotKey));
      const versionInputDigest=activeSnapshot===undefined?snapshot.inputDigest:versionDigest(versionItems);
      // Preserve the full model work unit; signature filters apply to presented cards.
      let presentation = query.rawItems ? versionItems : presentExactProposals(versionItems);
      if (!query.rawItems && this.proposals && this.execution) {
        try { presentation = await this.proposals.present(workflowId, versionItems, this.sources.activeSnapshot?undefined:await this.execution.resolveBaseline(workflowId)); }
        catch { /* A failed aggregation/read never removes original suggestions or breaks the issue list. */ }
      }
      const visibleItems = presentation.filter(item => matchesScope(item, { signature: query.signature }) && (includeHistorical ||
        item.sourceAvailable || item.state === 'processing' || item.state === 'awaiting_verification'));
      const facets = { nodes: [...new Set(visibleItems.flatMap(item => (item.aggregation?.originals ?? [item]).map(original => itemFacets(original).node)))].sort(),
        failureModes: [...new Set(visibleItems.flatMap(item => (item.aggregation?.originals ?? [item]).map(original => itemFacets(original).mode)).filter(Boolean))].sort() };
      const scopedItems = visibleItems.filter(item => matchesScope(item, query));
      const revisions = query.includeTasks === false ? [] : await repairStage('revisions', () => new RepairBatchRepository(this.db).listRevisions(workflowId));
      const heads = new Map<string, RepairRevision>();
      const originals = new Map<string, RepairRevision>();
      for (const revision of revisions) originals.set(revision.taskId, revision);
      for (const revision of revisions) if (!heads.has(revision.taskId)) heads.set(revision.taskId, revision);
      for (const revision of revisions) if (revision.phase === 'published') heads.set(revision.taskId, revision);
      const counts = Object.fromEntries((['pending', 'processing', 'awaiting_verification', 'closed', 'no_action', 'all'] as RepairInboxFilter[])
        .map(key => [key, scopedItems.filter(item => inboxMatch(item, key)).length])) as Record<RepairInboxFilter, number>;
      const filtered = scopedItems.filter(item => inboxMatch(item, state));
      const repairSignatureKeys = [...new Set(scopedItems.flatMap(item =>
        (item.aggregation?.originals ?? [item]).flatMap(original => typeof original.context?.signature === 'string' && original.context.signature.trim() ? [repairSignatureKey(original.context.signature)] : [])))].sort();
      const summaries = query.rawItems ? filtered : filtered.map(inboxSummary);
      const pages = inboxPages(summaries, pageSize);
      const totalPages = pages.length;
      const selectedPage = Math.min(page, totalPages);
      const items = pages[selectedPage - 1];
      const response: Omit<RepairCandidatesResponse, 'canEdit'> = { schemaVersion: 'workflow-repair/v2', workflowId, inputDigest: versionInputDigest, items,
        ...(activeSnapshot!==undefined?{activeSnapshot,versionScope}:{}),
        tasks: [...heads.values()].map(head => {
          const retained=retainedFeedbackCandidate(revisions.filter(row=>row.taskId===head.taskId));
          const r=retained?{...retained,updatedAtMs:head.updatedAtMs}:head;
          const itemCount = isAutomaticBatch(r.input) ? r.input.automation!.totalItems ?? new Set(revisions.filter(v => v.taskId === r.taskId).flatMap(v => v.input.items.map(item => item.itemId))).size : r.input.items.length;
          let phase = r.phase;
          if (isAutomaticBatch(r.input) && phase === 'no_change') {
            try {
              const chain = automaticChain(revisions.filter(row => row.taskId === r.taskId), r, true);
              if (chain.some(row => Array.isArray(row.draft?.itemResults) && row.draft.itemResults.some(item => item.status === 'changed'))) phase = 'review';
            } catch (error) { if (!(error instanceof RepairBatchError)) throw error; }
          }
          return { taskId: r.taskId, revision: r.revision, phase, updatedAtMs: r.updatedAtMs, itemCount,
            ...repairTaskPresentation(originals.get(r.taskId)!.input) };
        }),
        repairSignatureKeys, facets,
        counts, page: { page: selectedPage, pageSize, total: filtered.length, totalPages,
          ...(query.rawItems ? { snapshotDigest: digestRepairJson(filtered) } : {}) },
        includeHistorical, activeLookbackDays: REPAIR_ACTIVE_LOOKBACK_DAYS,
        capabilities: this.capabilities(), limits: { maxItems: MAX_ITEMS, maxRequestBytes: MAX_REQUEST_BYTES } };
      // Complete metadata is independently pageable; callers must not treat the first page as all tasks/facets.
      const metadataPage = query.metadataPage ?? 1;
      if (!safeInteger(metadataPage, 1)) fail('INVALID_INPUT', 'Invalid metadata page');
      response.tasks.sort((a,b)=>b.updatedAtMs-a.updatedAtMs||a.taskId.localeCompare(b.taskId));
      const metadataArrays = [response.tasks, response.repairSignatureKeys, response.facets.nodes, response.facets.failureModes];
      response.metadata = { page: metadataPage, totalPages: Math.max(1, ...metadataArrays.map(rows => Math.ceil(rows.length / 50))),
        digest: digestRepairJson(metadataArrays),
        tasks: response.tasks.length, signatureKeys: repairSignatureKeys.length, nodes: facets.nodes.length, failureModes: facets.failureModes.length };
      const start = (metadataPage - 1) * 50;
      response.tasks = response.tasks.slice(start,start+50);
      response.repairSignatureKeys = repairSignatureKeys.slice(start,start+50);
      response.facets = { nodes: facets.nodes.slice(start,start+50), failureModes: facets.failureModes.slice(start,start+50) };
      if (query.previewSignatures) {
        const groups = [...new Set(query.previewSignatures)].map(signature => ({ signature,
          matches: filtered.filter(item => matchesScope(item, { signature }))
            .sort((a, b) => Number(b.state === 'pending' && b.sourceAvailable) - Number(a.state === 'pending' && a.sourceAvailable)
              || a.itemId.localeCompare(b.itemId)) }));
        response.issuePreviews = groups.map(({ signature, matches }) => ({ signature, total: matches.length, items: [] }));
        // Account for the base page and every issue's total before adding optional items.
        // Leave room for the transport's canEdit field; never truncate an individual item.
        let remainingBytes = MAX_INBOX_READ_BYTES - 256 - Buffer.byteLength(canonicalRepairJson(response), 'utf8');
        groups.forEach(({ matches }, index) => {
          const preview = response.issuePreviews![index];
          for (const item of matches.slice(0, 3)) {
            const summary = inboxSummary(item);
            const bytes = Buffer.byteLength(canonicalRepairJson(summary), 'utf8') + (preview.items.length ? 1 : 0);
            if (bytes > remainingBytes) continue;
            preview.items.push(summary);
            remainingBytes -= bytes;
          }
        });
      }
      try { boundedRepairJson(response, MAX_INBOX_READ_BYTES - 256); } catch (error) {
        if (error instanceof RepairBatchError) throw new RepairBatchError(error.code, error.message, { ...error.details, stage: 'candidates_response', reasonCode: 'REPAIR_RESPONSE_TOO_LARGE' });
        throw error;
      }
      return response;
    });
  }
  async item(workflowId: string, itemId: string, originalOnly = false): Promise<RepairInboxItem> {
    return serial(this.db, async () => {
      if (!requiredText(itemId, 64)) fail('INVALID_INPUT', 'Invalid itemId');
      let item: RepairInboxItem | undefined;
      if (this.sources.hydrate) {
        const recent = await this.readSnapshot(workflowId, false);
        item = recent.items.find(candidate => candidate.itemId === itemId);
        if (!item) item = (await this.readSnapshot(workflowId, true)).items.find(candidate => candidate.itemId === itemId);
        if (item) item = { ...item, ...(await this.sources.hydrate(this.db, workflowId, [item]))[0] };
      } else {
        item = (await this.snapshot(this.db, workflowId, 'full', { itemIds: [itemId], includeHistorical: true })).items.find(candidate => candidate.itemId === itemId);
      }
      if (!item) fail('ITEM_NOT_FOUND', 'Repair item does not exist');
      if (!originalOnly && this.proposals && this.execution) {
        const all = (await this.readSnapshot(workflowId, true)).items;
        const cards = await this.proposals.present(workflowId, all, await this.execution.resolveBaseline(workflowId));
        const card = cards.find(value => value.itemId === itemId || value.aggregation?.itemIds.includes(itemId));
        if (card?.aggregation) {
          item = { ...item, aggregation: inboxSummary(card).aggregation };
        }
      }
      try { boundedRepairJson(item, MAX_TASK_READ_BYTES); } catch (error) {
        if (error instanceof RepairBatchError) throw new RepairBatchError(error.code, error.message, { ...error.details, stage: 'item_response', reasonCode: 'REPAIR_RESPONSE_TOO_LARGE' });
        throw error;
      }
      return item;
    });
  }
  materialChunk(taskId:string,revision:number,itemId:string,token:string,offset:number){return new RepairMaterialDownload(this.db).read(taskId,revision,itemId,token,offset);}
  async material(taskId:string,revision:number,itemId:string):Promise<RepairItem>{
    const workflowId=await new RepairBatchRepository(this.db).taskWorkflow(taskId);
    if(!workflowId)fail('TASK_NOT_FOUND','Repair task does not exist');
    const row=await new RepairBatchRepository(this.db).getRevision(workflowId,taskId,revision);
    if(!row)fail('REVISION_NOT_FOUND','Repair revision does not exist');
    const input=await new RepairMaterialRepository(this.db).expand(row.input);
    const item=input.items.find(item=>item.itemId===itemId);
    if(!item)fail('ITEM_NOT_FOUND','Item is not in the frozen selection');
    return item;
  }
  async items(workflowId: string, itemIds: string[]): Promise<RepairInboxItem[]> {
    if (!Array.isArray(itemIds) || !itemIds.length || itemIds.length > MAX_ITEMS || itemIds.some(id => !requiredText(id, 64)) || new Set(itemIds).size !== itemIds.length) fail('INVALID_INPUT', 'Invalid item scope');
    return serial(this.db, async () => {
      const snapshot = await this.snapshot(this.db, workflowId, 'full', { itemIds, includeHistorical: true });
      return itemIds.map(id => {
        const item = snapshot.items.find(candidate => candidate.itemId === id);
        if (!item) fail('ITEM_NOT_FOUND', 'Repair item does not exist');
        return item;
      });
    });
  }
  private async readTask(db: IDatabase, taskId: string): Promise<RepairTaskDetail> {
    if (!identity(taskId, 64)) fail('INVALID_INPUT', 'Invalid taskId');
    const repo = new RepairBatchRepository(db);
    const workflowId = await repo.taskWorkflow(taskId);
    if (!workflowId) fail('TASK_NOT_FOUND', 'Repair task does not exist');
    const revisions = await repo.listRevisions(workflowId, taskId);
    const executions = await new RepairTaskRepository(db).executions(taskId, revisions.map(row => row.revision));
    const execution = executions.get(revisions[0].revision);
    const response: RepairTaskDetail = { workflowId, taskId, ...repairTaskPresentation(revisions.at(-1)!.input), latestAttempt: revisions[0], latestSuccessful: revisions.find(r => r.draft !== null && (r.phase !== 'no_change' || isAutomaticBatch(r.input))) ?? null,
      revisions, capabilities: this.capabilities(), executions: Object.fromEntries(executions), ...(execution ? { execution } : {}) };
    response.scopeItemCount = isAutomaticBatch(revisions[0].input) ? revisions[0].input.automation!.totalItems ?? new Set(revisions.flatMap(r => r.input.items.map(item => item.itemId))).size : revisions[0].input.items.length;
    const progressRevision=isAutomaticBatch(revisions[0].input)?revisions[0]:retainedFeedbackCandidate(revisions);
    const progressRows=progressRevision?revisions.filter(row=>row.revision<=progressRevision.revision):[];
    if (progressRevision&&isAutomaticBatch(progressRevision.input)) response.automaticProgress = {
      completedBatches: new Set(progressRows.filter(r => ['review', 'no_change', 'publishing', 'published'].includes(r.phase)
        && (r.checks?.static as { status?: string })?.status === 'passed').map(r => r.input.automation!.batchIndex)).size,
      totalBatches: progressRevision.input.automation!.batchCount!,
      changedItems: new Set(progressRows.filter(r => ['review', 'no_change', 'publishing', 'published'].includes(r.phase)).flatMap(r => Array.isArray(r.draft?.itemResults) ? r.draft.itemResults.filter(outcome => outcome.status === 'changed').map(outcome => outcome.itemId) : [])).size,
      publishable: false,
    };
    if (response.automaticProgress) {
      try { automaticChain(revisions, progressRevision!, true); response.automaticProgress.publishable = response.automaticProgress.changedItems! > 0; }
      catch (error) { if (!(error instanceof RepairBatchError)) throw error; }
    }
    return response;
  }
  task(taskId: string): Promise<RepairTaskDetail> { return serial(this.db, () => this.readTask(this.db, taskId)); }

  async create(actorId: string, raw: RepairSelectionRequest): Promise<RepairRevision> {
    this.actor(actorId);
    const request = selection(raw);
    return this.generate(actorId, null, request);
  }
  async revise(actorId: string, taskId: string, raw: RepairFeedbackRequest): Promise<RepairRevision> {
    this.actor(actorId);
    const base = selection(raw);
    if (!identity(taskId, 64) || !safeInteger(raw.expectedAttemptRevision, 1)
      || !requiredText(raw.feedback, MAX_INSTRUCTIONS_CHARS, true)
      || !(raw.parentCandidateCommit === null || /^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(raw.parentCandidateCommit))) fail('INVALID_INPUT', 'Invalid feedback request');
    const previous = this.execution ? await repairStage('task_state', () => this.task(taskId)) : undefined;
    if (previous && await this.requireStoppedAttempt(previous, actorId)) fail('STATE_CONFLICT', 'The original candidate was recovered; refresh it before submitting feedback', { recoveryAction: 'refresh-task', reasonCode: 'RESULT_RECOVERED' });
    return this.generate(actorId, taskId, { ...base, expectedAttemptRevision: raw.expectedAttemptRevision, parentCandidateCommit: raw.parentCandidateCommit, feedback: raw.feedback }, previous);
  }
  /** Only used before the new revision transaction/dispatch, never for an unknown write result. */
  private async generationBaseline(workflowId: string, purpose?: 'draft') {
    try { return await this.execution!.resolveBaseline(workflowId, purpose); }
    catch (error) {
      if (error instanceof RepairBatchError) throw error;
      // Preserve the low-level diagnostic for server logs without exposing Git/credentials to clients.
      console.error('[workflow-repair] baseline read failed', { workflowId, error });
      fail('CAPABILITY_UNAVAILABLE', '无法读取当前生效版本的部署包，本次未创建优化任务', {
        reasonCode: 'BASELINE_READ_FAILED', stage: 'baseline', outcome: 'not_created',
      });
    }
  }
  private async generate(actorId: string, taskId: string | null, request: RepairSelectionRequest | RepairFeedbackRequest, previousTask?: RepairTaskDetail): Promise<RepairRevision> {
    if (!this.execution) fail('CAPABILITY_UNAVAILABLE', 'Repair generation provider is unavailable');
    const digest = digestRepairJson({ actorId, taskId, request });
    // A committed retry uses its original baseline even if current sources or deployment changed.
    const prior = await serial(this.db, () => new RepairBatchRepository(this.db).findRequest(request.workflowId, request.requestId, digest));
    let revision = prior;
    if (!revision) {
      const baseline = previousTask ? previousTask.latestAttempt.input.baseline : await this.generationBaseline(request.workflowId, request.automation ? undefined : 'draft');
      if (taskId && request.automation?.roundId && canonicalRepairJson(await this.generationBaseline(request.workflowId)) !== canonicalRepairJson(baseline)) fail('STATE_CONFLICT', '自动优化的生效基线已变化，请重新评估', { reasonCode: 'BASELINE_STALE' });
      if (baseline.workflowId !== request.workflowId) fail('CONTENT_MISMATCH', 'Provider resolved a different workflow');
      if (request.automation && !taskId && canonicalRepairJson(await this.generationBaseline(request.workflowId)) !== canonicalRepairJson(baseline)) fail('STATE_CONFLICT', '生效版本在准备期间再次变化，选择与要求保留', { reasonCode: 'BASELINE_STALE' });
      revision = await repairStage('revision_write', () => this.tx(async tx => {
        const repo = new RepairBatchRepository(tx);
        await repo.lockWorkflow(tx, request.workflowId);
        const retry = await repo.findRequest(request.workflowId, request.requestId, digest);
        if (retry) return retry;
        const previous = taskId ? await this.readTask(tx, taskId) : null;
        if (previous && previous.workflowId !== request.workflowId) fail('ITEM_NOT_FOUND', 'Task is not in the requested workflow');
        await new RepairTaskRepository(tx).ensureNoLegacyApply(request.workflowId);
        const snapshot = await this.snapshot(tx, request.workflowId, 'full', {
          itemIds: request.itemIds, includeHistorical: true,
        });
        // Read selected originals/evidence once, under the existing workflow lock.
        const target = await this.reviewVersionRequest(request, snapshot.items, previous, tx);
        if(target?.kind==='deployment'&&(target.deployNumber!==baseline.activeDeployNumber||baseline.proposalSpecDigest&&target.configDigest!==baseline.proposalSpecDigest))fail('SOURCE_CHANGED','生成目标与当前生效版本不一致，请重新核对',{reasonCode:'TARGET_VERSION_CHANGED'});
        if (request.automation) reviewSelected(request, snapshot.items, await this.selectionDigest(request,snapshot.items,snapshot.inputDigest,tx), taskId);
        if (!taskId) {
          const occupied = snapshot.items.filter(item => request.itemIds.includes(item.itemId));
          const taskIds = [...new Set(occupied.map(item => item.activeTaskId).filter((id): id is string => !!id))];
          if (occupied.length === request.itemIds.length && occupied.every(item => item.state === 'processing') && taskIds.length === 1) {
            const active = await this.readTask(tx, taskIds[0]);
            if (canonicalRepairJson(active.latestAttempt.input.items.map(item => item.itemId).sort()) === canonicalRepairJson([...request.itemIds].sort())
              && active.latestAttempt.input.instructions === request.instructions) return active.latestAttempt;
          }
        }
        const selected: RepairItem[] = [];
        for (const itemId of request.itemIds) {
          const item = snapshot.items.find(item => item.itemId === itemId);
          if (!item || (!item.sourceAvailable && (!taskId || item.activeTaskId !== taskId))) fail('ITEM_NOT_FOUND', 'Selected source is unavailable in this workflow');
          if (!request.historicalItemIds?.includes(itemId) && item.state !== 'pending' && !(taskId && item.state === 'processing' && item.activeTaskId === taskId)) fail('ITEM_BUSY', 'Selected item is not pending or owned by this task');
          if(request.historicalItemIds?.includes(itemId)){selected.push(freezeReference(item,target));continue;}
          const source = snapshot.materializable.get(itemId);
          const stored = source ? await repo.materializeItem(tx, { workflowId: request.workflowId, ...source }) : await repo.getItem(request.workflowId, itemId);
          if (!stored) fail('ITEM_NOT_FOUND', 'Selected item is unavailable');
          const { workflowId: _wf, episodeKey: _ep, state: _state, stateVersion: _sv, activeTaskId: _at, activeRevision: _ar, disposition: _d, updatedAtMs: _u, ...frozen } = stored;
          selected.push(freezeReference(frozen,target));
        }
        const hasReferences = selected.some(historicalReference);
        if(hasReferences&&this.execution?.integrationVersion!=='workflow-proposal-plan/v1')fail('CAPABILITY_UNAVAILABLE','生成器尚未支持历史建议参考',{reasonCode:'HISTORICAL_REFERENCE_UNSUPPORTED'});
        if (request.automation) assertProposalBaseline(selected, baseline);
        const integrated = hasReferences ? { instructions: '', integration: historicalIntegration(selected, baseline) }
          : selected.length > 1 ? { instructions: '', integration: manualIntegration(selected, baseline) } : { instructions: '', integration: undefined };
        if (!integrated.integration && selected.some(item => Array.isArray(item.proposal?.patches) && item.proposal.patches.length)) integrated.integration = manualIntegration(selected, baseline);
        const frozenInstructions = [request.instructions, integrated.instructions].filter(Boolean).join('\n\n');
        if (frozenInstructions.length > MAX_INSTRUCTIONS_CHARS) fail('PAYLOAD_TOO_LARGE', '整合目标与说明过长，请缩小本次选择范围');
        const id = taskId ?? randomUUID();
        const feedback = 'feedback' in request ? request.feedback : '';
        const parentCandidateCommit = 'parentCandidateCommit' in request ? request.parentCandidateCommit : null;
        const excludedSourceRefs = snapshot.items.filter(item => !request.itemIds.includes(item.itemId) && item.sourceAvailable)
          .flatMap(item => item.sources).sort((a, b) => canonicalRepairJson(a).localeCompare(canonicalRepairJson(b)));
        const input: RepairBatchInput = { schemaVersion: 'workflow-repair/v2', taskId: id,
          // A manual joint plan does not change the existing evidence preparation policy.
          revision: (previous?.latestAttempt.revision ?? 0) + 1, baseline, items: await repairStage('evidence_freeze', async () => prepareProposalEvidence(selected, !!request.automation,{complete:true})),
          excludedSources: { count: excludedSourceRefs.length, digest: digestRepairJson(excludedSourceRefs) },
          ...(request.automation ? { automation: request.automation } : {}),
          ...('integration' in integrated && integrated.integration ? { integration: integrated.integration } : {}),
          instructions: frozenInstructions, parentCandidateCommit, feedback,
          previousReportRef: previous?.latestSuccessful ? `${id}/revisions/${previous.latestSuccessful.revision}` : null,
          taskBranch: `repair/${request.workflowId}/${id}` };
        const created = await repo.createRevision(tx, { input, requestId: request.requestId, requestDigest: digest,
          expectedAttemptRevision: 'expectedAttemptRevision' in request ? request.expectedAttemptRevision : 0 });
        this.requireMaterialDelivery(created.input);
        await new RepairTaskRepository(tx).append(created, actorId);
        return created;
      }));
    }
    await this.dispatch(revision, actorId);
    const result = await repairStage('revision_response', () => serial(this.db, () => new RepairBatchRepository(this.db).getRevision(request.workflowId, revision!.taskId, revision!.revision)));
    if (!result) fail('REVISION_NOT_FOUND', 'Created revision is unavailable');
    return result;
  }
  private requireMaterialDelivery(input:RepairBatchInput):void {
    if(input.materials&&this.execution?.materialDeliveryVersion!=='repair-material-download/v1')fail('CAPABILITY_UNAVAILABLE','Execution provider does not support material downloads');
  }
  private async dispatch(revision: RepairRevision, actorId?: string): Promise<void> {
    if (!this.execution || revision.phase !== 'drafting') return;
    // Validate stored originals, but never put them back into the AIS parameters.
    await repairStage('dispatch_materials', () => new RepairMaterialRepository(this.db).expand(revision.input));
    const executionInput=revision.input;
    this.requireMaterialDelivery(executionInput);
    const executionId = randomUUID();
    const claimed = await this.tx(async tx => {
      const current = await new RepairBatchRepository(tx).getRevision(revision.workflowId, revision.taskId, revision.revision);
      if (current?.phase !== 'drafting') return false;
      const claim=await new RepairTaskRepository(tx).claimDispatch(revision.taskId, revision.revision, executionId);
      if(!claim)return false;
      const materialAccess=executionInput.materials?await new RepairMaterialDownload(tx).issue(executionInput,claim):undefined;
      return {...claim,materialAccess};
    });
    if (!claimed) return;
    const submittedBy = actorId ?? claimed.actorId;
    let failure: 'rejected' | 'uncertain' | null = null; let jobId: string | null = null;
    try {
      const identity = { stepId: claimed.stepId, attempt: claimed.attempt, executionId: claimed.executionId };
      const receipt = await repairStage('ais_submit', () => this.execution!.dispatch({ input: executionInput, ...(claimed.materialAccess?{materialAccess:claimed.materialAccess}:{}), actorId: submittedBy, identity }));
      if (!receipt?.jobId?.trim() || receipt.jobId.length > 255) throw new Error('Invalid AIS job id');
      jobId = receipt.jobId;
    } catch (error) {
      const message = error instanceof Error ? error.message : '';
      failure = error instanceof RepairAutomationDeniedError || /^AIS executeSnapshot HTTP (?:400|401|403|404|422):/u.test(message) || message.startsWith('AIS executeSnapshot 失败:')
        || message.includes('token/projectName 未配置') ? 'rejected' : 'uncertain';
    }
    await this.tx(tx => new RepairTaskRepository(tx).finishDispatch(revision.taskId, revision.revision, jobId, failure, submittedBy));
  }
  async retryResult(actorId: string, taskId: string, expectedRevision: number): Promise<void> {
    this.actor(actorId);
    if (!this.execution) fail('CAPABILITY_UNAVAILABLE', 'Repair generation provider is unavailable');
    const task = await this.task(taskId);
    if (task.revisions.some(revision => revision.phase === 'published')) fail('STATE_CONFLICT', '此任务已有部署，请先验证效果；需要继续修复时创建新基线任务');
    if (!safeInteger(expectedRevision, 1) || task.latestAttempt.revision !== expectedRevision) fail('STATE_CONFLICT', 'Result is no longer current');
    if (task.latestAttempt.draft) return;
    const run = task.execution;
    if (task.latestAttempt.phase === 'failed' && ['AIS_FAILED', 'AIS_STOPPED', 'AIS_TIMEOUT'].includes(String(task.latestAttempt.error?.code)) && run?.jobId && run.executionId) {
      // Result-only recovery: a trusted absence permits a later explicit revision,
      // but this action itself must never create a revision or dispatch a Job.
      await this.requireStoppedAttempt(task, actorId);
      return;
    }
    if (task.latestAttempt.phase !== 'failed' || task.latestAttempt.error?.code !== 'GIT_RESULT_UNAVAILABLE'
      || !run?.jobId || !run.executionId) fail('STATE_CONFLICT', 'Only an unavailable Git result can be reread');
    const result = await this.execution.readResult({ input: task.latestAttempt.input, actorId, jobId: run.jobId,
      identity: { stepId: new RepairTaskRepository(this.db).stepId(taskId, expectedRevision), attempt: run.attempt, executionId: run.executionId } });
    if (result.status === 'failed') fail('STATE_CONFLICT', 'No successful candidate is available');
    await this.report({ workflowId: task.workflowId, taskId, revision: expectedRevision,
      expectedStateVersion: task.latestAttempt.stateVersion, ...result }, true);
  }
  private async requireStoppedAttempt(task: RepairTaskDetail, actorId: string): Promise<RepairRevision | null> {
    const run = task.execution;
    if (task.latestAttempt.phase === 'failed' && task.latestAttempt.error?.code === 'GIT_RESULT_UNAVAILABLE') fail('STATE_CONFLICT', 'Reread the original Git result before generating again', { recoveryAction: 'retry-result', reasonCode: 'RESULT_READ_UNKNOWN' });
    if (task.latestAttempt.phase !== 'failed' || !run?.jobId || !run.executionId) return null;
    let remote: Awaited<ReturnType<RepairExecutionPort['status']>>;
    try {
      remote = await this.execution!.status({ input: task.latestAttempt.input, actorId, jobId: run.jobId,
        identity: { stepId: new RepairTaskRepository(this.db).stepId(task.taskId, task.latestAttempt.revision),
          attempt: run.attempt, executionId: run.executionId } });
    } catch { fail('STATE_CONFLICT', 'Remote execution state is unknown; reconcile before generating again', { recoveryAction: 'retry-result', reasonCode: 'EXECUTION_UNKNOWN' }); }
    if (remote.status === 'running') fail('STATE_CONFLICT', 'Previous AIS execution is still running; stop or reconcile it first', { recoveryAction: 'retry-result', reasonCode: 'EXECUTION_RUNNING' });
    if (!['AIS_FAILED', 'AIS_STOPPED', 'AIS_TIMEOUT'].includes(String(task.latestAttempt.error?.code))) return null;
    // A Job can fail after its result has already been pushed. Recover the exact
    // execution before deciding its branch is safe for another generation.
    let result: Awaited<ReturnType<RepairExecutionPort['readResult']>>;
    try {
      result = await this.execution!.readResult({ input: task.latestAttempt.input, actorId, jobId: run.jobId,
        identity: { stepId: new RepairTaskRepository(this.db).stepId(task.taskId, task.latestAttempt.revision),
          attempt: run.attempt, executionId: run.executionId } });
    } catch (error) {
      if (remote.status !== 'success' && error instanceof RepairResultAbsentError) return null;
      fail('STATE_CONFLICT', 'Original Git result could not be confirmed; retry its read before generating again', { recoveryAction: 'retry-result', reasonCode: 'RESULT_READ_UNKNOWN' });
    }
    if (result.status === 'failed') return null;
    return this.report({ workflowId: task.workflowId, taskId: task.taskId, revision: task.latestAttempt.revision,
      expectedStateVersion: task.latestAttempt.stateVersion, ...result }, true);
  }
  async retryGeneration(actorId: string, taskId: string, request: { expectedRevision: number; requestId: string }): Promise<RepairRevision> {
    this.actor(actorId);
    if (!this.execution) fail('CAPABILITY_UNAVAILABLE', 'Repair generation provider is unavailable');
    if (!safeInteger(request.expectedRevision, 1) || !requiredText(request.requestId, 128)) fail('INVALID_INPUT', 'Invalid retry request');
    const task = await this.task(taskId);
    if (task.revisions.some(revision => revision.phase === 'published')) fail('STATE_CONFLICT', '此任务已有部署，请先验证效果；需要继续修复时创建新基线任务');
    if (task.latestAttempt.revision === request.expectedRevision && task.latestAttempt.draft
      && ['AIS_FAILED', 'AIS_STOPPED', 'AIS_TIMEOUT'].includes(String(task.latestAttempt.error?.code))) return task.latestAttempt;
    const digest = digestRepairJson({ actorId, taskId, retry: request });
    let next = await serial(this.db, () => new RepairBatchRepository(this.db).findRequest(task.workflowId, request.requestId, digest));
    if (!next) {
      if (task.latestAttempt.phase !== 'failed' || task.latestAttempt.revision !== request.expectedRevision) fail('STATE_CONFLICT', 'Only the latest failed attempt can be regenerated');
      const recovered = await this.requireStoppedAttempt(task, actorId);
      if (recovered) return recovered;
      next = await this.tx(async tx => {
        const repo = new RepairBatchRepository(tx);
        await repo.lockWorkflow(tx, task.workflowId);
        const committed = await repo.findRequest(task.workflowId, request.requestId, digest);
        if (committed) return committed;
        const input = { ...task.latestAttempt.input, revision: request.expectedRevision + 1,
          ...(!task.latestAttempt.input.integration&&(!task.latestAttempt.input.automation&&task.latestAttempt.input.items.length>1
            ||task.latestAttempt.input.items.some(item=>Array.isArray(item.proposal?.patches)&&item.proposal.patches.length))
            ?{integration:manualIntegration(task.latestAttempt.input.items,task.latestAttempt.input.baseline)}:{}),
          parentCandidateCommit: typeof task.latestSuccessful?.draft?.candidateCommit === 'string' ? task.latestSuccessful.draft.candidateCommit : null,
          previousReportRef: task.latestSuccessful ? `${taskId}/revisions/${task.latestSuccessful.revision}` : null };
        const created = await repo.createRevision(tx, { input, requestId: request.requestId, requestDigest: digest,
          expectedAttemptRevision: request.expectedRevision });
        this.requireMaterialDelivery(created.input);
        await new RepairTaskRepository(tx).append(created, actorId);
        return created;
      });
    }
    await this.dispatch(next, actorId);
    return (await this.task(taskId)).revisions.find(r => r.revision === next!.revision)!;
  }
  async retryDispatch(actorId: string, taskId: string, expectedRevision: number): Promise<void> {
    this.actor(actorId);
    if (!this.execution) fail('CAPABILITY_UNAVAILABLE', 'Repair generation provider is unavailable');
    if (!safeInteger(expectedRevision, 1)) fail('INVALID_INPUT', 'Invalid expectedRevision');
    const task = await this.task(taskId);
    if (task.latestAttempt.revision !== expectedRevision || task.latestAttempt.phase !== 'drafting'
      || !task.execution || !['created', 'dispatch_failed'].includes(task.execution.status)) fail('STATE_CONFLICT', 'Only an unclaimed or failed dispatch can be retried');
    await this.dispatch(task.latestAttempt, actorId);
  }
  async reconcile(limit = 50): Promise<number> {
    if (!this.execution) return 0;
    if (!safeInteger(limit, 1) || limit > 200) fail('INVALID_INPUT', 'Invalid reconcile limit');
    await serial(this.db, () => new RepairTaskRepository(this.db).expireStaleDispatches(Date.now() - 120_000));
    const executions = await serial(this.db, () => new RepairTaskRepository(this.db).active(limit));
    let settled = 0;
    for (const active of executions) settled += await withWorkflowBackgroundContext({ operation: 'result', taskId: active.taskId,
      revision: active.revision, jobId: active.jobId, attempt: active.identity.attempt, executionId: active.identity.executionId }, async () => {
      const task = await this.task(active.taskId);
      const revision = task.revisions.find(item => item.revision === active.revision);
      if (!revision || revision.phase !== 'drafting') return 0;
      const request = { input: revision.input, actorId: active.actorId, identity: active.identity, jobId: active.jobId };
      const fields = { workflowId: revision.workflowId };
      if (!active.successObservedAtMs && Date.now() - active.requestedAtMs > 2_400_000) {
        if (!await serial(this.db, () => new RepairTaskRepository(this.db).claimTimeout(active.taskId, active.identity))) return 0;
        try { await this.execution.stop?.(request); } catch { /* Persist timeout even if remote stop is unavailable. */ }
        const expectedStateVersion = revision.stateVersion;
        await this.report({ workflowId: revision.workflowId, taskId: revision.taskId, revision: revision.revision,
          expectedStateVersion, status: 'failed', error: { code: 'AIS_TIMEOUT', message: 'AIS repair exceeded the 40-minute deadline' } });
        logWorkflowBackground('repair_result', 'settled', { ...fields, outcome: 'failed', errorCode: 'AIS_TIMEOUT' });
        return 1;
      }
      let remote: Awaited<ReturnType<RepairExecutionPort['status']>> = { status: 'success', rawStatus: 'success', errorMessage: null };
      if (!active.successObservedAtMs) {
        try { remote = await this.execution!.status(request); this.statusFailures.delete(active.identity.executionId); }
        catch {
          const prior = this.statusFailures.get(active.identity.executionId);
          if (!prior || Date.now() - prior.at >= 60_000) {
            logWorkflowBackground('repair_result', 'failed', { ...fields, phase: 'ais_status', errorCode: 'AIS_STATUS_UNAVAILABLE', suppressed: prior?.suppressed ?? 0 });
            if (this.statusFailures.size >= 200) this.statusFailures.delete(this.statusFailures.keys().next().value!);
            this.statusFailures.set(active.identity.executionId, { at: Date.now(), suppressed: 0 });
          } else prior.suppressed++;
          return 0;
        }
        if (!await serial(this.db, () => new RepairTaskRepository(this.db).updateRemoteStatus(active.taskId, active.revision, active.identity, remote.rawStatus, remote.status === 'success'))) return 0;
        if (remote.status === 'running') return 0;
        logWorkflowBackground('repair_result', 'ais_terminal', { ...fields, status: remote.status });
      }
      const expectedStateVersion = (await this.task(active.taskId)).revisions.find(item => item.revision === active.revision)?.stateVersion;
      if (expectedStateVersion === undefined) return 0;
      let resultStatus: string = remote.status;
      let outcome: 'succeeded' | 'failed' = remote.status === 'success' ? 'succeeded' : 'failed';
      let errorCode = remote.status === 'stopped' ? 'AIS_STOPPED' : remote.status === 'failed' ? 'AIS_FAILED' : undefined;
      if (remote.status === 'success') {
        const claim = await serial(this.db, () => new RepairTaskRepository(this.db).claimResult(active.taskId, active.identity));
        if (claim === 'busy') return 0;
        let result: Awaited<ReturnType<RepairExecutionPort['readResult']>>;
        const end = startWorkflowBackgroundRun('repair_result', 'result_read', { ...fields, phase: 'result_read' });
        try {
          if (claim === 'interrupted') throw new Error('RESULT_READ_INTERRUPTED');
          result = await this.execution!.readResult(request);
          end('succeeded');
        } catch {
          end('failed', { errorCode: 'GIT_RESULT_UNAVAILABLE' });
          await this.report({ workflowId: revision.workflowId, taskId: revision.taskId, revision: revision.revision,
            expectedStateVersion, status: 'failed', error: { code: 'GIT_RESULT_UNAVAILABLE',
              message: 'Result ingestion failed or was interrupted; explicitly retry reading this execution result. No automatic Git retry.' } });
          logWorkflowBackground('repair_result', 'settled', { ...fields, outcome: 'failed', errorCode: 'GIT_RESULT_UNAVAILABLE' });
          return 1;
        }
        const settleEnd = startWorkflowBackgroundRun('repair_result', 'db_settlement', { ...fields, phase: 'db_settlement' });
        try {
          await this.report({ workflowId: revision.workflowId, taskId: revision.taskId, revision: revision.revision,
            expectedStateVersion, ...result });
          resultStatus = result.status;
          if (result.status === 'failed') {
            outcome = 'failed';
            errorCode = typeof result.error.code === 'string' && /^[A-Z][A-Z0-9_]{0,127}$/.test(result.error.code) ? result.error.code : 'GIT_RESULT_FAILED';
          }
          settleEnd('succeeded');
        } catch (error) {
          const invalid = error instanceof RepairBatchError && ['INVALID_INPUT', 'PAYLOAD_TOO_LARGE', 'CONTENT_MISMATCH'].includes(error.code);
          settleEnd('failed', { errorCode: invalid ? 'RESULT_VALIDATION_FAILED' : 'RESULT_SETTLEMENT_FAILED' });
          if (invalid) {
            await this.report({ workflowId: revision.workflowId, taskId: revision.taskId, revision: revision.revision,
              expectedStateVersion, status: 'failed', error: { code: 'GIT_RESULT_UNAVAILABLE', reasonCode: 'RESULT_VALIDATION_FAILED',
                message: 'Result did not match the frozen task input; inspect and explicitly retry this execution result.' } });
            logWorkflowBackground('repair_result', 'settled', { ...fields, outcome: 'failed', errorCode: 'RESULT_VALIDATION_FAILED' });
            return 1;
          }
          throw error;
        }
      } else {
        await this.report({ workflowId: revision.workflowId, taskId: revision.taskId, revision: revision.revision,
          expectedStateVersion, status: 'failed', error: { code: remote.status === 'stopped' ? 'AIS_STOPPED' : 'AIS_FAILED',
            rawStatus: remote.rawStatus, message: remote.errorMessage } });
      }
      logWorkflowBackground('repair_result', 'settled', { ...fields, outcome, status: resultStatus, errorCode });
      return 1;
    });
    return settled;
  }
  async disposition(actorId: string, input: Parameters<RepairWorkbenchService['disposition']>[1]): Promise<StoredRepairItem> {
    this.actor(actorId);
    boundedRepairJson(input, MAX_REQUEST_BYTES);
    if (!input || !identity(input.workflowId, 190) || !requiredText(input.itemId, 64) || !safeInteger(input.contentRevision, 1)
      || !safeInteger(input.expectedStateVersion) || !/^[a-f0-9]{64}$/.test(input.inputDigest)) fail('INVALID_INPUT', 'Invalid disposition input');
    if (input.includeHistorical !== undefined && typeof input.includeHistorical !== 'boolean') fail('INVALID_INPUT', 'Invalid history scope');
    const request = { ...input, includeHistorical: input.includeHistorical === true, actorId };
    return this.tx(async tx => {
      const repo = new RepairBatchRepository(tx);
      await repo.lockWorkflow(tx, input.workflowId);
      const retry = await repo.dispositionRetry(request);
      if (retry) return { ...(await repo.getItem(input.workflowId, input.itemId))!, ...retry, updatedAtMs: retry.disposition!.atMs };
      const snapshot = await this.snapshot(tx, input.workflowId, 'full', {
        itemIds: [input.itemId], includeHistorical: input.includeHistorical === true,
      });
      const activeSnapshot = await this.sources.activeSnapshot?.(tx, input.workflowId);
      const acceptedDigests = activeSnapshot === undefined ? [snapshot.inputDigest] : (['current', 'historical', 'all'] as const).map(scope => versionDigest(snapshot.items.filter(item => inVersionScope(item, activeSnapshot, scope))));
      if (!acceptedDigests.includes(input.inputDigest)) fail('SOURCE_CHANGED', 'Repair sources changed');
      let stored = await repo.getItem(input.workflowId, input.itemId);
      const source = snapshot.materializable.get(input.itemId);
      if (!stored && !source) fail('ITEM_NOT_FOUND', 'Item is unavailable or legacy history is read-only');
      if (source) stored = await repo.materializeItem(tx, { workflowId: input.workflowId, ...source });
      if (!stored || stored.contentRevision !== input.contentRevision) fail('CONTENT_MISMATCH', 'Item content version changed');
      await repo.setDisposition(tx, request);
      return (await repo.getItem(input.workflowId, stored.itemId))!;
    });
  }
  async cancel(actorId: string, taskId: string, expectedRevision: number): Promise<void> {
    this.actor(actorId);
    if (!safeInteger(expectedRevision, 1)) fail('INVALID_INPUT', 'Invalid expectedRevision');
    const before = await this.task(taskId);
    await this.tx(async tx => {
      const task = await this.readTask(tx, taskId);
      if (task.revisions.some(revision => revision.phase === 'published')) fail('STATE_CONFLICT', '已部署任务不能取消或隐式回滚，请在效果验证中记录结论');
      if (task.latestAttempt.revision === expectedRevision && task.latestAttempt.phase === 'cancelled') return;
      await new RepairBatchRepository(tx).cancelTask(tx, { workflowId: task.workflowId, taskId, expectedAttemptRevision: expectedRevision, expectedStateVersion: task.latestAttempt.stateVersion });
      await new RepairTaskRepository(tx).cancel(taskId);
    });
    const execution = before.execution;
    if (this.execution?.stop && execution?.jobId && execution.executionId && execution.attempt > 0) {
      const request = { input: before.latestAttempt.input, actorId,
        identity: { stepId: new RepairTaskRepository(this.db).stepId(taskId, expectedRevision), attempt: execution.attempt, executionId: execution.executionId },
        jobId: execution.jobId };
      try {
        const submittedBy = await new RepairTaskRepository(this.db).submissionUser(taskId, expectedRevision);
        await this.execution.stop({ ...request, actorId: submittedBy });
      } catch { /* Local cancellation is authoritative; remote stop is best effort. */ }
    }
  }
  async diff(taskId: string, revision: number, base: 'baseline' | 'parent') {
    if (!this.execution?.diff) fail('CAPABILITY_UNAVAILABLE', 'Repair diff provider is unavailable');
    if (!safeInteger(revision, 1) || !['baseline', 'parent'].includes(base)) fail('INVALID_INPUT', 'Invalid diff request');
    const task = await this.task(taskId);
    const selected = task.revisions.find(r => r.revision === revision);
    if (!selected) fail('REVISION_NOT_FOUND', 'Repair revision does not exist');
    if (!selected.draft) fail('STATE_CONFLICT', 'Revision has no successful candidate');
    const candidateCommit = String(selected.draft.candidateCommit);
    const baseCommit = base === 'parent' ? selected.input.parentCandidateCommit ?? selected.input.baseline.packCommit : selected.input.baseline.packCommit;
    const result = await this.execution.diff({ workflowId: task.workflowId, taskId, revision, baseCommit, candidateCommit });
    if (!result || result.baseCommit !== baseCommit || result.candidateCommit !== candidateCommit) fail('CONTENT_MISMATCH', 'Diff did not bind the selected commits');
    if (typeof result.truncated !== 'boolean' || !Array.isArray(result.files) || result.files.some(file => !file
      || !requiredText(file.path, 1024) || file.path.startsWith('/') || file.path.includes('\\') || file.path.split('/').some(part => !part || part === '.' || part === '..')
      || !requiredText(file.status, 32) || typeof file.binary !== 'boolean' || typeof file.truncated !== 'boolean'
      || !(file.before === null || typeof file.before === 'string') || !(file.after === null || typeof file.after === 'string'))) fail('INVALID_INPUT', 'Invalid diff response');
    boundedRepairJson(result, MAX_RESULT_BYTES);
    return result;
  }
  /** Trusted Host call only. The AIS owner must verify the execution ticket before invoking. */
  async report(input: TrustedRepairReport, recoverRead = false): Promise<RepairRevision> {
    if (!input || !['succeeded', 'blocked', 'no_change', 'failed'].includes(input.status) || !identity(input.workflowId, 190) || !identity(input.taskId, 64)
      || !safeInteger(input.revision, 1) || !safeInteger(input.expectedStateVersion)) fail('INVALID_INPUT', 'Invalid report identity');
    return this.tx(async tx => {
      const repo = new RepairBatchRepository(tx);
      await repo.lockWorkflow(tx, input.workflowId);
      const current = await repo.getRevision(input.workflowId, input.taskId, input.revision);
      if (!current) fail('REVISION_NOT_FOUND', 'Repair revision does not exist');
      let settled: RepairRevision;
      if (input.status === 'failed') settled = await repo.failDraft(tx, input);
      else {
        boundedRepairJson(input.draft, MAX_RESULT_BYTES); boundedRepairJson(input.checks, MAX_REPORT_BYTES);
        const draft = input.draft; const checks = input.checks;
        validateRepairModelInputs(draft.modelInputs, current.input);
        const ids = current.input.items.map(item => item.itemId).sort();
        const itemResults = draft.itemResults as Array<{ itemId: string; status: string; reason: string }>;
        const coverage = checks.coverage as Array<{ itemId: string }>;
        if (draft.parentCandidateCommit !== (current.input.parentCandidateCommit ?? current.input.baseline.packCommit)
          || typeof draft.packDigest !== 'string' || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(draft.packDigest)
          || checks.schemaVersion !== 'workflow-repair-checks/v1' || checks.candidateCommit !== draft.candidateCommit
          || checks.packDigest !== draft.packDigest || checks.candidateDigest !== digestRepairJson(draft)
          || !Array.isArray(itemResults) || itemResults.some(item => !item || !['changed', 'not_needed', 'unresolved'].includes(item.status) || !requiredText(item.reason, 4000))
          || canonicalRepairJson(itemResults.map(item => item.itemId).sort()) !== canonicalRepairJson(ids)
          || !Array.isArray(coverage) || coverage.some(item => !item || !requiredText(item.itemId, 64))
          || canonicalRepairJson(coverage.map(item => item.itemId).sort()) !== canonicalRepairJson(ids)) fail('INVALID_INPUT', 'Result must bind the frozen candidate and every selected item');
        const staticStatus = (checks.static as { status?: string })?.status;
        const mockStatus = (checks.mock as { status?: string })?.status;
        if (!['passed', 'failed'].includes(staticStatus ?? '') || !['passed', 'failed', 'not_covered'].includes(mockStatus ?? '')) fail('INVALID_INPUT', 'Invalid check status');
        const checkFailed = staticStatus === 'failed' || mockStatus === 'failed';
        if ((input.status === 'blocked') !== checkFailed || (input.status === 'no_change' && itemResults.some(item => item.status !== 'not_needed')))
          fail('INVALID_INPUT', 'AIS status does not match checks or item results');
        settled = await repo.completeDraft(tx, { ...input, recoverRead, phase: input.status === 'blocked' ? 'blocked'
          : input.status === 'no_change' ? 'no_change' : 'review' });
      }
      await new RepairTaskRepository(tx).settle(settled);
      await this.onCompleted?.(tx, settled);
      return settled;
    });
  }
}

export function createRepairWorkbenchService(db: IDatabase, sourcePort: RepairSourcePort, executionPort?: RepairExecutionPort, proposals?: ProposalCoordinator,
  onCompleted?: (tx: IDatabase, revision: RepairRevision) => Promise<void>): RepairWorkbenchService & { report(input: TrustedRepairReport): Promise<RepairRevision>; proposalSnapshot(workflowId: string): Promise<import('./repair-proposal-integration.js').ProposalSnapshot>; retryProposalIntegration(workflowId: string): Promise<void> } {
  return new WorkflowRepairWorkbench(db, sourcePort, executionPort, proposals, onCompleted);
}
