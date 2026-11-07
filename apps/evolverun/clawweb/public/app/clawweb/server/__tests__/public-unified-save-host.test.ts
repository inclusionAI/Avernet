// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import Database from 'better-sqlite3';
import express from 'express';
import { once } from 'node:events';
import { mkdir, mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { type Server } from 'node:http';
import { SqliteDatabase, runMigrations, type IDatabase } from '@avernet/clawweb-shared/server/db';
import { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { EditorDraftCasService } from '@avernet/workflow/server/services/editor-draft-cas-service';
import { createLocalRemote, runGit } from './clawflows-git-client-fixture.js';
import { projectWorkflowSnapshotYaml } from '@avernet/workflow/server/services/workflow-snapshot-projection';
import type { WorkflowPackWriteAuthorization } from '@avernet/workflow/server/services/workflow-unified-save-service';
import type { ClawWebLifecycle } from '../bootstrap.js';
const state = vi.hoisted(() => ({ db: null as IDatabase | null }));
vi.mock('@avernet/clawweb-shared/server/db', async original => ({
  ...await original<typeof import('@avernet/clawweb-shared/server/db')>(),
  initDatabase: async () => ({ db: state.db }), closeDatabase: vi.fn(),
}));
vi.mock('@avernet/clawevolve', async () => {
  const { Router } = await import('express');
  return { createClawevolveModule: () => ({ start: async () => {}, stop: async () => {}, publicRouter: Router(), benchRouter: Router() }) };
});
vi.mock('@avernet/clawinsight', async () => {
  const { Router } = await import('express');
  return { createInsightRuntime: () => ({ service: {} }), createInsightManagementRuntime: () => ({ router: Router() }), createInsightRouter: () => Router(), createSessionRecoveryRouter: () => Router() };
});
vi.mock('../repair-ais-execution.js', () => ({ resolveRepairAisConfiguration: async () => null }));
import { createClawWebBootstrap } from '../public-bootstrap.js';
let lifecycle: ClawWebLifecycle | void;
let gitDir: string | undefined;
afterEach(async () => {
  await lifecycle?.stop?.(); lifecycle = undefined;
  await state.db?.close(); state.db = null;
  if (gitDir) await rm(gitDir, { recursive: true, force: true }); gitDir = undefined;
  vi.restoreAllMocks(); vi.unstubAllEnvs();
});
it.each(['absent', 'allow', 'late-deny', 'late-throw'])('uses real SQLite and Git with candidate policy: %s', async (mode) => {
  const withPolicy = mode !== 'absent';
  const raw = new Database(':memory:'), db = new SqliteDatabase(raw); state.db = db;
  await runMigrations(db, 'sqlite');
  const before = { id: 'flow', title: 'before', nodes: [{ id: 'n', executor: { type: 'agent' } }] };
  await db.exec(`INSERT INTO workflow_specs (workflow_id, pack_id, spec_json, title, owner_id, gmt_create, gmt_modified) VALUES ('flow', 'pack', '${JSON.stringify(before)}', 'before', 'editor', 1, 2)`);
  await db.exec(`INSERT INTO bot_workflow_permissions (bot_owner_id, workflow_id, can_view, can_execute, can_edit, gmt_create, gmt_modified) VALUES ('editor', 'flow', 1, 1, 1, 1, 1)`);
  gitDir = await mkdtemp(path.join(tmpdir(), 'public-unified-'));
  const root = gitDir, bare = createLocalRemote(root), seed = path.join(root, 'seed');
  await mkdir(path.join(seed, 'packs/pack/workflows'), { recursive: true });
  await mkdir(path.join(seed, 'packs/pack/scripts'));
  await writeFile(path.join(seed, 'packs/pack/workflow.pack.yaml'), 'id: pack\nworkflows: [flow]\n');
  await writeFile(path.join(seed, 'packs/pack/workflows/flow.yaml'), 'id: flow\nnodes: []\n');
  await writeFile(path.join(seed, 'packs/pack/scripts/run.sh'), 'old\n');
  runGit(seed, ['add', '.']); runGit(seed, ['commit', '-m', 'pack']);
  const sourceCommit = runGit(seed, ['rev-parse', 'HEAD']); runGit(seed, ['push', bare, `${sourceCommit}:refs/heads/wf/flow`]);
  const draft = new EditorDraftCasService(db, new BotWorkflowPermissionRepository(db));
  const spec = { ...before, title: 'after' }, script = 'echo updated\n';
  const change = { path: 'scripts/run.sh', operation: 'replace', mode: '100644', contentBase64: Buffer.from(script).toString('base64') };
  const input = { requestId: 'request_123', sourceVersion: 'current', sourceCommit, expectedSavedHead: sourceCommit,
    expectedDraftToken: (await draft.read('flow', 'editor')).draftToken, specJson: JSON.stringify(spec), changes: [change] };
  let policy: 'deny' | 'throw' | 'allow' = 'deny', principal: string | null = 'editor';
  const calls: Array<{ candidate: WorkflowPackWriteAuthorization; number: number; dbSpec: unknown; gitStatus: number | null; gitRef: string; action: 'allow' | 'deny' | 'throw' }> = [];
  const identity = vi.fn(async () => principal ? { actorId: principal } : null);
  let repositoryAvailable = true;
  const repository = vi.fn(async () => repositoryAvailable ? ({ repositoryId: 'a'.repeat(64), config: { remoteUrl: bare, username: 'test', email: 'test@example.invalid' } }) : null);
  const acl = vi.fn(async (candidate: WorkflowPackWriteAuthorization) => {
    const number = calls.length + 1;
    const dbSpec = JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('flow') as { spec_json: string }).spec_json);
    const ref = spawnSync('git', ['--git-dir', bare, 'rev-parse', '--verify', '--quiet', 'refs/heads/wf/flow'], { encoding: 'utf8' });
    const action = policy !== 'allow' ? policy
      : number === 2 && mode === 'late-deny' ? 'deny'
      : number === 2 && mode === 'late-throw' ? 'throw' : 'allow';
    calls.push({ candidate, number, dbSpec, gitStatus: ref.status, gitRef: ref.stdout, action });
    if (action === 'throw') throw new Error(number === 2 ? 'sensitive late policy error' : 'sensitive policy error');
    return action === 'allow';
  });
  let authorizedCommit: string | undefined;
  const assertCalls = async (actions: Array<'allow' | 'deny' | 'throw'>) => {
    expect(calls).toHaveLength(actions.length);
    expect(acl.mock.results).toHaveLength(actions.length);
    for (const [index, action] of actions.entries()) {
      const call = calls[index];
      expect(call.number).toBe(index + 1);
      expect(call.action).toBe(action);
      expect(acl.mock.results[index].type).toBe('return');
      if (action === 'throw') {
        await expect(acl.mock.results[index].value).rejects.toThrow(index === 0 ? 'sensitive policy error' : 'sensitive late policy error');
      } else {
        await expect(acl.mock.results[index].value).resolves.toBe(action === 'allow');
      }
      expect(call.candidate).toMatchObject({ actorId: 'editor', workflowId: 'flow', packId: 'pack', repositoryId: 'a'.repeat(64), branch: 'wf/flow', changes: [change] });
      expect(call.candidate).toMatchObject({ sourceVersion: 'current', sourceCommit, expectedSavedHead: sourceCommit });
      expect(call.candidate.commit).toMatch(/^[a-f0-9]{40}$/);
      expect(call.dbSpec).toEqual(index === 0 ? before : spec);
      expect(call.gitStatus).toBe(0);
      expect(call.gitRef.trim()).toBe(sourceCommit);
    }
    if (calls.length === 2) {
      expect(calls[1].candidate).toEqual(calls[0].candidate);
      authorizedCommit = calls[0].candidate.commit;
    }
    calls.length = 0;
    acl.mockClear();
  };
  vi.stubEnv('PORT', '0'); let server: Server | undefined; const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) { server = listen.apply(this, args); return server; });
  lifecycle = await createClawWebBootstrap({ unifiedSave: { resolveVerifiedPrincipal: identity, resolveRepository: repository, ...(withPolicy ? { canWriteExactCandidate: acl } : {}) } })
    .start({ environment: 'dev', profile: 'public', config: { auth: { admins: [] } }, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  const base = `http://127.0.0.1:${(server!.address() as { port: number }).port}`;
  const post = (headers: Record<string, string> = {}) => fetch(`${base}/api/workflows/flow/unified-save`, {
    method: 'POST', headers: { 'content-type': 'application/json', 'x-pack-version-csrf': '1', origin: base, ...headers }, body: JSON.stringify(input),
  });
  expect((await post({ origin: 'https://evil.invalid' })).status).toBe(403); expect(identity).not.toHaveBeenCalled();
  principal = null;
  expect((await post({ cookie: 'staff_id=editor', 'x-user-id': 'editor' })).status).toBe(401); expect(repository).not.toHaveBeenCalled();
  principal = 'viewer';
  expect((await post()).status).toBe(404); expect(repository).not.toHaveBeenCalled();
  principal = 'editor';
  repositoryAvailable = false;
  expect((await post()).status).toBe(404);
  repositoryAvailable = true;
  raw.prepare('UPDATE workflow_specs SET pack_id = ? WHERE workflow_id = ?').run('other-pack', 'flow');
  const wrongPack = await post();
  expect(wrongPack.status).toBe(409);
  expect(await wrongPack.json()).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
  expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('flow') as { spec_json: string }).spec_json)).toEqual(before);
  expect(runGit(root, ['--git-dir', bare, 'rev-parse', '--verify', 'refs/heads/wf/flow'])).toBe(sourceCommit);
  raw.prepare('UPDATE workflow_specs SET pack_id = ? WHERE workflow_id = ?').run('pack', 'flow');
  input.expectedDraftToken = (await draft.read('flow', 'editor')).draftToken;
  await assertCalls([]);
  // A stale wf lease must reject before the host policy or either write.
  const staleCommit = runGit(seed, ['rev-parse', 'HEAD^']);
  input.sourceCommit = staleCommit;
  input.expectedSavedHead = staleCommit;
  const stale = await post();
  expect(stale.status).toBe(409);
  expect(await stale.json()).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
  expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('flow') as { spec_json: string }).spec_json)).toEqual(before);
  expect(runGit(root, ['--git-dir', bare, 'rev-parse', '--verify', 'refs/heads/wf/flow'])).toBe(sourceCommit);
  await assertCalls([]);
  input.sourceCommit = sourceCommit;
  input.expectedSavedHead = sourceCommit;
  if (withPolicy) {
    for (const rejectedPolicy of ['deny', 'throw'] as const) {
      policy = rejectedPolicy;
      const denied = await post();
      await assertCalls([rejectedPolicy]);
      expect(denied.status).toBe(409);
      const body = await denied.text();
      expect(body).not.toContain('sensitive policy error');
      expect(JSON.parse(body)).toMatchObject({ outcome: 'rejected', evidence: { db: 'not_started', git: 'not_started' } });
      expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('flow') as { spec_json: string }).spec_json)).toEqual(before);
      expect(runGit(root, ['--git-dir', bare, 'rev-parse', '--verify', 'refs/heads/wf/flow'])).toBe(sourceCommit);
    }
    policy = 'allow';
  }
  const response = await post();
  await assertCalls(!withPolicy ? [] : ['allow', mode === 'late-deny' ? 'deny' : mode === 'late-throw' ? 'throw' : 'allow']);
  if (mode === 'late-deny' || mode === 'late-throw') {
    expect(response.status).toBe(409);
    const body = await response.text();
    expect(body).not.toContain('sensitive late policy error');
    expect(JSON.parse(body)).toMatchObject({ outcome: 'partial', evidence: { db: 'confirmed', git: 'not_started' } });
    expect(JSON.parse((raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('flow') as { spec_json: string }).spec_json)).toEqual(spec);
    expect(runGit(root, ['--git-dir', bare, 'rev-parse', '--verify', 'refs/heads/wf/flow'])).toBe(sourceCommit);
    return;
  }
  expect(response.status).toBe(200);
  const result = await response.json(); expect(result).toMatchObject({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed' } });
  const commit = runGit(root, ['--git-dir', bare, 'rev-parse', 'refs/heads/wf/flow']); expect(commit).toBe(result.evidence.commit);
  if (withPolicy) expect(commit).toBe(authorizedCommit);
  expect(commit).not.toBe(sourceCommit);
  expect(runGit(root, ['--git-dir', bare, 'rev-parse', `${commit}^`])).toBe(sourceCommit);
  expect(runGit(root, ['--git-dir', bare, 'tag', '--list'])).toBe('');
  const yaml = runGit(root, ['--git-dir', bare, 'show', `${commit}:packs/pack/workflows/flow.yaml`]);
  expect(JSON.parse(projectWorkflowSnapshotYaml('flow', yaml).specJson)).toEqual(spec);
  expect(runGit(root, ['--git-dir', bare, 'show', `${commit}:packs/pack/scripts/run.sh`])).toBe(script.trim());
}, 30_000);

it.each(['missing identity', 'missing repository', 'invalid policy', 'undefined policy'])('disables a malformed host binding: %s', async (kind) => {
  const db = new SqliteDatabase(new Database(':memory:')); state.db = db;
  await runMigrations(db, 'sqlite');
  const identity = vi.fn(async () => ({ actorId: 'editor' }));
  const repository = vi.fn(async () => null);
  vi.stubEnv('PORT', '0'); let server: Server | undefined; const listen = express.application.listen;
  vi.spyOn(express.application, 'listen').mockImplementation(function (...args: Parameters<typeof listen>) { server = listen.apply(this, args); return server; });
  const host = kind === 'missing identity' ? { resolveRepository: repository }
    : kind === 'missing repository' ? { resolveVerifiedPrincipal: identity }
    : { resolveVerifiedPrincipal: identity, resolveRepository: repository, canWriteExactCandidate: kind === 'invalid policy' ? null : undefined };
  lifecycle = await createClawWebBootstrap({ unifiedSave: host as any })
    .start({ environment: 'dev', profile: 'public', config: { auth: { admins: [] } }, configSources: [] });
  if (!server!.listening) await once(server!, 'listening');
  const response = await fetch(`http://127.0.0.1:${(server!.address() as { port: number }).port}/api/workflows/flow/unified-save`, {
    method: 'POST', headers: { 'x-pack-version-csrf': '1', 'content-type': 'application/json' }, body: '{}',
  });
  expect(response.status).toBe(401); expect(identity).not.toHaveBeenCalled(); expect(repository).not.toHaveBeenCalled();
});
