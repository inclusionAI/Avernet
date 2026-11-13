// @vitest-environment node
import Database from 'better-sqlite3';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { FacadeBindingRepository } from '../../repositories/facade-binding-repository.js';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { PackCandidateOutcomeUnknownError } from '../pack-candidate-save.js';
import type { PackScriptSnapshot } from '../pack-script-snapshot.js';
import type { PreparedPackCandidate } from '../pack-candidate-save.js';
import { WorkflowCloneError, WorkflowCloneService, type WithPreparedClonedPack } from '../workflow-clone-service.js';

const commit = 'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa';
const sourceYaml = `id: source
version: '1'
title: Source workflow
packId: source-pack
facade:
  command: old-command
nodes:
  - id: start
    title: Start
    executor:
      type: script
      command: python scripts/main.py
`;
const manifest = `id: source-pack
version: '1'
workflows:
  - id: source
    file: workflows/source.yaml
`;
const base64 = (text: string) => Buffer.from(text, 'utf8').toString('base64');

function sourceSnapshot(): PackScriptSnapshot {
  return {
    workflowId: 'source',
    packId: 'source-pack',
    baseCommit: commit,
    baseRef: `refs/remotes/origin/wf/source`,
    files: [],
    versionFiles: [
      { path: 'workflow.pack.yaml', contentBase64: base64(manifest), mode: '100644' },
      { path: 'workflows/source.yaml', contentBase64: base64(sourceYaml), mode: '100644' },
      { path: 'scripts/main.py', contentBase64: base64('print("cloned")\n'), mode: '100755' },
    ],
  };
}

describe('WorkflowCloneService', () => {
  let raw: Database.Database;
  let db: SqliteDatabase;
  let captured: Parameters<WithPreparedClonedPack>[0] | undefined;
  const prepared = { workflowId: 'copy', branch: 'wf/copy', commit } as PreparedPackCandidate;
  const prepare: WithPreparedClonedPack = async (input, callback) => {
    captured = input;
    return callback(prepared, async () => ({ branch: 'wf/copy', commit }));
  };

  beforeEach(() => {
    raw = new Database(':memory:');
    raw.exec(`
      CREATE TABLE workflow_specs (
        id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT,
        title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER
      );
      CREATE TABLE bot_workflow_permissions (
        id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, env TEXT,
        can_view INTEGER, can_execute INTEGER, can_edit INTEGER, gmt_create INTEGER, gmt_modified INTEGER
      );
      CREATE TABLE facade_bindings (
        id INTEGER PRIMARY KEY, command TEXT UNIQUE, workflow_id TEXT, pack_id TEXT,
        remark TEXT, gmt_create INTEGER, gmt_modified INTEGER
      );
      CREATE TABLE cm_app_config (
        id INTEGER PRIMARY KEY, config_key TEXT, config_yaml TEXT, version INTEGER,
        enabled INTEGER, description TEXT, updated_by TEXT, gmt_create INTEGER, gmt_modified INTEGER
      );
      INSERT INTO workflow_specs
        (workflow_id, pack_id, spec_json, title, owner_id, gmt_create, gmt_modified)
      VALUES ('source', 'source-pack', '{}', 'Source workflow', 'source-owner', 1, 1);
    `);
    db = new SqliteDatabase(raw);
    captured = undefined;
  });

  function service() {
    return new WorkflowCloneService({
      db,
      workflowSpecRepo: new WorkflowSpecRepository(db),
      facadeRepo: new FacadeBindingRepository(db),
      gitConfig: { remoteUrl: 'file:///fixture/source.git', username: 'Fixture', email: 'fixture@example.invalid', token: '' },
      gitAuthor: { name: 'Fixture', email: 'fixture@example.invalid' },
      readSnapshot: vi.fn(async () => sourceSnapshot()),
      prepareClonedPack: prepare,
    });
  }

  it('lists every saved workflow even when no actor has permission rows', async () => {
    await expect(service().candidates()).resolves.toEqual([{
      workflowId: 'source',
      title: 'Source workflow',
      packId: 'source-pack',
      updatedAt: 1,
    }]);
  });

  it('previews the frozen source snapshot and counts only files under scripts', async () => {
    await expect(service().preview('source')).resolves.toEqual({
      sourceWorkflowId: 'source',
      sourcePackId: 'source-pack',
      sourceTitle: 'Source workflow',
      baseCommit: commit,
      scriptCount: 1,
    });
  });

  it('rejects unclonable scripts during preview with the same rules as clone', async () => {
    const invalid = sourceSnapshot();
    invalid.versionFiles.push({ path: 'scripts/tool.exe', contentBase64: base64('binary'), mode: '100644' });
    const target = new WorkflowCloneService({
      db, workflowSpecRepo: new WorkflowSpecRepository(db), facadeRepo: new FacadeBindingRepository(db),
      gitConfig: { remoteUrl: 'file:///fixture/source.git', username: 'Fixture', email: 'fixture@example.invalid', token: '' },
      gitAuthor: { name: 'Fixture', email: 'fixture@example.invalid' },
      readSnapshot: vi.fn(async () => invalid), prepareClonedPack: prepare,
    });

    await expect(target.preview('source')).rejects.toMatchObject({
      status: 409,
      code: 'SOURCE_SCRIPTS_INVALID',
    });
  });

  it('creates a target identity and copies uploaded YAML and Git scripts', async () => {
    const result = await service().clone({
      sourceWorkflowId: 'source',
      targetWorkflowId: 'copy',
      targetTitle: 'My copy',
      facade: {},
    }, 'owner-1');

    expect(result).toMatchObject({
      sourceWorkflowId: 'source', sourcePackId: 'source-pack', baseCommit: commit,
      targetWorkflowId: 'copy', targetPackId: 'copy', branch: 'wf/copy', commit,
    });
    expect(captured?.targetWorkflowYaml).toContain('id: copy');
    expect(captured?.targetWorkflowYaml).toContain('title: My copy');
    expect(captured?.targetWorkflowYaml).not.toContain('source-pack');
    expect(captured?.targetWorkflowYaml).not.toContain('old-command');
    expect(captured?.scripts).toEqual([{ path: 'scripts/main.py', content: 'print("cloned")\n', mode: '100755' }]);

    const target = await new WorkflowSpecRepository(db).findByWorkflowId('copy');
    expect(target?.pack_id).toBe('copy');
    expect(JSON.parse(target!.spec_json)).toMatchObject({ id: 'copy', title: 'My copy' });
    expect(JSON.parse(target!.spec_json)).not.toHaveProperty('packId');
    expect(JSON.parse(target!.spec_json)).not.toHaveProperty('facade');
    expect(await new BotWorkflowPermissionRepository(db).hasEditPermission('copy', 'owner-1')).toBe(true);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM facade_bindings').get()).toEqual({ n: 0 });
  });

  it('rejects an oversized facade remark before creating a target', async () => {
    await expect(service().clone({
      sourceWorkflowId: 'source',
      targetWorkflowId: 'copy',
      targetTitle: 'My copy',
      facade: { command: 'copy-command', remark: 'x'.repeat(257) },
    }, 'owner-1')).rejects.toMatchObject({
      status: 400,
      code: 'INVALID_FACADE_REMARK',
    });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_specs WHERE workflow_id = ?').get('copy')).toEqual({ n: 0 });
  });

  it('persistently binds only the explicitly supplied new command', async () => {
    await service().clone({
      sourceWorkflowId: 'source', targetWorkflowId: 'copy', targetTitle: 'My copy',
      facade: { command: 'copy-command', remark: 'my copy' },
    }, 'owner-1');

    const facade = await new FacadeBindingRepository(db).findByCommand('copy-command');
    expect(facade).toMatchObject({ workflow_id: 'copy', pack_id: 'copy', remark: 'my copy' });
  });

  it('rejects an existing target identity before building the clone', async () => {
    raw.exec("INSERT INTO workflow_specs (workflow_id, pack_id, spec_json, title, gmt_create, gmt_modified) VALUES ('copy', 'copy', '{}', 'Existing', 1, 1)");
    const failingPrepare = vi.fn<WithPreparedClonedPack>();
    const target = new WorkflowCloneService({
      db, workflowSpecRepo: new WorkflowSpecRepository(db), facadeRepo: new FacadeBindingRepository(db),
      gitConfig: { remoteUrl: 'file:///fixture/source.git', username: 'Fixture', email: 'fixture@example.invalid', token: '' },
      gitAuthor: { name: 'Fixture', email: 'fixture@example.invalid' },
      readSnapshot: vi.fn(async () => sourceSnapshot()), prepareClonedPack: failingPrepare,
    });

    await expect(target.clone({
      sourceWorkflowId: 'source', targetWorkflowId: 'copy', targetTitle: 'My copy', facade: {},
    }, 'owner-1')).rejects.toMatchObject({ code: 'TARGET_WORKFLOW_ALREADY_EXISTS' });
    expect(failingPrepare).not.toHaveBeenCalled();
  });

  it('does not adopt the source command when creating without a command', async () => {
    raw.exec("INSERT INTO facade_bindings (command, workflow_id, pack_id, remark, gmt_create, gmt_modified) VALUES ('old-command', 'source', 'source-pack', NULL, 1, 1)");

    await service().clone({
      sourceWorkflowId: 'source', targetWorkflowId: 'copy', targetTitle: 'My copy', facade: {},
    }, 'owner-1');

    expect(await new FacadeBindingRepository(db).findByCommand('old-command')).toMatchObject({ workflow_id: 'source' });
    expect(await new FacadeBindingRepository(db).findByWorkflowId('copy')).toEqual([]);
  });

  it('reports an unknown Git push outcome instead of pretending retry is safe', async () => {
    const unknownPrepare: WithPreparedClonedPack = async (_input, callback) => {
      try {
        return await callback(prepared, async () => { throw new PackCandidateOutcomeUnknownError('wf/copy', commit); });
      } catch (error) {
        throw error;
      }
    };
    const service = new WorkflowCloneService({
      db, workflowSpecRepo: new WorkflowSpecRepository(db), facadeRepo: new FacadeBindingRepository(db),
      gitConfig: { remoteUrl: 'file:///fixture/source.git', username: 'Fixture', email: 'fixture@example.invalid', token: '' },
      gitAuthor: { name: 'Fixture', email: 'fixture@example.invalid' },
      readSnapshot: vi.fn(async () => sourceSnapshot()), prepareClonedPack: unknownPrepare,
    });

    await expect(service.clone({
      sourceWorkflowId: 'source', targetWorkflowId: 'copy', targetTitle: 'My copy', facade: {},
    }, 'owner-1')).rejects.toMatchObject({ code: 'CLONE_OUTCOME_UNKNOWN', status: 503 });
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_specs WHERE workflow_id = ?').get('copy')).toEqual({ n: 1 });
  });

  it('fails closed when the target command is already bound', async () => {
    raw.exec("INSERT INTO facade_bindings (command, workflow_id, pack_id, remark, gmt_create, gmt_modified) VALUES ('copy-command', 'source', 'source-pack', NULL, 1, 1)");

    await expect(service().clone({
      sourceWorkflowId: 'source', targetWorkflowId: 'copy', targetTitle: 'My copy',
      facade: { command: 'copy-command' },
    }, 'owner-1')).rejects.toBeInstanceOf(WorkflowCloneError);
    expect(raw.prepare('SELECT COUNT(*) AS n FROM workflow_specs WHERE workflow_id = ?').get('copy')).toEqual({ n: 0 });
  });
});
