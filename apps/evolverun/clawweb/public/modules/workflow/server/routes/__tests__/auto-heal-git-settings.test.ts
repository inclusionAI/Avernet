import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Request, Response, Router } from "express";
import { stringify as stringifyYaml } from "yaml";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { createAutoHealConfigTestDb, fallbackAuthor, fallbackGitConfig } from "../../config/__tests__/auto-heal-git-fixtures.js";
import { AppConfigRepository } from "../../repositories/app-config-repository.js";
import { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import { AutoHealSaveService } from "../../services/auto-heal-save-service.js";
import { AutoHealDeployService } from "../../services/auto-heal-deploy-service.js";
import * as gitClient from "../../services/clawflows-git-client.js";
import type { WorkflowSpec } from "../../workflow.js";
import { createAutoHealRouter, type AutoHealRouteDependencies } from "../auto-heal.js";

const spec: WorkflowSpec = {
  id: "wf-git-settings",
  title: "Git settings regression",
  nodes: [
    { id: "start", type: "start", title: "Start", config: {}, executor: { type: "start" }, dependsOn: [] },
    { id: "end", type: "end", title: "End", config: {}, executor: { type: "end" }, dependsOn: ["start"] },
  ],
};

// Dispatch through the real Express router without opening a listening socket.
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

describe.each(["save", "deploy"] as const)("AutoHeal /%s Git settings", (endpoint) => {
  let db: IDatabase;
  let repo: AppConfigRepository;
  let deps: AutoHealRouteDependencies;

  const requestBody = { workflowId: spec.id, spec, packId: "pack-1", autoRun: true };

  beforeEach(async () => {
    db = createAutoHealConfigTestDb();
    repo = new AppConfigRepository(db);
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
    const deployHistoryRepo = new WorkflowDeployHistoryRepository(db);
    const workflowSpecRepo = new WorkflowSpecRepository(db, deployHistoryRepo);
    const botPermRepo = new BotWorkflowPermissionRepository(db);
    await workflowSpecRepo.upsert(spec.id, "pack-1", JSON.stringify(spec), "operator");
    await botPermRepo.upsert({
      bot_id: null, bot_owner_id: "operator", workflow_id: spec.id, can_view: 1, can_execute: 1, can_edit: 1,
    });
    deps = {
      db, workflowSpecRepo, deployHistoryRepo, botPermRepo,
      gitConfig: { ...fallbackGitConfig }, gitAuthor: { ...fallbackAuthor },
      triggerRun: vi.fn().mockResolvedValue("run-1"),
    };
    vi.spyOn(gitClient, "createTempRepo").mockResolvedValue("/tmp/auto-heal-git-settings-fixture");
    vi.spyOn(gitClient, "removeTempRepo").mockResolvedValue();
    vi.spyOn(gitClient, "cloneClawflows").mockResolvedValue();
    vi.spyOn(gitClient, "writeWorkflowSpecToRepo").mockResolvedValue();
    vi.spyOn(gitClient, "ensurePackManifest").mockResolvedValue();
    vi.spyOn(gitClient, "getMaxDeployNumberFromGitTags").mockResolvedValue(0);
    vi.spyOn(gitClient, "commitAndPushSaved").mockResolvedValue({ branch: `saved/${spec.id}`, commit: "abc123" });
    vi.spyOn(gitClient, "commitAndPushDeploy").mockImplementation(async (_dir, workflowId, deployNumber) => ({
      branch: `wf/${workflowId}`, tagName: `deploy/${workflowId}/#${deployNumber}`, commit: "abc123", deployNumber,
    }));
  });

  afterEach(async () => {
    vi.restoreAllMocks();
    await db.close();
  });

  it("reloads DB URL, credentials, and author between two requests on the same router", async () => {
    await repo.create({ config_key: "git", config_yaml: stringifyYaml({
      remoteUrl: " https://git.example.com/first.git ", username: "first-user", email: "first-git@example.com",
      token: " first-token ", authorName: "First Author", authorEmail: "first-author@example.com",
    }) });
    const query = vi.spyOn(db, "query");
    const router = createAutoHealRouter(deps);
    expect(query).not.toHaveBeenCalled();
    expect(await post(router, endpoint, requestBody)).toMatchObject({ status: 200, body: { ok: true } });
    expect(query.mock.calls.filter(([sql]) => sql.includes("cm_app_config"))).toHaveLength(1);

    await repo.update("git", { config_yaml: stringifyYaml({
      remoteUrl: "https://git.example.com/second.git", username: "second-user", email: "second-git@example.com",
      token: " second-token ", authorName: "Second Author", authorEmail: "second-author@example.com",
    }) });
    query.mockClear();
    expect(await post(router, endpoint, requestBody)).toMatchObject({ status: 200, body: { ok: true } });
    expect(query.mock.calls.filter(([sql]) => sql.includes("cm_app_config"))).toHaveLength(1);
    expect(gitClient.cloneClawflows).toHaveBeenNthCalledWith(1, expect.any(String), {
      remoteUrl: "https://git.example.com/first.git", username: "first-user", email: "first-git@example.com", token: " first-token ",
    });
    expect(gitClient.cloneClawflows).toHaveBeenNthCalledWith(2, expect.any(String), {
      remoteUrl: "https://git.example.com/second.git", username: "second-user", email: "second-git@example.com", token: " second-token ",
    });
    const commitCalls = endpoint === "save"
      ? vi.mocked(gitClient.commitAndPushSaved).mock.calls
      : vi.mocked(gitClient.commitAndPushDeploy).mock.calls;
    expect(commitCalls.map((args) => args.at(-1))).toEqual([
      { name: "First Author", email: "first-author@example.com" },
      { name: "Second Author", email: "second-author@example.com" },
    ]);
    if (endpoint === "deploy") {
      expect(deps.triggerRun).toHaveBeenCalledTimes(2);
      // Third argument carries the source run; absent here, so the re-run has no target Bot.
      expect(deps.triggerRun).toHaveBeenLastCalledWith(spec.id, "operator", null);
    }
    expect(deps.gitConfig).toEqual(fallbackGitConfig);
    expect(deps.gitAuthor).toEqual(fallbackAuthor);
  });

  it("uses fallbacks after the DB row is disabled on the same router", async () => {
    await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "https://git.example.com/database.git"' });
    const router = createAutoHealRouter(deps);
    expect((await post(router, endpoint, requestBody)).status).toBe(200);
    await repo.update("git", { enabled: 0 });
    expect((await post(router, endpoint, requestBody)).status).toBe(200);
    expect(gitClient.cloneClawflows).toHaveBeenNthCalledWith(2, expect.any(String), fallbackGitConfig);
  });

  it.each(["absent", "whitespace"])("returns 503 before any side effects for %s remote settings", async (state) => {
    deps.gitConfig.remoteUrl = " \t\n ";
    if (state === "whitespace") {
      await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "  "' });
    }
    const save = vi.spyOn(AutoHealSaveService.prototype, "save");
    const deploy = vi.spyOn(AutoHealDeployService.prototype, "deploy");
    const write = vi.spyOn(db, "exec");
    const transaction = vi.spyOn(db, "transaction");
    const router = createAutoHealRouter(deps);
    const result = await post(router, endpoint, requestBody);
    expect(result).toEqual({ status: 503, body: {
      error: "Service Unavailable",
      message: expect.stringMatching(/cm_app_config.*git\.remoteUrl.*AUTO_HEAL_CLAWFLOWS_REMOTE_URL.*autoHeal\.clawflowsRemoteUrl/),
    } });
    expect(save).not.toHaveBeenCalled();
    expect(deploy).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
    expect(transaction).not.toHaveBeenCalled();
    expect(gitClient.createTempRepo).not.toHaveBeenCalled();
    expect(gitClient.cloneClawflows).not.toHaveBeenCalled();
    expect(gitClient.writeWorkflowSpecToRepo).not.toHaveBeenCalled();
    expect(gitClient.ensurePackManifest).not.toHaveBeenCalled();
    expect(gitClient.commitAndPushSaved).not.toHaveBeenCalled();
    expect(gitClient.commitAndPushDeploy).not.toHaveBeenCalled();
    expect(deps.triggerRun).not.toHaveBeenCalled();
    expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]);
    expect((await deps.workflowSpecRepo.findByWorkflowId(spec.id))?.spec_json).toBe(JSON.stringify(spec));
  });

  it("recovers from missing remote settings on the next request without rebuilding the router", async () => {
    deps.gitConfig.remoteUrl = "";
    const router = createAutoHealRouter(deps);
    expect((await post(router, endpoint, requestBody)).status).toBe(503);
    await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "https://git.example.com/configured.git"' });
    expect((await post(router, endpoint, requestBody)).status).toBe(200);
    expect(gitClient.cloneClawflows).toHaveBeenCalledOnce();
    expect(gitClient.cloneClawflows).toHaveBeenCalledWith(expect.any(String), {
      ...fallbackGitConfig, remoteUrl: "https://git.example.com/configured.git",
    });
  });

  it("handles query failure inside the route without invoking either service", async () => {
    vi.spyOn(db, "query").mockRejectedValue(new Error("database unavailable"));
    const save = vi.spyOn(AutoHealSaveService.prototype, "save");
    const deploy = vi.spyOn(AutoHealDeployService.prototype, "deploy");
    const transaction = vi.spyOn(db, "transaction");
    const write = vi.spyOn(db, "exec");
    const router = createAutoHealRouter(deps);
    expect(await post(router, endpoint, requestBody)).toEqual({
      status: 500, body: { error: "Internal Server Error", message: "database unavailable" },
    });
    expect(save).not.toHaveBeenCalled();
    expect(deploy).not.toHaveBeenCalled();
    expect(transaction).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled();
    expect(gitClient.createTempRepo).not.toHaveBeenCalled();
    expect(deps.triggerRun).not.toHaveBeenCalled();
  });

  it("preserves basic request validation before querying Git configuration", async () => {
    const query = vi.spyOn(db, "query");
    const router = createAutoHealRouter(deps);
    expect(await post(router, endpoint, {})).toMatchObject({ status: 400, body: { error: "Bad Request" } });
    expect(query).not.toHaveBeenCalled();
    expect(gitClient.createTempRepo).not.toHaveBeenCalled();
  });
});
