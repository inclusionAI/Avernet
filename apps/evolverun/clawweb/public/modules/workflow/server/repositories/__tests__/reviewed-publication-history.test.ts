// @vitest-environment node
import { expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db';
import { WorkflowDeployHistoryRepository } from '../workflow-deploy-history-repository.js';

it('activates a reviewed release atomically only from its frozen deployment baseline', async () => {
  const raw = new Database(':memory:');
  const db = new SqliteDatabase(raw);
  raw.exec(`CREATE TABLE workflow_specs (workflow_id TEXT PRIMARY KEY, pack_id TEXT, version INTEGER, spec_json TEXT, gmt_modified INTEGER);
    INSERT INTO workflow_specs VALUES ('wf', 'pack', 7, '{"id":"wf"}', 0);
    CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER,
      tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER,
      gmt_create INTEGER, gmt_modified INTEGER, UNIQUE (workflow_id, deploy_number));`);
  const repo = new WorkflowDeployHistoryRepository(db);
  await db.exec(`CREATE TABLE facade_bindings (id INTEGER PRIMARY KEY, command TEXT UNIQUE, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    INSERT INTO facade_bindings VALUES (1, 'old-command', 'wf', 'pack', NULL, 0, 0), (2, 'other-command', 'other', 'other-pack', NULL, 0, 0)`);
  try {
    await repo.insert({ packId: 'pack', workflowId: 'wf', version: 7, deployNumber: 7, action: 'deploy', specJson: '{"id":"wf"}', isActive: true });
    const release = { packId: 'pack', workflowId: 'wf', version: 8, deployNumber: 8, tagName: 'deploy/wf/#8', action: 'deploy' as const,
      specJson: '{"id":"wf","title":"new"}', note: 'workflow-repair-deployment:deployment-1', botId: 'bot', ownerId: 'owner', isActive: true, facadeCommands: ['new-command'] };
    await repo.publishReviewed(release, 7);
    expect((await repo.findActiveByWorkflowId('wf'))?.deploy_number).toBe(8);
    expect((await db.query<{ spec_json: string }>('SELECT spec_json FROM workflow_specs'))[0].spec_json).toBe(release.specJson);
    expect((await db.query<{ command: string }>("SELECT command FROM facade_bindings WHERE workflow_id = 'wf'"))[0].command).toBe('new-command');
    await repo.publishReviewed(release, 7); // Lost acknowledgement: same release is idempotent.
    await expect(repo.publishReviewed({ ...release, deployNumber: 9, version: 9, tagName: 'deploy/wf/#9' }, 7)).rejects.toThrow('BASELINE_STALE');
    expect((await repo.listHistory('wf', 10)).length).toBe(2);
    expect((await repo.findActiveByWorkflowId('wf'))?.deploy_number).toBe(8);
    expect((await db.query<{ command: string }>("SELECT command FROM facade_bindings WHERE workflow_id = 'other'"))[0].command).toBe('other-command');
  } finally { await db.close(); }
});
