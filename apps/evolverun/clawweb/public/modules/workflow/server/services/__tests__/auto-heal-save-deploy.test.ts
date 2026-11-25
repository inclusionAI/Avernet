// @vitest-environment node
/** Real SQLite without an operation journal and local Git: DB saves are drafts, deploys publish history. */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { parse, stringify } from "yaml";
import { tmpdir } from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db/sqlite-database";
import { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { AutoHealSaveService } from "../auto-heal-save-service.js";
import { AutoHealDeployService } from "../auto-heal-deploy-service.js";
import * as gitClient from "../clawflows-git-client.js";
import * as deployGit from "../auto-heal-deploy-git.js";
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

function createRemote(_saved: boolean) {
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
  git(work, "add", "."); git(work, "commit", "-m", "initial pack");
  const candidateCommit = git(work, "rev-parse", "HEAD");
  git(work, "remote", "add", "origin", remote);
  git(work, "push", "origin", "HEAD:refs/heads/main", `HEAD:refs/heads/wf/wf-test`);
  return { root, remote, work, candidateCommit,
    config: { remoteUrl: remote, username: gitAuthor.name, email: gitAuthor.email, token: "" } };
}

async function repositories(ctx: ReturnType<typeof createTestDb>) {
  const history = new WorkflowDeployHistoryRepository(ctx.db);
  const specs = new WorkflowSpecRepository(ctx.db, history);
  const permissions = new BotWorkflowPermissionRepository(ctx.db);
  await specs.upsert("wf-test", "pack-1", JSON.stringify(TEST_SPEC), "user-1");
  await permissions.upsert({ bot_id: null, bot_owner_id: "user-1", workflow_id: "wf-test",
    can_view: 1, can_execute: 1, can_edit: 1 });
  return { history, specs, permissions };
}

describe("AutoHealSaveService", () => {
  let ctx: ReturnType<typeof createTestDb>, remote: ReturnType<typeof createRemote>;
  let repos: Awaited<ReturnType<typeof repositories>>;
  const service = () => new AutoHealSaveService(ctx.db, repos.specs, repos.history, repos.permissions, remote.config, gitAuthor);
  const input = { workflowId: "wf-test", actorId: "user-1", packId: "pack-1",
    requestId: "save-request-001", spec: { ...TEST_SPEC, title: "Edited workflow" } };
  const remoteGit = (...args: string[]) => git(remote.root, "--git-dir", remote.remote, ...args);
  beforeEach(async () => { ctx = createTestDb(); repos = await repositories(ctx); remote = createRemote(false); });
  afterEach(async () => { vi.restoreAllMocks(); await ctx.db.close(); rmSync(remote.root, { recursive: true, force: true }); });

  it("denies save without edit permission before changing DB or invoking Git", async () => {
    const before = await repos.specs.findByWorkflowId("wf-test");
    const clone = vi.spyOn(gitClient, "initClawflowsReadRepo");
    await expect(service().save({ ...input, actorId: "viewer" })).rejects.toMatchObject({ status: 403, code: "FORBIDDEN" });
    expect(await repos.specs.findByWorkflowId("wf-test")).toEqual(before);
    expect(clone).not.toHaveBeenCalled();
    expect(ctx.raw.prepare("SELECT * FROM workflow_deploy_history").all()).toEqual([]);
  });

  it("saves the database draft and wf without creating release history", async () => {
    const saved = await service().save(input);
    expect(saved).toEqual({ ok: true, saved: true, workflowId: "wf-test", packId: "pack-1",
      branch: "wf/wf-test", commit: expect.stringMatching(/^[a-f0-9]{40}$/) });
    expect(remoteGit("branch", "--list", "saved/wf-test")).toBe("");
    const savedHead = remoteGit("rev-parse", "refs/heads/wf/wf-test");
    expect(savedHead).toBe(saved.commit);
    expect(savedHead).not.toBe(remote.candidateCommit);
    expect(parse(remoteGit("show", `${savedHead}:workflows/wf-test.yaml`)).title).toBe(input.spec.title);
    expect(remoteGit("tag", "-l")).toBe("");
    const row = await repos.specs.findByWorkflowId("wf-test");
    expect(row).toMatchObject({ pack_id: "pack-1", owner_id: "user-1" });
    expect(JSON.parse(row!.spec_json).title).toBe(input.spec.title);
    expect(ctx.raw.prepare("SELECT * FROM workflow_deploy_history").all()).toEqual([]);
  });

  it("rejects Git preconditions and duplicate create-only identity without writing", async () => {
    const before = await repos.specs.findByWorkflowId("wf-test");
    await expect(service().save({ ...input, sourceCommit: remote.candidateCommit })).rejects.toMatchObject({ code: "UNSUPPORTED_GIT_PRECONDITION" });
    await expect(service().save({ ...input, createOnly: true })).rejects.toMatchObject({ code: "WORKFLOW_ALREADY_EXISTS" });
    expect(await repos.specs.findByWorkflowId("wf-test")).toEqual(before);
    expect(remoteGit("tag", "-l")).toBe("");
  });

  it("rejects a workflow ID mismatch before saving", async () => {
    const before = await repos.specs.findByWorkflowId("wf-test");
    await expect(service().save({ ...input, spec: { ...input.spec, id: "another-workflow" } }))
      .rejects.toMatchObject({ status: 400, code: "WORKFLOW_ID_MISMATCH" });
    expect(await repos.specs.findByWorkflowId("wf-test")).toEqual(before);
    expect(remoteGit("tag", "-l")).toBe("");
  });

  it("reports an ignored draft write and leaves the database and Git unchanged", async () => {
    const before = await repos.specs.findByWorkflowId("wf-test");
    ctx.raw.exec("CREATE TRIGGER ignore_draft BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(IGNORE); END");
    await expect(service().save(input)).rejects.toThrow("Workflow update failed");
    expect(await repos.specs.findByWorkflowId("wf-test")).toEqual(before);
    expect(remoteGit("branch", "--list", "saved/wf-test")).toBe("");
    expect(ctx.raw.prepare("SELECT * FROM workflow_deploy_history").all()).toEqual([]);
  });
});

describe("AutoHealDeployService", () => {
  let ctx: ReturnType<typeof createTestDb>, remote: ReturnType<typeof createRemote>;
  let repos: Awaited<ReturnType<typeof repositories>>, triggerRun: ReturnType<typeof vi.fn>;
  const service = () => new AutoHealDeployService(ctx.db, repos.specs, repos.history, repos.permissions, remote.config, gitAuthor, triggerRun);
  const input = () => ({ workflowId: "wf-test", candidateCommit: remote.candidateCommit, actorId: "user-1" });
  const remoteGit = (...args: string[]) => git(remote.root, "--git-dir", remote.remote, ...args);
  const historyRows = () => ctx.raw.prepare("SELECT deploy_number, action, is_active, version FROM workflow_deploy_history ORDER BY deploy_number").all();
  const addExternalTag = () => {
    git(remote.work, "tag", "-a", "deploy/wf-test/#5", "-m", "external deployment");
    git(remote.work, "push", "origin", "refs/tags/deploy/wf-test/#5");
  };
  const activateProduction = async (deployNumber: number, version = 1) => {
    const tagName = `deploy/wf-test/#${deployNumber}`;
    git(remote.work, "tag", "-a", tagName, remote.candidateCommit, "-m", "production baseline");
    git(remote.work, "push", "origin", `refs/tags/${tagName}`);
    await repos.history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber, version,
      tagName, action: "deploy", specJson: JSON.stringify(TEST_SPEC), isActive: true });
  };
  beforeEach(async () => {
    ctx = createTestDb(); repos = await repositories(ctx); remote = createRemote(true);
    triggerRun = vi.fn().mockResolvedValue("message-123");
  });
  afterEach(async () => { vi.restoreAllMocks(); await ctx.db.close(); rmSync(remote.root, { recursive: true, force: true }); });

  it("requires its own wf save baseline and rejects an unproven candidate", async () => {
    git(remote.work, "push", "origin", "HEAD:refs/heads/saved/wf-test", ":refs/heads/wf/wf-test");
    await expect(service().deploy({ workflowId: "wf-test", actorId: "user-1" })).rejects.toMatchObject({ status: 409, code: "SAVE_REQUIRED" });
    git(remote.work, "push", "origin", "HEAD:refs/heads/wf/wf-test");
    await expect(service().deploy({ ...input(), candidateCommit: "a".repeat(40) })).rejects.toMatchObject({ status: 409, code: "CANDIDATE_NOT_SAVED" });
    expect(historyRows()).toEqual([]); expect(remoteGit("tag", "-l")).toBe("");
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("rejects a mismatched Pack before publishing", async () => {
    await expect(service().deploy({ ...input(), packId: "other-pack" })).rejects.toMatchObject({ status: 409, code: "PACK_ID_MISMATCH" });
    expect(historyRows()).toEqual([]); expect(remoteGit("tag", "-l")).toBe("");
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("deploys the saved Pack exactly and triggers rerun only after publication", async () => {
    const saver = new AutoHealSaveService(ctx.db, repos.specs, repos.history, repos.permissions, remote.config, gitAuthor);
    await saver.save({ workflowId: "wf-test", actorId: "user-1", spec: { ...TEST_SPEC, title: "DB draft" } });
    const savedHead = remoteGit("rev-parse", "refs/heads/wf/wf-test");
    triggerRun.mockImplementationOnce(async () => {
      expect((await repos.history.findActiveByWorkflowId("wf-test"))?.deploy_number).toBe(1);
      expect((await repos.specs.findByWorkflowId("wf-test"))?.version).toBe(1);
      expect(remoteGit("rev-parse", "refs/tags/deploy/wf-test/#1^{commit}")).toBe(remoteGit("rev-parse", "refs/heads/wf/wf-test"));
      return "message-123";
    });
    const result = await service().deploy({ ...input(), candidateCommit: savedHead, packId: "pack-1" });
    expect(result).toMatchObject({ deployed: true, candidateCommit: savedHead, commit: savedHead, deployNumber: 1, version: 1, tagName: "deploy/wf-test/#1", messageId: "message-123" });
    expect(historyRows()).toEqual([{ deploy_number: 1, action: "deploy", is_active: 1, version: 1 }]);
    expect(remoteGit("rev-parse", "refs/heads/wf/wf-test")).toBe(savedHead);
    expect(parse(remoteGit("show", `${result.commit}:workflows/wf-test.yaml`)).title).toBe("DB draft");
    expect(remoteGit("show", `${result.commit}:scripts/run.sh`)).toBe("saved script");
    expect(remoteGit("show", `${result.commit}:asset.txt`)).toBe("saved asset");
    expect(parse(JSON.parse((await repos.history.findByVersion("wf-test", 1))!.spec_json).content).title).toBe("DB draft");
    expect(triggerRun).toHaveBeenCalledWith("wf-test", "user-1", null);
  });

  it("uses its existing wf save baseline without consulting saved refs", async () => {
    const result = await service().deploy({ workflowId: "wf-test", actorId: "user-1", autoRun: false });
    expect(result.candidateCommit).toBe(remote.candidateCommit);
    expect(remoteGit("show", `${result.commit}:scripts/run.sh`)).toBe("saved script");
    expect(remoteGit("branch", "--list", "saved/wf-test")).toBe("");
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("reports uncertain push without recording formal history or invoking rerun", async () => {
    await activateProduction(9);
    vi.spyOn(deployGit, "pushPreparedDeployment").mockRejectedValueOnce(new Error("git push failed"));
    await expect(service().deploy({ ...input(), requestId: "deploy-unknown-01" }))
      .rejects.toMatchObject({ status: 503, code: "DEPLOY_OUTCOME_UNKNOWN", requestId: "deploy-unknown-01" });
    expect(historyRows()).toEqual([{ deploy_number: 9, action: "deploy", is_active: 1, version: 1 }]);
    expect((await repos.history.findActiveByWorkflowId("wf-test"))?.deploy_number).toBe(9);
    expect(remoteGit("tag", "-l")).toBe("deploy/wf-test/#9"); expect(triggerRun).not.toHaveBeenCalled();
  });

  it("exposes lost push acknowledgment without blindly retrying", async () => {
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, "pushPreparedDeployment").mockImplementationOnce(async (...args) => {
      await realPush(...args);
      throw new Error("push acknowledgment lost");
    });
    const request = { ...input(), requestId: "lost-acknowledgment" };
    await expect(service().deploy(request)).rejects.toMatchObject({ status: 503, code: "DEPLOY_OUTCOME_UNKNOWN" });
    expect(remoteGit("tag", "-l")).toBe("deploy/wf-test/#1");
    expect(historyRows()).toEqual([]); expect(triggerRun).not.toHaveBeenCalled();
    expect(remoteGit("rev-parse", "refs/tags/deploy/wf-test/#1^{commit}")).toBe(remote.candidateCommit);
    expect(remoteGit("rev-parse", "refs/heads/wf/wf-test")).toBe(remote.candidateCommit);
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it.each(["history", "spec"])("reports ignored %s completion write after Git push and rolls back DB publication", async target => {
    await activateProduction(1);
    const before = await repos.specs.findByWorkflowId("wf-test");
    const trigger = target === "history" ? "BEFORE UPDATE ON workflow_deploy_history WHEN NEW.is_active = 1" : "BEFORE UPDATE ON workflow_specs";
    ctx.raw.exec(`CREATE TRIGGER ignore_write ${trigger} BEGIN SELECT RAISE(IGNORE); END`);
    await expect(service().deploy({ ...input(), requestId: "deploy-db-failure" }))
      .rejects.toMatchObject({ status: 503, code: "DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN", requestId: "deploy-db-failure" });
    expect(historyRows()).toEqual([{ deploy_number: 1, action: "deploy", is_active: 1, version: 1 }]);
    expect(await repos.specs.findByWorkflowId("wf-test")).toEqual(before);
    expect(remoteGit("rev-parse", "refs/tags/deploy/wf-test/#2^{commit}")).toBe(remoteGit("rev-parse", "refs/heads/wf/wf-test"));
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("does not overwrite a concurrent DB draft after Git publication", async () => {
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, "pushPreparedDeployment").mockImplementationOnce(async (...args) => {
      await realPush(...args);
      ctx.raw.prepare("UPDATE workflow_specs SET spec_json = ? WHERE workflow_id = ?").run(JSON.stringify({ ...TEST_SPEC, title: "Concurrent draft" }), "wf-test");
    });
    await expect(service().deploy(input())).rejects.toMatchObject({ code: "DEPLOY_GIT_SUCCEEDED_DB_OUTCOME_UNKNOWN", cause: { code: "BASELINE_STALE" } });
    expect(JSON.parse((await repos.specs.findByWorkflowId("wf-test"))!.spec_json).title).toBe("Concurrent draft");
    expect(historyRows()).toEqual([]); expect(remoteGit("tag", "-l")).toBe("deploy/wf-test/#1");
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("rolls back when setActive's write is ignored", async () => {
    await repos.history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 1, version: 1, tagName: "one", action: "deploy", specJson: "{}", isActive: true });
    await repos.history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 2, version: 2, tagName: "two", action: "deploy", specJson: "{}" });
    ctx.raw.exec("CREATE TRIGGER ignore_activation BEFORE UPDATE ON workflow_deploy_history WHEN NEW.is_active = 1 BEGIN SELECT RAISE(IGNORE); END");
    await expect(repos.history.setActive("wf-test", 2)).rejects.toThrow();
    expect((await repos.history.findActiveByWorkflowId("wf-test"))?.deploy_number).toBe(1);
  });

  it("forwards the source run so the rerun targets its own Bot", async () => {
    await service().deploy({ ...input(), sourceFlowId: "60c5aff6-2a07-42ac-8d93-dbd8bbbaabb6" });
    expect(triggerRun).toHaveBeenCalledWith("wf-test", "user-1", "60c5aff6-2a07-42ac-8d93-dbd8bbbaabb6");
  });

  it.each([{ can_execute: 1, can_edit: 0 }, { can_execute: 0, can_edit: 1 }])("allows deploy with either execute or edit permission: %j", async permissions => {
    await repos.permissions.upsert({ bot_id: null, bot_owner_id: "user-2", workflow_id: "wf-test", can_view: 1, ...permissions });
    expect((await service().deploy({ ...input(), actorId: "user-2" })).deployed).toBe(true);
  });

  it("denies deploy when actor lacks execute and edit permission before Git", async () => {
    await repos.permissions.upsert({ bot_id: null, bot_owner_id: "user-3", workflow_id: "wf-test", can_view: 1, can_execute: 0, can_edit: 0 });
    const clone = vi.spyOn(gitClient, "initClawflowsReadRepo");
    await expect(service().deploy({ ...input(), actorId: "user-3" })).rejects.toMatchObject({ status: 403, code: "FORBIDDEN" });
    expect(clone).not.toHaveBeenCalled(); expect(historyRows()).toEqual([]);
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("does not undo a successful release if the optional rerun fails", async () => {
    triggerRun.mockRejectedValueOnce(new Error("bot unavailable"));
    expect(await service().deploy(input())).toMatchObject({ deployed: true, messageId: null });
    expect(historyRows()).toEqual([{ deploy_number: 1, action: "deploy", is_active: 1, version: 1 }]);
  });

  it("does not deduplicate a repeated request ID without a journal", async () => {
    const push = vi.spyOn(deployGit, "pushPreparedDeployment");
    const request = { ...input(), requestId: "deploy-repeated" };
    const first = await service().deploy(request), second = await service().deploy(request);
    expect([first.deployNumber, second.deployNumber]).toEqual([1, 2]);
    expect(first.commit).toBe(second.commit);
    expect(push).toHaveBeenCalledTimes(2); expect(triggerRun).toHaveBeenCalledTimes(2);
    expect(historyRows()).toEqual([
      { deploy_number: 1, action: "deploy", is_active: 0, version: 1 },
      { deploy_number: 2, action: "deploy", is_active: 1, version: 2 },
    ]);
  });

  it("numbers the Git tag above external tags without adding placeholder history", async () => {
    addExternalTag();
    const prepare = vi.spyOn(deployGit, "prepareDeployCandidate"), push = vi.spyOn(deployGit, "pushPreparedDeployment");
    const result = await service().deploy(input());
    expect(prepare).toHaveBeenCalledWith(expect.any(String), "wf-test", expect.stringMatching(/^[a-f0-9]{40}$/), "", 6, 1, gitAuthor);
    expect(push).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ deployNumber: 6, tagName: "deploy/wf-test/#6" }));
    expect(result).toMatchObject({ deployNumber: 6, tagName: "deploy/wf-test/#6" });
    expect(historyRows()).toEqual([{ deploy_number: 6, action: "deploy", is_active: 1, version: 1 }]);
    expect(await repos.history.setActive("wf-test", result.version)).toBe(true);
  });

  it("publishes the frozen candidate without overwriting a concurrently saved wf", async () => {
    writeFileSync(path.join(remote.work, "scripts/run.sh"), "newer saved script\n");
    git(remote.work, "add", "."); git(remote.work, "commit", "-m", "concurrent wf save");
    const newer = git(remote.work, "rev-parse", "HEAD");
    const realPush = deployGit.pushPreparedDeployment;
    vi.spyOn(deployGit, "pushPreparedDeployment").mockImplementationOnce(async (...args) => {
      git(remote.work, "push", "origin", `${newer}:refs/heads/wf/wf-test`);
      return realPush(...args);
    });
    expect(await service().deploy(input())).toMatchObject({ deployed: true, commit: remote.candidateCommit });
    expect(remoteGit("rev-parse", "refs/heads/wf/wf-test")).toBe(newer);
    expect(remoteGit("rev-parse", "refs/tags/deploy/wf-test/#1^{commit}")).toBe(remote.candidateCommit);
    expect(historyRows()).toEqual([{ deploy_number: 1, action: "deploy", is_active: 1, version: 1 }]);
    expect(triggerRun).toHaveBeenCalledTimes(1);
  });

});

describe("WorkflowDeployHistoryRepository.setActive contract", () => {
  let ctx: ReturnType<typeof createTestDb>, history: WorkflowDeployHistoryRepository;
  beforeEach(() => { ctx = createTestDb(); history = new WorkflowDeployHistoryRepository(ctx.db); });
  afterEach(async () => { vi.restoreAllMocks(); await ctx.db.close(); });
  const rows = () => ctx.raw.prepare("SELECT * FROM workflow_deploy_history ORDER BY id").all();

  it("rolls back all deactivations when one previous active row ignores the write", async () => {
    for (const deployNumber of [1, 2, 3]) await history.insert({
      packId: "pack-1", workflowId: "wf-test", deployNumber, version: deployNumber,
      tagName: `deploy/wf-test/#${deployNumber}`, action: "deploy", specJson: "{}", isActive: deployNumber < 3,
    });
    const before = rows();
    ctx.raw.exec(`CREATE TRIGGER ignore_deactivation BEFORE UPDATE ON workflow_deploy_history
      WHEN OLD.deploy_number = 2 AND NEW.is_active = 0 BEGIN SELECT RAISE(IGNORE); END`);
    await expect(history.setActive("wf-test", 3)).rejects.toThrow("Deployment deactivation failed");
    expect(rows()).toEqual(before);
    expect(await history.findActiveByWorkflowId("wf-test")).toMatchObject({ deploy_number: 2 });
  });

  it("selects one latest deployment by number then id, preserving existing action and workflow scope", async () => {
    await history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 1, version: 1,
      tagName: "previous", action: "deploy", specJson: "{}", isActive: true });
    for (const item of [
      { packId: "pack-1", deployNumber: 5, action: "deploy" },
      { packId: "pack-1", deployNumber: 7, action: "rollback" },
      { packId: "pack-2", deployNumber: 7, action: "edit" },
      { packId: "pack-1", deployNumber: 6, action: "pull" },
    ] as const) await history.insert({ ...item, workflowId: "wf-test", version: 3, tagName: "existing-action", specJson: "{}" });
    await history.insert({ packId: "pack-1", workflowId: "another-workflow", deployNumber: 99, version: 3,
      tagName: "unrelated", action: "migration", specJson: "{}", isActive: true });
    const before = rows() as Array<{ id: number; workflow_id: string; action: string; is_active: number }>;
    const expected = before.map(row => ({ ...row, is_active: row.workflow_id === "wf-test" ? Number(row.action === "edit") : row.is_active }));
    expect(await history.setActive("wf-test", 3)).toBe(true);
    expect(rows()).toEqual(expected);
    // Re-selecting the already-active version must still succeed without adding history.
    expect(await history.setActive("wf-test", 3)).toBe(true);
    expect(rows()).toEqual(expected);
  });

  it("returns false for a missing target version or workflow without changing active history", async () => {
    await history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 1, version: 1,
      tagName: "existing", action: "deploy", specJson: "{}", isActive: true });
    const before = rows();
    const write = vi.spyOn(SqliteDatabase.prototype, "exec");
    expect(await history.setActive("wf-test", 999)).toBe(false);
    expect(await history.setActive("missing-workflow", 1)).toBe(false);
    expect(write).not.toHaveBeenCalled(); expect(rows()).toEqual(before);
  });
});

describe("createTempRepo", () => {
  let tempDir: string;
  beforeEach(() => { tempDir = mkdtempSync(path.join(tmpdir(), "cw-autoheal-test-")); vi.stubEnv("CLAWWEB_TEMP_DIR", tempDir); });
  afterEach(() => { vi.unstubAllEnvs(); rmSync(tempDir, { recursive: true, force: true }); });
  it("creates a temp directory under CLAWWEB_TEMP_DIR", async () => {
    const repo = await gitClient.createTempRepo();
    expect(repo.startsWith(tempDir)).toBe(true); expect(statSync(repo).isDirectory()).toBe(true);
    rmSync(repo, { recursive: true, force: true });
  });
  it("falls back to TMPDIR when CLAWWEB_TEMP_DIR is absent", async () => {
    vi.unstubAllEnvs(); vi.stubEnv("TMPDIR", tempDir);
    const repo = await gitClient.createTempRepo();
    expect(repo.startsWith(tempDir)).toBe(true);
    rmSync(repo, { recursive: true, force: true });
  });
  it("throws a clear error when the configured temp dir parent is missing", async () => {
    vi.stubEnv("CLAWWEB_TEMP_DIR", path.join(tempDir, "missing", "deep"));
    await expect(gitClient.createTempRepo()).rejects.toThrow("临时目录不可用或配置无效");
  });
});
