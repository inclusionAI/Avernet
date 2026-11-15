// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import type { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { createAutoHealConfigTestDb } from '../../config/__tests__/auto-heal-git-fixtures.js';
import { AppConfigRepository } from '../../repositories/app-config-repository.js';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { savePackVersionToRemote } from '../pack-version-remote.js';
import type { PackCandidateChange } from '../pack-candidate-save.js';

const yaml = 'id: flow\ntitle: Original\n';
const changed = 'id: flow\ntitle: Edited in canvas\n';
const b64 = (s: string) => Buffer.from(s).toString('base64');
const put = (file: string, content: string, operation: 'create' | 'replace' = 'replace'): PackCandidateChange =>
  ({ path: file, operation, contentBase64: b64(content), mode: '100644' });
const remove = (file: string): PackCandidateChange => ({ path: file, operation: 'delete' });
const manifest = (id: string, file: string) => `id: ${id}\nworkflows:\n  - id: flow\n    file: ${file}\n`;
let root: string, bare: string, seed: string, source: string, db: SqliteDatabase;
let prefix: string;

async function setup(layout = 'packs/pack', extra?: string) {
  prefix = layout ? `${layout}/` : '';
  mkdirSync(path.join(seed, prefix, 'workflows'), { recursive: true });
  writeFileSync(path.join(seed, prefix, 'workflow.pack.yaml'), manifest('pack', 'workflows/flow.yaml'));
  writeFileSync(path.join(seed, prefix, 'workflows/flow.yaml'), yaml);
  if (extra !== undefined) writeFileSync(path.join(seed, prefix, 'workflows/existing.yaml'), extra);
  writeFileSync(path.join(seed, prefix, 'asset.txt'), 'unchanged asset\n');
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'source']);
  source = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', bare, `${source}:refs/heads/wf/flow`]);
  db = createAutoHealConfigTestDb();
  await db.exec(`CREATE TABLE workflow_deploy_history (workflow_id TEXT, version INTEGER);
    CREATE TABLE workflow_specs (workflow_id TEXT PRIMARY KEY, spec_json TEXT, pack_id TEXT);
    INSERT INTO workflow_specs VALUES ('flow', 'database draft', 'pack');`);
  await new AppConfigRepository(db).create({ config_key: 'git', config_yaml: `remoteUrl: ${bare}\n` });
}
function context(changes: PackCandidateChange[]) {
  return { db, workflowId: 'flow', actorId: 'editor', requestId: 'move-test', sourceVersion: 'draft' as const,
    sourceCommit: source, expectedSavedHead: source, changes,
    gitConfig: { remoteUrl: bare, username: 'fixture', email: 'fixture@example.invalid' },
    gitAuthor: { name: 'Fixture', email: 'fixture@example.invalid' } };
}
const remote = (...args: string[]) => runGit(root, ['--git-dir', bare, ...args]);
function bytes(commit: string, file: string) { return execFileSync('git', ['--git-dir', bare, 'show', `${commit}:${file}`]); }
async function assertUnchanged() {
  expect(remote('ls-remote', bare, 'refs/heads/saved/flow')).toBe('');
  expect(remote('rev-parse', 'refs/heads/wf/flow')).toBe(source);
  expect(await db.query('SELECT spec_json FROM workflow_specs')).toEqual([{ spec_json: 'database draft' }]);
}
beforeEach(() => { root = mkdtempSync(path.join(tmpdir(), 'pack-move-')); bare = createLocalRemote(root); seed = path.join(root, 'seed'); });
afterEach(async () => { if (db) await db.close(); db = undefined as unknown as SqliteDatabase; rmSync(root, { recursive: true, force: true }); });

describe('no-journal selected workflow structural moves against a real Git remote', () => {
  it.each(['', 'packs/pack'])('moves unchanged YAML in layout %s and preserves the DB draft', async layout => {
    await setup(layout);
    const result = await savePackVersionToRemote(context([
      put('workflow.pack.yaml', manifest('pack', 'workflows/moved.yaml')),
      remove('workflows/flow.yaml'), put('workflows/moved.yaml', yaml, 'create'),
    ]));
    expect(remote('rev-parse', 'refs/heads/wf/flow')).toBe(result.commit);
    expect(remote('ls-remote', bare, 'refs/heads/saved/flow')).toBe('');
    expect(bytes(result.commit, `${prefix}workflows/moved.yaml`)).toEqual(Buffer.from(yaml));
    expect(remote('ls-tree', '-r', '--name-only', result.commit)).not.toContain(`${prefix}workflows/flow.yaml`);
    expect(await db.query('SELECT spec_json FROM workflow_specs')).toEqual([{ spec_json: 'database draft' }]);
    expect(await db.query('SELECT * FROM workflow_deploy_history')).toEqual([]);
  });
  it('moves the complete pack and workflow together when pack identity changes', async () => {
    await setup();
    await db.exec("UPDATE workflow_specs SET pack_id = 'renamed' WHERE workflow_id = 'flow'");
    const result = await savePackVersionToRemote(context([
      put('workflow.pack.yaml', manifest('renamed', 'workflows/moved.yaml')),
      remove('workflows/flow.yaml'), put('workflows/moved.yaml', yaml, 'create'),
    ]));
    expect(result.packId).toBe('renamed');
    expect(bytes(result.commit, 'packs/renamed/workflows/moved.yaml')).toEqual(Buffer.from(yaml));
    expect(bytes(result.commit, 'packs/renamed/asset.txt')).toEqual(Buffer.from('unchanged asset\n'));
    expect(remote('ls-tree', '-r', '--name-only', result.commit)).not.toContain('packs/pack/');
  });
  it('allows a manifest-only switch to an existing identical blob, including from a draft', async () => {
    await setup('packs/pack', yaml);
    const first = await savePackVersionToRemote(context([put('asset.txt', 'draft asset\n')]));
    const second = await savePackVersionToRemote({ ...context([
      put('workflow.pack.yaml', manifest('pack', 'workflows/existing.yaml')),
      remove('workflows/flow.yaml'),
    ]), sourceVersion: 'draft', sourceCommit: first.commit, expectedSavedHead: first.commit });
    expect(bytes(second.commit, 'packs/pack/workflows/existing.yaml')).toEqual(Buffer.from(yaml));
    expect(bytes(second.commit, 'packs/pack/asset.txt')).toEqual(Buffer.from('draft asset\n'));
    expect(remote('rev-parse', 'refs/heads/wf/flow')).toBe(second.commit);
    expect(await db.query('SELECT spec_json FROM workflow_specs')).toEqual([{ spec_json: 'database draft' }]);
  });
  it('rejects direct content edits, even if the YAML is malformed', async () => {
    await setup();
    for (const content of [changed, 'not: [valid']) {
      await expect(savePackVersionToRemote(context([put('workflows/flow.yaml', content)])))
        .rejects.toMatchObject({ code: 'WORKFLOW_YAML_REQUIRES_DB_SAVE' });
      await assertUnchanged();
    }
  });
  it('rejects edited bytes at the moved destination, including newline-only changes', async () => {
    await setup();
    for (const content of [changed, yaml.trimEnd()]) {
      await expect(savePackVersionToRemote(context([
        put('workflow.pack.yaml', manifest('pack', 'workflows/moved.yaml')),
        remove('workflows/flow.yaml'), put('workflows/moved.yaml', content, 'create'),
      ]))).rejects.toMatchObject({ code: 'WORKFLOW_YAML_REQUIRES_DB_SAVE' });
      await assertUnchanged();
    }
  });
  it('rejects a manifest-only switch to a different pre-existing blob', async () => {
    await setup('packs/pack', changed);
    await expect(savePackVersionToRemote(context([put('workflow.pack.yaml', manifest('pack', 'workflows/existing.yaml'))])))
      .rejects.toMatchObject({ code: 'WORKFLOW_YAML_REQUIRES_DB_SAVE' });
    await assertUnchanged();
  });
  it('rejects an edit left at the old path even when the new selected file is unchanged', async () => {
    await setup();
    await expect(savePackVersionToRemote(context([
      put('workflow.pack.yaml', manifest('pack', 'workflows/moved.yaml')),
      put('workflows/flow.yaml', changed), put('workflows/moved.yaml', yaml, 'create'),
    ]))).rejects.toMatchObject({ code: 'WORKFLOW_YAML_REQUIRES_DB_SAVE' });
    await assertUnchanged();
  });
  it('rejects unsafe or dangling destination metadata without pushing', async () => {
    await setup();
    for (const file of ['../outside.yaml', 'workflows/missing.yaml']) {
      await expect(savePackVersionToRemote(context([put('workflow.pack.yaml', manifest('pack', file))]))).rejects.toThrow();
      await assertUnchanged();
    }
  });
});
