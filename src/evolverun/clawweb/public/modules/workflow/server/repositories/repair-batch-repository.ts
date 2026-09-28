import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import {
  RepairBatchError, boundedRepairJson, canonicalRepairJson, digestRepairJson,
  validateRepairBatchInput, validateRepairItem, MAX_FROZEN_BYTES, MAX_REPORT_BYTES, MAX_RESULT_BYTES,
  type RepairBatchInput, type RepairDisposition, type RepairItem, type RepairRevision, type StoredRepairItem,
} from '../contracts/repair-batch.js';

type Row = Record<string, any>;
type RevisionIdentity = { workflowId: string; taskId: string; revision: number; expectedStateVersion: number };
export type RepairDispositionResult = Pick<StoredRepairItem, 'itemId' | 'state' | 'stateVersion' | 'disposition'>;

function conflict(code: ConstructorParameters<typeof RepairBatchError>[0], message: string): never { throw new RepairBatchError(code, message); }
function content(item: RepairItem) {
  return { groupKey: item.groupKey, proposalKey: item.proposalKey, contentRevision: item.contentRevision,
    previousItemId: item.previousItemId, proposal: item.proposal, instruction: item.instruction };
}
function snapshotContent(item: RepairItem) { return { ...content(item), ...(item.context ? { context: item.context } : {}) }; }
function itemFromRow(row: Row): StoredRepairItem {
  return { ...JSON.parse(row.content_json), itemId: row.item_id, workflowId: row.workflow_id, episodeKey: row.episode_key,
    sources: JSON.parse(row.source_refs_json), state: row.state, stateVersion: Number(row.state_version),
    activeTaskId: row.active_task_id, activeRevision: row.active_revision == null ? null : Number(row.active_revision),
    disposition: row.disposition_json == null ? null : JSON.parse(row.disposition_json), updatedAtMs: Number(row.updated_at_ms) };
}
function revisionFromRow(row: Row): RepairRevision {
  return { workflowId: row.workflow_id, taskId: row.task_id, revision: Number(row.revision), phase: row.phase,
    stateVersion: Number(row.state_version), requestId: row.request_id, requestDigest: row.request_digest,
    input: JSON.parse(row.input_json), draft: row.draft_json == null ? null : JSON.parse(row.draft_json),
    checks: row.checks_json == null ? null : JSON.parse(row.checks_json), candidateDigest: row.candidate_digest,
    checksDigest: row.checks_digest, error: row.error_json == null ? null : JSON.parse(row.error_json),
    createdAtMs: Number(row.created_at_ms), updatedAtMs: Number(row.updated_at_ms) };
}

/** Storage API: writes require the caller's IDatabase transaction, shared with task creation/step settlement.
 * Authorization, source loading, execution-ticket verification and task pointers belong to the service.
 * No method dispatches work, changes old suggestions or activates a deployment.
 */
export class RepairBatchRepository {
  constructor(private readonly db: IDatabase) {}

  async lockWorkflow(tx: IDatabase, workflowId: string): Promise<void> {
    if (tx.dbType === 'noop') conflict('CAPABILITY_UNAVAILABLE', 'Repair storage is unavailable');
    if (tx.dbType === 'sqlite') {
      // Acquire the SQLite write reservation before reading identities. Do not rely on SELECT locking.
      await tx.exec('UPDATE workflow_specs SET workflow_id = workflow_id WHERE workflow_id = ?', [workflowId]);
    }
    const lock = tx.dbType === 'mysql' || tx.dbType === 'zdas' ? ' FOR UPDATE' : '';
    const rows = await tx.query(`SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?${lock}`, [workflowId]);
    if (!rows.length) conflict('WORKFLOW_NOT_FOUND', 'Workflow does not exist');
  }

  private async readItem(tx: IDatabase, workflowId: string, itemId: string): Promise<StoredRepairItem | null> {
    const row = (await tx.query<Row>('SELECT * FROM workflow_repair_items WHERE workflow_id = ? AND item_id = ?', [workflowId, itemId]))[0];
    return row ? itemFromRow(row) : null;
  }
  getItem(workflowId: string, itemId: string): Promise<StoredRepairItem | null> { return this.readItem(this.db, workflowId, itemId); }

  async listItems(workflowId: string): Promise<StoredRepairItem[]> {
    return (await this.db.query<Row>('SELECT * FROM workflow_repair_items WHERE workflow_id = ? ORDER BY item_id', [workflowId])).map(itemFromRow);
  }
  async findRequest(workflowId: string, requestId: string, requestDigest: string): Promise<RepairRevision | null> {
    const row = (await this.db.query<Row>('SELECT * FROM workflow_repair_revisions WHERE request_key = ?', [digestRepairJson([workflowId, requestId])]))[0];
    if (!row) return null;
    if (row.workflow_id !== workflowId || row.request_id !== requestId || row.request_digest !== requestDigest) conflict('IDEMPOTENCY_MISMATCH', 'Revision request id already used');
    return revisionFromRow(row);
  }
  async taskWorkflow(taskId: string): Promise<string | null> {
    const row = (await this.db.query<{ workflow_id: string }>('SELECT workflow_id FROM workflow_repair_revisions WHERE task_id = ? LIMIT 1', [taskId]))[0];
    return row?.workflow_id ?? null;
  }
  async listRevisions(workflowId: string, taskId?: string): Promise<RepairRevision[]> {
    return (await this.db.query<Row>(`SELECT * FROM workflow_repair_revisions WHERE workflow_id = ?${taskId ? ' AND task_id = ?' : ''} ORDER BY revision DESC`, taskId ? [workflowId, taskId] : [workflowId])).map(revisionFromRow);
  }
  async dispositionRetry(input: Parameters<RepairBatchRepository['setDisposition']>[1]): Promise<RepairDispositionResult | null> {
    const key = digestRepairJson(['repair-disposition', input.workflowId, input.itemId, input.actorId, input.requestId]);
    const row = (await this.db.query<Row>('SELECT evidence_json FROM workflow_healing_outcomes WHERE event_key = ?', [key]))[0];
    if (!row) return null;
    const audit = JSON.parse(row.evidence_json);
    if (audit.requestDigest !== digestRepairJson(input)) conflict('IDEMPOTENCY_MISMATCH', 'Disposition request already used');
    return audit.response;
  }

  async materializeItem(tx: IDatabase, input: { workflowId: string; episodeKey: string; item: RepairItem }): Promise<StoredRepairItem> {
    const item = validateRepairItem(input.item);
    if (!input.episodeKey?.trim() || input.episodeKey.length > 128) conflict('INVALID_INPUT', 'Invalid episodeKey');
    await this.lockWorkflow(tx, input.workflowId);
    if (item.previousItemId && !await this.readItem(tx, input.workflowId, item.previousItemId)) conflict('ITEM_NOT_FOUND', 'Previous item is not in this workflow');
    const identity = [input.workflowId, item.groupKey, item.proposalKey, input.episodeKey];
    const identityDigest = digestRepairJson(identity);
    const existing = (await tx.query<Row>('SELECT * FROM workflow_repair_items WHERE identity_digest = ?', [identityDigest]))[0];
    const contentJson = boundedRepairJson(snapshotContent(item), MAX_FROZEN_BYTES);
    if (existing) {
      if (canonicalRepairJson([existing.workflow_id, existing.group_key, existing.proposal_key, existing.episode_key]) !== canonicalRepairJson(identity)
        || canonicalRepairJson(content(itemFromRow(existing))) !== canonicalRepairJson(content(item))) conflict('CONTENT_MISMATCH', 'An immutable content identity cannot be replaced');
      const sources = [...new Map([...JSON.parse(existing.source_refs_json), ...item.sources].map(source => [canonicalRepairJson(source), source])).values()];
      await tx.exec('UPDATE workflow_repair_items SET source_refs_json = ?, content_json = ?, updated_at_ms = ? WHERE item_id = ?',
        [boundedRepairJson(sources, MAX_FROZEN_BYTES), contentJson, Date.now(), existing.item_id]);
      return (await this.readItem(tx, input.workflowId, existing.item_id))!;
    }
    const now = Date.now();
    await tx.exec(`INSERT INTO workflow_repair_items
      (item_id, workflow_id, group_key, proposal_key, episode_key, identity_digest, content_revision, previous_item_id,
       content_json, source_refs_json, state, state_version, updated_at_ms)
      VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 0, ?)`,
    [item.itemId, input.workflowId, item.groupKey, item.proposalKey, input.episodeKey, identityDigest,
      item.contentRevision, item.previousItemId, contentJson, boundedRepairJson(item.sources, MAX_FROZEN_BYTES), now]);
    return (await this.readItem(tx, input.workflowId, item.itemId))!;
  }

  async setDisposition(tx: IDatabase, input: {
    workflowId: string; itemId: string; expectedStateVersion: number; action: 'no_action' | 'restore';
    actorId: string; requestId: string; reason: string;
  }): Promise<RepairDispositionResult> {
    if (!['no_action', 'restore'].includes(input.action) || !input.requestId?.trim() || input.requestId.length > 128
      || !input.actorId?.trim() || input.actorId.length > 128 || typeof input.reason !== 'string' || input.reason.length > 4000) conflict('INVALID_INPUT', 'Invalid disposition request');
    await this.lockWorkflow(tx, input.workflowId);
    const eventKey = digestRepairJson(['repair-disposition', input.workflowId, input.itemId, input.actorId, input.requestId]);
    const requestDigest = digestRepairJson(input);
    const prior = (await tx.query<Row>('SELECT evidence_json FROM workflow_healing_outcomes WHERE event_key = ?', [eventKey]))[0];
    if (prior) {
      const audit = JSON.parse(prior.evidence_json);
      if (audit.requestDigest !== requestDigest) conflict('IDEMPOTENCY_MISMATCH', 'Request id was already used with different content');
      return audit.response;
    }
    const item = await this.readItem(tx, input.workflowId, input.itemId);
    if (!item) conflict('ITEM_NOT_FOUND', 'Repair item does not exist in this workflow');
    if (item.activeTaskId) conflict('ITEM_BUSY', 'A task owns this content version');
    const previousState = input.action === 'no_action' ? 'pending' : 'no_action';
    if (item.state !== previousState || item.stateVersion !== input.expectedStateVersion) conflict('STATE_CONFLICT', 'Repair item changed');
    // The unique identity key prevents a second same-content/same-episode item, including on restore.
    const atMs = Date.now();
    const disposition: RepairDisposition = { action: input.action, actorId: input.actorId, reason: input.reason,
      requestId: input.requestId, contentRevision: item.contentRevision, atMs };
    const state = input.action === 'no_action' ? 'no_action' : 'pending';
    const updated = await tx.exec(`UPDATE workflow_repair_items SET state = ?, state_version = state_version + 1,
      disposition_json = ?, updated_at_ms = ? WHERE workflow_id = ? AND item_id = ? AND state_version = ?
      AND state = ? AND active_task_id IS NULL`,
    [state, canonicalRepairJson(disposition), atMs, input.workflowId, input.itemId, input.expectedStateVersion, previousState]);
    if (updated.affectedRows !== 1) conflict('STATE_CONFLICT', 'Repair item changed');
    const response: RepairDispositionResult = { itemId: item.itemId, state, stateVersion: item.stateVersion + 1, disposition };
    await tx.exec(`INSERT INTO workflow_healing_outcomes
      (outcome_id, lesson_id, suggestion_id, workflow_id, action, applied, succeeded, verdict, note,
       source_task_id, source_step_id, created_by, repair_item_id, evidence_json, event_key)
      VALUES (?, NULL, NULL, ?, ?, 0, 0, 'neutral', ?, NULL, NULL, ?, ?, ?, ?)`,
    [eventKey, input.workflowId, `repair_${input.action}`, input.reason, input.actorId, item.itemId,
      boundedRepairJson({ requestDigest, response }, 65_535), eventKey]);
    return response;
  }

  async claimItems(tx: IDatabase, input: {
    workflowId: string; taskId: string; revision: number; items: Array<{ itemId: string; expectedStateVersion: number }>;
  }): Promise<void> {
    for (const item of input.items) {
      const result = await tx.exec(`UPDATE workflow_repair_items SET state = 'processing', active_task_id = ?, active_revision = ?,
        state_version = state_version + 1, updated_at_ms = ? WHERE workflow_id = ? AND item_id = ? AND state_version = ?
        AND ((state = 'pending' AND active_task_id IS NULL) OR (state = 'processing' AND active_task_id = ?))`,
      [input.taskId, input.revision, Date.now(), input.workflowId, item.itemId, item.expectedStateVersion, input.taskId]);
      if (result.affectedRows !== 1) conflict('STATE_CONFLICT', 'Repair item is unavailable or its ownership changed');
    }
  }

  private async readRevision(tx: IDatabase, workflowId: string, taskId: string, revision: number): Promise<RepairRevision | null> {
    const row = (await tx.query<Row>('SELECT * FROM workflow_repair_revisions WHERE workflow_id = ? AND task_id = ? AND revision = ?', [workflowId, taskId, revision]))[0];
    return row ? revisionFromRow(row) : null;
  }
  getRevision(workflowId: string, taskId: string, revision: number): Promise<RepairRevision | null> { return this.readRevision(this.db, workflowId, taskId, revision); }
  private async latest(tx: IDatabase, workflowId: string, taskId: string, successOnly = false): Promise<RepairRevision | null> {
    const row = (await tx.query<Row>(`SELECT * FROM workflow_repair_revisions WHERE workflow_id = ? AND task_id = ?
      ${successOnly ? 'AND draft_json IS NOT NULL' : ''} ORDER BY revision DESC LIMIT 1`, [workflowId, taskId]))[0];
    return row ? revisionFromRow(row) : null;
  }
  latestSuccessfulRevision(workflowId: string, taskId: string): Promise<RepairRevision | null> { return this.latest(this.db, workflowId, taskId, true); }

  async createRevision(tx: IDatabase, request: {
    input: RepairBatchInput; requestId: string; requestDigest: string; expectedAttemptRevision: number;
  }): Promise<RepairRevision> {
    const input = validateRepairBatchInput(request.input);
    if (!request.requestId?.trim() || request.requestId.length > 128 || !/^[a-f0-9]{64}$/.test(request.requestDigest)) conflict('INVALID_INPUT', 'Invalid revision request identity');
    const workflowId = input.baseline.workflowId;
    await this.lockWorkflow(tx, workflowId);
    const requestKey = digestRepairJson([workflowId, request.requestId]);
    const retry = (await tx.query<Row>('SELECT * FROM workflow_repair_revisions WHERE request_key = ?', [requestKey]))[0];
    if (retry) {
      if (retry.workflow_id !== workflowId || retry.request_id !== request.requestId || retry.request_digest !== request.requestDigest) conflict('IDEMPOTENCY_MISMATCH', 'Revision request id already used');
      return revisionFromRow(retry);
    }
    const active = await tx.query<Row>(`SELECT r.task_id, r.phase FROM workflow_repair_revisions r WHERE r.workflow_id = ?
      AND r.revision = (SELECT MAX(r2.revision) FROM workflow_repair_revisions r2 WHERE r2.task_id = r.task_id)
      AND r.phase NOT IN ('published', 'no_change', 'cancelled')`, [workflowId]);
    if (active.some(row => row.task_id !== input.taskId)) conflict('ACTIVE_TASK_CONFLICT', 'Workflow already has an editable repair task');
    const previous = await this.latest(tx, workflowId, input.taskId);
    if (request.expectedAttemptRevision !== (previous?.revision ?? 0) || input.revision !== (previous?.revision ?? 0) + 1
      || (previous && !['review', 'blocked', 'failed'].includes(previous.phase))) conflict('STATE_CONFLICT', 'Revision is not editable or latest attempt changed');
    if (previous && canonicalRepairJson(previous.input.baseline) !== canonicalRepairJson(input.baseline)) conflict('CONTENT_MISMATCH', 'A task cannot change its frozen baseline');
    const successful = await this.latest(tx, workflowId, input.taskId, true);
    if (input.parentCandidateCommit !== (successful?.draft?.candidateCommit ?? null)) conflict('CONTENT_MISMATCH', 'Parent must be the task last successful candidate');
    const claims = [];
    for (const frozenItem of input.items) {
      const stored = await this.readItem(tx, workflowId, frozenItem.itemId);
      if (!stored) conflict('ITEM_NOT_FOUND', 'Selected item does not exist in this workflow');
      if (canonicalRepairJson(content(stored)) !== canonicalRepairJson(content(frozenItem))) conflict('CONTENT_MISMATCH', 'Selected content changed');
      const knownSources = new Set(stored.sources.map(canonicalRepairJson));
      if (frozenItem.sources.some(source => !knownSources.has(canonicalRepairJson(source)))) conflict('CONTENT_MISMATCH', 'Selected source is not materialized');
      claims.push({ itemId: stored.itemId, expectedStateVersion: stored.stateVersion });
    }
    await this.claimItems(tx, { workflowId, taskId: input.taskId, revision: input.revision, items: claims });
    const selected = new Set(input.items.map(item => item.itemId));
    const owned = await tx.query<Row>('SELECT item_id FROM workflow_repair_items WHERE workflow_id = ? AND active_task_id = ?', [workflowId, input.taskId]);
    for (const row of owned) if (!selected.has(row.item_id)) {
      await tx.exec(`UPDATE workflow_repair_items SET state = 'pending', active_task_id = NULL, active_revision = NULL,
        state_version = state_version + 1, updated_at_ms = ? WHERE workflow_id = ? AND item_id = ? AND active_task_id = ? AND state = 'processing'`,
      [Date.now(), workflowId, row.item_id, input.taskId]);
    }
    const now = Date.now();
    await tx.exec(`INSERT INTO workflow_repair_revisions (task_id, revision, workflow_id, phase, state_version,
      request_id, request_digest, request_key, input_json, created_at_ms, updated_at_ms)
      VALUES (?, ?, ?, 'drafting', 0, ?, ?, ?, ?, ?, ?)`,
    [input.taskId, input.revision, workflowId, request.requestId, request.requestDigest, requestKey, boundedRepairJson(input, MAX_FROZEN_BYTES), now, now]);
    return (await this.readRevision(tx, workflowId, input.taskId, input.revision))!;
  }

  private async currentAttempt(tx: IDatabase, input: RevisionIdentity): Promise<RepairRevision> {
    await this.lockWorkflow(tx, input.workflowId);
    const current = await this.latest(tx, input.workflowId, input.taskId);
    if (!current || current.revision !== input.revision) conflict('STATE_CONFLICT', 'Draft is no longer current');
    return current;
  }
  private requireDraftCas(current: RepairRevision, expectedStateVersion: number): void {
    if (current.stateVersion !== expectedStateVersion || current.phase !== 'drafting') conflict('STATE_CONFLICT', 'Draft result was already settled');
  }
  async completeDraft(tx: IDatabase, input: RevisionIdentity & {
    draft: Record<string, unknown>; checks: Record<string, unknown>; phase: 'review' | 'blocked';
  }): Promise<RepairRevision> {
    const current = await this.currentAttempt(tx, input);
    if (!['review', 'blocked'].includes(input.phase)
      || typeof input.draft.candidateCommit !== 'string' || !/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/.test(input.draft.candidateCommit)
      || input.checks.candidateCommit !== input.draft.candidateCommit) conflict('INVALID_INPUT', 'Checks must bind the same immutable candidate commit');
    const draftJson = boundedRepairJson(input.draft, MAX_RESULT_BYTES);
    const checksJson = boundedRepairJson(input.checks, MAX_REPORT_BYTES);
    if (current.phase === input.phase && current.draft !== null && current.checks !== null
      && canonicalRepairJson(current.draft) === draftJson && canonicalRepairJson(current.checks) === checksJson) return current;
    this.requireDraftCas(current, input.expectedStateVersion);
    const result = await tx.exec(`UPDATE workflow_repair_revisions SET phase = ?, state_version = state_version + 1,
      draft_json = ?, checks_json = ?, candidate_digest = ?, checks_digest = ?, updated_at_ms = ?
      WHERE workflow_id = ? AND task_id = ? AND revision = ? AND state_version = ? AND phase = 'drafting' AND draft_json IS NULL`,
    [input.phase, draftJson, checksJson, digestRepairJson(input.draft), digestRepairJson(input.checks), Date.now(),
      input.workflowId, input.taskId, input.revision, input.expectedStateVersion]);
    if (result.affectedRows !== 1) conflict('STATE_CONFLICT', 'Draft result was already settled');
    return (await this.readRevision(tx, input.workflowId, input.taskId, input.revision))!;
  }
  async failDraft(tx: IDatabase, input: RevisionIdentity & { error: Record<string, unknown> }): Promise<RepairRevision> {
    const current = await this.currentAttempt(tx, input);
    const errorJson = boundedRepairJson(input.error, 65_535);
    if (current.phase === 'failed' && current.error !== null && canonicalRepairJson(current.error) === errorJson) return current;
    this.requireDraftCas(current, input.expectedStateVersion);
    const result = await tx.exec(`UPDATE workflow_repair_revisions SET phase = 'failed', state_version = state_version + 1,
      error_json = ?, updated_at_ms = ? WHERE workflow_id = ? AND task_id = ? AND revision = ? AND state_version = ?
      AND phase = 'drafting' AND draft_json IS NULL`,
    [errorJson, Date.now(), input.workflowId, input.taskId, input.revision, input.expectedStateVersion]);
    if (result.affectedRows !== 1) conflict('STATE_CONFLICT', 'Draft result was already settled');
    return (await this.readRevision(tx, input.workflowId, input.taskId, input.revision))!;
  }
  async cancelTask(tx: IDatabase, input: { workflowId: string; taskId: string; expectedAttemptRevision: number; expectedStateVersion: number }): Promise<void> {
    await this.lockWorkflow(tx, input.workflowId);
    const current = await this.latest(tx, input.workflowId, input.taskId);
    if (!current || current.revision !== input.expectedAttemptRevision || current.stateVersion !== input.expectedStateVersion
      || !['drafting', 'review', 'blocked', 'failed'].includes(current.phase)) conflict('STATE_CONFLICT', 'Task cannot be cancelled');
    const result = await tx.exec(`UPDATE workflow_repair_revisions SET phase = 'cancelled', state_version = state_version + 1, updated_at_ms = ?
      WHERE workflow_id = ? AND task_id = ? AND revision = ? AND state_version = ?`,
    [Date.now(), input.workflowId, input.taskId, current.revision, current.stateVersion]);
    if (result.affectedRows !== 1) conflict('STATE_CONFLICT', 'Task changed');
    await tx.exec(`UPDATE workflow_repair_items SET state = 'pending', active_task_id = NULL, active_revision = NULL,
      state_version = state_version + 1, updated_at_ms = ? WHERE workflow_id = ? AND active_task_id = ? AND state = 'processing'`,
    [Date.now(), input.workflowId, input.taskId]);
  }
}
