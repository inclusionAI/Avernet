// @vitest-environment node
import { afterEach, beforeEach, describe, expect, it, vi, type MockInstance } from "vitest";
import type { Request, Response, Router } from "express";
import { stringify as stringifyYaml } from "yaml";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { packOperationDdl } from "@avernet/clawweb-shared/server/schema-pack-operations";
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
import * as preparedGit from "../../services/pack-git-prepared.js";
import type { WorkflowSpec } from "../../workflow.js";
import { createAutoHealRouter, type AutoHealRouteDependencies } from "../auto-heal.js";

const candidate = "a".repeat(40), savedCommit = "b".repeat(40), releaseCommit = "c".repeat(40);
const spec: WorkflowSpec = {
  id: "wf-git-settings", version: "1.0.0", title: "Git settings regression", nodes: [
    { id: "start", type: "start", title: "Start", config: {}, executor: { type: "start" }, dependsOn: [] },
    { id: "end", type: "end", title: "End", config: {}, executor: { type: "end" }, dependsOn: ["start"] },
  ],
};
const metadata = { packId: "pack-1", packPath: "", workflowFile: `workflows/${spec.id}.yaml`, content: stringifyYaml(spec),
  manifestContent: stringifyYaml({ id: "pack-1", workflows: [{ id: spec.id, file: `workflows/${spec.id}.yaml` }] }) };

// Exercise the real Express handlers and real SQL journal without opening a listening socket.
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

describe.each(["save", "deploy"] as const)("AutoHeal /%s Git settings", endpoint => {
  let db: IDatabase, repo: AppConfigRepository, deps: AutoHealRouteDependencies;
  let pushSaved: ReturnType<typeof vi.fn>;
  const requestBody = { workflowId: spec.id, spec, packId: "pack-1", autoRun: true, candidateCommit: candidate };
  const configQueries = (query: MockInstance<IDatabase["query"]>) => query.mock.calls.filter(([sql]) => sql.includes("cm_app_config"));
  const writes = () => endpoint === "save" ? vi.mocked(specGit.withPreparedWorkflowSpec).mock.calls.map(([input]) => input.config)
    : vi.mocked(gitClient.cloneClawflows).mock.calls.map(([, config]) => config);
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
    for (const ddl of packOperationDdl) await db.exec(ddl);
    const deployHistoryRepo = new WorkflowDeployHistoryRepository(db);
    const workflowSpecRepo = new WorkflowSpecRepository(db, deployHistoryRepo);
    const botPermRepo = new BotWorkflowPermissionRepository(db);
    await workflowSpecRepo.upsert(spec.id, "pack-1", JSON.stringify(spec), "operator");
    await botPermRepo.upsert({ bot_id: null, bot_owner_id: "operator", workflow_id: spec.id, can_view: 1, can_execute: 1, can_edit: 1 });
    deps = { resolvePrincipal: async () => ({ actorId: "operator" }), db, workflowSpecRepo, deployHistoryRepo, botPermRepo, gitConfig: { ...fallbackGitConfig },
      gitAuthor: { ...fallbackAuthor }, triggerRun: vi.fn().mockResolvedValue("run-1") };
    pushSaved = vi.fn().mockResolvedValue({ branch: `saved/${spec.id}`, commit: savedCommit });
    // Only replace Git preparation/publication. Permissions, journal transactions and completion are real.
    vi.spyOn(specGit, "withPreparedWorkflowSpec").mockImplementation(async (input, callback) => callback({
      ...metadata, content: stringifyYaml(input.spec), workflowId: input.workflowId, branch: `saved/${input.workflowId}`,
      commit: savedCommit, sourceVersion: "current", sourceCommit: candidate, expectedSavedHead: null,
      bundleBase64: Buffer.from("mocked Git bundle, never replayed in this fixture").toString("base64"),
    }, pushSaved));
    vi.spyOn(gitClient, "createTempRepo").mockResolvedValue("/tmp/auto-heal-git-settings-fixture");
    vi.spyOn(gitClient, "removeTempRepo").mockResolvedValue();
    vi.spyOn(gitClient, "cloneClawflows").mockResolvedValue();
    vi.spyOn(gitClient, "getMaxDeployNumberFromGitTags").mockResolvedValue(0);
    vi.spyOn(preparedGit, "readPreparedRemoteRefs").mockResolvedValue(new Map([
      [`refs/heads/saved/${spec.id}`, candidate], [`refs/heads/wf/${spec.id}`, null],
    ]));
    vi.spyOn(deployGit, "prepareDeployCandidate").mockImplementation(async (_dir, workflowId, candidateCommit, previousCommit, deployNumber, version) => ({
      ...metadata, workflowId, candidateCommit, previousCommit, deployNumber, version, branch: `wf/${workflowId}`,
      tagName: `deploy/${workflowId}/#${deployNumber}`, commit: releaseCommit, releaseCommit, tagObject: "d".repeat(40),
      bundleBase64: Buffer.from("mocked deployment bundle").toString("base64"),
    }));
    vi.spyOn(deployGit, "pushPreparedDeployment").mockImplementation(async (_dir, prepared) => ({
      branch: prepared.branch, tagName: prepared.tagName, commit: prepared.commit, deployNumber: prepared.deployNumber,
    }));
  });
  afterEach(async () => { vi.restoreAllMocks(); await db.close(); });

  it("rejects unverified header identities when no trusted principal is supplied", async () => {
    deps.resolvePrincipal = undefined;
    const response = await post(createAutoHealRouter(deps), endpoint, { ...requestBody, createOnly: true, workflowId: "new-workflow" });
    expect(response.status).toBe(401);
    expect(writes()).toHaveLength(0);
    expect(await db.query("SELECT workflow_id FROM workflow_specs WHERE workflow_id = ?", ["new-workflow"])).toEqual([]);
  });

  it("never treats a Git process exit status as an HTTP informational status", async () => {
    if (endpoint === "save") vi.mocked(specGit.withPreparedWorkflowSpec).mockRejectedValueOnce(Object.assign(new Error("git failed"), { status: 128 }));
    else vi.mocked(gitClient.cloneClawflows).mockRejectedValueOnce(Object.assign(new Error("git failed"), { status: 128 }));
    expect((await post(createAutoHealRouter(deps), endpoint, requestBody)).status).toBe(500);
  });

  it("reloads DB URL, credentials, and author between two requests on the same router", async () => {
    await repo.create({ config_key: "git", config_yaml: stringifyYaml({
      remoteUrl: " https://git.example.com/first.git ", username: "first-user", email: "first@example.invalid",
      token: " first-token ", authorName: "First Author", authorEmail: "first@example.invalid",
    }) });
    const query = vi.spyOn(db, "query"), router = createAutoHealRouter(deps);
    expect(query).not.toHaveBeenCalled();
    expect(await post(router, endpoint, requestBody)).toMatchObject({ status: 200, body: { ok: true } });
    expect(configQueries(query)).toHaveLength(1);
    await repo.update("git", { config_yaml: stringifyYaml({
      remoteUrl: "https://git.example.com/second.git", username: "second-user", email: "second@example.invalid",
      token: " second-token ", authorName: "Second Author", authorEmail: "second@example.invalid",
    }) });
    query.mockClear();
    expect(await post(router, endpoint, requestBody)).toMatchObject({ status: 200, body: { ok: true } });
    expect(configQueries(query)).toHaveLength(1);
    expect(writes()).toEqual([
      { remoteUrl: "https://git.example.com/first.git", username: "first-user", email: "first@example.invalid", token: " first-token " },
      { remoteUrl: "https://git.example.com/second.git", username: "second-user", email: "second@example.invalid", token: " second-token " },
    ]);
    expect(authors()).toEqual([{ name: "First Author", email: "first@example.invalid" }, { name: "Second Author", email: "second@example.invalid" }]);
    expect(deps.gitConfig).toEqual(fallbackGitConfig); expect(deps.gitAuthor).toEqual(fallbackAuthor);
    if (endpoint === "save") {
      expect(pushSaved).toHaveBeenCalledTimes(2);
      expect(await db.query("SELECT * FROM workflow_deploy_history")).toEqual([]);
      expect(deployGit.pushPreparedDeployment).not.toHaveBeenCalled(); expect(deps.triggerRun).not.toHaveBeenCalled();
    } else {
      expect(deployGit.pushPreparedDeployment).toHaveBeenCalledTimes(2);
      expect(await db.query("SELECT deploy_number, action, is_active FROM workflow_deploy_history ORDER BY deploy_number"))
        .toEqual([{ deploy_number: 1, action: "deploy", is_active: 0 }, { deploy_number: 2, action: "deploy", is_active: 1 }]);
      expect(deps.triggerRun).toHaveBeenCalledTimes(2);
    }
  });

  it("uses fallbacks after the DB row is disabled on the same router", async () => {
    await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "https://git.example.com/database.git"' });
    const router = createAutoHealRouter(deps);
    expect((await post(router, endpoint, requestBody)).status).toBe(200);
    await repo.update("git", { enabled: 0 });
    expect((await post(router, endpoint, requestBody)).status).toBe(200);
    expect(writes()[1]).toEqual(fallbackGitConfig); expect(authors()[1]).toEqual(fallbackAuthor);
  });

  it.each(["absent", "whitespace"])("returns 503 before any side effects for %s remote settings", async state => {
    deps.gitConfig.remoteUrl = " \t\n ";
    if (state === "whitespace") await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "  "' });
    const save = vi.spyOn(AutoHealSaveService.prototype, "save"), deploy = vi.spyOn(AutoHealDeployService.prototype, "deploy");
    const write = vi.spyOn(db, "exec"), transaction = vi.spyOn(db, "transaction");
    const result = await post(createAutoHealRouter(deps), endpoint, requestBody);
    expect(result.status).toBe(503);
    expect(result.body).toMatchObject(endpoint === "save"
      ? { error: "Save could not be completed", code: "SAVE_FAILED" }
      : { error: "Deployment could not be completed", code: "DEPLOYMENT_FAILED" });
    expect(save).not.toHaveBeenCalled(); expect(deploy).not.toHaveBeenCalled();
    expect(write).not.toHaveBeenCalled(); expect(transaction).not.toHaveBeenCalled();
    expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled(); expect(pushSaved).not.toHaveBeenCalled();
    expect(gitClient.createTempRepo).not.toHaveBeenCalled(); expect(gitClient.cloneClawflows).not.toHaveBeenCalled();
    expect(deployGit.prepareDeployCandidate).not.toHaveBeenCalled(); expect(deployGit.pushPreparedDeployment).not.toHaveBeenCalled();
    expect(deps.triggerRun).not.toHaveBeenCalled();
    expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]);
    expect(await db.query("SELECT request_id FROM clawweb_pack_operations")).toEqual([]);
    expect((await deps.workflowSpecRepo.findByWorkflowId(spec.id))?.spec_json).toBe(JSON.stringify(spec));
  });

  it("recovers from missing remote settings on the next request without rebuilding the router", async () => {
    deps.gitConfig.remoteUrl = "";
    const router = createAutoHealRouter(deps);
    expect((await post(router, endpoint, requestBody)).status).toBe(503);
    await repo.create({ config_key: "git", config_yaml: 'remoteUrl: "https://git.example.com/configured.git"' });
    expect((await post(router, endpoint, requestBody)).status).toBe(200);
    expect(writes()).toEqual([{ ...fallbackGitConfig, remoteUrl: "https://git.example.com/configured.git" }]);
  });

  it("handles query failure inside the route without invoking either service", async () => {
    vi.spyOn(db, "query").mockRejectedValue(new Error("database unavailable"));
    const save = vi.spyOn(AutoHealSaveService.prototype, "save"), deploy = vi.spyOn(AutoHealDeployService.prototype, "deploy");
    const transaction = vi.spyOn(db, "transaction"), write = vi.spyOn(db, "exec");
    const result = await post(createAutoHealRouter(deps), endpoint, requestBody);
    expect(result.status).toBe(500);
    expect(result.body).toMatchObject({ code: endpoint === "save" ? "SAVE_FAILED" : "DEPLOYMENT_FAILED" });
    expect(JSON.stringify(result.body)).not.toContain("database unavailable");
    expect(save).not.toHaveBeenCalled(); expect(deploy).not.toHaveBeenCalled();
    expect(transaction).not.toHaveBeenCalled(); expect(write).not.toHaveBeenCalled();
    expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled();
    expect(gitClient.createTempRepo).not.toHaveBeenCalled(); expect(deps.triggerRun).not.toHaveBeenCalled();
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
    expect((await post(createAutoHealRouter(deps), endpoint, requestBody)).status).toBe(403);
    expect(specGit.withPreparedWorkflowSpec).not.toHaveBeenCalled(); expect(gitClient.cloneClawflows).not.toHaveBeenCalled();
    expect(await db.query("SELECT request_id FROM clawweb_pack_operations")).toEqual([]);
    expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]);
    expect(deps.triggerRun).not.toHaveBeenCalled();
  });

  it("returns explicit unknown and never reruns after a failed Git publication", async () => {
    if (endpoint === "save") pushSaved.mockRejectedValueOnce(new Error("git failed"));
    else vi.mocked(deployGit.pushPreparedDeployment).mockRejectedValueOnce(new Error("git failed"));
    const response = await post(createAutoHealRouter(deps), endpoint, { ...requestBody, requestId: "git-failed-request" });
    expect(response.status).toBe(503);
    expect(response.body).toMatchObject({ code: `${endpoint.toUpperCase()}_OUTCOME_UNKNOWN`, requestId: "git-failed-request" });
    expect(await db.query("SELECT phase, lock_workflow_id FROM clawweb_pack_operations"))
      .toEqual([{ phase: "unknown", lock_workflow_id: (await deps.workflowSpecRepo.findByWorkflowId(spec.id))!.id }]);
    expect(await db.query("SELECT id FROM workflow_deploy_history")).toEqual([]);
    expect((await deps.workflowSpecRepo.findByWorkflowId(spec.id))?.spec_json).toBe(JSON.stringify(spec));
    expect(deps.triggerRun).not.toHaveBeenCalled();
  });

  if (endpoint === "deploy") {
    it.each([
      { workflowId: spec.id, candidateCommit: candidate, packId: "pack-1", autoRun: true },
      { workflowId: spec.id },
    ])("publishes valid explicit and legacy deployment requests instead of disabling the route: %j", async body => {
      const result = await post(createAutoHealRouter(deps), endpoint, body);
      expect(result).toMatchObject({ status: 200, body: { ok: true, deployed: true, candidateCommit: candidate } });
      expect(deployGit.prepareDeployCandidate).toHaveBeenCalledWith(expect.any(String), spec.id, candidate, "", 1, 1, fallbackAuthor);
      expect(deployGit.pushPreparedDeployment).toHaveBeenCalledOnce(); expect(deps.triggerRun).toHaveBeenCalledOnce();
    });

    it("rejects an unproven 64-character candidate without pushing or creating public history", async () => {
      expect(await post(createAutoHealRouter(deps), endpoint, { workflowId: spec.id, candidateCommit: "b".repeat(64) }))
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
