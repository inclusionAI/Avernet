// @vitest-environment node
import Database from 'better-sqlite3';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { migrations } from '@avernet/clawweb-shared/server/schema';
import { repairBatchMigrations } from '@avernet/clawweb-shared/server/schema-repair-batch';
import { RepairBatchRepository } from '../repair-batch-repository.js';
import { digestRepairJson, validateRepairBatchInput, type RepairBatchInput, type RepairItem } from '../../contracts/repair-batch.js';

const source = (id = 'suggestion-1') => ({ kind: 'suggestion' as const, suggestionId: id, proposalDigest: 'a'.repeat(64), instructionDigest: 'b'.repeat(64) });
const item = (overrides: Partial<RepairItem> = {}): RepairItem => ({
  itemId: 'item-1', groupKey: 'fetch-timeout', proposalKey: 'c'.repeat(64), contentRevision: 1,
  previousItemId: null, proposal: { summary: 'Increase fetch timeout' }, instruction: '', sources: [source()], ...overrides,
});
const frozen = (items = [item()], revision = 1): RepairBatchInput => ({
  schemaVersion: 'workflow-repair/v2', taskId: 'task-1', revision,
  baseline: { workflowId: 'wf-1', packId: 'pack-1', releaseRevision: 0, activeDeployNumber: null,
    specDigest: 'd'.repeat(64), repoId: 'managed', specPath: 'workflows/demo.yaml', packCommit: 'e'.repeat(40), packDigest: 'f'.repeat(40) },
  items, excludedSourceRefs: [], instructions: '', parentCandidateCommit: revision > 1 ? '1'.repeat(40) : null,
  feedback: '', previousReportRef: null, taskBranch: 'repair/wf-1/task-1',
});

describe('repair item/revision storage using real SQLite', () => {
  let db: SqliteDatabase;
  let raw: Database.Database;
  let repo: RepairBatchRepository;
  beforeEach(async () => {
    raw = new Database(':memory:');
    db = new SqliteDatabase(raw);
    raw.exec(`CREATE TABLE workflow_specs (workflow_id TEXT PRIMARY KEY);
      INSERT INTO workflow_specs VALUES ('wf-1'), ('wf-2');
      CREATE TABLE workflow_healing_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT, outcome_id VARCHAR(64) UNIQUE NOT NULL,
        lesson_id VARCHAR(64), suggestion_id VARCHAR(64), workflow_id VARCHAR(255), node_id VARCHAR(64),
        action VARCHAR(64) NOT NULL, applied INTEGER NOT NULL DEFAULT 0, succeeded INTEGER NOT NULL DEFAULT 0,
        verdict VARCHAR(64) NOT NULL DEFAULT 'neutral', note TEXT, source_task_id VARCHAR(64), source_step_id VARCHAR(64),
        created_by VARCHAR(128), gmt_create INTEGER DEFAULT (unixepoch()), gmt_modified INTEGER DEFAULT (unixepoch())
      );
      INSERT INTO workflow_healing_outcomes (outcome_id, lesson_id, action) VALUES ('legacy', 'lesson-1', 'legacy');`);
    const additions = [
      ...migrations.filter(m => m.version === 72),
      ...repairBatchMigrations.filter(m => !m.mysqlOnly),
    ];
    expect(additions.length).toBeGreaterThan(0);
    for (const migration of additions) for (const sql of migration.sql) await db.exec(db.dialect.renderDdl(sql));
    repo = new RepairBatchRepository(db);
  });
  afterEach(async () => { await db?.close(); });
  const makeItem = async (repo: RepairBatchRepository, db: SqliteDatabase, value = item()) =>
    db.transaction(tx => repo.materializeItem(tx, { workflowId: 'wf-1', episodeKey: 'initial', item: value }));
  const create = async (repo: RepairBatchRepository, db: SqliteDatabase, input = frozen(), requestId = 'request-1', expectedAttemptRevision = 0) =>
    db.transaction(tx => repo.createRevision(tx, { input, requestId, requestDigest: digestRepairJson({ requestId }), expectedAttemptRevision }));

  it('preserves old outcomes and permits audited no_action before a task exists', async () => {
    await makeItem(repo, db);
    const request = { workflowId: 'wf-1', itemId: 'item-1', expectedStateVersion: 0, action: 'no_action' as const, actorId: 'human', requestId: 'ignore-1', reason: 'Not relevant' };
    const result = await db.transaction(tx => repo.setDisposition(tx, request));
    expect(result.state).toBe('no_action');
    const retry = await db.transaction(tx => repo.setDisposition(tx, request));
    expect(retry).toEqual(result);
    expect((await db.query('SELECT source_task_id, source_step_id, repair_item_id, action FROM workflow_healing_outcomes WHERE outcome_id <> ?', ['legacy']))).toEqual([
      { source_task_id: null, source_step_id: null, repair_item_id: 'item-1', action: 'repair_no_action' },
    ]);
    expect((await db.query('SELECT lesson_id FROM workflow_healing_outcomes WHERE outcome_id = ?', ['legacy']))[0]).toEqual({ lesson_id: 'lesson-1' });
    await expect(db.transaction(tx => repo.setDisposition(tx, { ...request, reason: 'changed' }))).rejects.toMatchObject({ code: 'IDEMPOTENCY_MISMATCH' });
    await db.transaction(tx => repo.setDisposition(tx, { ...request, expectedStateVersion: 1, action: 'restore', requestId: 'restore-1' }));
    expect((await repo.getItem('wf-1', 'item-1'))?.state).toBe('pending');
    expect(await db.query('SELECT action FROM workflow_healing_outcomes WHERE repair_item_id = ?', ['item-1'])).toEqual([{ action: 'repair_no_action' }, { action: 'repair_restore' }]);
  });

  it('merges evidence for the same content, but new content creates a pending linked item', async () => {
    await makeItem(repo, db);
    await db.transaction(tx => repo.setDisposition(tx, { workflowId: 'wf-1', itemId: 'item-1', expectedStateVersion: 0, action: 'no_action', actorId: 'human', requestId: 'ignore', reason: '' }));
    const merged = await makeItem(repo, db, item({ itemId: 'alternative-id', sources: [source(), source('suggestion-2')] }));
    expect(merged.itemId).toBe('item-1');
    expect(merged.state).toBe('no_action');
    expect(merged.sources).toHaveLength(2);
    const changed = await makeItem(repo, db, item({ itemId: 'item-2', proposalKey: '2'.repeat(64), proposal: { summary: 'Add backoff' }, contentRevision: 2, previousItemId: 'item-1' }));
    expect(changed.state).toBe('pending');
    expect(changed.previousItemId).toBe('item-1');
    expect((await repo.getItem('wf-1', 'item-1'))?.proposal).toEqual({ summary: 'Increase fetch timeout' });
    await expect(makeItem(repo, db, item({ proposal: { summary: 'silently replace' } }))).rejects.toMatchObject({ code: 'CONTENT_MISMATCH' });
  });

  it('freezes input, owns items atomically and deduplicates request retries before CAS', async () => {
    await makeItem(repo, db);
    const first = await create(repo, db);
    expect(first.phase).toBe('drafting');
    expect((await repo.getItem('wf-1', 'item-1'))?.activeTaskId).toBe('task-1');
    expect(await create(repo, db)).toEqual(first);
    await makeItem(repo, db, item({ sources: [source(), source('new-evidence')] }));
    expect((await repo.getRevision('wf-1', 'task-1', 1))?.input.items[0].sources).toHaveLength(1);
    await expect(db.transaction(tx => repo.createRevision(tx, { input: frozen(), requestId: 'request-1', requestDigest: '9'.repeat(64), expectedAttemptRevision: 0 }))).rejects.toMatchObject({ code: 'IDEMPOTENCY_MISMATCH' });
    await expect(db.transaction(tx => repo.setDisposition(tx, { workflowId: 'wf-1', itemId: 'item-1', expectedStateVersion: 1, action: 'no_action', actorId: 'human', requestId: 'ignore', reason: '' }))).rejects.toMatchObject({ code: 'ITEM_BUSY' });
    const other = { ...frozen(), taskId: 'task-2', taskBranch: 'repair/wf-1/task-2' };
    await expect(create(repo, db, other, 'other-request')).rejects.toMatchObject({ code: 'ACTIVE_TASK_CONFLICT' });
    expect(await repo.getRevision('wf-1', 'task-2', 1)).toBeNull();
  });

  it('rolls back all item claims and the revision when one item cannot be selected', async () => {
    await makeItem(repo, db);
    await expect(create(repo, db, frozen([item(), item({ itemId: 'missing', proposalKey: '8'.repeat(64) })]))).rejects.toMatchObject({ code: 'ITEM_NOT_FOUND' });
    expect((await repo.getItem('wf-1', 'item-1'))?.state).toBe('pending');
    expect(await repo.getRevision('wf-1', 'task-1', 1)).toBeNull();
  });

  it('keeps the last successful candidate after a failed new revision and rejects stale result CAS', async () => {
    await makeItem(repo, db);
    await create(repo, db);
    const result = { workflowId: 'wf-1', taskId: 'task-1', revision: 1, expectedStateVersion: 0,
      draft: { candidateCommit: '1'.repeat(40), packDigest: 'f'.repeat(40) }, checks: { candidateCommit: '1'.repeat(40), status: 'not_covered' }, phase: 'review' as const };
    await db.transaction(tx => repo.completeDraft(tx, result));
    await expect(db.transaction(tx => repo.failDraft(tx, { workflowId: 'wf-1', taskId: 'task-1', revision: 1, expectedStateVersion: 1, error: { code: 'LATE_FAILURE' } }))).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await create(repo, db, frozen([item()], 2), 'request-2', 1);
    await db.transaction(tx => repo.failDraft(tx, { workflowId: 'wf-1', taskId: 'task-1', revision: 2, expectedStateVersion: 0, error: { code: 'AIS_FAILED' } }));
    expect((await repo.latestSuccessfulRevision('wf-1', 'task-1'))?.revision).toBe(1);
    expect((await repo.getRevision('wf-1', 'task-1', 1))?.draft).toEqual(result.draft);
    expect((await repo.getItem('wf-1', 'item-1'))?.activeRevision).toBe(2);
    await expect(db.transaction(tx => repo.completeDraft(tx, { ...result, draft: { candidateCommit: '2'.repeat(40) } }))).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });

  it('accepts exact terminal result retries, but rejects conflicting results and stale old attempts', async () => {
    await makeItem(repo, db);
    await create(repo, db);
    const result = { workflowId: 'wf-1', taskId: 'task-1', revision: 1, expectedStateVersion: 0,
      draft: { candidateCommit: '1'.repeat(40) }, checks: { candidateCommit: '1'.repeat(40), status: 'not_covered' }, phase: 'review' as const };
    const completed = await db.transaction(tx => repo.completeDraft(tx, result));
    expect(await db.transaction(tx => repo.completeDraft(tx, result))).toEqual(completed);
    await expect(db.transaction(tx => repo.completeDraft(tx, { ...result, checks: { ...result.checks, status: 'passed' } }))).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await create(repo, db, frozen([item()], 2), 'request-2', 1);
    await expect(db.transaction(tx => repo.completeDraft(tx, result))).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const failure = { workflowId: 'wf-1', taskId: 'task-1', revision: 2, expectedStateVersion: 0, error: { code: 'AIS_FAILED' } };
    const failed = await db.transaction(tx => repo.failDraft(tx, failure));
    expect(await db.transaction(tx => repo.failDraft(tx, failure))).toEqual(failed);
    await expect(db.transaction(tx => repo.failDraft(tx, { ...failure, error: { code: 'DIFFERENT' } }))).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });

  it('rolls back a successful first claim when the second selected item is no_action', async () => {
    await makeItem(repo, db);
    const second = item({ itemId: 'item-2', proposalKey: '2'.repeat(64) });
    await makeItem(repo, db, second);
    await db.transaction(tx => repo.setDisposition(tx, { workflowId: 'wf-1', itemId: 'item-2', expectedStateVersion: 0, action: 'no_action', actorId: 'human', requestId: 'ignore', reason: '' }));
    await expect(create(repo, db, frozen([item(), second]))).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect((await repo.getItem('wf-1', 'item-1'))?.stateVersion).toBe(0);
    expect((await repo.getItem('wf-1', 'item-1'))?.activeTaskId).toBeNull();
    expect(await repo.getRevision('wf-1', 'task-1', 1)).toBeNull();
  });

  it('never reports a disposition as persisted if the audit write fails', async () => {
    await makeItem(repo, db);
    raw.exec(`CREATE TRIGGER fail_audit BEFORE INSERT ON workflow_healing_outcomes
      BEGIN SELECT RAISE(ABORT, 'audit storage unavailable'); END`);
    await expect(db.transaction(tx => repo.setDisposition(tx, { workflowId: 'wf-1', itemId: 'item-1', expectedStateVersion: 0, action: 'no_action', actorId: 'human', requestId: 'ignore', reason: '' }))).rejects.toThrow('audit storage unavailable');
    expect((await repo.getItem('wf-1', 'item-1'))?.state).toBe('pending');
  });

  it('keeps the task and revision in the same caller transaction', async () => {
    await makeItem(repo, db);
    await expect(db.transaction(async tx => {
      await repo.createRevision(tx, { input: frozen(), requestId: 'request-1', requestDigest: 'a'.repeat(64), expectedAttemptRevision: 0 });
      await tx.exec('INSERT INTO missing_task_table (task_id) VALUES (?)', ['task-1']);
    })).rejects.toThrow();
    expect(await repo.getRevision('wf-1', 'task-1', 1)).toBeNull();
    expect((await repo.getItem('wf-1', 'item-1'))?.state).toBe('pending');
  });

  it('requires a successful parent and releases deselected items only in the next revision', async () => {
    await makeItem(repo, db);
    const second = item({ itemId: 'item-2', proposalKey: '2'.repeat(64) });
    await makeItem(repo, db, second);
    await create(repo, db, frozen([item(), second]));
    await db.transaction(tx => repo.completeDraft(tx, { workflowId: 'wf-1', taskId: 'task-1', revision: 1, expectedStateVersion: 0,
      draft: { candidateCommit: '1'.repeat(40) }, checks: { candidateCommit: '1'.repeat(40) }, phase: 'review' }));
    await expect(create(repo, db, { ...frozen([item()], 2), parentCandidateCommit: '2'.repeat(40) }, 'wrong-parent', 1)).rejects.toMatchObject({ code: 'CONTENT_MISMATCH' });
    await create(repo, db, frozen([item()], 2), 'request-2', 1);
    expect((await repo.getItem('wf-1', 'item-2'))?.state).toBe('pending');
    expect((await repo.getItem('wf-1', 'item-2'))?.activeTaskId).toBeNull();
    expect((await repo.getRevision('wf-1', 'task-1', 1))?.input.items).toHaveLength(2);
  });

  it('deduplicates a deterministic same-episode key and keeps a new episode separate', async () => {
    await makeItem(repo, db);
    const next = item({ itemId: 'recurrence-1', previousItemId: 'item-1' });
    const recurrence = await db.transaction(tx => repo.materializeItem(tx, { workflowId: 'wf-1', episodeKey: 'confirmed:release-1', item: next }));
    const retry = await db.transaction(tx => repo.materializeItem(tx, { workflowId: 'wf-1', episodeKey: 'confirmed:release-1', item: { ...next, itemId: 'ignored-duplicate' } }));
    expect(retry.itemId).toBe(recurrence.itemId);
    expect((await db.query('SELECT item_id FROM workflow_repair_items'))).toHaveLength(2);
  });

  it('uses item state-version CAS and rejects a source from another workflow', async () => {
    await makeItem(repo, db);
    const outcomes = await Promise.allSettled([
      repo.claimItems(db, { workflowId: 'wf-1', taskId: 'task-1', revision: 1, items: [{ itemId: 'item-1', expectedStateVersion: 0 }] }),
      repo.claimItems(db, { workflowId: 'wf-1', taskId: 'task-2', revision: 1, items: [{ itemId: 'item-1', expectedStateVersion: 0 }] }),
    ]);
    expect(outcomes.filter(r => r.status === 'fulfilled')).toHaveLength(1);
    expect((await repo.getItem('wf-1', 'item-1'))?.stateVersion).toBe(1);
    await expect(db.transaction(tx => repo.materializeItem(tx, { workflowId: 'wf-2', episodeKey: 'initial', item: item({ itemId: 'item-2', previousItemId: 'item-1' }) }))).rejects.toMatchObject({ code: 'ITEM_NOT_FOUND' });
  });

  it('releases explicitly cancelled unpublished ownership and rejects stale cancellation', async () => {
    await makeItem(repo, db);
    await create(repo, db);
    await db.transaction(tx => repo.cancelTask(tx, { workflowId: 'wf-1', taskId: 'task-1', expectedAttemptRevision: 1, expectedStateVersion: 0 }));
    expect((await repo.getItem('wf-1', 'item-1'))?.state).toBe('pending');
    expect((await repo.getItem('wf-1', 'item-1'))?.activeTaskId).toBeNull();
    expect((await repo.getRevision('wf-1', 'task-1', 1))?.phase).toBe('cancelled');
    await expect(create(repo, db, frozen([item()], 2), 'after-cancel', 1)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
  });
});

describe('frozen repair input contract', () => {
  it.each(['../escape', 'path/child', 'name.lock', 'bad name', 'bad@{ref', '-leading'])('rejects unsafe Git identity %s', id => {
    expect(() => validateRepairBatchInput({ ...frozen(), taskId: id, taskBranch: `repair/wf-1/${id}` })).toThrow();
    expect(() => validateRepairBatchInput({ ...frozen(), baseline: { ...frozen().baseline, workflowId: id }, taskBranch: `repair/${id}/task-1` })).toThrow();
  });
  it.each([39, 100])('accepts %i independent items', count => {
    expect(validateRepairBatchInput(frozen(Array.from({ length: count }, (_, i) => item({ itemId: `item-${i}` }))))).toBeDefined();
  });
  it('rejects 101 items, invalid source digests, duplicate IDs and oversized UTF-8 evidence', () => {
    expect(() => validateRepairBatchInput(frozen(Array.from({ length: 101 }, (_, i) => item({ itemId: `item-${i}` }))))).toThrow();
    expect(() => validateRepairBatchInput(frozen([item(), item()]))).toThrow();
    expect(() => validateRepairBatchInput(frozen([item({ sources: [{ ...source(), instructionDigest: '' }] })]))).toThrow();
    expect(() => validateRepairBatchInput(frozen([item({ proposal: { evidence: '界'.repeat(350_000) } })]))).toThrow(expect.objectContaining({ code: 'PAYLOAD_TOO_LARGE' }));
  });
});
