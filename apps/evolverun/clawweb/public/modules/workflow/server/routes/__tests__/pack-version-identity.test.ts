// @vitest-environment node
import express from 'express';
import { once } from 'node:events';
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { afterEach, expect, it, vi } from 'vitest';
import { stringify } from 'yaml';
import type { SqliteDatabase } from '@avernet/clawweb-shared/server/db/sqlite-database';
import { createAutoHealConfigTestDb } from '../../config/__tests__/auto-heal-git-fixtures.js';
import { AppConfigRepository } from '../../repositories/app-config-repository.js';
import { WorkflowSpecRepository } from '../../repositories/workflow-spec-repository.js';
import { createLocalRemote, runGit } from '../../services/__tests__/clawflows-git-client-fixture.js';
import { readPackVersionFromRemote, savePackVersionToRemote } from '../../services/pack-version-remote.js';
import { createPackVersionRouter } from '../pack-version.js';

let root = '';
let db: SqliteDatabase | undefined;
let server: ReturnType<express.Application['listen']> | undefined;
afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(e => e ? reject(e) : resolve()));
  server = undefined;
  if (db) await db.close(); db = undefined;
  vi.restoreAllMocks();
  if (root) rmSync(root, { recursive: true, force: true });
});

async function fixture(packId = 'pack', dbPackId: string | null = packId) {
  root = mkdtempSync(path.join(tmpdir(), 'pack-identity-'));
  const bare = createLocalRemote(root), seed = path.join(root, 'seed');
  mkdirSync(path.join(seed, `packs/${packId}/workflows`), { recursive: true });
  writeFileSync(path.join(seed, `packs/${packId}/workflow.pack.yaml`), `id: ${packId}\nworkflows: [flow]\n`);
  writeFileSync(path.join(seed, `packs/${packId}/workflows/flow.yaml`), 'id: flow\ntitle: Fixture\n');
  writeFileSync(path.join(seed, `packs/${packId}/run.sh`), 'original\n');
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'fixture']);
  const sha = runGit(seed, ['rev-parse', 'HEAD']);
  runGit(seed, ['push', bare, `${sha}:refs/heads/wf/flow`]);
  db = createAutoHealConfigTestDb();
  await db.exec(`CREATE TABLE workflow_specs (id INTEGER PRIMARY KEY, workflow_id TEXT UNIQUE, pack_id TEXT, spec_json TEXT,
    title TEXT, version INTEGER, owner_id TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE workflow_deploy_history (id INTEGER PRIMARY KEY, workflow_id TEXT, version INTEGER);`);
  await db.exec('INSERT INTO workflow_specs (workflow_id, pack_id, spec_json) VALUES (?, ?, ?)', ['flow', dbPackId, '{}']);
  await new AppConfigRepository(db).create({ config_key: 'git', config_yaml: stringify({ remoteUrl: bare }) });
  const workflowSpecRepo = new WorkflowSpecRepository(db);
  const ctx = { db, gitConfig: { remoteUrl: '/invalid', username: 'fixture', email: 'fixture@example.invalid', token: '' },
    gitAuthor: { name: 'fixture', email: 'fixture@example.invalid' } };
  const app = express(); app.use(express.json()); app.use(createPackVersionRouter({
    resolvePrincipal: async () => ({ actorId: 'editor' }), botPermRepo: { hasEditPermission: async () => true }, workflowSpecRepo,
    readSnapshot: (workflowId, id, version) => readPackVersionFromRemote({ ...ctx, workflowId, packId: id, version }),
    saveCandidate: input => savePackVersionToRemote({ ...ctx, ...input }),
  }));
  server = app.listen(0, '127.0.0.1'); await once(server, 'listening');
  const address = server.address(); if (!address || typeof address === 'string') throw new Error('address');
  const base = `http://127.0.0.1:${address.port}/flow/pack-version`;
  const save = (file: string, content: string) => fetch(base, { method: 'PUT', headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ sourceVersion: 'draft', sourceCommit: sha, expectedSavedHead: sha, requestId: 'identity-test-001',
      changes: [{ path: file, operation: 'replace', contentBase64: Buffer.from(content).toString('base64'), mode: '100644' }] }) });
  const remote = (...args: string[]) => runGit(root, ['--git-dir', bare, ...args]);
  return { db, sha, save, remote };
}

it('rejects a manifest rename before pushing when the database has the old identity', async () => {
  const f = await fixture();
  const before = await f.db.query('SELECT * FROM workflow_specs');
  const result = await f.save('workflow.pack.yaml', 'id: renamed\nworkflows: [flow]\n');
  expect(result.status).toBe(409);
  expect(await result.json()).toMatchObject({ code: 'PACK_ID_MISMATCH' });
  expect(f.remote('branch', '--list', 'saved/flow')).toBe('');
  expect(f.remote('rev-parse', 'refs/heads/wf/flow')).toBe(f.sha);
  expect(await f.db.query('SELECT * FROM workflow_specs')).toEqual(before);
}, 30000);

it('accepts a rename already coordinated in the database and pushes the renamed tree', async () => {
  const f = await fixture();
  await f.db.exec("UPDATE workflow_specs SET pack_id = 'renamed' WHERE workflow_id = 'flow'");
  const before = await f.db.query('SELECT * FROM workflow_specs');
  const result = await f.save('workflow.pack.yaml', 'id: renamed\nworkflows: [flow]\n');
  expect(result.status).toBe(200);
  const body = await result.json();
  expect(body).toMatchObject({ saved: true, packId: 'renamed', branch: 'wf/flow' });
  expect(f.remote('rev-parse', 'refs/heads/wf/flow')).toBe(body.commit);
  expect(f.remote('show', `${body.commit}:packs/renamed/run.sh`)).toBe('original');
  expect(f.remote('show', `${body.commit}:packs/renamed/workflow.pack.yaml`)).toContain('id: renamed');
  expect(f.remote('branch', '--list', 'saved/flow')).toBe('');
  expect(await f.db.query('SELECT * FROM workflow_specs')).toEqual(before);
}, 30000);

it('allows ordinary saves when final and database identities match, including legacy NULL', async () => {
  const f = await fixture('flow', null);
  const before = await f.db.query('SELECT * FROM workflow_specs');
  const result = await f.save('run.sh', 'changed\n');
  expect(result.status).toBe(200); const body = await result.json();
  expect(body).toMatchObject({ saved: true, packId: 'flow' });
  expect(f.remote('rev-parse', 'refs/heads/wf/flow')).toBe(body.commit);
  expect(f.remote('show', `${body.commit}:packs/flow/run.sh`)).toBe('changed');
  expect(await f.db.query('SELECT * FROM workflow_specs')).toEqual(before);
}, 30000);

it('does not treat legacy NULL as permission to rename to an arbitrary Pack', async () => {
  const f = await fixture('flow', null);
  const result = await f.save('workflow.pack.yaml', 'id: renamed\nworkflows: [flow]\n');
  expect(result.status).toBe(409); expect((await result.json()).code).toBe('PACK_ID_MISMATCH');
  expect(f.remote('branch', '--list', 'saved/flow')).toBe('');
  expect(await f.db.query('SELECT pack_id FROM workflow_specs WHERE workflow_id = ?', ['flow'])).toEqual([{ pack_id: null }]);
}, 30000);

it('fails closed without a push when the pre-push identity read fails', async () => {
  const f = await fixture();
  const query = f.db.query.bind(f.db);
  vi.spyOn(f.db, 'query').mockImplementation(async (sql, params) => {
    if (sql === 'SELECT workflow_id, pack_id FROM workflow_specs WHERE workflow_id = ?') throw new Error('database unavailable');
    return query(sql, params);
  });
  const result = await f.save('run.sh', 'changed\n');
  expect(result.status).toBe(503); expect((await result.json()).code).toBe('SERVICE_UNAVAILABLE');
  expect(f.remote('branch', '--list', 'saved/flow')).toBe('');
  expect(f.remote('rev-parse', 'refs/heads/wf/flow')).toBe(f.sha);
}, 30000);

it('allows an ordinary script save with an explicit unchanged database identity', async () => {
  const f = await fixture();
  const before = await f.db.query('SELECT * FROM workflow_specs');
  const result = await f.save('run.sh', 'explicit identity\n');
  expect(result.status).toBe(200); const body = await result.json();
  expect(body).toMatchObject({ saved: true, packId: 'pack' });
  expect(f.remote('rev-parse', 'refs/heads/wf/flow')).toBe(body.commit);
  expect(f.remote('show', `${body.commit}:packs/pack/run.sh`)).toBe('explicit identity');
  expect(await f.db.query('SELECT * FROM workflow_specs')).toEqual(before);
}, 30000);

it('rechecks database identity after the route lookup and rejects an intervening change', async () => {
  const f = await fixture();
  const query = f.db.query.bind(f.db);
  vi.spyOn(f.db, 'query').mockImplementation(async (sql, params) => {
    if (sql === 'SELECT workflow_id, pack_id FROM workflow_specs WHERE workflow_id = ?') {
      await f.db.exec("UPDATE workflow_specs SET pack_id = 'other' WHERE workflow_id = 'flow'");
    }
    return query(sql, params);
  });
  const result = await f.save('run.sh', 'changed\n');
  expect(result.status).toBe(409); expect((await result.json()).code).toBe('PACK_ID_MISMATCH');
  expect(f.remote('branch', '--list', 'saved/flow')).toBe('');
  expect(f.remote('rev-parse', 'refs/heads/wf/flow')).toBe(f.sha);
}, 30000);

it('fails closed if the workflow disappears after the route lookup', async () => {
  const f = await fixture();
  const query = f.db.query.bind(f.db);
  vi.spyOn(f.db, 'query').mockImplementation(async (sql, params) => {
    if (sql === 'SELECT workflow_id, pack_id FROM workflow_specs WHERE workflow_id = ?') {
      await f.db.exec("DELETE FROM workflow_specs WHERE workflow_id = 'flow'");
    }
    return query(sql, params);
  });
  const result = await f.save('run.sh', 'changed\n');
  expect(result.status).toBe(404); expect((await result.json()).code).toBe('WORKFLOW_NOT_FOUND');
  expect(f.remote('branch', '--list', 'saved/flow')).toBe('');
  expect(f.remote('rev-parse', 'refs/heads/wf/flow')).toBe(f.sha);
}, 30000);
