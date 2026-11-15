// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import Database from 'better-sqlite3';
import { chmodSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { EditorDraftCasService } from '../editor-draft-cas-service.js';
import { withPreparedPackCandidateForUnifiedSave } from '../pack-candidate-save.js';
import { projectWorkflowSnapshotYaml } from '../workflow-snapshot-projection.js';
import { WorkflowUnifiedSaveService } from '../workflow-unified-save-service.js';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';

const spec = { id: 'flow', title: 'before', nodes: [{ id: 'n', executor: { type: 'agent' } }] };
const repoId = 'a'.repeat(64);
let root: string, bare: string, seed: string, current: string;
let raw: Database.Database, db: SqliteDatabase, cas: EditorDraftCasService, perms: BotWorkflowPermissionRepository;
// Saved now means the wf snapshot, including the pre-existing baseline on rejected writes.
const saved = () => runGit(root, ['--git-dir', bare, 'rev-parse', '--verify', 'refs/heads/wf/flow']);
const row = () => raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'flow'").get() as { spec_json: string };
const service = (overrides: Partial<ConstructorParameters<typeof WorkflowUnifiedSaveService>[0]> = {}) => new WorkflowUnifiedSaveService({
  draft: cas, canEdit: (workflowId, actorId) => perms.hasEditPermission(workflowId, actorId),
  repositoryId: repoId, config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' }, ...overrides,
});
async function request() {
  return { requestId: 'request_123', actorId: 'editor', workflowId: 'flow', packId: 'pack', repositoryId: repoId,
    sourceVersion: 'current' as const, sourceCommit: current, expectedSavedHead: current, expectedDraftToken: (await cas.read('flow', 'editor')).draftToken,
    specJson: JSON.stringify({ ...spec, title: 'after', value: 3 }), changes: [] };
}
beforeEach(() => {
  root = mkdtempSync(path.join(tmpdir(), 'unified-save-')); bare = createLocalRemote(root); seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true });
  writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow, other]\n');
  writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\nnodes: []\n');
  writeFileSync(path.join(seed, 'packs/pack/workflows/other.yaml'), 'id: other\nnodes: []\n');
  mkdirSync(path.join(seed, 'outside'), { recursive: true });
  writeFileSync(path.join(seed, 'outside/unrelated.txt'), 'outside selected pack\n');
  mkdirSync(path.join(seed, 'packs/pack/scripts'));
  writeFileSync(path.join(seed, 'packs/pack/scripts/run.sh'), '#!/bin/sh\necho old\n');
  chmodSync(path.join(seed, 'packs/pack/scripts/run.sh'), 0o755);
  writeFileSync(path.join(seed, 'packs/pack/scripts/resource.txt'), 'unchanged resource\n');
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'seed']); current = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
  raw = new Database(':memory:');
  raw.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, env TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    INSERT INTO bot_workflow_permissions VALUES (1,NULL,'editor','flow',1,0,1,'test',0,0), (2,NULL,'viewer','flow',1,0,0,'test',0,0);`);
  raw.prepare('INSERT INTO workflow_specs VALUES (1,?,?,?,?,?,?,?,?)').run('flow', 'pack', JSON.stringify(spec), 'before', 1, 'editor', 1, 2);
  raw.prepare('INSERT INTO workflow_specs VALUES (2,?,?,?,?,?,?,?,?)').run('other', 'pack', JSON.stringify({ id: 'other', nodes: [] }), 'other', 1, 'owner', 1, 2);
  db = new SqliteDatabase(raw); perms = new BotWorkflowPermissionRepository(db); cas = new EditorDraftCasService(db, perms);
});
afterEach(async () => { if (db) await db.close(); rmSync(root, { recursive: true, force: true }); });

describe('unified save without a durable coordinator', () => {
  it('confirms both stages and persists one projected document in SQLite and bare Git', async () => {
    const req = await request(); const result = await service().save(req, 'editor');
    expect(result.outcome).toBe('confirmed'); expect(result.evidence.db).toBe('confirmed'); expect(result.evidence.git).toBe('confirmed');
    expect(saved()).toBe(result.evidence.commit);
    const yaml = runGit(root, ['--git-dir', bare, 'show', `${saved()}:packs/pack/workflows/flow.yaml`]);
    expect(JSON.parse(row().spec_json)).toEqual(JSON.parse(req.specJson));
    expect(projectWorkflowSnapshotYaml('flow', yaml).specJson).toBe(row().spec_json);
  });
  it('writes DB before Git even with no script changes, preserving the rest of the tree', async () => {
    const req = await request(); const events: string[] = [];
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        events.push('git');
        expect(JSON.parse(row().spec_json)).toEqual(JSON.parse(req.specJson));
        return push();
      }));
    const result = await service({ prepare, draft: { read: cas.read.bind(cas), save: async input => {
      const written = await cas.save(input); events.push('db'); return written;
    } } }).save(req, 'editor');
    expect(result.outcome).toBe('confirmed'); expect(events).toEqual(['db', 'git']);
    const show = (file: string) => runGit(root, ['--git-dir', bare, 'show', `${saved()}:packs/pack/${file}`]);
    expect(projectWorkflowSnapshotYaml('flow', show('workflows/flow.yaml')).specJson).toBe(row().spec_json);
    expect(show('scripts/run.sh')).toContain('echo old');
    expect(show('scripts/resource.txt')).toBe('unchanged resource');
    expect(show('workflows/other.yaml')).toContain('id: other');
    expect(runGit(root, ['--git-dir', bare, 'diff', '--name-only', current, saved()!])).toBe('packs/pack/workflows/flow.yaml');
  });
  it('preserves a legacy NULL Pack binding while saving the workflow Pack', async () => {
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL WHERE workflow_id = ?').run('flow');
    runGit(seed, ['mv', 'packs/pack', 'packs/flow']);
    writeFileSync(path.join(seed, 'packs/flow/workflow.pack.yaml'), 'id: flow\nworkflows: [flow, other]\n');
    runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'legacy pack identity']);
    current = runGit(seed, ['rev-parse', 'HEAD']);
    runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    const req = { ...await request(), packId: 'flow' };
    const result = await service().save(req, 'editor');
    expect(result.outcome).toBe('confirmed');
    expect(raw.prepare('SELECT pack_id FROM workflow_specs WHERE workflow_id = ?').get('flow')).toEqual({ pack_id: null });
    const yaml = runGit(root, ['--git-dir', bare, 'show', `${saved()}:packs/flow/workflows/flow.yaml`]);
    expect(projectWorkflowSnapshotYaml('flow', yaml).specJson).toBe(row().spec_json);
  });
  it('rejects a NULL Pack binding interpreted as any other Pack before writes', async () => {
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL WHERE workflow_id = ?').run('flow');
    const before = row().spec_json;
    const req = await request(); // pack, not the workflow's own Pack ID
    const result = await service().save(req, 'editor');
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(row().spec_json).toBe(before);
    expect(saved()).toBe(current);
  });
  it('rejects a candidate with another Pack ID for a NULL binding', async () => {
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL WHERE workflow_id = ?').run('flow');
    const before = row().spec_json;
    let candidatePackId: string | undefined;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => {
        candidatePackId = candidate.packId; return callback(candidate, push);
      });
    const result = await service({ prepare }).save({ ...await request(), packId: 'flow' }, 'editor');
    expect(candidatePackId).toBe('pack');
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(row().spec_json).toBe(before); expect(saved()).toBe(current);
  });
  it.each(['edit', 'pack'])('denies %s permission before writing a NULL-bound workflow', async denied => {
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL WHERE workflow_id = ?').run('flow');
    runGit(seed, ['mv', 'packs/pack', 'packs/flow']);
    writeFileSync(path.join(seed, 'packs/flow/workflow.pack.yaml'), 'id: flow\nworkflows: [flow, other]\n');
    runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'legacy pack identity']);
    current = runGit(seed, ['rev-parse', 'HEAD']); runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    const before = row().spec_json;
    let policyCalls = 0;
    const result = await service({ canEdit: async () => denied !== 'edit', canWritePack: async () => {
      policyCalls++; return denied !== 'pack';
    } }).save({ ...await request(), packId: 'flow' }, 'editor');
    expect(policyCalls).toBe(denied === 'pack' ? 1 : 0);
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(row().spec_json).toBe(before); expect(saved()).toBe(current);
  });
  it('rejects a concurrent raw NULL binding change even when effective Pack identity is unchanged', async () => {
    raw.prepare('UPDATE workflow_specs SET pack_id = NULL WHERE workflow_id = ?').run('flow');
    runGit(seed, ['mv', 'packs/pack', 'packs/flow']);
    writeFileSync(path.join(seed, 'packs/flow/workflow.pack.yaml'), 'id: flow\nworkflows: [flow, other]\n');
    runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'legacy pack identity']);
    current = runGit(seed, ['rev-parse', 'HEAD']); runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    const before = row().spec_json;
    const result = await service({ canWritePack: async () => {
      raw.prepare('UPDATE workflow_specs SET pack_id = ? WHERE workflow_id = ?').run('flow', 'flow'); return true;
    } }).save({ ...await request(), packId: 'flow' }, 'editor');
    expect(result).toMatchObject({ outcome: 'unknown', evidence: { db: 'unknown', git: 'not_started' } });
    expect(row().spec_json).toBe(before); expect(saved()).toBe(current);
    expect(raw.prepare('SELECT pack_id FROM workflow_specs WHERE workflow_id = ?').get('flow')).toEqual({ pack_id: 'flow' });
  });
  it.each(['edit', 'pack', 'edit-error', 'pack-error'])('reports partial and never pushes when %s authorization fails after DB confirmation', async failure => {
    const req = await request(); let editCalls = 0; let packCalls = 0; let pushes = 0;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        pushes++; return push();
      }));
    const result = await service({ prepare,
      canEdit: async () => { editCalls++; if (editCalls === 2 && failure === 'edit-error') throw new Error('secret');
        return !(editCalls === 2 && failure === 'edit'); },
      canWritePack: async () => { packCalls++; if (packCalls === 2 && failure === 'pack-error') throw new Error('secret');
        return !(packCalls === 2 && failure === 'pack'); },
    }).save(req, 'editor');
    expect(result).toMatchObject({ outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' } });
    expect(result.evidence.commit).toMatch(/^(?:[a-f0-9]{40}|[a-f0-9]{64})$/); expect(result.evidence.draftToken).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.stringify(result)).not.toContain('secret');
    expect(row().spec_json).toContain('after'); expect(saved()).toBe(current); expect(pushes).toBe(0);
  });
  it('rejects authorization, token, binding and manifest modifications before either write', async () => {
    const req = await request(); const original = row().spec_json;
    for (const [input, actor] of [
      [req, 'other'], [{ ...req, actorId: 'viewer' }, 'viewer'], [{ ...req, expectedDraftToken: 'f'.repeat(64) }, 'editor'],
      [{ ...req, packId: 'other' }, 'editor'], [{ ...req, repositoryId: 'b'.repeat(64) }, 'editor'],
      [{ ...req, changes: [{ path: 'workflow.pack.yaml', operation: 'delete' }] }, 'editor'],
    ] as const) {
      const result = await service().save(input as typeof req, actor);
      expect(result.outcome).not.toBe('confirmed'); expect(result.evidence.db).toBe('not_started'); expect(result.evidence.git).toBe('not_started');
    }
    expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });
  it('does not write DB when source belongs to another pack or candidate is invalid', async () => {
    const req = await request(); const original = row().spec_json;
    raw.prepare('UPDATE workflow_specs SET pack_id = ?').run('other');
    const mismatch = await service().save({ ...req, packId: 'other', expectedDraftToken: (await cas.read('flow', 'editor')).draftToken }, 'editor');
    expect(mismatch.evidence.db).toBe('not_started');
    raw.prepare('UPDATE workflow_specs SET pack_id = ?').run('pack');
    const invalid = await service().save({ ...req, expectedDraftToken: (await cas.read('flow', 'editor')).draftToken,
      changes: [{ path: 'workflows/flow.yaml', operation: 'delete' }] } as typeof req, 'editor');
    expect(invalid.evidence.db).toBe('not_started'); expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });
  it('does not push when the DB save fails before writing', async () => {
    const req = await request(); const original = row().spec_json; let pushes = 0;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        pushes++; return push();
      }));
    const result = await service({ prepare, draft: { read: cas.read.bind(cas), save: async () => {
      throw new Error('database unavailable');
    } } }).save(req, 'editor');
    expect(result).toMatchObject({ outcome: 'unknown', evidence: { db: 'unknown', git: 'not_started' } });
    expect(pushes).toBe(0); expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });
  it('treats a lost DB acknowledgement as unknown and never pushes', async () => {
    const req = await request(); const result = await service({ draft: { read: cas.read.bind(cas), save: async input => {
      await cas.save(input); throw new Error('secret database details');
    } } }).save(req, 'editor');
    expect(result.outcome).toBe('unknown'); expect(result.evidence).toMatchObject({ db: 'unknown', git: 'not_started' });
    expect(row().spec_json).toContain('after'); expect(saved()).toBe(current); expect(JSON.stringify(result)).not.toContain('secret');
  });
  it('re-reads the just-committed draft before pushing when there is no concurrent writer', async () => {
    const req = await request(); const events: string[] = [];
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        events.push('push'); return push();
      }));
    const result = await service({ prepare, draft: {
      read: async (workflowId, actorId) => { events.push('read'); return cas.read(workflowId, actorId); },
      save: async input => { const written = await cas.save(input); events.push('db'); return written; },
    } }).save(req, 'editor');
    expect(result).toMatchObject({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed' } });
    expect(events).toEqual(['read', 'db', 'read', 'push']); expect(saved()).toBe(result.evidence.commit);
  });
  it('leaves a concurrent DB update intact and does not push the older candidate', async () => {
    const req = await request(); let pushes = 0;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        pushes++; return push();
      }));
    const result = await service({ prepare, draft: { read: cas.read.bind(cas), save: async input => {
      const written = await cas.save(input);
      await cas.save({ ...input, expectedDraftToken: written.draftToken, spec: { ...spec, title: 'concurrent writer' } });
      return written;
    } } }).save(req, 'editor');
    expect(result).toMatchObject({ outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' } });
    expect(result.evidence.draftToken).toMatch(/^[a-f0-9]{64}$/);
    expect(JSON.parse(row().spec_json).title).toBe('concurrent writer');
    expect(saved()).toBe(current); expect(pushes).toBe(0);
  });
  it('does not recreate a concurrently deleted draft or push the older candidate', async () => {
    const req = await request(); let pushes = 0;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        pushes++; return push();
      }));
    const result = await service({ prepare, draft: { read: cas.read.bind(cas), save: async input => {
      const written = await cas.save(input);
      raw.prepare('DELETE FROM workflow_specs WHERE workflow_id = ?').run('flow');
      return written;
    } } }).save(req, 'editor');
    expect(result).toMatchObject({ outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' } });
    expect(raw.prepare('SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?').get('flow')).toBeUndefined();
    expect(saved()).toBe(current); expect(pushes).toBe(0);
  });
  it.each(['token', 'workflowId', 'packId', 'specJson', 'read-error'] as const)(
    'stops after DB confirmation if the re-read has a changed %s or fails', async mismatch => {
      const req = await request(); let reads = 0, pushes = 0;
      const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
        withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
          pushes++; return push();
        }));
      const result = await service({ prepare, draft: { save: cas.save.bind(cas), read: async (workflowId, actorId) => {
        const currentDraft = await cas.read(workflowId, actorId);
        if (++reads === 1) return currentDraft;
        if (mismatch === 'read-error') throw new Error('private read or permission failure');
        if (mismatch === 'token') return { ...currentDraft, draftToken: 'f'.repeat(64) };
        if (mismatch === 'workflowId') return { ...currentDraft, workflowId: 'other' };
        if (mismatch === 'packId') return { ...currentDraft, packId: null };
        return { ...currentDraft, specJson: currentDraft.specJson + ' ' };
      } } }).save(req, 'editor');
      expect(result).toMatchObject({ outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' } });
      expect(reads).toBe(2); expect(pushes).toBe(0); expect(saved()).toBe(current);
      expect(row().spec_json).toContain('after'); expect(JSON.stringify(result)).not.toContain('private');
    },
  );
  it('treats a lost Git acknowledgement as unknown despite a real successful push', async () => {
    const req = await request();
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (prepared, push) => callback(prepared, async () => {
        await push(); throw new Error('secret transport details');
      }));
    const result = await service({ prepare }).save(req, 'editor');
    expect(result.outcome).toBe('unknown'); expect(result.evidence).toMatchObject({ db: 'confirmed', git: 'unknown' });
    expect(saved()).toBe(result.evidence.commit); expect(JSON.stringify(result)).not.toContain('secret');
  });
  it('snapshots mutable input before the first async boundary', async () => {
    const req = await request(); let release!: () => void; const barrier = new Promise<void>(resolve => { release = resolve; });
    const pending = service({ canEdit: async () => { await barrier; return true; } }).save(req, 'editor');
    req.specJson = JSON.stringify({ ...spec, title: 'mutated' }); release();
    const result = await pending; expect(result.outcome).toBe('confirmed'); expect(row().spec_json).toContain('after');
  });
  it('ignores an independent saved ref appearing after DB confirmation', async () => {
    const req = await request(); let pushes = 0;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (prepared, push) => callback(prepared, async () => {
        pushes++; runGit(seed, ['push', bare, `${current}:refs/heads/saved/flow`]); return push();
      }));
    const result = await service({ prepare }).save(req, 'editor');
    expect(result.outcome).toBe('confirmed'); expect(result.evidence).toMatchObject({ db: 'confirmed', git: 'confirmed' });
    expect(saved()).toBe(result.evidence.commit); expect(pushes).toBe(1); expect(row().spec_json).toContain('after');
  });
  it('does not push when another writer wins the DB CAS after candidate validation', async () => {
    const req = await request();
    const result = await service({ draft: { read: cas.read.bind(cas), save: async input => {
      await cas.save({ ...input, spec: { ...spec, title: 'other writer' } });
      return cas.save(input);
    } } }).save(req, 'editor');
    expect(result.outcome).toBe('unknown'); expect(result.evidence).toMatchObject({ db: 'unknown', git: 'not_started' });
    expect(row().spec_json).toContain('other writer'); expect(saved()).toBe(current);
  });
  it('freezes the exact change snapshot through authorization and candidate push', async () => {
    const req = await request();
    const changes = [{ path: 'scripts/run.sh', operation: 'replace' as const, contentBase64: Buffer.from('#!/bin/sh\necho original\n').toString('base64'), mode: '100755' as const }];
    let release!: () => void; const barrier = new Promise<void>(resolve => { release = resolve; });
    let checked = false;
    const pending = service({ canEdit: async () => { await barrier; return true; }, canWritePack: async candidate => {
      expect(Object.isFrozen(candidate)).toBe(true);
      expect(Object.isFrozen(candidate.changes)).toBe(true);
      expect(Object.isFrozen(candidate.changes[0])).toBe(true);
      expect(candidate.changes[0]).toEqual({ path: 'scripts/run.sh', operation: 'replace', contentBase64: Buffer.from('#!/bin/sh\necho original\n').toString('base64'), mode: '100755' });
      checked = true; return true;
    } }).save({ ...req, changes }, 'editor');
    changes[0].contentBase64 = Buffer.from('tampered').toString('base64'); changes.length = 0; release();
    const result = await pending;
    expect(checked).toBe(true); expect(result.outcome).toBe('confirmed');
    expect(runGit(root, ['--git-dir', bare, 'show', `${saved()}:packs/pack/scripts/run.sh`])).toContain('echo original');
  });

  it('saves projected YAML and shared script under workflow edit authority while preserving other files', async () => {
    const req = await request();
    expect(await perms.hasEditPermission('flow', 'editor')).toBe(true);
    expect(await perms.hasEditPermission('other', 'editor')).toBe(false);
    const change = { path: 'scripts/run.sh', operation: 'replace' as const, contentBase64: Buffer.from('#!/bin/sh\necho updated\n').toString('base64'), mode: '100755' as const };
    const result = await service().save({ ...req, changes: [change] }, 'editor');
    expect(result).toMatchObject({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed' } });
    const commit = result.evidence.commit; expect(saved()).toBe(commit);
    const show = (file: string) => runGit(root, ['--git-dir', bare, 'show', `${commit}:packs/pack/${file}`]);
    expect(projectWorkflowSnapshotYaml('flow', show('workflows/flow.yaml')).specJson).toBe(row().spec_json);
    expect(show('scripts/run.sh')).toContain('echo updated');
    expect(show('scripts/resource.txt')).toBe('unchanged resource');
    expect(runGit(root, ['--git-dir', bare, 'ls-tree', `${commit}`, 'packs/pack/scripts/run.sh'])).toMatch(/^100755 blob /);
    expect(show('workflows/other.yaml')).toContain('id: other');
    expect(runGit(root, ['--git-dir', bare, 'diff', '--name-only', current, saved()!]).split('\n')).toEqual([
      'packs/pack/scripts/run.sh', 'packs/pack/workflows/flow.yaml',
    ]);
  });

  it('honors an optional additional denial for a shared script', async () => {
    const req = await request(); const original = row().spec_json;
    const changes = [{ path: 'scripts/run.sh', operation: 'delete' as const }];
    for (const canWritePack of [async () => false, async () => { throw new Error('denied'); }]) {
      const result = await service({ canWritePack }).save({ ...req, changes }, 'editor');
      expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
      expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
    }
  });

  it('denies a viewer shared Pack script edits before preparing or writing', async () => {
    const req = await request(); const original = row().spec_json; let preparations = 0;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) => {
      preparations++; return withPreparedPackCandidateForUnifiedSave(input, yaml, callback);
    };
    const result = await service({ prepare }).save({ ...req, actorId: 'viewer', changes: [{
      path: 'scripts/run.sh', operation: 'delete',
    }] }, 'viewer');
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(preparations).toBe(0); expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });

  it('rejects an out-of-Pack path without modifying DB or Git', async () => {
    const req = await request(); const original = row().spec_json;
    const result = await service().save({ ...req, changes: [{
      path: '../other/scripts/run.sh', operation: 'create', contentBase64: 'YQ==', mode: '100644',
    }] }, 'editor');
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });

  it('reports unknown on a lost Git receipt for a candidate containing script changes', async () => {
    const req = await request();
    const changes = [{ path: 'scripts/run.sh', operation: 'replace' as const, contentBase64: Buffer.from('new script').toString('base64'), mode: '100755' as const }];
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        await push(); throw new Error('lost receipt');
      }));
    const result = await service({ prepare }).save({ ...req, changes }, 'editor');
    expect(result).toMatchObject({ outcome: 'unknown', evidence: { db: 'confirmed', git: 'unknown' } });
    expect(saved()).toBe(result.evidence.commit);
    expect(runGit(root, ['--git-dir', bare, 'show', `${saved()}:packs/pack/scripts/run.sh`])).toBe('new script');
  });

  it('requires policy authorization of every path including unlisted DB workflows and both sides of moves', async () => {
    const req = await request(); const original = row().spec_json;
    raw.prepare('INSERT INTO workflow_specs VALUES (3,?,?,?,?,?,?,?,?)').run('unlisted', 'pack', '{"id":"unlisted"}', 'unlisted', 1, 'owner', 1, 2);
    const changes = [
      { path: 'scripts/run.sh', operation: 'delete' as const },
      { path: 'workflows/unlisted.yaml', operation: 'create' as const, contentBase64: Buffer.from('id: unlisted\n').toString('base64'), mode: '100644' as const },
    ];
    let observed: readonly string[] = [];
    const result = await service({ canWritePack: async candidate => {
      observed = candidate.changes.map(change => change.path);
      return candidate.changes.every(change => change.path.startsWith('scripts/'));
    } }).save({ ...req, changes }, 'editor');
    expect(observed).toEqual(['scripts/run.sh', 'workflows/unlisted.yaml']);
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });

  it('denies a real shared Pack when an optional additional restriction denies or fails', async () => {
    const req = await request(); const original = row().spec_json;
    expect(await perms.hasEditPermission('flow', 'editor')).toBe(true);
    expect(await perms.hasEditPermission('other', 'editor')).toBe(false);
    for (const policy of [async () => false, async () => { throw new Error('private policy data'); }]) {
      const result = await service({ canWritePack: policy }).save(req, 'editor');
      expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
      expect(JSON.stringify(result)).not.toContain('private policy data');
      expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
    }
  });

  it('authorizes the exact server-prepared candidate for the shared Pack, not just the requested workflow', async () => {
    const req = await request(); let observed: Parameters<NonNullable<ConstructorParameters<typeof WorkflowUnifiedSaveService>[0]['canWritePack']>>[0] | undefined;
    const result = await service({ canWritePack: async identity => {
      observed = identity;
      return identity.actorId === 'editor' && identity.packId === 'pack' && identity.repositoryId === repoId
        && identity.sourceCommit === current && identity.packPath === 'packs/pack/';
    } }).save(req, 'editor');
    expect(result.outcome).toBe('confirmed');
    expect(typeof observed?.bundleBase64).toBe('string');
    const { bundleBase64: _bundle, ...identity } = observed!;
    expect(identity).toEqual({ actorId: 'editor', workflowId: 'flow', packId: 'pack', repositoryId: repoId,
      packPath: 'packs/pack/', workflowFile: 'workflows/flow.yaml', sourceVersion: 'current',
      sourceCommit: current, expectedSavedHead: current, branch: 'wf/flow', commit: result.evidence.commit, inputHash: result.evidence.inputHash,
      changes: [], manifestContent: 'id: pack\nworkflows: [flow, other]\n' });
    expect(Object.isFrozen(observed)).toBe(true);
    expect(runGit(root, ['--git-dir', bare, 'show', `${saved()}:packs/pack/workflows/other.yaml`])).toContain('id: other');
  });

  it('enforces candidate-specific Pack policy and rejects non-boolean grants', async () => {
    const req = await request(); const original = row().spec_json; let calls = 0;
    const denyDifferentCommit = await service({ canWritePack: async candidate => {
      calls++; return candidate.commit === '0'.repeat(40);
    } }).save(req, 'editor');
    expect(calls).toBe(1);
    expect(denyDifferentCommit).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    const notTrue = await service({ canWritePack: async () => 'yes' as unknown as boolean }).save(req, 'editor');
    expect(notTrue).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });

  it('rejects changes to another workflow even with Pack write authorization', async () => {
    const req = await request(); const original = row().spec_json;
    const otherBefore = raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'other'").get();
    const result = await service().save({ ...req, changes: [{ path: 'workflows/other.yaml', operation: 'replace',
      contentBase64: Buffer.from('id: other\ntitle: changed\nnodes: []\n').toString('base64'), mode: '100644' }] }, 'editor');
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(row().spec_json).toBe(original);
    expect(raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'other'").get()).toEqual(otherBefore);
    expect(saved()).toBe(current);
  });

  it('rejects manifest replacement and custom manifest workflow paths even when policy grants', async () => {
    writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows:\n  - flow\n  - id: other\n    file: scripts/shared.yaml\n');
    writeFileSync(path.join(seed, 'packs/pack/scripts/shared.yaml'), 'id: other\nnodes: []\n');
    runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'custom workflow path']);
    current = runGit(seed, ['rev-parse', 'HEAD']); runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    const req = await request(); const original = row().spec_json; let policies = 0;
    for (const change of [
      { path: 'workflow.pack.yaml', operation: 'replace' as const, contentBase64: Buffer.from('id: pack\nworkflows: [flow, other]\n').toString('base64'), mode: '100644' as const },
      { path: 'scripts/shared.yaml', operation: 'replace' as const, contentBase64: Buffer.from('id: other\ntitle: changed\nnodes: []\n').toString('base64'), mode: '100644' as const },
      { path: 'SCRIPTS/SHARED.YAML', operation: 'create' as const, contentBase64: Buffer.from('id: other\nnodes: []\n').toString('base64'), mode: '100644' as const },
      { path: 'workflows/flow.yaml', operation: 'replace' as const, contentBase64: Buffer.from('id: flow\nnodes: []\n').toString('base64'), mode: '100644' as const },
    ]) {
      const result = await service({ canWritePack: async () => { policies++; return true; } }).save({ ...req, changes: [change] }, 'editor');
      expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
      expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
    }
    expect(policies).toBe(0);
  });

  it('rejects case aliases of declared workflow paths and their ancestors or descendants', async () => {
    const req = await request(); const original = row().spec_json;
    const otherBefore = raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'other'").get();
    let policyCalls = 0; const preparedFiles: string[] = [];
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => {
        preparedFiles.push(input.changes[0].path); return callback(candidate, push);
      });
    for (const file of ['WORKFLOWS/other.yaml', 'workflows/OTHER.YAML', 'WORKFLOWS/FLOW.YAML',
      'WORKFLOWS', 'WORKFLOWS/OTHER.YAML/child']) {
      const result = await service({ prepare, canWritePack: async () => { policyCalls++; return true; } }).save({ ...req, changes: [{
        path: file, operation: 'create', contentBase64: Buffer.from('id: other\nnodes: []\n').toString('base64'), mode: '100644',
      }] }, 'editor');
      expect(result, file).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
      expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
    }
    expect(policyCalls).toBe(0);
    expect(preparedFiles).not.toContain('WORKFLOWS/other.yaml');
    expect(raw.prepare("SELECT spec_json FROM workflow_specs WHERE workflow_id = 'other'").get()).toEqual(otherBefore);
  });

  it('rejects non-portable, Unicode, manifest-case and overlapping change paths before candidate preparation', async () => {
    const req = await request(); const original = row().spec_json; let preparations = 0;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) => {
      preparations++; return withPreparedPackCandidateForUnifiedSave(input, yaml, callback);
    };
    const create = (file: string) => ({ path: file, operation: 'create' as const, contentBase64: 'YQ==', mode: '100644' as const });
    for (const changes of [
      [create('WORKFLOW.PACK.YAML')], [create('WORKFLOW.PACK.YAML/child')],
      [create('WORKFLOWS/other.yaml')], [create('scripts/café.sh')], [create('scripts/cafe\u0301.sh')],
      [create('workflows/other.yaml.')], [create('workflows/other.yaml:stream')], [create('WORKFL~1/OTHER~1.YAM')],
      [create('scripts/a'), create('SCRIPTS/A')], [create('scripts/Dir'), create('SCRIPTS/dir/child')],
    ]) {
      const result = await service({ prepare }).save({ ...req, changes }, 'editor');
      expect(result, JSON.stringify(changes)).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
      expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
    }
    expect(preparations).toBe(0);
  });

  it('fails closed if a manifest-declared workflow path is non-ASCII', async () => {
    writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows:\n  - flow\n  - id: other\n    file: scripts/café.yaml\n');
    writeFileSync(path.join(seed, 'packs/pack/scripts/café.yaml'), 'id: other\nnodes: []\n');
    runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'unicode workflow path']);
    current = runGit(seed, ['rev-parse', 'HEAD']); runGit(seed, ['push', bare, `${current}:refs/heads/wf/flow`]);
    const req = await request(); const original = row().spec_json; let policies = 0; let prepared = false;
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => { prepared = true; return callback(candidate, push); });
    const result = await service({ prepare, canWritePack: async () => { policies++; return true; } }).save({ ...req, changes: [{
      path: 'scripts/new.sh', operation: 'create', contentBase64: 'YQ==', mode: '100644',
    }] }, 'editor');
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(prepared).toBe(false); expect(policies).toBe(0); expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });

  it.each([
    ['manifest alias', [['packs/pack/WORKFLOW.PACK.YAML', 'id: other-pack\nworkflows: []\n']]],
    ['workflow file alias', [['packs/pack/WORKFLOWS/flow.yaml', 'id: flow\nnodes: []\n']]],
    ['protected ancestor alias', [['PACKS/other/unrelated.txt', 'unrelated']]],
    ['manifest ids and default paths alias', [
      ['packs/pack/workflow.pack.yaml', 'id: pack\nworkflows: [flow, FLOW]\n'],
      ['packs/pack/workflows/FLOW.yaml', 'id: FLOW\nnodes: []\n'],
    ]],
    ['manifest ids alias even with distinct paths', [
      ['packs/pack/workflow.pack.yaml', 'id: pack\nworkflows:\n  - flow\n  - id: FLOW\n    file: scripts/other.yaml\n'],
      ['packs/pack/scripts/other.yaml', 'id: FLOW\nnodes: []\n'],
    ]],
    ['manifest declared paths alias', [
      ['packs/pack/workflow.pack.yaml', 'id: pack\nworkflows:\n  - flow\n  - id: other\n    file: WORKFLOWS/flow.yaml\n'],
      ['packs/pack/WORKFLOWS/flow.yaml', 'id: other\nnodes: []\n'],
    ]],
  ])('rejects pre-existing %s with empty changes before DB or Git writes', async (_label, files) => {
    // Construct the tree in Git's index, without relying on the host worktree's
    // case sensitivity. Both differently spelled entries genuinely exist.
    runGit(seed, ['read-tree', current]);
    for (const [file, content] of files) {
      const oid = runGit(seed, ['hash-object', '-w', '--stdin'], content);
      runGit(seed, ['update-index', '--add', '--cacheinfo', '100644', oid, file]);
    }
    const tree = runGit(seed, ['write-tree']);
    current = runGit(seed, ['commit-tree', tree, '-p', current, '-m', 'source alias']);
    runGit(seed, ['push', '--force', bare, `${current}:refs/heads/wf/flow`]);
    const treePaths = runGit(root, ['--git-dir', bare, 'ls-tree', '-r', '--name-only', current]).split('\n');
    for (const [file] of files) expect(treePaths).toContain(file);
    const req = await request(); const original = row().spec_json; let policies = 0;
    const result = await service({ canWritePack: async () => { policies++; return true; } }).save(req, 'editor');
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(policies).toBe(0); expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });

  it('rejects a new candidate-tree alias of a protected ancestor before DB or Git writes', async () => {
    const req = await request(); const original = row().spec_json; let policies = 0;
    const result = await service({ canWritePack: async () => { policies++; return true; } }).save({ ...req, changes: [{
      path: 'WORKFLOWS/unrelated.txt', operation: 'create', contentBase64: 'YQ==', mode: '100644',
    }] }, 'editor');
    expect(result).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect(policies).toBe(0); expect(row().spec_json).toBe(original); expect(saved()).toBe(current);
  });

  it('rejects an overlapping save based on the new DB token and old Git head across service instances', async () => {
    const first = await request();
    let resume!: () => void; const paused = new Promise<void>(resolve => { resume = resolve; });
    let entered!: () => void; const inPush = new Promise<void>(resolve => { entered = resolve; });
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        entered(); await paused; return push();
      }));
    const pending = service({ prepare }).save(first, 'editor');
    let resumeSecond!: () => void; const secondPaused = new Promise<void>(resolve => { resumeSecond = resolve; });
    let secondEntered!: () => void; const secondInPush = new Promise<void>(resolve => { secondEntered = resolve; });
    const secondPrepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        secondEntered(); await secondPaused; return push();
      }));
    let secondPending: Promise<Awaited<ReturnType<WorkflowUnifiedSaveService['save']>>> | undefined;
    let dbDuring = '', gitDuring: string | null = null;
    try {
      await inPush;
      const second = { ...await request(), requestId: 'request_456', specJson: JSON.stringify({ ...spec, title: 'second' }) };
      secondPending = service({ prepare: secondPrepare }).save(second, 'editor');
      // With the fix, B is rejected; without it, B writes DB and waits before
      // its push. Let A push first, then B loses the lease: DB B vs Git A.
      await Promise.race([secondInPush, secondPending]);
      dbDuring = row().spec_json; gitDuring = saved();
    } finally { resume(); await pending; resumeSecond(); if (secondPending) await secondPending; }
    const secondResult = await secondPending!;
    expect.soft(secondResult).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect.soft(JSON.parse(dbDuring).title).toBe('after');
    expect(gitDuring).toBe(current);
    expect((await pending).outcome).toBe('confirmed');
    const yaml = runGit(root, ['--git-dir', bare, 'show', `${saved()}:packs/pack/workflows/flow.yaml`]);
    expect(projectWorkflowSnapshotYaml('flow', yaml).specJson).toBe(row().spec_json);
  });

  it.each(['abort', 'deadline'])('does not release the in-flight guard on %s while an issued push is pending', async stop => {
    const first = await request();
    let resume!: () => void; const paused = new Promise<void>(resolve => { resume = resolve; });
    let entered!: () => void; const inPush = new Promise<void>(resolve => { entered = resolve; });
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        entered(); await paused; return push();
      }));
    const controller = new AbortController();
    const control = { signal: controller.signal, deadline: Number.MAX_VALUE };
    const pending = service({ prepare }).save(first, 'editor', control);
    try {
      await inPush;
      if (stop === 'abort') controller.abort(); else control.deadline = 0;
      const second = { ...await request(), requestId: 'request_456' };
      expect(await service().save(second, 'editor')).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    } finally { resume(); await pending; }
    expect((await pending).outcome).toBe('confirmed');
    const head = saved()!;
    const next = { ...await request(), requestId: 'request_789', sourceVersion: 'draft' as const, sourceCommit: head, expectedSavedHead: head };
    expect((await service().save(next, 'editor')).outcome).toBe('confirmed');
  });

  it('releases the guard after preparation failure without starting either write', async () => {
    const first = await request();
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = async () => { throw new Error('preparation failed'); };
    expect(await service({ prepare }).save(first, 'editor')).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
    expect((await service().save(first, 'editor')).outcome).toBe('confirmed');
  });

  it('does not block a different workflow and never lets a rejected contender release the owner', async () => {
    const first = await request();
    let resume!: () => void; const paused = new Promise<void>(resolve => { resume = resolve; });
    let entered!: () => void; const inPush = new Promise<void>(resolve => { entered = resolve; });
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (candidate, push) => callback(candidate, async () => {
        entered(); await paused; return push();
      }));
    const pending = service({ prepare }).save(first, 'editor');
    let otherRead = false;
    try {
      await inPush;
      const second = { ...await request(), requestId: 'request_456' };
      expect((await service().save(second, 'editor')).outcome).toBe('rejected');
      expect((await service().save(second, 'editor')).outcome).toBe('rejected');
      const other = { ...second, workflowId: 'other', specJson: JSON.stringify({ ...spec, id: 'other' }) };
      const otherService = service({ canEdit: async () => true, draft: { read: async () => {
        otherRead = true; throw new Error('stop after read');
      }, save: cas.save.bind(cas) } });
      expect((await otherService.save(other, 'editor')).outcome).toBe('rejected');
      expect(otherRead).toBe(true);
    } finally { resume(); await pending; }
    expect((await pending).outcome).toBe('confirmed');
  });

  it('keeps the guard until the prepared operation and its cleanup have settled', async () => {
    const first = await request();
    let resume!: () => void; const paused = new Promise<void>(resolve => { resume = resolve; });
    let entered!: () => void; const inCleanup = new Promise<void>(resolve => { entered = resolve; });
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = async (input, yaml, callback) => {
      const value = await withPreparedPackCandidateForUnifiedSave(input, yaml, callback);
      entered(); await paused; return value;
    };
    const pending = service({ prepare }).save(first, 'editor');
    try {
      await inCleanup;
      const head = saved()!;
      const second = { ...await request(), requestId: 'request_456', sourceVersion: 'draft' as const, sourceCommit: head, expectedSavedHead: head };
      expect((await service().save(second, 'editor')).outcome).toBe('rejected');
    } finally { resume(); await pending; }
    expect((await pending).outcome).toBe('confirmed');
  });

  it('releases the guard after a post-DB authorization failure', async () => {
    const first = await request(); let checks = 0;
    const result = await service({ canWritePack: async () => ++checks === 1 }).save(first, 'editor');
    expect(result).toMatchObject({ outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' } });
    const next = { ...await request(), requestId: 'request_456' };
    expect((await service().save(next, 'editor')).outcome).toBe('confirmed');
  });

  it('confirmed is only this invocation’s two acknowledgements when DB changes during push', async () => {
    const req = await request();
    const prepare: typeof withPreparedPackCandidateForUnifiedSave = (input, yaml, callback) =>
      withPreparedPackCandidateForUnifiedSave(input, yaml, (prepared, push) => callback(prepared, async () => {
        const latest = await cas.read('flow', 'editor');
        await cas.save({ workflowId: 'flow', actorId: 'editor', packId: 'pack', expectedDraftToken: latest.draftToken,
          spec: { ...spec, title: 'concurrent DB edit' } });
        return push();
      }));
    const result = await service({ prepare }).save(req, 'editor');
    expect(result).toMatchObject({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed' } });
    const yaml = runGit(root, ['--git-dir', bare, 'show', `${saved()}:packs/pack/workflows/flow.yaml`]);
    expect(projectWorkflowSnapshotYaml('flow', yaml).specJson).not.toBe(row().spec_json);
    expect(JSON.parse(row().spec_json).title).toBe('concurrent DB edit');
    expect(JSON.parse(projectWorkflowSnapshotYaml('flow', yaml).specJson).title).toBe('after');
  });
});
