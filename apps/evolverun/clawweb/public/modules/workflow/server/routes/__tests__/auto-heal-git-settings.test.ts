// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import type { Request, Response, Router } from "express";
import { stringify as stringifyYaml } from "yaml";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { createHash } from "node:crypto";
import { tmpdir } from "node:os";
import path from "node:path";
import { parse as parseYaml } from "yaml";
import { runGit } from "../../services/__tests__/clawflows-git-client-fixture.js";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { createAutoHealConfigTestDb, fallbackAuthor, fallbackGitConfig } from "../../config/__tests__/auto-heal-git-fixtures.js";
import { AppConfigRepository } from "../../repositories/app-config-repository.js";
import { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import { AutoHealSaveService } from "../../services/auto-heal-save-service.js";
import { AutoHealDeployService } from "../../services/auto-heal-deploy-service.js";
import * as gitClient from "../../services/clawflows-git-client.js";
import * as deployGit from "../../services/auto-heal-deploy-git.js";
import * as specGit from "../../services/pack-spec-save.js";
import type { WorkflowSpec } from "../../workflow.js";
import { createAutoHealRouter, type AutoHealRouteDependencies } from "../auto-heal.js";

const spec: WorkflowSpec = {
  id: "wf-git-settings", version: "1.0.0", title: "Git settings regression", nodes: [
    { id: "start", type: "start", title: "Start", config: {}, executor: { type: "start" }, dependsOn: [] },
    { id: "end", type: "end", title: "End", config: {}, executor: { type: "end" }, dependsOn: ["start"] },
  ],
};
// Real route handlers and SQLite, with a local Git remote and no journal table.
function post(router: Router, endpoint: string, body: unknown): Promise<{ status: number; body: Record<string, unknown> }> {
  return new Promise((resolve, reject) => {
    const headers: Record<string, string> = { "x-user-id": "operator" };
    const get = (name: string) => headers[name.toLowerCase()];
    const req = { method: "POST", url: `/${endpoint}`, headers, body, get, header: get } as Request;
    let status = 200;
    const res = {
      status(code: number) { status = code; return this; },
      json(payload: Record<string, unknown>) { resolve({ status, body: payload }); return this; },
    } as Response;
    router(req, res, (error?: unknown) => reject(error ?? new Error("AutoHeal route did not respond")));
  });
}

describe.each(["save", "deploy", "editor-deploy"] as const)("AutoHeal /%s Git settings", endpoint => {
  let db: IDatabase, repo: AppConfigRepository, deps: AutoHealRouteDependencies;
  let root: string, remote: string, candidate: string;
  const requestBody = async () => {
    const row = (await deps.workflowSpecRepo.findByWorkflowId(spec.id))!;
    const expectedDraftToken = createHash('sha256').update(JSON.stringify(['editor-draft-current-value-v1', row.id, row.workflow_id, row.pack_id, row.spec_json,
      row.version, row.title, row.owner_id, row.gmt_create, row.gmt_modified])).digest('hex');
    return { workflowId: spec.id, spec: { ...spec, title: "DB draft" }, packId: "pack-1", autoRun: true, candidateCommit: candidate, expectedDraftToken };
  };
  const remoteGit = (...args: string[]) => runGit(root, ["--git-dir", remote, ...args]);
  const noJournal = async () => expect(await db.query("SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'clawweb_pack_operations'")).toEqual([]);
  const configQueries = (query: MockInstance<IDatabase["query"]>) => query.mock.calls.filter(([sql]) => sql.includes("cm_app_config"));
  const writes = () => vi.mocked(gitClient.initClawflowsReadRepo).mock.calls.map(([, config]) => config);
  const authors = () => endpoint === "save" ? vi.mocked(specGit.withPreparedWorkflowSpec).mock.calls.map(([input]) => input.author)
    : vi.mocked(deployGit.prepareDeployCandidate).mock.calls.map(call => call[6]);

  beforeEach(async () => {
    db = createAutoHealConfigTestDb(); repo = new AppConfigRepository(db);
    await db.exec(`
      CREATE TABLE workflow_specs (
        id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id TEXT NOT NULL UNIQUE,
        pack_id TEXT, spec_json TEXT NOT NULL, title TEXT, version INTEGER, owner_id TEXT,
        gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
      );
      CREATE TABLE workflow_deploy_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, pack_id TEXT NOT NULL, workflow_id TEXT NOT NULL,
        deploy_number INTEGER NOT NULL, version INTEGER NOT NULL, tag_name TEXT,
        action TEXT NOT NULL, from_deploy_number INTEGER, spec_json TEXT, note TEXT,
        bot_id TEXT, owner_id TEXT, is_active INTEGER DEFAULT 0,
        gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
      );
      CREATE TABLE bot_workflow_permissions (
        id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, bot_owner_id TEXT NOT NULL,
        workflow_id TEXT NOT NULL, can_view INTEGER DEFAULT 0, can_execute INTEGER DEFAULT 0,
        can_edit INTEGER DEFAULT 0, env TEXT NOT NULL DEFAULT 'test',
        gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
      );
    `);
    await db.exec("CREATE TABLE facade_bindings (command TEXT PRIMARY KEY, workflow_id TEXT, pack_id TEXT, remark TEXT, gmt_create INTEGER, gmt_modified INTEGER)");
    const deployHistoryRepo = new WorkflowDeployHistoryRepository(db);
    const workflowSpecRepo = new WorkflowSpecRepository(db, deployHistoryRepo);
    const botPermRepo = new BotWorkflowPermissionRepository(db);
    await workflowSpecRepo.upsert(spec.id, "pack-1", JSON.stringify(spec), "operator");
    await botPermRepo.upsert({ bot_id: null, bot_owner_id: "operator", workflow_id: spec.id, can_view: 1, can_execute: 1, can_edit: 1 });
    deps = { resolvePrincipal: async () => ({ actorId: "operator" }), authorizeSnapshotDeploy: async () => true, db, workflowSpecRepo, deployHistoryRepo, botPermRepo, gitConfig: { ...fallbackGitConfig },
      gitAuthor: { ...fallbackAuthor }, triggerRun: vi.fn().mockResolvedValue("run-1") };
    root = mkdtempSync(path.join(tmpdir(), "auto-heal-settings-"));
    remote = path.join(root, "remote.git"); const seed = path.join(root, "seed");
    runGit(root, ["init", "--bare", "--initial-branch=main", remote]);
    runGit(root, ["init", "--initial-branch=main", seed]);
    runGit(seed, ["config", "user.name", "Fixture Author"]); runGit(seed, ["config", "user.email", "fixture@example.invalid"]);
    mkdirSync(path.join(seed, "workflows"));
    writeFileSync(path.join(seed, "workflow.pack.yaml"), stringifyYaml({ id: "pack-1", version: "1.0.0", workflows: [{ id: spec.id, file: `workflows/${spec.id}.yaml` }] }));
    writeFileSync(path.join(seed, "workflows", `${spec.id}.yaml`), stringifyYaml(spec));
    writeFileSync(path.join(seed, "asset.txt"), "saved asset\n");
    runGit(seed, ["add", "."]); runGit(seed, ["commit", "-m", "saved pack"]);
    candidate = runGit(seed, ["rev-parse", "HEAD"]);
    runGit(seed, ["push", remote, "HEAD:refs/heads/main", `HEAD:refs/heads/saved/${spec.id}`, `HEAD:refs/heads/wf/${spec.id}`]);
    const init = gitClient.initClawflowsReadRepo;
    vi.spyOn(gitClient, "initClawflowsReadRepo").mockImplementation((dir, config, format) => init(dir, { ...config, remoteUrl: remote }, format));
    const clone = gitClient.cloneClawflows;
    vi.spyOn(gitClient, "cloneClawflows").mockImplementation((dir, config) => clone(dir, { ...config, remoteUrl: remote }));
    vi.spyOn(gitClient, "createTempRepo"); vi.spyOn(specGit, "withPreparedWorkflowSpec");
    vi.spyOn(deployGit, "prepareDeployCandidate"); vi.spyOn(deployGit, "pushPreparedDeployment");
  });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); rmSync(root, { recursive: true, force: true }); });

  it("provides no-store absence proof only to an authorized actor", async () => {
    if (endpoint !== "editor-deploy") return;
    const getState = (router: Router) => new Promise<{ status: number; body: Record<string, unknown>; cache: string }>((resolve, reject) => {
      let status = 200, cache = "";
      const req = { method: "GET", url: `/editor-deploy/snapshot-v2/state?workflowId=${spec.id}`, query: { workflowId: spec.id }, headers: {} } as Request;
      const res = { set(name: string, value: string) { if (name === "Cache-Control") cache = value; return this; },
        status(code: number) { status = code; return this; },
        json(body: Record<string, unknown>) { resolve({ status, body, cache }); return this; } } as Response;
      router(req, res, (error?: unknown) => reject(error ?? new Error("No response")));
    });
    expect(await getState(createAutoHealRouter(deps))).toEqual({ status: 200, cache: "no-store", body: {
      protocol: "editor-deployment-state-v1", workflowId: spec.id, packId: "pack-1", canDeploy: true, savedCommit: candidate, activeCommit: null } });
    expect((await getState(createAutoHealRouter({ ...deps, resolvePrincipal: async () => null }))).status).toBe(401);
    expect((await getState(createAutoHealRouter({ ...deps, botPermRepo: null }))).status).toBe(403);
    expect(await getState(createAutoHealRouter({ ...deps, authorizeSnapshotDeploy: undefined }))).toMatchObject({ status: 200, body: { savedCommit: candidate } });
  });

  it("reports authenticated unified-save availability only for an explicit host resolver", async () => {
    if (endpoint !== "editor-deploy") return;
    const get = (router: Router) => new Promise<{ status: number; body: Record<string, unknown>; cache: string }>((resolve, reject) => {
      let status = 200, cache = "";
      const req = { method: "GET", url: "/capabilities", headers: {} } as Request;
      const res = { set(name: string, value: string) { if (name === "Cache-Control") cache = value; return this; },
        status(code: number) { status = code; return this; },
        json(body: Record<string, unknown>) { resolve({ status, body, cache }); return this; } } as Response;
      router(req, res, (error?: unknown) => reject(error ?? new Error("No capability response")));
    });
    expect(await get(createAutoHealRouter(deps))).toEqual({ status: 200, body: { autoHealUnifiedSave: false }, cache: "no-store" });
    deps.resolveUnifiedSave = async () => null;
    expect(await get(createAutoHealRouter(deps))).toEqual({ status: 200, body: { autoHealUnifiedSave: "v1" }, cache: "no-store" });
    deps.resolvePrincipal = async () => null;
    expect(await get(createAutoHealRouter(deps))).toEqual({ status: 401, body: { code: "AUTHENTICATION_REQUIRED" }, cache: "no-store" });
  });

  it("requires the snapshot protocol on its separate Editor endpoint", async () => {
    if (endpoint !== "editor-deploy") return;
    const router = createAutoHealRouter(deps);
    const body = { ...await requestBody(), autoRun: false };
    expect((await post(router, "editor-deploy/snapshot-v2", body)).status).toBe(400);
    expect((await post(router, "editor-deploy/snapshot-v2", { ...body, snapshotProtocol: "future" })).status).toBe(400);
    expect((await post(router, "editor-deploy", { ...body, snapshotProtocol: "saved-tree-v2" })).status).toBe(400);
    remoteGit("update-ref", `refs/heads/wf/${spec.id}`, candidate);
    const response = await post(router, "editor-deploy/snapshot-v2", { ...body, snapshotProtocol: "saved-tree-v2", expectedActiveCommit: "", confirmedFileDiff: true });
    expect(response.status).toBe(200);
    expect(response.body).toMatchObject({ snapshotProtocol: "saved-tree-v2", candidateCommit: candidate });
    expect(remoteGit("rev-parse", `${response.body.commit}^{tree}`)).toBe(remoteGit("rev-parse", `${candidate}^{tree}`));
  });

  it("resolves configured Git for create-only save as well as existing drafts", async () => {
    if (endpoint !== "save") return;
    await repo.create({ config_key: "git", config_yaml: stringifyYaml({ remoteUrl: remote }) });
    const create = vi.spyOn(AutoHealSaveService.prototype, "save").mockResolvedValue({ ok: true, saved: true,
      workflowId: "new-flow", packId: "new-flow", branch: "wf/new-flow", commit: "a".repeat(40) });
    const query = vi.spyOn(db, "query");
    const result = await post(createAutoHealRouter(deps), "save", { workflowId: "new-flow", spec: { ...spec, id: "new-flow" }, createOnly: true });
    expect(result.status).toBe(200);
    expect(result.body).toMatchObject({ branch: "wf/new-flow", commit: "a".repeat(40) });
    expect(configQueries(query)).toHaveLength(1);
    expect(create).toHaveBeenCalledWith(expect.objectContaining({ createOnly: true, actorId: "operator" }));
  });

  it("rejects unverified header identities when no trusted principal is supplied", async () => {
    deps.resolvePrincipal = undefined;
    const response = await post(createAutoHealRouter(deps), endpoint, { ...await requestBody(), createOnly: true, workflowId: "new-workflow" });
    expect(response.status).toBe(401);
    expect(writes()).toHaveLength(0);
    expect(await db.query("SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?", ["new-workflow"])).toEqual([]);
  });

  it("never treats a process exit status as an HTTP informational status", async () => {
    if (endpoint === "save") vi.spyOn(AutoHealSaveService.prototype, "save").mockRejectedValueOnce(Object.assign(new Error("database failed"), { status: 128 }));
    else vi.mocked(gitClient.initClawflowsReadRepo).mockRejectedValueOnce(Object.assign(new Error("git failed"), { status: 128 }));
    expect((await post(createAutoHealRouter(deps), endpoint, await requestBody())).status).toBe(500);
  });

  it("reloads DB URL, credentials, and author between two requests on the same router", async () => {
    await repo.create({ config_key: "git", config_yaml: stringifyYaml({
      remoteUrl: " https://git.example.com/first.git ", username: "first-user", email: "first@example.invalid",
      token: " first-token ", authorName: "First Author", authorEmail: "first@example.invalid",
    }) });
    const query = vi.spyOn(db, "query"), router = createAutoHealRouter(deps);
    expect(query).not.toHaveBeenCalled();
    expect(await post(router, endpoint, await requestBody())).toMatchObject({ status: 200, body: { ok: true } });
    expect(configQueries(query)).toHaveLength(1);
    await repo.update("git", { config_yaml: stringifyYaml({
      remoteUrl: "https://git.example.com/second.git", username: "second-user", email: "second@example.invalid",
      token: " second-token ", authorName: "Second Author", authorEmail: "second@example.invalid",
    }) });
    query.mockClear();
    expect(await post(router, endpoint, await requestBody())).toMatchObject({ status: 200, body: { ok: true } });
    expect(configQueries(query)).toHaveLength(1);
    expect(deps.gitConfig).toEqual(fallbackGitConfig); expect(deps.gitAuthor).toEqual(fallbackAuthor);
    expect(writes()).toEqual([
      { remoteUrl: "https://git.example.com/first.git", username: "first-user", email: "first@example.invalid", token: " first-token " },
      { remoteUrl: "https://git.example.com/second.git", username: "second-user", email: "second@example.invalid", token: " second-token " },
    ]);
    expect(authors()).toEqual([{ name: "First Author", email: "first@example.invalid" }, { name: "Second Author", email: "second@example.invalid" }]);
    if (endpoint === "save") {
      expect(specGit.withPreparedWorkflowSpec).toHaveBeenCalledTimes(2);
      expect(JSON.parse((await deps.workflowSpecRepo.findByWorkflowId(spec.id))!.spec_json).title).toBe("DB draft");
      expect(await db.query("SELECT * FROM workflow_deploy_history")).toEqual([]);
      expect(deployGit.pushPreparedDeployment).not.toHaveBeenCalled(); expect(deps.triggerRun).not.toHaveBeenCalled();
      expect(remoteGit("rev-parse", `refs/heads/saved/${spec.id}`)).toBe(candidate);
      expect(parseYaml(remoteGit("show", `wf/${spec.id}:workflows/${spec.id}.yaml`)).title).toBe("DB draft");
    } else {
      expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled();
      expect(writes()).toEqual([
        { remoteUrl: "https://git.example.com/first.git", username: "first-user", email: "first@example.invalid", token: " first-token " },
        { remoteUrl: "https://git.example.com/second.git", username: "second-user", email: "second@example.invalid", token: " second-token " },
      ]);
      expect(authors()).toEqual([{ name: "First Author", email: "first@example.invalid" }, { name: "Second Author", email: "second@example.invalid" }]);
      expect(deployGit.pushPreparedDeployment).toHaveBeenCalledTimes(2);
      expect(await db.query("SELECT deploy_number, action, is_active FROM workflow_deploy_history ORDER BY deploy_number"))
        .toEqual([{ deploy_number: 1, action: "deploy", is_active: 0 }, { deploy_number: 2, action: "deploy", is_active: 1 }]);
      expect(deps.triggerRun).toHaveBeenCalledTimes(2);
    }
    await noJournal();
  });

  it("uses fallbacks after the DB row is disabled on the same router", async () => {
    await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "https://git.example.com/database.git"' });
    const router = createAutoHealRouter(deps);
    expect((await post(router, endpoint, await requestBody())).status).toBe(200);
    await repo.update("git", { enabled: 0 });
    expect((await post(router, endpoint, await requestBody())).status).toBe(200);
    expect(writes()[1]).toEqual(fallbackGitConfig); expect(authors()[1]).toEqual(fallbackAuthor);
  });

  it.each(["absent", "whitespace"])("handles %s remote settings according to the endpoint contract", async state => {
    deps.gitConfig.remoteUrl = " \t\n ";
    if (state === "whitespace") await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "  "' });
    const save = vi.spyOn(AutoHealSaveService.prototype, "save"), deploy = vi.spyOn(AutoHealDeployService.prototype, "deploy");
    const write = vi.spyOn(db, "exec"), transaction = vi.spyOn(db, "transaction");
    const result = await post(createAutoHealRouter(deps), endpoint, await requestBody());
    expect(result).toMatchObject({ status: 503, body: { code: endpoint === "save" ? "SAVE_FAILED" : "DEPLOYMENT_FAILED" } });
    expect(save).not.toHaveBeenCalled(); expect(deploy).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled(); expect(transaction).not.toHaveBeenCalled();
    expect((await deps.workflowSpecRepo.findByWorkflowId(spec.id))?.spec_json).toBe(JSON.stringify(spec));
    expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled();
    expect(gitClient.createTempRepo).not.toHaveBeenCalled(); expect(gitClient.cloneClawflows).not.toHaveBeenCalled();
    expect(deployGit.prepareDeployCandidate).not.toHaveBeenCalled(); expect(deployGit.pushPreparedDeployment).not.toHaveBeenCalled();
    expect(deps.triggerRun).not.toHaveBeenCalled(); expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]);
    await noJournal();
  });

  it("recovers from missing remote settings on the next request without rebuilding the router", async () => {
    deps.gitConfig.remoteUrl = "";
    const router = createAutoHealRouter(deps);
    expect((await post(router, endpoint, await requestBody())).status).toBe(503);
    await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "https://git.example.com/configured.git"' });
    expect((await post(router, endpoint, await requestBody())).status).toBe(200);
    expect(writes()).toEqual([{ ...fallbackGitConfig, remoteUrl: "https://git.example.com/configured.git" }]);
  });

  it("fails closed on Git config query failure without leaking details or saving", async () => {
    const query = db.query.bind(db);
    vi.spyOn(db, "query").mockImplementation((sql, params) => sql.includes("cm_app_config") ? Promise.reject(new Error("database unavailable")) : query(sql, params));
    const save = vi.spyOn(AutoHealSaveService.prototype, "save"), deploy = vi.spyOn(AutoHealDeployService.prototype, "deploy");
    const result = await post(createAutoHealRouter(deps), endpoint, await requestBody());
    expect(result).toMatchObject({ status: 500, body: { code: endpoint === "save" ? "SAVE_FAILED" : "DEPLOYMENT_FAILED" } }); expect(save).not.toHaveBeenCalled();
    expect(deploy).not.toHaveBeenCalled(); expect(JSON.stringify(result.body)).not.toContain("database unavailable");
    expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled(); expect(gitClient.createTempRepo).not.toHaveBeenCalled(); expect(deps.triggerRun).not.toHaveBeenCalled();
  });

  it("handles database query failure without leaking details or writing", async () => {
    const body = await requestBody();
    vi.spyOn(db, "query").mockRejectedValue(new Error("database unavailable"));
    const transaction = vi.spyOn(db, "transaction"), write = vi.spyOn(db, "exec");
    const result = await post(createAutoHealRouter(deps), endpoint, body);
    expect(result).toMatchObject({ status: 500, body: { code: endpoint === "save" ? "SAVE_FAILED" : "DEPLOYMENT_FAILED" } });
    expect(JSON.stringify(result.body)).not.toContain("database unavailable");
    expect(transaction).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
    expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled(); expect(gitClient.createTempRepo).not.toHaveBeenCalled();
    expect(deps.triggerRun).not.toHaveBeenCalled();
  });

  it("preserves basic request validation before querying Git configuration", async () => {
    const query = vi.spyOn(db, "query"), router = createAutoHealRouter(deps);
    expect(await post(router, endpoint, {})).toMatchObject({ status: 400, body: { error: "Bad Request" } });
    expect(query).not.toHaveBeenCalled(); expect(gitClient.createTempRepo).not.toHaveBeenCalled();
    expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled();
  });

  it("checks permissions before preparing or pushing a private operation", async () => {
    await deps.botPermRepo!.upsert({ bot_id: null, bot_owner_id: "operator", workflow_id: spec.id,
      can_view: 1, can_execute: 0, can_edit: 0 });
    expect((await post(createAutoHealRouter(deps), endpoint, await requestBody())).status).toBe(403);
    expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled(); expect(gitClient.cloneClawflows).not.toHaveBeenCalled();
    await noJournal();
    expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]);
    expect(deps.triggerRun).not.toHaveBeenCalled();
  });

  if (endpoint === "save") {
    it("creates a workflow through the real route with the resolved Git remote", async () => {
      await repo.create({ config_key: "git", config_yaml: stringifyYaml({ remoteUrl: remote }) });
      const newSpec = { ...spec, id: "new-flow", title: "Created" };
      const response = await post(createAutoHealRouter(deps), "save", {
        workflowId: "new-flow", packId: "new-flow", spec: newSpec, createOnly: true, expectedSavedHead: null, requestId: "create-route-001",
      });
      expect(response.status).toBe(200);
      expect(response.body).toMatchObject({ ok: true, saved: true, workflowId: "new-flow", packId: "new-flow", branch: "wf/new-flow" });
      expect(remoteGit("rev-parse", "refs/heads/wf/new-flow")).toBe(response.body.commit);
      expect(remoteGit("ls-tree", "--name-only", "refs/heads/wf/new-flow").split("\n")).toEqual(["packs"]);
      expect(await deps.botPermRepo!.hasEditPermission("new-flow", "operator")).toBe(true);
      expect(JSON.parse((await deps.workflowSpecRepo.findByWorkflowId("new-flow"))!.spec_json)).toEqual(newSpec);
      expect(remoteGit("branch", "--list", "saved/new-flow")).toBe("");
      await noJournal();
    });
    it("reports a DB write failure without publishing or changing the draft", async () => {
      vi.spyOn(db, "transaction").mockRejectedValueOnce(new Error("database unavailable"));
      const response = await post(createAutoHealRouter(deps), endpoint, await requestBody());
      expect(response).toMatchObject({ status: 500, body: { code: "SAVE_FAILED" } });
      expect(JSON.stringify(response.body)).not.toContain("database unavailable");
      expect((await deps.workflowSpecRepo.findByWorkflowId(spec.id))?.spec_json).toBe(JSON.stringify(spec));
      // Preparation is read-only; failed DB acknowledgement must not publish wf.
      expect(specGit.withPreparedWorkflowSpec).toHaveBeenCalledOnce();
      expect(remoteGit("rev-parse", `refs/heads/wf/${spec.id}`)).toBe(candidate);
      expect(deps.triggerRun).not.toHaveBeenCalled();
      expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]); await noJournal();
    });
  } else {
    it("returns explicit unknown after a failed Git publication without a journal or run", async () => {
      vi.mocked(deployGit.pushPreparedDeployment).mockRejectedValueOnce(new Error("git failed"));
      const response = await post(createAutoHealRouter(deps), endpoint, { ...await requestBody(), requestId: "git-failed-request" });
      expect(response).toMatchObject({ status: 503, body: { code: "DEPLOY_OUTCOME_UNKNOWN", requestId: "git-failed-request", branch: `wf/${spec.id}`, tagName: `deploy/${spec.id}/#1` } });
      expect(response.body.commit).toMatch(/^[a-f0-9]{40,64}$/);
      expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]); await noJournal();
      expect((await deps.workflowSpecRepo.findByWorkflowId(spec.id))?.spec_json).toBe(JSON.stringify(spec));
      expect(remoteGit("rev-parse", `refs/heads/wf/${spec.id}`)).toBe(candidate); expect(remoteGit("tag", "-l")).toBe("");
      expect(deps.triggerRun).not.toHaveBeenCalled();
    });
  }

  if (endpoint !== "save") {
    if (endpoint === "editor-deploy") it("rejects an execute-only editor deployment without exposing shared repository paths", async () => {
      await deps.botPermRepo!.upsert({ bot_id: null, bot_owner_id: "operator", workflow_id: spec.id, can_view: 0, can_execute: 1, can_edit: 0 });
      const seed = path.join(root, "seed");
      mkdirSync(path.join(seed, "other-pack"));
      writeFileSync(path.join(seed, "other-pack", "private-asset.txt"), "private");
      runGit(seed, ["add", "."]); runGit(seed, ["commit", "-m", "other pack asset"]);
      runGit(seed, ["push", remote, `HEAD:refs/heads/wf/${spec.id}`]);
      const previous = remoteGit("rev-parse", `refs/heads/wf/${spec.id}`);
      const response = await post(createAutoHealRouter(deps), endpoint, await requestBody());
      expect(response).toMatchObject({ status: 403, body: { code: "FORBIDDEN" } });
      expect(JSON.stringify(response.body)).not.toContain("other-pack");
      expect(response.body).not.toHaveProperty("paths");
      expect(remoteGit("rev-parse", `refs/heads/wf/${spec.id}`)).toBe(previous);
      expect(remoteGit("tag", "-l")).toBe("");
      expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]);
    });

    it.each([true, false])("publishes explicit and legacy deployment requests (explicit=%s)", async explicit => {
      await deps.workflowSpecRepo.upsert(spec.id, "pack-1", JSON.stringify(spec), "operator");
      const editorPrecondition = endpoint === "editor-deploy" ? { expectedDraftToken: (await requestBody()).expectedDraftToken } : {};
      const body = explicit ? { workflowId: spec.id, candidateCommit: candidate, packId: "pack-1", autoRun: true, ...editorPrecondition } : { workflowId: spec.id, ...editorPrecondition };
      const result = await post(createAutoHealRouter(deps), endpoint, body);
      expect(result).toMatchObject({ status: 200, body: { ok: true, deployed: true, candidateCommit: candidate } });
      expect(deployGit.prepareDeployCandidate).toHaveBeenCalledWith(expect.any(String), spec.id, expect.stringMatching(/^[a-f0-9]{40,64}$/), "", 1, 1, fallbackAuthor);
      expect(deployGit.pushPreparedDeployment).toHaveBeenCalledOnce(); expect(deps.triggerRun).toHaveBeenCalledOnce();
      const release = String(result.body.commit);
      expect(remoteGit("rev-parse", `refs/heads/wf/${spec.id}`)).toBe(release);
      expect(remoteGit("rev-parse", `refs/tags/deploy/${spec.id}/#1^{}`)).toBe(release);
      expect(remoteGit("rev-parse", `refs/heads/saved/${spec.id}`)).toBe(candidate);
      expect(parseYaml(remoteGit("show", `${release}:workflows/${spec.id}.yaml`)).title).toBe(spec.title);
      expect(remoteGit("show", `${release}:asset.txt`)).toBe("saved asset");
      expect(await db.query("SELECT deploy_number, action, is_active FROM workflow_deploy_history")).toEqual([{ deploy_number: 1, action: "deploy", is_active: 1 }]);
      await noJournal();
    });

    it("requires saving a changed DB document before tag-only deployment", async () => {
      await deps.workflowSpecRepo.upsert(spec.id, "pack-1", JSON.stringify({ ...spec, title: "unsaved DB draft" }), "operator");
      const body = await requestBody(); const before = remoteGit("show-ref");
      expect(await post(createAutoHealRouter(deps), endpoint, body)).toMatchObject({ status: 409, body: { code: "SNAPSHOT_DB_MISMATCH" } });
      expect(deployGit.prepareDeployCandidate).not.toHaveBeenCalled(); expect(remoteGit("show-ref")).toBe(before);
      expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]);
    });

    it("rejects an unproven 64-character candidate without pushing or creating public history", async () => {
      expect(await post(createAutoHealRouter(deps), endpoint, { workflowId: spec.id, candidateCommit: "b".repeat(64),
        ...(endpoint === "editor-deploy" ? { expectedDraftToken: (await requestBody()).expectedDraftToken } : {}) }))
        .toMatchObject({ status: 409, body: { code: "CANDIDATE_NOT_SAVED" } });
      expect(deployGit.prepareDeployCandidate).not.toHaveBeenCalled(); expect(deployGit.pushPreparedDeployment).not.toHaveBeenCalled();
      expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]); expect(deps.triggerRun).not.toHaveBeenCalled();
    });

    it("rejects a null deployment request before querying or writing", async () => {
      const query = vi.spyOn(db, "query"), write = vi.spyOn(db, "exec");
      expect((await post(createAutoHealRouter(deps), endpoint, null)).status).toBe(400);
      expect(query).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
      expect(gitClient.createTempRepo).not.toHaveBeenCalled();
    });
  }
});
