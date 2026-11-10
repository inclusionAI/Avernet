// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { stringify } from 'yaml';
import { afterEach, expect, it, vi } from 'vitest';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import type { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { createAutoHealConfigTestDb } from '../../config/__tests__/auto-heal-git-fixtures.js';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { WorkflowDeployHistoryRepository } from '../../repositories/workflow-deploy-history-repository.js';
import { AutoHealSaveService } from '../../services/auto-heal-save-service.js';
import { editorActiveDeploymentToken } from '../../services/editor-active-deployment-token.js';
import { EditorDraftCasService } from '../../services/editor-draft-cas-service.js';
import { candidateProjection } from '../../services/pack-operation-service.js';
import { WorkflowUnifiedSaveService } from '../../services/workflow-unified-save-service.js';
import { readActivePackVersionFromRemote, readPackVersionFromRemote } from '../../services/pack-version-remote.js';
import { createLocalRemote, runGit } from '../../services/__tests__/clawflows-git-client-fixture.js';
import { createPackVersionRouter } from '../pack-version.js';
const spec = { id: 'flow', title: 'Published', nodes: [
  { id: 'start', title: 'Start', executor: { type: 'start' }, dependsOn: [] },
  { id: 'end', title: 'End', executor: { type: 'end' }, dependsOn: ['start'] },
] };
let root = '', db: SqliteDatabase | undefined, server: ReturnType<express.Application['listen']> | undefined;
afterEach(async () => { if (server) await new Promise<void>(r => server!.close(() => r())); server = undefined;
  if (db) await db.close(); db = undefined; vi.restoreAllMocks(); if (root) rmSync(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 200 }); });
async function fixture() {
  root = mkdtempSync(path.join(tmpdir(), 'active-pack-baseline-')); const bare = createLocalRemote(root), seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, 'packs/pack/workflows'), { recursive: true }); mkdirSync(path.join(seed, 'packs/pack/scripts'));
  writeFileSync(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(seed, 'packs/pack/workflows/flow.yaml'), stringify(spec));
  writeFileSync(path.join(seed, 'packs/pack/scripts/run.sh'), 'old script\n'); writeFileSync(path.join(seed, 'packs/pack/asset.txt'), 'keep\n');
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'release']); const sha = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['tag', '-a', 'deploy/flow/#1', '-m', 'release']); runGit(seed, ['push', bare, `${sha}:refs/heads/wf/flow`, 'refs/tags/deploy/flow/#1']);
  db = createAutoHealConfigTestDb();
  await db.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT, title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, pack_id TEXT, deploy_number INTEGER, version INTEGER, tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE bot_workflow_permissions (id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, env TEXT, can_view INTEGER, can_execute INTEGER, can_edit INTEGER, gmt_create INTEGER, gmt_modified INTEGER);`);
  const localDb = db, specs = new WorkflowSpecRepository(db), history = new WorkflowDeployHistoryRepository(db), perms = new BotWorkflowPermissionRepository(db);
  await history.insert({ workflowId: 'flow', packId: 'pack', deployNumber: 1, version: 1, tagName: 'deploy/flow/#1', action: 'deploy', specJson: JSON.stringify(spec), isActive: true });
  await perms.upsert({ bot_id: null, bot_owner_id: 'editor', workflow_id: 'flow', can_view: 1, can_execute: 0, can_edit: 1 });
  await perms.upsert({ bot_id: null, bot_owner_id: 'viewer', workflow_id: 'flow', can_view: 1, can_execute: 1, can_edit: 0 });
  const ctx = { db, workflowId: 'flow', gitConfig: { remoteUrl: bare, username: 'fixture', email: 'fixture@example.invalid' }, gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' } };
  const app = express(); app.use(express.json()); app.use(createPackVersionRouter({ resolvePrincipal: async req => ({ actorId: String(req.headers['x-actor'] ?? 'editor') }), botPermRepo: perms, workflowSpecRepo: specs,
    readActiveSnapshot: (workflowId, version) => readActivePackVersionFromRemote({ ...ctx, workflowId, version }),
    readSnapshot: (workflowId, packId, version) => readPackVersionFromRemote({ ...ctx, workflowId, packId, version }),
    saveCandidate: async () => { throw new Error('unexpected PUT'); },
  }));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening'); const addr = server.address(); if (!addr || typeof addr === 'string') throw new Error('address');
  return { root, bare, seed, sha, db: localDb, specs, history, perms, ctx, base: `http://127.0.0.1:${addr.port}/flow/pack-version` };
}
it('reads active baseline then creates a DB draft and unified-saves YAML and scripts', async () => {
  const f = await fixture(); const response = await fetch(`${f.base}?version=current`); expect(response.status).toBe(200);
  const snapshot = await response.json(); expect(snapshot).toMatchObject({ workflowId: 'flow', packId: 'pack', baseCommit: f.sha, baseRef: 'refs/tags/deploy/flow/#1', draftHead: f.sha });
  expect(snapshot.versionFiles).toContainEqual({ path: 'asset.txt', contentBase64: Buffer.from('keep\n').toString('base64'), mode: '100644' });
  expect((await fetch(`${f.base}?version=draft`)).status).toBe(200);
  const active = (await f.db.query<any>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', ['flow']))[0];
  const updated = { ...spec, title: 'Updated' };
  await new AutoHealSaveService(f.db, f.specs, f.history, f.perms, f.ctx.gitConfig, f.ctx.gitAuthor).save({ workflowId: 'flow', actorId: 'editor', spec: updated,
    expectedDraftAbsent: true, expectedActiveDeployNumber: 1, expectedActiveToken: editorActiveDeploymentToken('flow', active) });
  const savedSnapshot = await (await fetch(`${f.base}?version=draft`)).json();
  const cas = new EditorDraftCasService(f.db, f.perms), draft = await cas.read('flow', 'editor');
  const result = await new WorkflowUnifiedSaveService({ draft: cas, canEdit: (w, a) => f.perms.hasEditPermission(w, a), repositoryId: 'a'.repeat(64), config: f.ctx.gitConfig }).save({
    requestId: 'first_draft_123', workflowId: 'flow', actorId: 'editor', packId: 'pack', repositoryId: 'a'.repeat(64), sourceVersion: 'draft', sourceCommit: savedSnapshot.baseCommit,
    expectedSavedHead: savedSnapshot.draftHead, expectedDraftToken: draft.draftToken, specJson: JSON.stringify(updated),
    changes: [{ path: 'scripts/run.sh', operation: 'replace', mode: '100644', contentBase64: Buffer.from('new script\n').toString('base64') }],
  }, 'editor');
  expect(result.outcome).toBe('confirmed'); expect(result.evidence).toMatchObject({ db: 'confirmed', git: 'confirmed' });
  const show = (file: string) => runGit(f.root, ['--git-dir', f.bare, 'show', `refs/heads/wf/flow:packs/pack/${file}`]);
  expect(show('workflows/flow.yaml')).toContain('title: Updated'); expect(show('scripts/run.sh')).toBe('new script'); expect(show('asset.txt')).toBe('keep');
}, 30000);
it('requires edit, a unique active row, and never allows PUT without a draft', async () => {
  const f = await fixture(); expect((await fetch(`${f.base}?version=current`, { headers: { 'x-actor': 'viewer' } })).status).toBe(403);
  expect((await fetch(f.base, { method: 'PUT', headers: { 'content-type': 'application/json' }, body: '{}' })).status).toBe(404);
  await f.db.exec('UPDATE workflow_deploy_history SET is_active = 0'); expect((await fetch(`${f.base}?version=current`)).status).toBe(404);
  await f.db.exec('UPDATE workflow_deploy_history SET is_active = 1'); await f.history.insert({ workflowId: 'flow', packId: 'pack', deployNumber: 2, version: 2, action: 'deploy', tagName: 'deploy/flow/#2', specJson: JSON.stringify(spec), isActive: true });
  expect((await fetch(`${f.base}?version=current`)).status).toBe(404);
}, 30000);
it('rejects mismatched Pack but warns on active DB content mismatch', async () => {
  const f = await fixture(); await f.db.exec("UPDATE workflow_deploy_history SET pack_id = 'other'"); let r = await fetch(`${f.base}?version=current`); expect(r.status).toBe(503); expect(await r.text()).not.toContain('asset.txt');
  await f.db.exec("UPDATE workflow_deploy_history SET pack_id = 'pack', spec_json = ?", [JSON.stringify({ ...spec, title: 'different' })]); r = await fetch(`${f.base}?version=current`);
  expect(r.status).toBe(200); expect(await r.json()).toMatchObject({ baseCommit: f.sha, activeHistoryMismatch: true });
  await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify(spec)]); runGit(f.seed, ['commit', '--allow-empty', '-m', 'unrelated']); runGit(f.seed, ['push', f.bare, 'HEAD:refs/heads/wf/flow']);
  r = await fetch(`${f.base}?version=current`);
  expect(r.status).toBe(200); expect(await r.json()).not.toHaveProperty('activeHistoryMismatch');
}, 30000);
it('warns on a legacy active-history omission of the authored workflow version', async () => {
  const f = await fixture();
  const authored = { ...spec, version: '0' as const };
  writeFileSync(path.join(f.seed, 'packs/pack/workflows/flow.yaml'), stringify(authored));
  runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'legacy version omission']);
  const legacySha = runGit(f.seed, ['rev-parse', 'HEAD']);
  runGit(f.seed, ['tag', '-fa', 'deploy/flow/#1', '-m', 'release']);
  runGit(f.seed, ['push', '--force', f.bare, `${legacySha}:refs/heads/wf/flow`, 'refs/tags/deploy/flow/#1']);
  const response = await fetch(`${f.base}?version=current`);
  expect(response.status).toBe(200);
  expect(await response.json()).toMatchObject({ baseCommit: legacySha, baseRef: 'refs/tags/deploy/flow/#1', activeHistoryMismatch: true });
  const row = (await f.db.query<{ spec_json: string }>('SELECT spec_json FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', ['flow']))[0];
  expect(JSON.parse(row.spec_json)).not.toHaveProperty('version');
}, 30000);
it('fails closed when the active baseline changes while Git is read', async () => {
  const f = await fixture(); const query = f.db.query.bind(f.db); let activeReads = 0;
  vi.spyOn(f.db, 'query').mockImplementation(async (sql, params) => { if (sql.includes('SELECT workflow_id, is_active, pack_id') && ++activeReads === 2) await f.db.exec('UPDATE workflow_deploy_history SET version = 2'); return query(sql, params); });
  const response = await fetch(`${f.base}?version=current`); expect(response.status).toBe(409); expect(await response.json()).toMatchObject({ code: 'VERSION_CONFLICT' });
}, 30000);

it('reads wf and ignores an existing saved ref', async () => {
  const f = await fixture(); runGit(f.seed, ['push', f.bare, `${f.sha}:refs/heads/saved/flow`]);
  const response = await fetch(`${f.base}?version=draft`); expect(response.status).toBe(200);
  expect(await response.json()).toMatchObject({ packId: 'pack', baseCommit: f.sha, baseRef: 'origin/wf/flow', draftHead: f.sha });
}, 30000);
it('rejects a draft appearing while Git is read', async () => {
  const f = await fixture(); const query = f.db.query.bind(f.db); let checks = 0;
  vi.spyOn(f.db, 'query').mockImplementation(async (sql, params) => {
    if (sql === 'SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?' && ++checks === 2) await f.specs.upsert('flow', 'pack', JSON.stringify(spec), 'editor');
    return query(sql, params);
  });
  const response = await fetch(`${f.base}?version=current`); expect(response.status).toBe(409); expect(await response.json()).toMatchObject({ code: 'VERSION_CONFLICT' });
}, 30000);
it('reads the active tag without wf but does not use a saved-only draft', async () => {
  const f = await fixture();
  runGit(f.seed, ['push', f.bare, `${f.sha}:refs/heads/saved/flow`]);
  runGit(f.seed, ['push', f.bare, ':refs/heads/wf/flow']);
  const current = await fetch(`${f.base}?version=current`); expect(current.status).toBe(200);
  expect(await current.json()).toMatchObject({ baseRef: 'refs/tags/deploy/flow/#1', baseCommit: f.sha, draftHead: null });
  const response = await fetch(`${f.base}?version=draft`); expect(response.status).toBe(404);
  expect(await response.json()).toMatchObject({ code: 'SNAPSHOT_NOT_FOUND' });
}, 30000);
it('validates the complete history archive before unwrapping once, including nested authored content', async () => {
  const f = await fixture();
  const authored = { ...spec, content: 'nested authored content' };
  const yaml = stringify(authored);
  writeFileSync(path.join(f.seed, 'packs/pack/workflows/flow.yaml'), yaml);
  runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'archive fixture']);
  const sha = runGit(f.seed, ['rev-parse', 'HEAD']);
  runGit(f.seed, ['tag', '-fa', 'deploy/flow/#1', '-m', 'archive']);
  runGit(f.seed, ['push', '--force', f.bare, `${sha}:refs/heads/wf/flow`, 'refs/tags/deploy/flow/#1']);
  await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify({ ...authored, content: yaml })]);
  let response = await fetch(`${f.base}?version=current`); expect(response.status).toBe(200);
  expect(await response.json()).toMatchObject({ baseCommit: sha, packId: 'pack' });
  await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify({ ...authored, title: 'tampered', content: yaml })]);
  response = await fetch(`${f.base}?version=current`); expect(response.status).toBe(503); expect(await response.text()).not.toContain('nested authored content');
}, 30000);
it('never initializes an existing workflow from an ignored saved ref or default HEAD', async () => {
  const f = await fixture();
  runGit(f.seed, ['push', f.bare, `${f.sha}:refs/heads/saved/flow`]);
  runGit(f.seed, ['push', f.bare, ':refs/heads/wf/flow']);
  expect((await fetch(`${f.base}?version=draft`)).status).toBe(404);
  const active = (await f.db.query<any>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', ['flow']))[0];
  await expect(new AutoHealSaveService(f.db, f.specs, f.history, f.perms, f.ctx.gitConfig, f.ctx.gitAuthor).save({
    workflowId: 'flow', actorId: 'editor', spec: { ...spec, title: 'Must not lose files' },
    expectedDraftAbsent: true, expectedActiveDeployNumber: 1, expectedActiveToken: editorActiveDeploymentToken('flow', active),
  })).rejects.toMatchObject({ code: 'SAVE_REQUIRED' });
  expect(await f.specs.findByWorkflowId('flow')).toBeNull();
  expect(runGit(f.root, ['--git-dir', f.bare, 'branch', '--list', 'wf/flow'])).toBe('');
  expect(runGit(f.root, ['--git-dir', f.bare, 'rev-parse', 'refs/heads/saved/flow'])).toBe(f.sha);
  expect((await fetch(`${f.base}?version=current`)).status).toBe(200);
}, 30000);

it('ignores an unrelated saved root Pack but rejects an unauthorized wf layout change', async () => {
  const f = await fixture();
  runGit(f.seed, ['rm', '-r', 'packs/pack']);
  mkdirSync(path.join(f.seed, 'workflows'));
  writeFileSync(path.join(f.seed, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(f.seed, 'workflows/flow.yaml'), stringify(spec));
  writeFileSync(path.join(f.seed, 'private-outside.txt'), 'PRIVATE_OUTSIDE_SECRET\n');
  runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'malicious root pack']);
  runGit(f.seed, ['push', f.bare, 'HEAD:refs/heads/saved/flow']);
  const response = await fetch(`${f.base}?version=draft`); expect(response.status).toBe(200);
  const text = await response.text(); expect(text).not.toContain('private-outside'); expect(text).not.toContain('PRIVATE_OUTSIDE_SECRET');
  expect(text).not.toContain(Buffer.from('PRIVATE_OUTSIDE_SECRET\n').toString('base64'));
  runGit(f.seed, ['push', f.bare, 'HEAD:refs/heads/wf/flow']);
  const rejected = await fetch(`${f.base}?version=draft`); expect(rejected.status).toBe(503);
  const error = await rejected.text(); expect(error).not.toContain('private-outside'); expect(error).not.toContain('PRIVATE_OUTSIDE_SECRET');
  expect((await fetch(`${f.base}?version=current`)).status).toBe(200);
}, 30000);
it('reads a rollback from its original release tag while wf contains a newer draft', async () => {
  const f = await fixture();
  await f.db.exec("UPDATE workflow_deploy_history SET action = 'rollback', deploy_number = 9, from_deploy_number = 1");
  runGit(f.seed, ['push', f.bare, `${f.sha}:refs/heads/saved/flow`]);
  let r = await fetch(`${f.base}?version=current`); expect(r.status).toBe(200);
  runGit(f.seed, ['commit', '--allow-empty', '-m', 'same tree']); runGit(f.seed, ['push', f.bare, 'HEAD:refs/heads/wf/flow']);
  r = await fetch(`${f.base}?version=current`); expect(r.status).toBe(200);
  writeFileSync(path.join(f.seed, 'packs/pack/scripts/run.sh'), 'wrong active script\n');
  runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'different tree']); runGit(f.seed, ['push', f.bare, 'HEAD:refs/heads/wf/flow']);
  r = await fetch(`${f.base}?version=current`); expect(r.status).toBe(200); expect(await r.json()).toMatchObject({ baseCommit: f.sha, versionFiles: expect.arrayContaining([{ path: 'scripts/run.sh', contentBase64: Buffer.from('old script\n').toString('base64'), mode: '100644' }]) });
  r = await fetch(`${f.base}?version=draft`); expect(r.status).toBe(200); expect(await r.json()).toMatchObject({ baseCommit: runGit(f.seed, ['rev-parse', 'HEAD']), baseRef: 'origin/wf/flow', versionFiles: expect.arrayContaining([{ path: 'scripts/run.sh', contentBase64: Buffer.from('wrong active script\n').toString('base64'), mode: '100644' }]) });
  await f.db.exec("UPDATE workflow_deploy_history SET tag_name = 'deploy/other/#1'");
  expect((await fetch(`${f.base}?version=draft`)).status).toBe(404);
}, 30000);
it('reads lossless legacy YAML standard tags but rejects unsupported and lossy tags', async () => {
  const f = await fixture();
  const manifest = 'id: pack\nworkflows: [flow]\n';
  const yaml = stringify(spec).replace('title: Published', 'title: !!str Published') + 'config: !!map {amount: !!float 0.5, enabled: !!bool true, count: !!int 2, list: !!seq [a, b], empty: !!null null}\n';
  async function publish(content: string) {
    writeFileSync(path.join(f.seed, 'packs/pack/workflows/flow.yaml'), content);
    runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'tagged yaml']);
    runGit(f.seed, ['tag', '-fa', 'deploy/flow/#1', '-m', 'release']);
    runGit(f.seed, ['push', '--force', f.bare, 'HEAD:refs/heads/wf/flow', 'refs/tags/deploy/flow/#1']);
  }
  await publish(yaml);
  await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [candidateProjection('flow', yaml, manifest, 'legacy-autoheal').specJson]);
  let r = await fetch(`${f.base}?version=current`); expect(r.status).toBe(200);
  const unsafe = stringify(spec) + 'config: {number: !!float 0.10000000000000001}\n';
  await publish(unsafe); await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify({ ...spec, config: { number: 0.1 }, content: unsafe })]);
  r = await fetch(`${f.base}?version=current`); expect(r.status).toBe(503);
  const invalid = stringify(spec) + 'config: {bad: !!set {a: null}}\n';
  await publish(invalid); await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify({ ...spec, config: { bad: {} }, content: invalid })]);
  r = await fetch(`${f.base}?version=current`); expect(r.status).toBe(503);
}, 30000);
it('matches root Pack paths exactly, ignoring saved but rejecting a different wf Pack directory', async () => {
  const f = await fixture();
  runGit(f.seed, ['rm', '-r', 'packs/pack']); mkdirSync(path.join(f.seed, 'workflows'));
  writeFileSync(path.join(f.seed, 'workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  writeFileSync(path.join(f.seed, 'workflows/flow.yaml'), stringify(spec));
  runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'root release']);
  const rootSha = runGit(f.seed, ['rev-parse', 'HEAD']);
  runGit(f.seed, ['tag', '-fa', 'deploy/flow/#1', '-m', 'root release']);
  runGit(f.seed, ['push', '--force', f.bare, `${rootSha}:refs/heads/wf/flow`, `${rootSha}:refs/heads/saved/flow`, 'refs/tags/deploy/flow/#1']);
  let response = await fetch(`${f.base}?version=draft`); expect(response.status).toBe(200); expect(await response.json()).toMatchObject({ packPath: '' });
  runGit(f.seed, ['push', '--force', f.bare, `${f.sha}:refs/heads/saved/flow`]);
  response = await fetch(`${f.base}?version=draft`); expect(response.status).toBe(200);
  runGit(f.seed, ['push', '--force', f.bare, `${f.sha}:refs/heads/wf/flow`]);
  response = await fetch(`${f.base}?version=draft`); expect(response.status).toBe(503);
}, 30000);
it('preserves legacy authored content as the whole document before trying archive recovery', async () => {
  const f = await fixture();
  for (const content of ['authored plain text, not a YAML workflow', stringify(spec)]) {
    const authored = { ...spec, content };
    writeFileSync(path.join(f.seed, 'packs/pack/workflows/flow.yaml'), stringify(authored));
    runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'legacy authored content']);
    const sha = runGit(f.seed, ['rev-parse', 'HEAD']);
    runGit(f.seed, ['tag', '-fa', 'deploy/flow/#1', '-m', 'release']);
    runGit(f.seed, ['push', '--force', f.bare, `${sha}:refs/heads/wf/flow`, 'refs/tags/deploy/flow/#1']);
    await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify(authored)]);
    let response = await fetch(`${f.base}?version=current`); expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ baseCommit: sha, packId: 'pack' });
    await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify({ ...authored, title: 'tampered' })]);
    response = await fetch(`${f.base}?version=current`); expect(response.status).toBe(503);
  }
}, 30000);

it('rejects incomplete archive projections without leaking Git file names or contents', async () => {
  const f = await fixture();
  const authored = { ...spec, content: 'user inner content', custom: { nested: [false, 0, null] } };
  const yaml = stringify(authored);
  writeFileSync(path.join(f.seed, 'packs/pack/workflows/flow.yaml'), yaml);
  writeFileSync(path.join(f.seed, 'packs/pack/scripts/run.sh'), 'SECRET_SCRIPT_CONTENT\n');
  runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'archive with script']);
  runGit(f.seed, ['tag', '-fa', 'deploy/flow/#1', '-m', 'release']);
  runGit(f.seed, ['push', '--force', f.bare, 'HEAD:refs/heads/wf/flow', 'refs/tags/deploy/flow/#1']);
  const projected = { ...authored, content: yaml };
  for (const row of [
    { ...projected, unexpected: 'extra' },
    { ...projected, custom: { nested: [false, 1, null] } },
    { ...projected, content: stringify({ ...authored, id: 'different-flow' }) },
    { ...projected, content: 'id: flow\ntitle: [broken' },
  ]) {
    await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [JSON.stringify(row)]);
    const response = await fetch(`${f.base}?version=current`);
    expect(response.status).toBeGreaterThanOrEqual(400);
    const body = await response.text();
    expect(body).not.toContain('scripts/run.sh');
    expect(body).not.toContain('SECRET_SCRIPT_CONTENT');
    expect(body).not.toContain(Buffer.from('SECRET_SCRIPT_CONTENT\n').toString('base64'));
  }
  expect((await f.db.query<{ spec_json: string }>('SELECT spec_json FROM workflow_deploy_history'))[0].spec_json)
    .toBe(JSON.stringify({ ...projected, content: 'id: flow\ntitle: [broken' }));
}, 30000);

it('returns active-history evidence only after active read, never for a draft-backed read', async () => {
  const f = await fixture();
  const row = (await f.db.query<any>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', ['flow']))[0];
  const active = await fetch(`${f.base}?version=current`);
  expect(active.status).toBe(200);
  expect(await active.json()).toMatchObject({ activeHistory: { protocol: 'active-history-v1', activeToken: editorActiveDeploymentToken('flow', row) } });
  await f.specs.upsert('flow', 'pack', JSON.stringify(spec), 'editor');
  const draftBacked = await fetch(`${f.base}?version=current`);
  expect(draftBacked.status).toBe(200);
  expect(await draftBacked.json()).not.toHaveProperty('activeHistory');
}, 30000);

it('strips active-history evidence even if an ordinary host reader accidentally supplies it', async () => {
  const app = express();
  app.use(createPackVersionRouter({ resolvePrincipal: async () => ({ actorId: 'editor' }),
    botPermRepo: { hasEditPermission: async () => true }, workflowSpecRepo: { findByWorkflowId: async () => ({ workflow_id: 'flow', pack_id: 'pack' }) },
    readSnapshot: async () => ({ workflowId: 'flow', packId: 'pack', baseRef: 'refs/tags/deploy/flow/#1', baseCommit: 'a'.repeat(40), draftHead: null,
      files: [], versionFiles: [], activeHistory: { protocol: 'active-history-v1', activeToken: 'f'.repeat(64) } } as any),
    readActiveSnapshot: async () => { throw new Error('must not access active path'); },
  }));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('address');
  const response = await fetch(`http://127.0.0.1:${address.port}/flow/pack-version?version=current`);
  expect(response.status).toBe(200); expect(await response.json()).not.toHaveProperty('activeHistory');
});

it('accepts lossless YAML and one-layer string-wrapped history while binding each raw stored value', async () => {
  const f = await fixture();
  const authored = { ...spec, content: 'user text', custom: { keep: [false, 0, null] } };
  const yaml = stringify(authored), projected = { ...authored, content: yaml };
  writeFileSync(path.join(f.seed, 'packs/pack/workflows/flow.yaml'), yaml);
  runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'wrapped active history']);
  runGit(f.seed, ['tag', '-fa', 'deploy/flow/#1', '-m', 'release']);
  runGit(f.seed, ['push', '--force', f.bare, 'HEAD:refs/heads/wf/flow', 'refs/tags/deploy/flow/#1']);
  const rows = [
    stringify(authored),
    JSON.stringify(stringify(authored)),
    JSON.stringify(JSON.stringify(authored)),
    stringify(stringify(authored)),
    stringify(projected),
    JSON.stringify(stringify(projected)),
    JSON.stringify(JSON.stringify(projected)),
  ];
  const tokens = new Set<string>();
  for (const raw of rows) {
    await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [raw]);
    const row = (await f.db.query<any>('SELECT * FROM workflow_deploy_history WHERE workflow_id = ? AND is_active = 1', ['flow']))[0];
    const response = await fetch(`${f.base}?version=current`);
    expect(response.status, `valid stored history ${raw.slice(0, 30)}`).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject({ activeHistory: { protocol: 'active-history-v1', activeToken: editorActiveDeploymentToken('flow', row) } });
    expect(row.spec_json).toBe(raw);
    tokens.add(body.activeHistory.activeToken);
  }
  expect(tokens.size).toBe(rows.length);
}, 30000);

it('rejects lossy or invalid wrapped history even when naive parsing could match Git', async () => {
  const f = await fixture();
  const authored = { ...spec, content: 'user text', custom: { value: 0.1 } };
  const yaml = stringify(authored);
  writeFileSync(path.join(f.seed, 'packs/pack/workflows/flow.yaml'), yaml);
  runGit(f.seed, ['add', '.']); runGit(f.seed, ['commit', '-m', 'lossless active history']);
  runGit(f.seed, ['tag', '-fa', 'deploy/flow/#1', '-m', 'release']);
  runGit(f.seed, ['push', '--force', f.bare, 'HEAD:refs/heads/wf/flow', 'refs/tags/deploy/flow/#1']);
  const lossy = yaml.replace('value: 0.1', 'value: 0.10000000000000001');
  expect(lossy).not.toBe(yaml);
  const bad = [
    `${yaml}title: Published\n`,
    JSON.stringify(`${yaml}title: Published\n`),
    lossy,
    JSON.stringify(lossy),
    JSON.stringify(JSON.stringify(JSON.stringify(authored))),
    `${yaml}extra: !!set {a: null}\n`,
    stringify({ ...authored, id: 'other' }),
    'id: flow\ntitle: [broken',
  ];
  for (const raw of bad) {
    await f.db.exec('UPDATE workflow_deploy_history SET spec_json = ?', [raw]);
    const response = await fetch(`${f.base}?version=current`);
    expect(response.status).toBeGreaterThanOrEqual(400);
    const text = await response.text();
    expect(text).not.toContain('active-history-v1');
    expect(text).not.toContain('scripts/run.sh');
    expect(text).not.toContain('user text');
    const [row] = await f.db.query<{ spec_json: string }>('SELECT spec_json FROM workflow_deploy_history');
    expect(row.spec_json).toBe(raw);
  }
}, 30000);
