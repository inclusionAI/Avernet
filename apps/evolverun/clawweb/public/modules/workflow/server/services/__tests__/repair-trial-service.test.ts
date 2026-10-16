// @vitest-environment node
import { registerRunRequest } from '../run-request-registration.js';
import { expect, it } from 'vitest';
import { repairFixture } from './repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { createRepairTrialService } from '../repair-trial-service.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { hasSensitiveTrialInput, redactTrial } from '../repair-trial-input.js';

it('checks sensitive input beyond preview limits without treating bulk input as a secret', () => {
  const safe = { query: 'ordinary text '.repeat(700), items: Array.from({ length: 101 }, (_, i) => i) };
  expect(hasSensitiveTrialInput(safe)).toBe(false);
  expect(redactTrial(safe)).not.toEqual(safe);
  expect(hasSensitiveTrialInput({ items: [...safe.items, { credentials: { value: 'test-only' } }] })).toBe(true);
  expect(hasSensitiveTrialInput({ query: safe.query + ' Bearer test-only-value' })).toBe(true);
  expect(hasSensitiveTrialInput({ accessKey: 'test-only' })).toBe(true);
  expect(hasSensitiveTrialInput({ executionTicket: 'test-only' })).toBe(true);
});

it('freezes the reviewed Pack, validates input, deduplicates dispatch and binds once through ordinary run registration; preserves review', async () => {
  const f = await repairFixture();
  try {
    f.raw.exec("CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, workflow_id TEXT, origin_bot_id TEXT, status TEXT, result_json TEXT, total_duration_ms INTEGER)");
    for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
    const base = await f.executionPort.resolveBaseline('wf-1');
    const workbench = (await import('../repair-batch-service.js')).createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
    const inbox = await workbench.candidates('wf-1');
    const rev = await workbench.create('actor', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, requestId: 'gen', instructions: '' });
    const draft = { candidateCommit: 'd'.repeat(40), packDigest: 'e'.repeat(40), parentCandidateCommit: base.packCommit, itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'fixed' }] };
    await workbench.report({ workflowId: 'wf-1', taskId: rev.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft,
      checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'not_covered' }, coverage: [{ itemId: 'item-0', mode: 'not_covered' }], uncoveredChecks: [] } });
    let sends = 0, saves = 0;
    const service = createRepairTrialService({ db: f.db, workbench, port: {
      targets: async (_w, actor) => actor === 'actor' ? [{ id: 'bot', botId: 'bot', ownerId: 'owner', environment: 'prod', label: 'Bot' }] : [],
      inspect: async (_candidate, mode) => ({ ...(mode === 'saved' ? { source: 'saved' as const } : {}), packId: base.packId, workflowIds: ['wf-1', 'child'], candidateRef: `refs/heads/snapshots/${draft.candidateCommit}`, inputSchema: { type: 'object', required: ['query'], properties: { query: { type: 'string' }, password: { type: 'string' } } } }),
      save: async () => { saves++; }, dispatch: async () => { sends++; throw Error('response lost'); },
    } });
    const request = { revision: 1, expectedStateVersion: 1, requestId: 'trial', targetId: 'bot', input: { query: 'hello', password: 'very-secret' }, inputSchemaDigest: digestRepairJson((await service.options('actor', rev.taskId, 1)).inputSchema) };
    await expect(service.start('actor', rev.taskId, { ...request, input: {} })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    const trial = await service.start('actor', rev.taskId, request);
    expect(trial).toMatchObject({ status: 'unknown', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest, input: { password: '[REDACTED]' } });
    expect(JSON.stringify(trial)).not.toContain('very-secret');
    expect((await service.start('actor', rev.taskId, request)).id).toBe(trial.id);
    expect(sends).toBe(1); expect(saves).toBe(1);
    await expect(service.start('actor', rev.taskId, { ...request, input: { query: 'changed' } })).rejects.toMatchObject({ code: 'IDEMPOTENCY_MISMATCH' });
    const snapshot = await service.resolveRunRequest(trial.id, { botId: 'bot', ownerId: 'owner' });
    expect(snapshot).toMatchObject({ requestId: trial.id, purpose: 'trial', input: { password: 'very-secret' }, candidateCommit: draft.candidateCommit });
    expect(await service.resolveRunRequest(trial.id, { botId: 'bot', ownerId: 'owner' })).toEqual(snapshot);
    const registered = { flow_id: 'actual-native-flow', workflow_id: 'wf-1', workflow_version: -1, workflow_deploy_number: null,
      origin_bot_id: 'bot:owner', run_request_id: trial.id, run_request_bot_id: 'bot', run_request_owner_id: 'owner',
      candidate_commit: trial.candidateCommit, pack_digest: trial.packDigest, input_digest: trial.inputDigest };
    await expect(registerRunRequest(f.db, { flow_id: 'missing-exact-context', workflow_id: 'wf-1', origin_session_key: `agent:main:workflow-trial:${trial.id}` }, async () => {})).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const longIdInsert = async () => { throw Error('oversized id must not reach persistence'); };
    await expect(registerRunRequest(f.db, { ...registered, flow_id: 'a'.repeat(65) }, longIdInsert)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect((await service.list(rev.taskId))[0].flowId).toBeNull();
    const insert = async (tx: typeof f.db) => { await tx.exec("INSERT INTO flow_runs VALUES (?, ?, 'bot:owner', 'waiting', NULL, NULL)", [registered.flow_id, registered.workflow_id]); };
    await expect(registerRunRequest(f.db, registered, async () => { throw Error('insert failed'); })).rejects.toThrow('insert failed');
    expect(f.raw.prepare("SELECT COUNT(*) AS n FROM workflow_repair_release_records WHERE record_kind='trial_run'").get()).toEqual({ n: 0 });
    expect(await registerRunRequest(f.db, registered, insert)).toBe('created');
    expect(await registerRunRequest(f.db, registered, async () => { throw Error('must not insert twice'); })).toBe('existing');
    await expect(registerRunRequest(f.db, { ...registered, flow_id: 'another-root' }, insert)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await expect(registerRunRequest(f.db, { ...registered, run_request_id: undefined }, insert)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await expect(registerRunRequest(f.db, { ...registered, flow_id: 'foreign', workflow_id: 'foreign', run_request_parent_flow_id: 'actual-native-flow' }, insert)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const child = { ...registered, flow_id: 'child-flow', workflow_id: 'child', run_request_parent_flow_id: 'actual-native-flow' };
    await registerRunRequest(f.db, child, async tx => { await tx.exec("INSERT INTO flow_runs VALUES ('child-flow', 'child', 'bot:owner', 'running', NULL, NULL)"); });
    expect(f.raw.prepare("SELECT COUNT(*) AS n FROM workflow_repair_release_records WHERE record_kind='trial_run'").get()).toEqual({ n: 2 });
    expect(f.raw.prepare("SELECT workflow_id FROM workflow_repair_release_records WHERE record_id='child-flow'").get()).toEqual({ workflow_id: 'child' });
    expect((await service.list(rev.taskId))[0]).toMatchObject({ flowId: 'actual-native-flow', status: 'waiting' });
    const boundary = 'b'.repeat(64);
    await registerRunRequest(f.db, { ...child, flow_id: boundary }, async tx => { await tx.exec("INSERT INTO flow_runs VALUES (?, 'child', 'bot:owner', 'running', NULL, NULL)", [boundary]); });
    expect(f.raw.prepare("SELECT record_id FROM workflow_repair_release_records WHERE record_id=?").get(boundary)).toEqual({ record_id: boundary });
    f.raw.prepare("UPDATE flow_runs SET status='aborted' WHERE flow_id='actual-native-flow'").run();
    expect((await service.list(rev.taskId))[0].status).toBe('failed');
    expect((await workbench.task(rev.taskId)).latestSuccessful?.phase).toBe('review');
    await expect(service.resolveRunRequest(trial.id, { botId: 'other', ownerId: 'owner' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    f.raw.prepare("UPDATE flow_runs SET status='failed' WHERE flow_id='actual-native-flow'").run();
    await expect(registerRunRequest(f.db, { ...child, flow_id: 'late-terminal-child' }, insert)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const failed = f.raw.prepare("SELECT body_json FROM workflow_repair_release_records WHERE record_id=?").get(trial.id) as { body_json: string };
    f.raw.prepare("UPDATE workflow_repair_release_records SET body_json=? WHERE record_id=?").run(JSON.stringify({ ...JSON.parse(failed.body_json), status: 'failed' }), trial.id);
    await expect(registerRunRequest(f.db, registered, insert)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await expect(registerRunRequest(f.db, { ...child, flow_id: 'late-child' }, insert)).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    await expect(service.start('actor', rev.taskId, { ...request, mode: 'saved', requestId: 'sensitive-legacy' })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    await expect(service.start('actor', rev.taskId, { ...request, mode: 'saved', requestId: 'nested-sensitive-legacy', input: { query: 'ordinary', credentials: { value: 'test-only' } } })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    for (const key of ['accessKey', 'executionTicket']) await expect(service.start('actor', rev.taskId, { ...request, mode: 'saved', requestId: `sensitive-${key}`, input: { query: 'ordinary', [key]: 'test-only' } })).rejects.toMatchObject({ code: 'INVALID_INPUT' });
    const bulkInput = { query: 'ordinary text '.repeat(700), items: Array.from({ length: 101 }, (_, i) => i), fields: Object.fromEntries(Array.from({ length: 101 }, (_, i) => [`field${i}`, i])) };
    const legacy = await service.start('actor', rev.taskId, { ...request, mode: 'saved', requestId: 'legacy', input: bulkInput });
    expect(legacy.inputDigest).toBe(digestRepairJson(bulkInput));
    expect(legacy.mode).toBe('saved'); expect(saves).toBe(1);
    await expect(service.resolveRunRequest(legacy.id, { botId: 'bot', ownerId: 'owner' })).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    const ordinary = { flow_id: 'legacy-root', workflow_id: 'wf-1',
      origin_bot_id: 'bot', origin_session_key: `agent:main:workflow-trial:${legacy.id}` };
    const legacyInsert = (flowId: string, workflowId = 'wf-1') => async (tx: typeof f.db) => { await tx.exec("INSERT INTO flow_runs VALUES (?, ?, 'bot', 'succeeded', NULL, NULL)", [flowId, workflowId]); };
    expect(await registerRunRequest(f.db, ordinary, legacyInsert('legacy-root'))).toBe('created');
    expect(await registerRunRequest(f.db, { ...ordinary, flow_id: 'legacy-null', workflow_version: null }, legacyInsert('legacy-null'))).toBe('created');
    expect(await registerRunRequest(f.db, { ...ordinary, flow_id: 'legacy-draft', workflow_version: -1 }, legacyInsert('legacy-draft'))).toBe('created');
    await expect(registerRunRequest(f.db, { ...ordinary, workflow_version: 1 }, legacyInsert('must-not-insert'))).rejects.toMatchObject({ code: 'STATE_CONFLICT' });
    expect(await registerRunRequest(f.db, ordinary, async () => { throw Error('duplicate persistence'); })).toBe('existing');
    // Old runtimes cannot stop a duplicate execution: retain its actual record and purpose.
    expect(await registerRunRequest(f.db, { ...ordinary, flow_id: 'legacy-second' }, legacyInsert('legacy-second'))).toBe('created');
    expect(await registerRunRequest(f.db, { flow_id: 'legacy-child', workflow_id: 'child', state_json: JSON.stringify({ subworkflowMeta: { parentFlowId: 'legacy-root' } }) }, legacyInsert('legacy-child', 'child'))).toBe('created');
    expect((await service.list(rev.taskId)).find(row => row.id === legacy.id)).toMatchObject({ mode: 'saved', flowId: 'legacy-root', status: 'succeeded' });

  } finally { await f.db.close(); }
});
