// @vitest-environment node
import { afterEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { createWorkflowVersionSourceService } from '../workflow-version-source-service.js';

describe('trusted publication origin read model (SQLite)', () => {
  const connections: Database.Database[] = [];
  afterEach(() => connections.splice(0).forEach(db => db.close()));
  function setup(automatic = false) {
    const raw = new Database(':memory:'); connections.push(raw);
    raw.exec(`CREATE TABLE workflow_deploy_history (workflow_id TEXT, pack_id TEXT, deploy_number INTEGER, version INTEGER,
      action TEXT, tag_name TEXT, from_deploy_number INTEGER, owner_id TEXT, gmt_create INTEGER);
      CREATE TABLE workflow_repair_release_records (workflow_id TEXT, record_kind TEXT, body_json TEXT);
      CREATE TABLE workflow_repair_revisions (workflow_id TEXT, task_id TEXT, revision INTEGER, input_json TEXT, draft_json TEXT, phase TEXT);
      INSERT INTO workflow_deploy_history VALUES ('wf', 'pack', 8, 2, 'deploy', 'deploy/wf/#8', NULL, 'owner', 1792015200);`);
    const baseline = { workflowId: 'wf', packId: 'pack', activeDeployNumber: 7 };
    const origin = { kind: 'daily', intentId: 'round', policyVersion: 1, evidenceCutoffMs: 123 };
    const deployment = { id: 'deployment', workflowId: 'wf', taskId: 'task', revision: 3, requestId: 'publish',
      actorId: 'owner', approvalId: 'approval', candidateCommit: 'commit', packDigest: 'digest', baselineDeployNumber: 7,
      baseline, target: { id: 'target' }, status: 'succeeded', receipt: { requestId: 'publish', candidateCommit: 'commit',
        packDigest: 'digest', deployNumber: 8, releaseVersion: 2, tagName: 'deploy/wf/#8', targetId: 'target' } };
    const approval = { ...deployment, id: 'approval', status: 'approved' };
    const insert = (kind: string, data: object, workflowId = 'wf') => raw.prepare('INSERT INTO workflow_repair_release_records VALUES (?, ?, ?)').run(workflowId, kind, JSON.stringify(data));
    insert('deployment', deployment); insert('approval', approval);
    if (automatic) insert('auto_draft', { id: 'round', workflowId: 'wf', kind: 'auto_draft',
      data: { realTaskId: 'task', request: { automation: origin } } });
    raw.prepare('INSERT INTO workflow_repair_revisions VALUES (?, ?, ?, ?, ?, ?)').run('wf', 'task', 3,
      JSON.stringify({ taskId: 'task', baseline, ...(automatic ? { automation: origin } : {}) }),
      JSON.stringify({ candidateCommit: 'commit', packDigest: 'digest' }), 'published');
    return { raw, deployment, approval, insert, service: createWorkflowVersionSourceService(new SqliteDatabase(raw)) };
  }
  it('joins exact receipt, approval, task candidate and history; manual source has the real baseline', async () => {
    const f = setup();
    expect(await f.service.getSource('wf', 8)).toMatchObject({ kind: 'manual_optimization', baselineDeployNumber: 7,
      deployNumber: 8, revision: 3, actorId: 'owner', taskId: 'task', automationRoundId: null, effectiveAtMs: 1792015200000 });
    expect(await f.service.getSource('other', 8)).toMatchObject({ kind: 'unknown', baselineDeployNumber: null });
  });
  it('keeps database UTC timestamp and epoch milliseconds exact', async () => {
    const f = setup();
    f.raw.prepare('UPDATE workflow_deploy_history SET gmt_create = ?').run('2026-10-15 00:00:00');
    expect((await f.service.getSource('wf', 8)).effectiveAtMs).toBe(Date.parse('2026-10-15T00:00:00Z'));
    f.raw.prepare('UPDATE workflow_deploy_history SET gmt_create = ?').run(1792015200000);
    expect((await f.service.getSource('wf', 8)).effectiveAtMs).toBe(1792015200000);
  });
  it('automatic origin requires the matching persisted automation round, not only task input', async () => {
    const f = setup(true);
    expect((await f.service.forWorkflow('wf')).get(8)).toMatchObject({ kind: 'auto_optimization', automationRoundId: 'round' });
    f.raw.exec("DELETE FROM workflow_repair_release_records WHERE record_kind = 'auto_draft'");
    expect(await f.service.getSource('wf', 8)).toMatchObject({ kind: 'unknown', automationRoundId: null });
  });
  it.each(['failed', 'uncertain', 'running'])('never labels %s publication as effective', async status => {
    const f = setup(true);
    f.raw.exec("DELETE FROM workflow_repair_release_records WHERE record_kind = 'deployment'");
    f.insert('deployment', { ...f.deployment, status });
    expect((await f.service.getSource('wf', 8)).kind).toBe('unknown');
  });
  it.each(['deployNumber', 'releaseVersion', 'tagName', 'candidateCommit', 'packDigest', 'targetId', 'requestId'])('rejects mismatched receipt %s', async field => {
    const f = setup(true);
    f.raw.exec("DELETE FROM workflow_repair_release_records WHERE record_kind = 'deployment'");
    f.insert('deployment', { ...f.deployment, receipt: { ...f.deployment.receipt, [field]: 'wrong' } });
    expect((await f.service.getSource('wf', 8)).kind).toBe('unknown');
  });
  it('does not infer source from spec version, activity or tag alone; rollback retains separate restored origin', async () => {
    const f = setup(true);
    f.raw.exec(`INSERT INTO workflow_deploy_history VALUES ('wf', 'pack', 9, 2, 'deploy', 'deploy/wf/#9', NULL, 'owner', 1792015300);
      INSERT INTO workflow_deploy_history VALUES ('wf', 'pack', 10, 3, 'rollback', 'deploy/wf/#10', 8, 'rollback-owner', 1792015400);`);
    expect((await f.service.getSource('wf', 9)).kind).toBe('unknown');
    expect(await f.service.getSource('wf', 10)).toMatchObject({ kind: 'rollback', actorId: 'rollback-owner', baselineDeployNumber: null,
      restoredFromDeployNumber: 8, restoredSource: { kind: 'auto_optimization', deployNumber: 8, automationRoundId: 'round' } });
  });
  it('ambiguous receipts, missing review, unpublished task or malformed persisted record remain unknown', async () => {
    const f = setup();
    f.insert('deployment', { ...f.deployment, id: 'duplicate' });
    expect((await f.service.getSource('wf', 8)).kind).toBe('unknown');
    f.raw.exec("DELETE FROM workflow_repair_release_records WHERE record_kind = 'deployment'");
    f.insert('deployment', f.deployment);
    f.raw.exec("UPDATE workflow_repair_revisions SET phase = 'review'");
    expect((await f.service.getSource('wf', 8)).kind).toBe('unknown');
    f.raw.exec("UPDATE workflow_repair_revisions SET phase = 'published'; DELETE FROM workflow_repair_release_records WHERE record_kind = 'approval'");
    expect((await f.service.getSource('wf', 8)).kind).toBe('unknown');
    f.raw.prepare('INSERT INTO workflow_repair_release_records VALUES (?, ?, ?)').run('wf', 'approval', '{');
    expect((await f.service.getSource('wf', 8)).kind).toBe('unknown');
  });
});
