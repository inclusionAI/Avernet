// @vitest-environment node
import { expect, it, vi } from 'vitest';
import { decideRepairEffect } from '../repair-auto-verification.js';
const rule = { version: 'node-timeout/v1' as const, nodeId: 'n', signature: 'timeout:n', windowDays: 7, minimumSamples: 5 };
const sample = { flowId: 'run', deployNumber: 8, completed: true, nodeStatus: 'succeeded', analysisComplete: true, signatures: [] as string[] };
it('requires an explicit rule, exact version, related completed node, complete analysis and minimum samples', () => {
  expect(decideRepairEffect(null, 8, [sample]).result).toBe('observing');
  for (const rows of [[], [sample], Array.from({ length: 5 }, () => sample),
    Array.from({ length: 5 }, (_, i) => ({ ...sample, flowId: String(i), deployNumber: 7 })),
    Array.from({ length: 5 }, (_, i) => ({ ...sample, flowId: String(i), analysisComplete: false })),
    Array.from({ length: 5 }, (_, i) => ({ ...sample, flowId: String(i), nodeStatus: 'skipped' }))]) {
    expect(decideRepairEffect(rule, 8, rows).result).toBe('observing');
  }
  expect(decideRepairEffect(rule, 8, Array.from({ length: 5 }, (_, i) => ({ ...sample, flowId: String(i) })))).toMatchObject({ result: 'verified', eligibleSamples: 5 });
});
it('reopens on explicit current-version recurrence but excludes late old-version logs', () => {
  expect(decideRepairEffect(rule, 8, [{ ...sample, signatures: ['timeout:n'], deployNumber: 7 }]).result).toBe('observing');
  expect(decideRepairEffect(rule, 8, [{ ...sample, nodeStatus: 'failed', signatures: ['timeout:n'] }])).toMatchObject({ result: 'ineffective', flowIds: ['run'] });
});

import { repairFixture } from './repair-batch-fixtures.js';
import { repairReleaseMigrations } from '@avernet/clawweb-shared/server/schema-repair-release';
import { createRepairWorkbenchService } from '../repair-batch-service.js';
import { createRepairAutoVerification } from '../repair-auto-verification.js';
import { digestRepairJson } from '../../contracts/repair-batch.js';
import { RepairReleaseRepository } from '../../repositories/repair-release-repository.js';
import type { RepairDeployment } from '../../contracts/repair-release.js';
it('automatically records effective/recurring phases in a real DB and creates a fresh optimization scope', async () => {
  const f = await repairFixture(1);
  try {
    for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
    f.raw.exec(`CREATE TABLE workflow_evolution_analysis_runs (id INTEGER PRIMARY KEY, analysis_id TEXT, request_key TEXT, scope_type TEXT,
      scope_json TEXT, workflow_id TEXT, flow_id TEXT, status TEXT, analysis_version TEXT, requested_by TEXT, requested_at_ms INTEGER,
      completed_at_ms INTEGER, result_json TEXT, state_version INTEGER);
      CREATE TABLE flow_runs (flow_id TEXT, workflow_id TEXT, workflow_deploy_number INTEGER, status TEXT, started_at INTEGER, completed_at INTEGER);
      CREATE TABLE node_executions (flow_id TEXT, node_id TEXT, status TEXT, started_at INTEGER);
      CREATE TABLE workflow_deploy_history (workflow_id TEXT, deploy_number INTEGER, is_active INTEGER);
      INSERT INTO workflow_deploy_history VALUES ('wf-1',8,1);`);
    f.items[0].context = { signature: 'timeout:n', failureMode: 'timeout', nodeId: 'n' };
    const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
    const inbox = await service.candidates('wf-1');
    const revision = await service.create('actor', { workflowId: 'wf-1', itemIds: ['item-0'], inputDigest: inbox.inputDigest, requestId: 'create', instructions: '' });
    const draft = { candidateCommit: 'd'.repeat(40), parentCandidateCommit: revision.input.baseline.packCommit, packDigest: 'e'.repeat(40), itemResults: [{ itemId: 'item-0', status: 'changed', reason: 'timeout' }] };
    await service.report({ workflowId: 'wf-1', taskId: revision.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft,
      checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft.candidateCommit, packDigest: draft.packDigest,
        candidateDigest: digestRepairJson(draft), static: { status: 'passed' }, mock: { status: 'passed' }, coverage: [{ itemId: 'item-0' }] } });
    await f.db.exec("UPDATE workflow_repair_items SET state = 'awaiting_verification'");
    const deployment = { id: 'd', workflowId: 'wf-1', taskId: revision.taskId, revision: 1, candidateCommit: draft.candidateCommit,
      stateVersion: 0, status: 'succeeded', receipt: { deployNumber: 8 }, createdAtMs: Date.now() } as RepairDeployment;
    const records = new RepairReleaseRepository(f.db);
    await records.insert('deployment', { requestId: 'd' }, deployment);
    const analyze = async (id: string, number: number, diagnoses: unknown[] = [], node = 'n', startedAt = Math.floor(Date.now() / 1000)) => {
      const now = startedAt;
      await f.db.exec('INSERT INTO flow_runs VALUES (?, ?, ?, ?, ?, ?)', [id, 'wf-1', number, 'succeeded', now, now]);
      await f.db.exec('INSERT INTO node_executions VALUES (?, ?, ?, ?)', [id, node, 'succeeded', now]);
      await f.db.exec(`INSERT INTO workflow_evolution_analysis_runs (analysis_id, request_key, scope_type, scope_json, workflow_id, flow_id,
        status, analysis_version, requested_by, requested_at_ms, completed_at_ms, result_json, state_version)
        VALUES (?, ?, 'run', ?, 'wf-1', ?, 'completed', 'workflow-evolution/v1', 'test', ?, ?, ?, 0)`,
      [id, id, JSON.stringify({ flowIds: [id] }), id, Date.now(), Date.now(), JSON.stringify({ schemaVersion: 'workflow-evolution-analysis/v1', analysisId: id,
        facts: [], inferences: [], unknowns: [], diagnoses })]);
    };
    const recurring = (id: string) => [{ diagnosisId: 'diag', flowIds: [id], nodeId: 'n', failureSignature: 'timeout:n', failureMode: 'timeout', severity: 'high', reasoning: 'timeout observed', evidenceEventIds: [] }];
    await analyze('old', 7, recurring('old')); await analyze('unrelated', 8, [], 'other');
    const auto = createRepairAutoVerification(f.db);
    for (let i = 0; i < 5; i++) {
      await analyze(`stale-seconds-${i}`, 8, [], 'n', Math.floor(Date.now() / 1000) - 30 * 86400);
      await analyze(`stale-millis-${i}`, 8, [], 'n', Date.now() - 30 * 86400000);
      await analyze(`future-millis-${i}`, 8, [], 'n', Date.now() + 86400000);
    }
    expect(await auto.decisions(deployment)).toMatchObject([{ result: 'observing', eligibleSamples: 0 }]);
    await auto.observe(deployment);
    expect(await records.list(revision.taskId, 'verification')).toHaveLength(0);
    for (let i = 0; i < 5; i++) await analyze(`new-${i}`, 8, [], 'n', i % 2 ? Date.now() : Math.floor(Date.now() / 1000));
    for (let i = 0; i < 2001; i++) await analyze(`traffic-${i}`, 8, [], 'other');
    await auto.reconcile(); await auto.reconcile();
    expect(await records.list(revision.taskId, 'verification')).toHaveLength(1);
    expect((await service.candidates('wf-1')).items.find(item => item.itemId === 'item-0')?.state).toBe('verified');
    await f.db.exec('UPDATE workflow_deploy_history SET deploy_number = 9');
    const stable = createRepairAutoVerification(f.db, { appliedState: async () => ({ status: 'unchanged', activeDeployNumber: 9, packCommit: 'f'.repeat(40) }) } as never);
    await stable.observe(deployment);
    expect((await service.candidates('wf-1')).items.some(item => item.previousItemId === 'item-0')).toBe(false);
    for (let i = 0; i < 5; i++) await analyze(`inherited-${i}`, 9);
    await stable.observe(deployment);
    expect(await records.list(revision.taskId, 'verification')).toHaveLength(2);
    const rolledBack = createRepairAutoVerification(f.db, { appliedState: async () => ({ status: 'changed', activeDeployNumber: 9, packCommit: 'f'.repeat(40), itemStates: { 'item-0': 'changed' } }) } as never);
    await rolledBack.observe(deployment); await rolledBack.observe(deployment);
    expect(await records.list(revision.taskId, 'verification_reassessment')).toHaveLength(1);
    expect((await service.candidates('wf-1')).items.find(item => item.previousItemId === 'item-0')).toMatchObject({ state: 'pending', proposal: null });
    await f.db.exec('UPDATE workflow_deploy_history SET deploy_number = 8');
    await analyze('recurring', 8, recurring('recurring'));
    for (let i = 0; i < 2001; i++) await analyze(`later-traffic-${i}`, 8, [], 'other');
    await auto.observe(deployment); await auto.observe(deployment);
    expect(await records.list(revision.taskId, 'verification')).toHaveLength(3);
    const next = (await service.candidates('wf-1')).items.find(item => item.previousItemId === 'item-0');
    expect(next).toMatchObject({ state: 'pending', sourceAvailable: true, proposal: null });
    expect(next?.instruction).toContain('不得原样重复');
    const fresh = await service.candidates('wf-1');
    const round2 = await service.create('actor', { workflowId: 'wf-1', itemIds: [next!.itemId], inputDigest: fresh.inputDigest, requestId: 'round-two', instructions: '' });
    const draft2 = { ...draft, candidateCommit: 'f'.repeat(40), itemResults: [{ itemId: next!.itemId, status: 'changed', reason: 'adjusted timeout strategy' }] };
    await service.report({ workflowId: 'wf-1', taskId: round2.taskId, revision: 1, expectedStateVersion: 0, status: 'succeeded', draft: draft2,
      checks: { schemaVersion: 'workflow-repair-checks/v1', candidateCommit: draft2.candidateCommit, packDigest: draft2.packDigest, candidateDigest: digestRepairJson(draft2),
        static: { status: 'passed' }, mock: { status: 'passed' }, coverage: [{ itemId: next!.itemId }] } });
    await f.db.exec("UPDATE workflow_repair_items SET state = 'awaiting_verification' WHERE item_id = ?", [next!.itemId]);
    await f.db.exec('UPDATE workflow_deploy_history SET deploy_number = 9');
    const d2 = { ...deployment, id: 'd2', taskId: round2.taskId, receipt: { deployNumber: 9 } } as RepairDeployment;
    await records.insert('deployment', { requestId: 'd2' }, d2);
    await analyze('recurring-twice', 9, recurring('recurring-twice'));
    await auto.observe(d2);
    expect((await service.candidates('wf-1')).items.find(item => item.previousItemId === next!.itemId)).toMatchObject({ state: 'pending', proposal: null });

  } finally { await f.db.close(); }
});


it('reconciles only deployments with a currently watched task revision, without duplicate observations', async () => {
  const f = await repairFixture(3);
  try {
    for (const m of repairReleaseMigrations) for (const sql of m.sql) await f.db.exec(f.db.dialect.renderDdl(sql));
    const service = createRepairWorkbenchService(f.db, f.sourcePort, f.executionPort);
    const inbox = await service.candidates('wf-1');
    await service.create('actor', { workflowId: 'wf-1', itemIds: f.items.map(item => item.itemId),
      inputDigest: inbox.inputDigest, requestId: 'watched-fixture', instructions: '' });
    await f.db.exec("UPDATE workflow_repair_items SET active_task_id = 'watched', active_revision = 2, state = 'awaiting_verification' WHERE item_id = 'item-0'");
    await f.db.exec("UPDATE workflow_repair_items SET active_task_id = 'watched', active_revision = 2, state = 'verified' WHERE item_id = 'item-1'");
    await f.db.exec("UPDATE workflow_repair_items SET active_task_id = 'closed', active_revision = 1, state = 'ineffective' WHERE item_id = 'item-2'");
    const records = new RepairReleaseRepository(f.db);
    for (let i = 0; i < 250; i++) await records.insert('deployment', { requestId: `history-${i}` }, {
      id: `history-${String(i).padStart(3, '0')}`, workflowId: 'wf-1', taskId: `history-${i}`, revision: 1,
      stateVersion: 0, createdAtMs: 1, status: 'succeeded', receipt: { deployNumber: i },
    } as RepairDeployment);
    for (const [id, taskId, revision] of [['closed', 'closed', 1], ['obsolete-revision', 'watched', 1], ['current', 'watched', 2]] as const) {
      await records.insert('deployment', { requestId: id }, { id, workflowId: 'wf-1', taskId, revision,
        stateVersion: 0, createdAtMs: 1, status: 'succeeded', receipt: { deployNumber: 300 } } as RepairDeployment);
    }
    const auto = createRepairAutoVerification(f.db);
    const observe = vi.spyOn(auto, 'observe').mockResolvedValue();
    const query = vi.spyOn(f.db, 'query');
    expect(await auto.reconcile()).toBe(1);
    expect(observe).toHaveBeenCalledTimes(1);
    expect(observe.mock.calls[0][0].id).toBe('current');
    const deployments = await Promise.all(query.mock.calls.flatMap(([sql], i) =>
      /SELECT .*record_id, .*body_json FROM workflow_repair_release_records/.test(sql)
        ? [query.mock.results[i].value] : []));
    expect(deployments.flat()).toHaveLength(1);
    // Verified items still need recurrence detection after the initial conclusion.
    await f.db.exec("UPDATE workflow_repair_items SET state = 'no_action' WHERE item_id = 'item-0'");
    observe.mockClear();
    expect(await auto.reconcile()).toBe(1);
    expect(observe).toHaveBeenCalledTimes(1);
    await f.db.exec("UPDATE workflow_repair_items SET state = 'ineffective' WHERE item_id = 'item-1'");
    observe.mockClear();
    expect(await auto.reconcile()).toBe(0);
    expect(observe).not.toHaveBeenCalled();
  } finally { await f.db.close(); }
});
