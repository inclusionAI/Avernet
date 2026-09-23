import { randomUUID } from 'node:crypto';
import type { IDatabase } from '@avernet/clawweb-shared/server/db';
import {
  RepairBatchError, boundedRepairJson, canonicalRepairJson, digestRepairJson, validateRepairItem,
  MAX_ITEMS, MAX_REQUEST_BYTES, MAX_INSTRUCTIONS_CHARS, MAX_RESULT_BYTES, MAX_REPORT_BYTES,
  type RepairBatchInput, type RepairItem, type RepairRevision, type StoredRepairItem,
} from '../contracts/repair-batch.js';
import type {
  RepairWorkbenchService, RepairSourcePort, RepairExecutionPort, RepairSelectionRequest,
  RepairFeedbackRequest, RepairCandidatesResponse, RepairTaskDetail, RepairInboxItem, RepairCapabilities,
} from '../contracts/repair-workbench.js';
import { RepairBatchRepository } from '../repositories/repair-batch-repository.js';
import { RepairTaskRepository } from '../repositories/repair-task-repository.js';

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
function fail(code: ConstructorParameters<typeof RepairBatchError>[0], message: string): never { throw new RepairBatchError(code, message); }
function requiredText(value: unknown, max: number, empty = false): value is string { return typeof value === 'string' && value.length <= max && (empty || value.trim().length > 0); }
function identity(value: unknown, max: number): value is string { return requiredText(value, max) && /^[A-Za-z0-9][A-Za-z0-9_-]*$/.test(value); }
function safeInteger(value: unknown, min = 0): value is number { return Number.isSafeInteger(value) && Number(value) >= min; }
function selection(raw: RepairSelectionRequest): RepairSelectionRequest {
  boundedRepairJson(raw, MAX_REQUEST_BYTES);
  if (!raw || !identity(raw.workflowId, 190) || !requiredText(raw.requestId, 128)
    || !Array.isArray(raw.itemIds) || raw.itemIds.length < 1 || raw.itemIds.length > MAX_ITEMS
    || raw.itemIds.some(id => !requiredText(id, 64)) || new Set(raw.itemIds).size !== raw.itemIds.length
    || !/^[a-f0-9]{64}$/.test(raw.inputDigest) || !requiredText(raw.instructions, MAX_INSTRUCTIONS_CHARS, true)) fail('INVALID_INPUT', 'Invalid repair selection');
  return { workflowId: raw.workflowId, itemIds: [...raw.itemIds].sort(), inputDigest: raw.inputDigest, instructions: raw.instructions, requestId: raw.requestId };
}
function identityKey(item: RepairItem, episodeKey: string): string { return canonicalRepairJson([item.groupKey, item.proposalKey, episodeKey]); }

export type TrustedRepairReport = {
  workflowId: string; taskId: string; revision: number; expectedStateVersion: number;
} & ({ status: 'succeeded'; draft: Record<string, unknown>; checks: Record<string, unknown> }
  | { status: 'failed'; error: Record<string, unknown> });

class WorkflowRepairWorkbench implements RepairWorkbenchService {
  constructor(private readonly db: IDatabase, private readonly sources: RepairSourcePort, private readonly execution?: RepairExecutionPort) {}
  private capabilities(): RepairCapabilities {
    return { generation: !!this.execution, diff: !!this.execution?.diff, publication: false,
      reason: this.execution ? null : 'Repair generation provider is unavailable' };
  }
  private tx<T>(fn: (tx: IDatabase) => Promise<T>): Promise<T> { return serial(this.db, () => this.db.transaction(fn)); }
  private actor(actorId: string): void { if (!requiredText(actorId, 128)) fail('INVALID_INPUT', 'Actor identity is required'); }

  private async snapshot(db: IDatabase, workflowId: string) {
    if (!identity(workflowId, 190)) fail('INVALID_INPUT', 'Invalid workflowId');
    if (!(await db.query('SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?', [workflowId])).length) fail('WORKFLOW_NOT_FOUND', 'Workflow does not exist');
    const current = await this.sources.load(db, workflowId);
    current.forEach(source => validateRepairItem(source.item));
    const ordered = [...current].sort((a, b) => a.item.itemId.localeCompare(b.item.itemId));
    const inputDigest = digestRepairJson(ordered);
    const repo = new RepairBatchRepository(db);
    const stored = await repo.listItems(workflowId);
    const byIdentity = new Map(stored.map(item => [identityKey(item, item.episodeKey), item]));
    const items = new Map<string, RepairInboxItem>(stored.map(item => [item.itemId, { ...item, sourceAvailable: false }]));
    const materializable = new Map<string, (typeof current)[number]>();
    for (const source of ordered) {
      const existing = byIdentity.get(identityKey(source.item, source.episodeKey));
      const itemId = existing?.itemId ?? source.item.itemId;
      const sourceAvailable = !source.initialState || source.initialState === 'pending';
      const preview: RepairInboxItem = existing ? { ...existing, ...source.item, itemId,
        // Stable state and content lineage are stored; only current evidence/sources are refreshed.
        contentRevision: existing.contentRevision, previousItemId: existing.previousItemId, sourceAvailable }
        : { ...source.item, itemId, workflowId, episodeKey: source.episodeKey, state: source.initialState ?? 'pending', stateVersion: 0,
          activeTaskId: null, activeRevision: null, disposition: null, updatedAtMs: 0, sourceAvailable };
      items.set(itemId, preview);
      if (sourceAvailable) materializable.set(itemId, { ...source, item: { ...source.item, itemId,
        contentRevision: preview.contentRevision, previousItemId: preview.previousItemId } });
    }
    return { items: [...items.values()], inputDigest, materializable };
  }
  async candidates(workflowId: string): Promise<Omit<RepairCandidatesResponse, 'canEdit'>> {
    return serial(this.db, async () => {
      const snapshot = await this.snapshot(this.db, workflowId);
      const revisions = await new RepairBatchRepository(this.db).listRevisions(workflowId);
      const heads = new Map<string, RepairRevision>();
      for (const revision of revisions) if (!heads.has(revision.taskId)) heads.set(revision.taskId, revision);
      const response: Omit<RepairCandidatesResponse, 'canEdit'> = { schemaVersion: 'workflow-repair/v2', workflowId, inputDigest: snapshot.inputDigest, items: snapshot.items,
        tasks: [...heads.values()].map(r => ({ taskId: r.taskId, revision: r.revision, phase: r.phase, updatedAtMs: r.updatedAtMs, itemCount: r.input.items.length })),
        capabilities: this.capabilities(), limits: { maxItems: MAX_ITEMS, maxRequestBytes: MAX_REQUEST_BYTES } };
      boundedRepairJson(response, MAX_INBOX_READ_BYTES);
      return response;
    });
  }
  private async readTask(db: IDatabase, taskId: string): Promise<RepairTaskDetail> {
    if (!identity(taskId, 64)) fail('INVALID_INPUT', 'Invalid taskId');
    const repo = new RepairBatchRepository(db);
    const workflowId = await repo.taskWorkflow(taskId);
    if (!workflowId) fail('TASK_NOT_FOUND', 'Repair task does not exist');
    const revisions = await repo.listRevisions(workflowId, taskId);
    const execution = await new RepairTaskRepository(db).execution(taskId, revisions[0].revision);
    const response: RepairTaskDetail = { workflowId, taskId, latestAttempt: revisions[0], latestSuccessful: revisions.find(r => r.draft !== null) ?? null,
      revisions, capabilities: this.capabilities(), ...(execution ? { execution } : {}) };
    boundedRepairJson(response, MAX_TASK_READ_BYTES);
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
    return this.generate(actorId, taskId, { ...base, expectedAttemptRevision: raw.expectedAttemptRevision, parentCandidateCommit: raw.parentCandidateCommit, feedback: raw.feedback });
  }
  private async generate(actorId: string, taskId: string | null, request: RepairSelectionRequest | RepairFeedbackRequest): Promise<RepairRevision> {
    if (!this.execution) fail('CAPABILITY_UNAVAILABLE', 'Repair generation provider is unavailable');
    const digest = digestRepairJson({ actorId, taskId, request });
    // A committed retry uses its original baseline even if current sources or deployment changed.
    const prior = await serial(this.db, () => new RepairBatchRepository(this.db).findRequest(request.workflowId, request.requestId, digest));
    let revision = prior;
    if (!revision) {
      const baseline = taskId ? (await this.task(taskId)).latestAttempt.input.baseline : await this.execution.resolveBaseline(request.workflowId);
      if (baseline.workflowId !== request.workflowId) fail('CONTENT_MISMATCH', 'Provider resolved a different workflow');
      revision = await this.tx(async tx => {
        const repo = new RepairBatchRepository(tx);
        await repo.lockWorkflow(tx, request.workflowId);
        const retry = await repo.findRequest(request.workflowId, request.requestId, digest);
        if (retry) return retry;
        const previous = taskId ? await this.readTask(tx, taskId) : null;
        if (previous && previous.workflowId !== request.workflowId) fail('ITEM_NOT_FOUND', 'Task is not in the requested workflow');
        await new RepairTaskRepository(tx).ensureNoLegacyApply(request.workflowId);
        const snapshot = await this.snapshot(tx, request.workflowId);
        if (snapshot.inputDigest !== request.inputDigest) fail('SOURCE_CHANGED', 'Repair sources changed; refresh the issue inbox');
        const selected: RepairItem[] = [];
        for (const itemId of request.itemIds) {
          const item = snapshot.items.find(item => item.itemId === itemId);
          if (!item || (!item.sourceAvailable && (!taskId || item.activeTaskId !== taskId))) fail('ITEM_NOT_FOUND', 'Selected source is unavailable in this workflow');
          if (item.state !== 'pending' && !(taskId && item.state === 'processing' && item.activeTaskId === taskId)) fail('ITEM_BUSY', 'Selected item is not pending or owned by this task');
          const source = snapshot.materializable.get(itemId);
          const stored = source ? await repo.materializeItem(tx, { workflowId: request.workflowId, ...source }) : await repo.getItem(request.workflowId, itemId);
          if (!stored) fail('ITEM_NOT_FOUND', 'Selected item is unavailable');
          const { workflowId: _wf, episodeKey: _ep, state: _state, stateVersion: _sv, activeTaskId: _at, activeRevision: _ar, disposition: _d, updatedAtMs: _u, ...frozen } = stored;
          selected.push(frozen);
        }
        const id = taskId ?? randomUUID();
        const feedback = 'feedback' in request ? request.feedback : '';
        const parentCandidateCommit = 'parentCandidateCommit' in request ? request.parentCandidateCommit : null;
        const input: RepairBatchInput = { schemaVersion: 'workflow-repair/v2', taskId: id,
          revision: (previous?.latestAttempt.revision ?? 0) + 1, baseline, items: selected,
          excludedSourceRefs: snapshot.items.filter(item => !request.itemIds.includes(item.itemId) && item.sourceAvailable).flatMap(item => item.sources),
          instructions: request.instructions, parentCandidateCommit, feedback,
          previousReportRef: previous?.latestSuccessful ? `${id}/revisions/${previous.latestSuccessful.revision}` : null,
          taskBranch: `repair/${request.workflowId}/${id}` };
        const created = await repo.createRevision(tx, { input, requestId: request.requestId, requestDigest: digest,
          expectedAttemptRevision: 'expectedAttemptRevision' in request ? request.expectedAttemptRevision : 0 });
        await new RepairTaskRepository(tx).append(created, actorId);
        return created;
      });
    }
    await this.dispatch(revision, actorId);
    return (await this.task(revision.taskId)).revisions.find(r => r.revision === revision!.revision)!;
  }
  private async dispatch(revision: RepairRevision, actorId?: string): Promise<void> {
    if (!this.execution || revision.phase !== 'drafting') return;
    const executionId = randomUUID();
    const claimed = await this.tx(async tx => {
      const current = await new RepairBatchRepository(tx).getRevision(revision.workflowId, revision.taskId, revision.revision);
      if (current?.phase !== 'drafting') return false;
      return new RepairTaskRepository(tx).claimDispatch(revision.taskId, revision.revision, executionId);
    });
    if (!claimed) return;
    let failed = false; let jobId: string | null = null;
    try {
      const identity = { stepId: claimed.stepId, attempt: claimed.attempt, executionId: claimed.executionId };
      const receipt = await this.execution.dispatch({ input: revision.input, actorId: actorId ?? claimed.actorId, identity });
      if (!receipt?.jobId?.trim() || receipt.jobId.length > 255) throw new Error('Invalid AIS job id');
      jobId = receipt.jobId;
    } catch { failed = true; }
    await this.tx(tx => new RepairTaskRepository(tx).finishDispatch(revision.taskId, revision.revision, jobId, failed));
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
    const executions = await serial(this.db, () => new RepairTaskRepository(this.db).active(limit));
    let settled = 0;
    for (const active of executions) {
      const task = await this.task(active.taskId);
      const revision = task.revisions.find(item => item.revision === active.revision);
      if (!revision || revision.phase !== 'drafting') continue;
      const request = { input: revision.input, actorId: active.actorId, identity: active.identity, jobId: active.jobId };
      let remote: Awaited<ReturnType<RepairExecutionPort['status']>>;
      try { remote = await this.execution.status(request); } catch { continue; }
      if (!await serial(this.db, () => new RepairTaskRepository(this.db).updateRemoteStatus(active.taskId, active.revision, active.identity, remote.rawStatus))) continue;
      if (remote.status === 'running') continue;
      const expectedStateVersion = (await this.task(active.taskId)).revisions.find(item => item.revision === active.revision)?.stateVersion;
      if (expectedStateVersion === undefined) continue;
      if (remote.status === 'success') {
        try {
          const result = await this.execution.readResult(request);
          await this.report({ workflowId: revision.workflowId, taskId: revision.taskId, revision: revision.revision,
            expectedStateVersion, ...result });
          settled += 1;
        } catch { /* Git publication may lag job success; retry on the next poll. */ }
      } else {
        await this.report({ workflowId: revision.workflowId, taskId: revision.taskId, revision: revision.revision,
          expectedStateVersion, status: 'failed', error: { code: remote.status === 'stopped' ? 'AIS_STOPPED' : 'AIS_FAILED',
            rawStatus: remote.rawStatus, message: remote.errorMessage } });
        settled += 1;
      }
    }
    return settled;
  }
  async disposition(actorId: string, input: Parameters<RepairWorkbenchService['disposition']>[1]): Promise<StoredRepairItem> {
    this.actor(actorId);
    boundedRepairJson(input, MAX_REQUEST_BYTES);
    if (!input || !identity(input.workflowId, 190) || !requiredText(input.itemId, 64) || !safeInteger(input.contentRevision, 1)
      || !safeInteger(input.expectedStateVersion) || !/^[a-f0-9]{64}$/.test(input.inputDigest)) fail('INVALID_INPUT', 'Invalid disposition input');
    const request = { ...input, actorId };
    return this.tx(async tx => {
      const repo = new RepairBatchRepository(tx);
      await repo.lockWorkflow(tx, input.workflowId);
      const retry = await repo.dispositionRetry(request);
      if (retry) return { ...(await repo.getItem(input.workflowId, input.itemId))!, ...retry, updatedAtMs: retry.disposition!.atMs };
      const snapshot = await this.snapshot(tx, input.workflowId);
      if (snapshot.inputDigest !== input.inputDigest) fail('SOURCE_CHANGED', 'Repair sources changed');
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
      if (task.latestAttempt.revision === expectedRevision && task.latestAttempt.phase === 'cancelled') return;
      await new RepairBatchRepository(tx).cancelTask(tx, { workflowId: task.workflowId, taskId, expectedAttemptRevision: expectedRevision, expectedStateVersion: task.latestAttempt.stateVersion });
      await new RepairTaskRepository(tx).cancel(taskId);
    });
    const execution = before.execution;
    if (this.execution?.stop && execution?.jobId && execution.executionId && execution.attempt > 0) {
      const request = { input: before.latestAttempt.input, actorId,
        identity: { stepId: new RepairTaskRepository(this.db).stepId(taskId, expectedRevision), attempt: execution.attempt, executionId: execution.executionId },
        jobId: execution.jobId };
      try { await this.execution.stop(request); } catch { /* Local cancellation is authoritative; remote stop is best effort. */ }
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
  async report(input: TrustedRepairReport): Promise<RepairRevision> {
    if (!input || !['succeeded', 'failed'].includes(input.status) || !identity(input.workflowId, 190) || !identity(input.taskId, 64)
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
        settled = await repo.completeDraft(tx, { ...input, phase: staticStatus === 'failed' || mockStatus === 'failed' ? 'blocked' : 'review' });
      }
      await new RepairTaskRepository(tx).settle(settled);
      return settled;
    });
  }
}

export function createRepairWorkbenchService(db: IDatabase, sourcePort: RepairSourcePort, executionPort?: RepairExecutionPort): RepairWorkbenchService & { report(input: TrustedRepairReport): Promise<RepairRevision> } {
  return new WorkflowRepairWorkbench(db, sourcePort, executionPort);
}
