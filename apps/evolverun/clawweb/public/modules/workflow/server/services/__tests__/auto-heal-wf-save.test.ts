// @vitest-environment node
/** Real SQLite and bare Git: the draft commit precedes the wf Pack push. */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { parse, stringify } from "yaml";
import { tmpdir } from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db/sqlite-database";
import { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { AutoHealSaveService } from "../auto-heal-save-service.js";
import { runGit } from "./clawflows-git-client-fixture.js";
import type { WorkflowSpec } from "../../workflow.js";

const TEST_SPEC: WorkflowSpec = {
  id: "wf-test", version: "1.0.0", title: "Test Workflow", nodes: [
    { id: "start", type: "start", title: "Start", config: {}, executor: { type: "start" }, dependsOn: [] },
    { id: "end", type: "end", title: "End", config: {}, executor: { type: "end" }, dependsOn: ["start"] },
  ],
};
const gitAuthor = { name: "Fixture", email: "fixture@example.invalid" };
const git = (dir: string, ...args: string[]) => runGit(dir, args);

function createTestDb() {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE workflow_specs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL UNIQUE, pack_id TEXT,
      spec_json TEXT NOT NULL, title TEXT, version INTEGER, owner_id TEXT,
      gmt_create INTEGER NOT NULL DEFAULT 0, gmt_modified INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE workflow_deploy_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT, pack_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
      deploy_number INTEGER NOT NULL, version INTEGER NOT NULL, tag_name TEXT, action TEXT NOT NULL,
      from_deploy_number INTEGER, spec_json TEXT, bot_id TEXT, owner_id TEXT, is_active INTEGER DEFAULT 0,
      note TEXT, gmt_create INTEGER NOT NULL DEFAULT 0, gmt_modified INTEGER NOT NULL DEFAULT 0
    );
    CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER);
    CREATE TABLE bot_workflow_permissions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, bot_owner_id TEXT NOT NULL,
      workflow_id TEXT NOT NULL, can_view INTEGER DEFAULT 0, can_execute INTEGER DEFAULT 0,
      can_edit INTEGER DEFAULT 0, env TEXT NOT NULL DEFAULT 'test',
      gmt_create INTEGER NOT NULL DEFAULT 0, gmt_modified INTEGER NOT NULL DEFAULT 0
    );
  `);
  return { db: new SqliteDatabase(raw), raw };
}

function createRemote(saved: boolean) {
  const root = mkdtempSync(path.join(tmpdir(), "autoheal-no-journal-"));
  const remote = path.join(root, "remote.git"), work = path.join(root, "work");
  git(root, "init", "--bare", "--initial-branch=main", remote);
  git(root, "init", "--initial-branch=main", work);
  git(work, "config", "user.name", gitAuthor.name); git(work, "config", "user.email", gitAuthor.email);
  mkdirSync(path.join(work, "workflows")); mkdirSync(path.join(work, "scripts"));
  writeFileSync(path.join(work, "workflow.pack.yaml"), stringify({
    id: "pack-1", version: "1.0.0", workflows: [{ id: "wf-test", file: "workflows/wf-test.yaml" }],
  }));
  writeFileSync(path.join(work, "workflows/wf-test.yaml"), stringify(TEST_SPEC));
  writeFileSync(path.join(work, "scripts/run.sh"), "saved script\n");
  writeFileSync(path.join(work, "asset.txt"), "saved asset\n");
  git(work, "add", "."); git(work, "update-index", "--chmod=+x", "scripts/run.sh"); git(work, "commit", "-m", "initial pack");
  const candidateCommit = git(work, "rev-parse", "HEAD");
  git(work, "remote", "add", "origin", remote);
  git(work, "push", "origin", "HEAD:refs/heads/main", `HEAD:refs/heads/${saved ? "saved" : "wf"}/wf-test`);
  return { root, remote, work, candidateCommit,
    config: { remoteUrl: remote, username: gitAuthor.name, email: gitAuthor.email, token: "" } };
}

async function repositories(ctx: ReturnType<typeof createTestDb>) {
  const history = new WorkflowDeployHistoryRepository(ctx.db);
  const specs = new WorkflowSpecRepository(ctx.db, history);
  const permissions = new BotWorkflowPermissionRepository(ctx.db);
  await specs.upsert("wf-test", "pack-1", JSON.stringify({ ...TEST_SPEC, content: stringify(TEST_SPEC) }), "user-1");
  await permissions.upsert({ bot_id: null, bot_owner_id: "user-1", workflow_id: "wf-test",
    can_view: 1, can_execute: 1, can_edit: 1 });
  return { history, specs, permissions };
}


import * as execution from '@avernet/clawweb-shared/server/services/git-execution';
import { editorActiveDeploymentToken } from '../editor-active-deployment-token.js';
import { EditorDraftCasService } from '../editor-draft-cas-service.js';
import { WorkflowUnifiedSaveService } from '../workflow-unified-save-service.js';
vi.setConfig({ testTimeout: 30_000 });
describe('AutoHeal DB then wf save contract', () => {
  let ctx: ReturnType<typeof createTestDb>, remote: ReturnType<typeof createRemote>;
  let repos: Awaited<ReturnType<typeof repositories>>;
  const edited = { ...TEST_SPEC, title: 'Edited', content: 'user content', version: '2' };
  const input = () => ({ workflowId: 'wf-test', actorId: 'user-1', requestId: 'save-contract-1', spec: edited });
  const service = () => new AutoHealSaveService(ctx.db, repos.specs, repos.history, repos.permissions, remote.config, gitAuthor);
  const remoteGit = (...args: string[]) => git(remote.root, '--git-dir', remote.remote, ...args);
  beforeEach(async () => { ctx = createTestDb(); repos = await repositories(ctx); remote = createRemote(false); });
  afterEach(async () => { vi.restoreAllMocks(); await ctx.db.close(); rmSync(remote.root, { recursive: true, force: true }); });
  function interceptPush() {
    const execute = execution.executeGit; let pushes = 0, acknowledged = false;
    const transaction = ctx.db.transaction.bind(ctx.db);
    vi.spyOn(ctx.db, 'transaction').mockImplementation(async fn => { const result = await transaction(fn); acknowledged = true; return result; });
    vi.spyOn(execution, 'executeGit').mockImplementation(async (args, options) => {
      if (args[0] === 'push') {
        pushes++; expect(acknowledged).toBe(true);
        const row = ctx.raw.prepare('SELECT spec_json FROM workflow_specs WHERE workflow_id = ?').get('wf-test') as { spec_json: string };
        expect(JSON.parse(row.spec_json)).toEqual(edited); expect(ctx.raw.inTransaction).toBe(false);
        expect(args).toContain(`--force-with-lease=refs/heads/wf/wf-test:${remote.candidateCommit}`);
      }
      return execute(args, options);
    });
    return () => pushes;
  }
  function assertPack(commit: string) {
    expect(remoteGit('rev-parse', 'refs/heads/wf/wf-test')).toBe(commit);
    expect(remoteGit('branch', '--list', 'saved/wf-test')).toBe('');
    expect(parse(remoteGit('show', `${commit}:workflows/wf-test.yaml`))).toEqual(edited);
    for (const file of ['workflow.pack.yaml', 'scripts/run.sh', 'asset.txt']) {
      expect(remoteGit('ls-tree', commit, file)).toBe(remoteGit('ls-tree', remote.candidateCommit, file));
    }
    expect(remoteGit('ls-tree', commit, 'scripts/run.sh')).toMatch(/^100755/);
    expect(remoteGit('tag', '-l')).toBe('');
  }
  it('keeps the observed Git baseline through Editor first draft and unified save', async () => {
    ctx.raw.prepare('DELETE FROM workflow_specs WHERE workflow_id = ?').run('wf-test');
    await repos.history.insert({ workflowId: 'wf-test', packId: 'pack-1', deployNumber: 3, version: 1, action: 'deploy',
      tagName: 'deploy/wf-test/#3', specJson: JSON.stringify(TEST_SPEC), isActive: true });
    const active = (await repos.history.findActiveByWorkflowId('wf-test'))!;
    const beforeRefs = remoteGit('show-ref');
    const first = await service().saveEditorFirstDraft({ ...input(), expectedDraftAbsent: true,
      expectedActiveDeployNumber: 3, expectedActiveToken: editorActiveDeploymentToken('wf-test', active) });
    expect(first).toEqual({ ok: true, saved: true, workflowId: 'wf-test', packId: 'pack-1' });
    expect(remoteGit('show-ref')).toBe(beforeRefs);
    expect(remoteGit('rev-parse', 'refs/heads/wf/wf-test')).toBe(remote.candidateCommit);
    const draft = new EditorDraftCasService(ctx.db, repos.permissions);
    const token = (await draft.read('wf-test', 'user-1')).draftToken;
    const repositoryId = 'a'.repeat(64);
    const unified = new WorkflowUnifiedSaveService({ draft, repositoryId, config: remote.config,
      canEdit: (workflowId, actorId) => repos.permissions.hasEditPermission(workflowId, actorId) });
    const result = await unified.save({ requestId: 'editor-unified-1', workflowId: 'wf-test', actorId: 'user-1',
      packId: 'pack-1', repositoryId, sourceVersion: 'current', sourceCommit: remote.candidateCommit,
      expectedSavedHead: remote.candidateCommit, expectedDraftToken: token, specJson: JSON.stringify(edited), changes: [] }, 'user-1');
    expect(result).toMatchObject({ outcome: 'confirmed', evidence: { db: 'confirmed', git: 'confirmed' } });
    expect(result.evidence.commit).toBeTruthy();
    expect(result.evidence.commit).not.toBe(remote.candidateCommit);
    assertPack(result.evidence.commit!);
  });
  it('commits an existing draft before pushing the full Pack', async () => {
    const pushes = interceptPush(); const result = await service().save(input());
    expect(result).toMatchObject({ ok: true, saved: true, branch: 'wf/wf-test', commit: expect.any(String) });
    expect(pushes()).toBe(1); assertPack(result.commit!);
  });
  it('commits a restored first draft before pushing the full Pack', async () => {
    ctx.raw.prepare('DELETE FROM workflow_specs WHERE workflow_id = ?').run('wf-test');
    await repos.history.insert({ workflowId: 'wf-test', packId: 'pack-1', deployNumber: 3, version: 1, action: 'deploy',
      tagName: 'deploy/wf-test/#3', specJson: JSON.stringify(TEST_SPEC), isActive: true });
    const active = (await repos.history.findActiveByWorkflowId('wf-test'))!;
    const pushes = interceptPush(); const result = await service().save({ ...input(), expectedDraftAbsent: true,
      expectedActiveDeployNumber: 3, expectedActiveToken: editorActiveDeploymentToken('wf-test', active) });
    expect(result).toMatchObject({ ok: true, branch: 'wf/wf-test', commit: expect.any(String) });
    expect(pushes()).toBe(1); assertPack(result.commit!);
  });
  it('requires wf for an existing DB draft instead of creating a YAML-only replacement', async () => {
    git(remote.work, 'push', 'origin', ':refs/heads/wf/wf-test');
    const before = (await repos.specs.findByWorkflowId('wf-test'))!.spec_json;
    const refs = remoteGit('show-ref');
    await expect(service().save(input())).rejects.toMatchObject({ status: 409, code: 'SAVE_REQUIRED' });
    expect((await repos.specs.findByWorkflowId('wf-test'))!.spec_json).toBe(before);
    expect(remoteGit('show-ref')).toBe(refs);
  });
  it('refuses restored first-draft fallback to default HEAD when saved Pack refs are missing', async () => {
    ctx.raw.prepare('DELETE FROM workflow_specs WHERE workflow_id = ?').run('wf-test');
    await repos.history.insert({ workflowId: 'wf-test', packId: 'pack-1', deployNumber: 3, version: 1, action: 'deploy',
      tagName: 'deploy/wf-test/#3', specJson: JSON.stringify(TEST_SPEC), isActive: true });
    git(remote.work, 'push', 'origin', ':refs/heads/wf/wf-test');
    const refs = remoteGit('show-ref');
    await expect(service().save(input())).rejects.toMatchObject({ status: 409, code: 'SAVE_REQUIRED' });
    expect(await repos.specs.findByWorkflowId('wf-test')).toBeNull();
    expect(remoteGit('show-ref')).toBe(refs);
  });
  it.each(['update', 'insert'] as const)('never pushes after a failed DB %s', async mode => {
    if (mode === 'insert') {
      ctx.raw.prepare('DELETE FROM workflow_specs WHERE workflow_id = ?').run('wf-test');
      await repos.history.insert({ workflowId: 'wf-test', packId: 'pack-1', deployNumber: 3, version: 1, action: 'deploy',
        tagName: 'deploy/wf-test/#3', specJson: JSON.stringify(TEST_SPEC), isActive: true });
    }
    ctx.raw.exec(`CREATE TRIGGER ignore_write BEFORE ${mode.toUpperCase()} ON workflow_specs BEGIN SELECT RAISE(IGNORE); END`);
    const execute = execution.executeGit;
    const push = vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => execute(args, options));
    await expect(service().save(input())).rejects.toThrow();
    expect(push.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(0);
    expect(remoteGit('rev-parse', 'refs/heads/wf/wf-test')).toBe(remote.candidateCommit);
  });
  it('does not push when commit acknowledgement is lost', async () => {
    const transaction = ctx.db.transaction.bind(ctx.db);
    vi.spyOn(ctx.db, 'transaction').mockImplementation(async fn => { await transaction(fn); throw new Error('ack lost'); });
    const execute = execution.executeGit;
    const spy = vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => execute(args, options));
    await expect(service().save(input())).rejects.toMatchObject({ status: 503, code: 'SAVE_DB_OUTCOME_UNKNOWN' });
    expect(spy.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(0);
    expect(JSON.parse((await repos.specs.findByWorkflowId('wf-test'))!.spec_json)).toEqual(edited);
  });
  it.each(['permission', 'document'] as const)('reports committed DB and no push when %s changes after commit', async kind => {
    const transaction = ctx.db.transaction.bind(ctx.db);
    vi.spyOn(ctx.db, 'transaction').mockImplementation(async fn => {
      const result = await transaction(fn);
      if (kind === 'permission') ctx.raw.exec('UPDATE bot_workflow_permissions SET can_edit = 0');
      else ctx.raw.prepare('UPDATE workflow_specs SET title = ?').run('concurrent');
      return result;
    });
    const execute = execution.executeGit;
    const spy = vi.spyOn(execution, 'executeGit').mockImplementation((args, options) => execute(args, options));
    await expect(service().save(input())).rejects.toMatchObject({ status: 503, code: 'SAVE_DB_CONFIRMED_GIT_NOT_STARTED' });
    expect(spy.mock.calls.filter(([args]) => args[0] === 'push')).toHaveLength(0);
    expect(JSON.parse((await repos.specs.findByWorkflowId('wf-test'))!.spec_json)).toEqual(edited);
  });
  it('reports a committed DB and unknown Git outcome after a lost push acknowledgement', async () => {
    const execute = execution.executeGit;
    vi.spyOn(execution, 'executeGit').mockImplementation(async (args, options) => {
      const value = await execute(args, options); if (args[0] === 'push') throw new Error('ack lost'); return value;
    });
    let failure: { code?: string; branch?: string; commit?: string } = {};
    try { await service().save(input()); } catch (error) { failure = error as typeof failure; }
    expect(failure).toMatchObject({ code: 'SAVE_DB_CONFIRMED_GIT_OUTCOME_UNKNOWN', branch: 'wf/wf-test', commit: expect.any(String) });
    expect(remoteGit('rev-parse', 'refs/heads/wf/wf-test')).toBe(failure.commit);
    expect(JSON.parse((await repos.specs.findByWorkflowId('wf-test'))!.spec_json)).toEqual(edited);
  });
  it('fails closed before DB mutation when Git settings are missing', async () => {
    const before = (await repos.specs.findByWorkflowId('wf-test'))!.spec_json;
    await expect(new AutoHealSaveService(ctx.db, repos.specs, repos.history, repos.permissions).save(input())).rejects.toMatchObject({ status: 503 });
    expect((await repos.specs.findByWorkflowId('wf-test'))!.spec_json).toBe(before);
  });
});
