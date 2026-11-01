// @vitest-environment node
/** Real SQLite + migration 149 and local Git: saves are drafts, only completed deploys are history. */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, mkdirSync, rmSync, statSync, writeFileSync } from "node:fs";
import { stringify } from "yaml";
import { tmpdir } from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db/sqlite-database";
import { packOperationDdl } from "@avernet/clawweb-shared/server/schema-pack-operations";
import { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import { PackOperationRepository } from "../../repositories/pack-operation-repository.js";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { AutoHealSaveService } from "../auto-heal-save-service.js";
import { AutoHealDeployService } from "../auto-heal-deploy-service.js";
import * as gitClient from "../clawflows-git-client.js";
import * as deployGit from "../auto-heal-deploy-git.js";
import * as candidateGit from "../pack-candidate-save.js";
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
    CREATE TABLE bot_workflow_permissions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, bot_owner_id TEXT NOT NULL,
      workflow_id TEXT NOT NULL, can_view INTEGER DEFAULT 0, can_execute INTEGER DEFAULT 0,
      can_edit INTEGER DEFAULT 0, env TEXT NOT NULL DEFAULT 'test',
      gmt_create INTEGER NOT NULL DEFAULT 0, gmt_modified INTEGER NOT NULL DEFAULT 0
    );
  `);
  for (const ddl of packOperationDdl) raw.exec(ddl);
  return { db: new SqliteDatabase(raw), raw };
}

function createRemote(saved: boolean) {
  const root = mkdtempSync(path.join(tmpdir(), "autoheal-journal-"));
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

describe("AutoHealSaveService", () => {
  let ctx: ReturnType<typeof createTestDb>, remote: ReturnType<typeof createRemote>;
  let repos: Awaited<ReturnType<typeof repositories>>;
  const service = () => new AutoHealSaveService(ctx.db, repos.specs, repos.history, repos.permissions, remote.config, gitAuthor);
  const input = { workflowId: "wf-test", actorId: "user-1", packId: "pack-1",
    requestId: "save-request-001", spec: { ...TEST_SPEC, title: "Edited workflow" } };
  beforeEach(async () => { ctx = createTestDb(); repos = await repositories(ctx); remote = createRemote(false); });
  afterEach(async () => { vi.restoreAllMocks(); await ctx.db.close(); rmSync(remote.root, { recursive: true, force: true }); });

  it("denies save when the actor lacks edit permission, before Git or journaling", async () => {
    const prepare = vi.spyOn(gitClient, "createTempRepo");
    await expect(service().save({ ...input, actorId: "viewer" })).rejects.toMatchObject({ status: 403, code: "FORBIDDEN" });
    expect(prepare).not.toHaveBeenCalled();
    expect(ctx.raw.prepare("SELECT * FROM clawweb_pack_operations").all()).toEqual([]);
    expect(ctx.raw.prepare("SELECT * FROM workflow_deploy_history").all()).toEqual([]);
  });

  it("saves a valid spec without publishing and preserves inherited scripts and resources", async () => {
    const result = await service().save(input);
    expect(result).toMatchObject({ ok: true, saved: true, workflowId: "wf-test", packId: "pack-1",
      requestId: input.requestId, branch: "saved/wf-test", commit: expect.stringMatching(/^[a-f0-9]{40}$/) });
    expect(git(remote.root, "--git-dir", remote.remote, "rev-parse", "refs/heads/saved/wf-test")).toBe(result.commit);
    expect(git(remote.root, "--git-dir", remote.remote, "rev-parse", "refs/heads/wf/wf-test")).toBe(remote.candidateCommit);
    expect(git(remote.root, "--git-dir", remote.remote, "tag", "-l")).toBe("");
    expect(git(remote.root, "--git-dir", remote.remote, "show", `${result.commit}:scripts/run.sh`)).toBe("saved script");
    expect(git(remote.root, "--git-dir", remote.remote, "show", `${result.commit}:asset.txt`)).toBe("saved asset");
    const row = await repos.specs.findByWorkflowId("wf-test");
    expect(row).toMatchObject({ pack_id: "pack-1", owner_id: "user-1" });
    expect(JSON.parse(row!.spec_json).title).toBe(input.spec.title);
    expect(ctx.raw.prepare("SELECT * FROM workflow_deploy_history").all()).toEqual([]);
    expect(ctx.raw.prepare("SELECT phase, lock_workflow_id FROM clawweb_pack_operations").get())
      .toEqual({ phase: "completed", lock_workflow_id: null });
  });

  it("inherits the saved base rather than reverting to current or the default branch", async () => {
    const first = await service().save(input);
    git(remote.work, "fetch", "origin", "saved/wf-test"); git(remote.work, "checkout", "--detach", "FETCH_HEAD");
    writeFileSync(path.join(remote.work, "asset.txt"), "new draft asset\n");
    git(remote.work, "add", "."); git(remote.work, "commit", "-m", "later draft");
    git(remote.work, "push", "origin", "HEAD:refs/heads/saved/wf-test");
    const next = await service().save({ ...input, requestId: "save-request-002", spec: { ...input.spec, title: "Second edit" } });
    expect(next.commit).not.toBe(first.commit);
    expect(git(remote.root, "--git-dir", remote.remote, "show", `${next.commit}:asset.txt`)).toBe("new draft asset");
    expect(git(remote.root, "--git-dir", remote.remote, "tag", "-l")).toBe("");
  });

  it("keeps a failed Git save private and leaves the database draft unchanged", async () => {
    const before = await repos.specs.findByWorkflowId("wf-test");
    vi.spyOn(candidateGit, "pushPreparedPackCandidate").mockRejectedValueOnce(new Error("git push failed"));
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: "SAVE_OUTCOME_UNKNOWN", requestId: input.requestId });
    expect(await repos.specs.findByWorkflowId("wf-test")).toEqual(before);
    expect(ctx.raw.prepare("SELECT * FROM workflow_deploy_history").all()).toEqual([]);
    expect(ctx.raw.prepare("SELECT phase, lock_workflow_id FROM clawweb_pack_operations").get())
      .toEqual({ phase: "unknown", lock_workflow_id: (await repos.specs.findByWorkflowId("wf-test"))!.id });
    expect(git(remote.root, "--git-dir", remote.remote, "branch", "--list", "saved/wf-test")).toBe("");
  });

  it("returns explicit unknown when Git succeeds but the draft DB update is ignored", async () => {
    const before = await repos.specs.findByWorkflowId("wf-test");
    ctx.raw.exec("CREATE TRIGGER ignore_draft BEFORE UPDATE ON workflow_specs BEGIN SELECT RAISE(IGNORE); END");
    await expect(service().save(input)).rejects.toMatchObject({ status: 503, code: "SAVE_OUTCOME_UNKNOWN", requestId: input.requestId });
    expect(await repos.specs.findByWorkflowId("wf-test")).toEqual(before);
    expect(git(remote.root, "--git-dir", remote.remote, "rev-parse", "refs/heads/saved/wf-test")).not.toBe(remote.candidateCommit);
    expect(ctx.raw.prepare("SELECT * FROM workflow_deploy_history").all()).toEqual([]);
    expect(ctx.raw.prepare("SELECT phase FROM clawweb_pack_operations").get()).toEqual({ phase: "unknown" });
  });
});

describe("AutoHealDeployService", () => {
  let ctx: ReturnType<typeof createTestDb>, remote: ReturnType<typeof createRemote>;
  let repos: Awaited<ReturnType<typeof repositories>>, triggerRun: ReturnType<typeof vi.fn>;
  const service = () => new AutoHealDeployService(ctx.db, repos.specs, repos.history, repos.permissions, remote.config, gitAuthor, triggerRun);
  const input = () => ({ workflowId: "wf-test", candidateCommit: remote.candidateCommit, actorId: "user-1" });
  const remoteGit = (...args: string[]) => git(remote.root, "--git-dir", remote.remote, ...args);
  const historyRows = () => ctx.raw.prepare("SELECT deploy_number, action, is_active, version FROM workflow_deploy_history ORDER BY deploy_number").all();
  const journal = () => ctx.raw.prepare("SELECT phase, lock_workflow_id FROM clawweb_pack_operations ORDER BY created_at_ms").all();
  const addExternalTag = () => {
    git(remote.work, "tag", "-a", "deploy/wf-test/#5", "-m", "external deployment");
    git(remote.work, "push", "origin", "refs/tags/deploy/wf-test/#5");
  };
  beforeEach(async () => {
    ctx = createTestDb(); repos = await repositories(ctx); remote = createRemote(true);
    triggerRun = vi.fn().mockResolvedValue("message-123");
  });
  afterEach(async () => { vi.restoreAllMocks(); await ctx.db.close(); rmSync(remote.root, { recursive: true, force: true }); });

  it("requires a saved candidate and rejects commits without saved provenance before publishing", async () => {
    git(remote.work, "push", "origin", ":refs/heads/saved/wf-test");
    await expect(service().deploy({ workflowId: "wf-test", actorId: "user-1" })).rejects.toMatchObject({ status: 409, code: "SAVE_REQUIRED" });
    git(remote.work, "push", "origin", "HEAD:refs/heads/saved/wf-test");
    await expect(service().deploy({ ...input(), candidateCommit: "a".repeat(40) })).rejects.toMatchObject({ status: 409, code: "CANDIDATE_NOT_SAVED" });
    expect(historyRows()).toEqual([]); expect(remoteGit("tag", "-l")).toBe("");
    expect(journal()).toEqual(Array(2).fill({ phase: "failed", lock_workflow_id: null }));
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("supports legacy requests by capturing saved once instead of publishing mutable DB YAML", async () => {
    await repos.specs.upsert("wf-test", "pack-1", JSON.stringify({ ...TEST_SPEC, title: "Unsaved DB edit" }), "user-1");
    const result = await service().deploy({ workflowId: "wf-test", actorId: "user-1", autoRun: false });
    expect(result.candidateCommit).toBe(remote.candidateCommit);
    expect(remoteGit("rev-parse", "refs/heads/wf/wf-test^{tree}")).toBe(remoteGit("rev-parse", `${remote.candidateCommit}^{tree}`));
    expect(JSON.parse((await repos.history.findByVersion("wf-test", 1))!.spec_json).title).toBe(TEST_SPEC.title);
  });

  it("retains an uncertain Git attempt only in the private journal, never formal history", async () => {
    await repos.history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 9, version: 1,
      tagName: "deploy/wf-test/#9", action: "deploy", specJson: "{}", isActive: true });
    vi.spyOn(deployGit, "pushPreparedDeployment").mockRejectedValueOnce(new Error("git push failed"));
    await expect(service().deploy({ ...input(), requestId: "deploy-unknown-01" }))
      .rejects.toMatchObject({ status: 503, code: "DEPLOY_OUTCOME_UNKNOWN", requestId: "deploy-unknown-01" });
    expect(historyRows()).toEqual([{ deploy_number: 9, action: "deploy", is_active: 1, version: 1 }]);
    expect(journal()).toEqual([{ phase: "unknown", lock_workflow_id: (await repos.specs.findByWorkflowId("wf-test"))!.id }]);
    expect((await repos.history.listHistory("wf-test", 10)).map(row => row.deploy_number)).toEqual([9]);
    expect((await repos.history.listHistory("wf-test", 10, { releaseOnly: true })).map(row => row.deploy_number)).toEqual([9]);
    expect(await repos.history.findByVersion("wf-test", 2)).toBeNull();
    expect(await repos.history.findByDeployNumber("pack-1", "wf-test", 10)).toBeNull();
    expect(await repos.history.findByWorkflowAndDeployNumber("wf-test", 10)).toBeNull();
    expect(await repos.history.setActive("wf-test", 2)).toBe(false);
    expect((await repos.history.findActiveByWorkflowId("wf-test"))?.deploy_number).toBe(9);
    expect(remoteGit("tag", "-l")).toBe(""); expect(triggerRun).not.toHaveBeenCalled();
  });

  it("fences later attempts after an unknown push without publishing it as latest", async () => {
    const push = vi.spyOn(deployGit, "pushPreparedDeployment").mockRejectedValueOnce(new Error("git failed"));
    await expect(service().deploy(input())).rejects.toMatchObject({ code: "DEPLOY_OUTCOME_UNKNOWN" });
    expect(await repos.history.getLatestVersion("wf-test")).toBe(0);
    await expect(service().deploy(input())).rejects.toMatchObject({ status: 409, code: "WORKFLOW_BUSY" });
    expect(push).toHaveBeenCalledOnce(); expect(historyRows()).toEqual([]);
    expect(journal()).toEqual([{ phase: "unknown", lock_workflow_id: (await repos.specs.findByWorkflowId("wf-test"))!.id }]);
    expect(remoteGit("tag", "-l")).toBe(""); expect(triggerRun).not.toHaveBeenCalled();
  });

  it("fences an unfinished operation across pack changes without deactivating the previous release", async () => {
    await repos.history.insert({ packId: "other-pack", workflowId: "wf-test", deployNumber: 1, version: 7,
      tagName: "deploy/wf-test/#1", action: "deploy", specJson: "{}", isActive: true });
    const operations = new PackOperationRepository(ctx.db);
    const other = await operations.begin({ requestId: "other-pack-operation", workflowId: "wf-test", actorId: "user-1", kind: "deploy", inputHash: "other-input" });
    await operations.prepare("other-pack-operation", JSON.stringify({ packId: "other-pack" }), other.operation.attempt);
    const clone = vi.spyOn(gitClient, "cloneClawflows");
    await expect(service().deploy({ ...input(), packId: "pack-1" })).rejects.toMatchObject({ status: 409, code: "WORKFLOW_BUSY" });
    expect(ctx.raw.prepare("SELECT pack_id, action, is_active FROM workflow_deploy_history").all())
      .toEqual([{ pack_id: "other-pack", action: "deploy", is_active: 1 }]);
    expect(journal()).toEqual([{ phase: "prepared", lock_workflow_id: (await repos.specs.findByWorkflowId("wf-test"))!.id }]);
    expect(clone).not.toHaveBeenCalled(); expect(triggerRun).not.toHaveBeenCalled();
  });

  it("preserves inactive legacy releases when an unfinished journal proposes the same version", async () => {
    await repos.history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 1, version: 3,
      tagName: "legacy", action: "deploy", specJson: "{}" });
    const operations = new PackOperationRepository(ctx.db);
    const pending = await operations.begin({ requestId: "private-reservation", workflowId: "wf-test", actorId: "user-1", kind: "deploy", inputHash: "legacy" });
    await operations.prepare("private-reservation", JSON.stringify({ version: 3, git: { deployNumber: 2 } }), pending.operation.attempt);
    expect((await repos.history.listHistory("wf-test", 10)).map(row => row.deploy_number)).toEqual([1]);
    expect((await repos.history.findByVersion("wf-test", 3))?.deploy_number).toBe(1);
    expect((await repos.history.getLatestDeploy("pack-1", "wf-test"))?.deploy_number).toBe(1);
    expect(await repos.history.setActive("wf-test", 3)).toBe(true);
    expect((await repos.history.findActiveByWorkflowId("wf-test"))?.deploy_number).toBe(1);
  });

  it.each(["history", "spec", "receipt"])("reports an ignored %s completion write as unknown and rolls back all DB publication", async target => {
    await repos.history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 1, version: 1,
      tagName: "legacy", action: "deploy", specJson: "{}", isActive: true });
    const before = await repos.specs.findByWorkflowId("wf-test");
    const trigger = target === "history"
      ? "BEFORE UPDATE ON workflow_deploy_history WHEN NEW.is_active = 1"
      : target === "spec" ? "BEFORE UPDATE ON workflow_specs" : "BEFORE UPDATE ON clawweb_pack_operations WHEN NEW.phase = 'completed'";
    ctx.raw.exec(`CREATE TRIGGER ignore_write ${trigger} BEGIN SELECT RAISE(IGNORE); END`);
    await expect(service().deploy({ ...input(), requestId: "deploy-db-failure" }))
      .rejects.toMatchObject({ status: 503, code: "DEPLOY_OUTCOME_UNKNOWN", requestId: "deploy-db-failure" });
    expect(historyRows()).toEqual([{ deploy_number: 1, action: "deploy", is_active: 1, version: 1 }]);
    expect(await repos.specs.findByWorkflowId("wf-test")).toEqual(before);
    expect(remoteGit("rev-parse", "refs/tags/deploy/wf-test/#2^{commit}")).toBe(remoteGit("rev-parse", "refs/heads/wf/wf-test"));
    expect(journal()).toEqual([{ phase: "unknown", lock_workflow_id: (await repos.specs.findByWorkflowId("wf-test"))!.id }]);
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("reports an ignored private journal insert before invoking Git", async () => {
    const clone = vi.spyOn(gitClient, "cloneClawflows");
    ctx.raw.exec("CREATE TRIGGER ignore_reservation BEFORE INSERT ON clawweb_pack_operations BEGIN SELECT RAISE(IGNORE); END");
    await expect(service().deploy(input())).rejects.toThrow("Pack operation insert did not persist exactly one row");
    expect(clone).not.toHaveBeenCalled(); expect(remoteGit("tag", "-l")).toBe("");
    expect(remoteGit("branch", "--list", "wf/wf-test")).toBe("");
    expect(historyRows()).toEqual([]); expect(journal()).toEqual([]); expect(triggerRun).not.toHaveBeenCalled();
  });

  it("does not push an externally advanced number when private preparation is not persisted", async () => {
    addExternalTag();
    const prepare = vi.spyOn(deployGit, "prepareDeployCandidate"), push = vi.spyOn(deployGit, "pushPreparedDeployment");
    ctx.raw.exec("CREATE TRIGGER ignore_prepare BEFORE UPDATE ON clawweb_pack_operations WHEN NEW.phase = 'prepared' BEGIN SELECT RAISE(IGNORE); END");
    await expect(service().deploy(input())).rejects.toThrow("Pack operation update did not persist exactly one row");
    expect(prepare).toHaveBeenCalledWith(expect.any(String), "wf-test", remote.candidateCommit, "", 6, 1, gitAuthor);
    expect(push).not.toHaveBeenCalled(); expect(historyRows()).toEqual([]);
    expect(journal()).toEqual([{ phase: "failed", lock_workflow_id: null }]);
    expect(remoteGit("tag", "-l")).toBe("deploy/wf-test/#5"); expect(triggerRun).not.toHaveBeenCalled();
  });

  it("rolls back when setActive's write is ignored", async () => {
    await repos.history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 1, version: 1, tagName: "one", action: "deploy", specJson: "{}", isActive: true });
    await repos.history.insert({ packId: "pack-1", workflowId: "wf-test", deployNumber: 2, version: 2, tagName: "two", action: "deploy", specJson: "{}" });
    ctx.raw.exec("CREATE TRIGGER ignore_activation BEFORE UPDATE ON workflow_deploy_history WHEN NEW.is_active = 1 BEGIN SELECT RAISE(IGNORE); END");
    await expect(repos.history.setActive("wf-test", 2)).rejects.toThrow();
    expect((await repos.history.findActiveByWorkflowId("wf-test"))?.deploy_number).toBe(1);
  });

  it("deploys the exact saved candidate and triggers rerun only after completed publication", async () => {
    triggerRun.mockImplementationOnce(async () => {
      expect(journal()).toEqual([{ phase: "completed", lock_workflow_id: null }]);
      expect((await repos.history.findActiveByWorkflowId("wf-test"))?.deploy_number).toBe(1);
      expect((await repos.specs.findByWorkflowId("wf-test"))?.version).toBe(1);
      expect(remoteGit("rev-parse", "refs/tags/deploy/wf-test/#1^{commit}")).toBe(remoteGit("rev-parse", "refs/heads/wf/wf-test"));
      return "message-123";
    });
    const result = await service().deploy({ ...input(), packId: "pack-1" });
    expect(result).toMatchObject({ deployed: true, deployNumber: 1, version: 1, tagName: "deploy/wf-test/#1", messageId: "message-123" });
    expect(historyRows()).toEqual([{ deploy_number: 1, action: "deploy", is_active: 1, version: 1 }]);
    expect(await repos.history.setActive("wf-test", 1)).toBe(true);
    const published = remoteGit("rev-parse", "refs/heads/wf/wf-test");
    expect(remoteGit("rev-parse", `${published}^{tree}`)).toBe(remoteGit("rev-parse", `${remote.candidateCommit}^{tree}`));
    expect(remoteGit("show", `${published}:scripts/run.sh`)).toBe("saved script");
    expect(remoteGit("show", `${published}:asset.txt`)).toBe("saved asset");
    expect(remoteGit("cat-file", "-p", `refs/tags/${result.tagName}`)).toContain(`candidateCommit=${remote.candidateCommit}`);
    expect(JSON.parse((await repos.history.findByVersion("wf-test", 1))!.spec_json).content).toBe(stringify(TEST_SPEC));
    expect(triggerRun).toHaveBeenCalledWith("wf-test", "user-1", null);
  });

  it("deploys confirmed S while preserving a later saved S2 and its database draft", async () => {
    const saver = new AutoHealSaveService(ctx.db, repos.specs, repos.history, repos.permissions, remote.config, gitAuthor);
    const first = await saver.save({ workflowId: "wf-test", actorId: "user-1", requestId: "save-candidate-s1",
      spec: { ...TEST_SPEC, title: "Confirmed candidate S" } });
    const newer = await saver.save({ workflowId: "wf-test", actorId: "user-1", requestId: "save-candidate-s2",
      spec: { ...TEST_SPEC, title: "Newer draft S2" } });
    expect(first.commit).not.toBe(newer.commit);
    const deployed = await service().deploy({ ...input(), candidateCommit: first.commit });
    expect(deployed.candidateCommit).toBe(first.commit);
    expect(remoteGit("rev-parse", "refs/heads/wf/wf-test^{tree}")).toBe(remoteGit("rev-parse", `${first.commit}^{tree}`));
    expect(remoteGit("rev-parse", "refs/heads/saved/wf-test")).toBe(newer.commit);
    expect(JSON.parse((await repos.specs.findByWorkflowId("wf-test"))!.spec_json).title).toBe("Newer draft S2");
    expect(JSON.parse((await repos.history.findByVersion("wf-test", 1))!.spec_json).title).toBe("Confirmed candidate S");
    expect(triggerRun).toHaveBeenCalledOnce();
  });

  it("forwards the source run so the re-run targets its own Bot", async () => {
    await service().deploy({ ...input(), sourceFlowId: "60c5aff6-2a07-42ac-8d93-dbd8bbbaabb6" });
    expect(triggerRun).toHaveBeenCalledWith("wf-test", "user-1", "60c5aff6-2a07-42ac-8d93-dbd8bbbaabb6");
  });

  it.each([{ can_execute: 1, can_edit: 0 }, { can_execute: 0, can_edit: 1 }])("allows deploy with either execute or edit permission: %j", async permissions => {
    await repos.permissions.upsert({ bot_id: null, bot_owner_id: "user-2", workflow_id: "wf-test", can_view: 1, ...permissions });
    expect((await service().deploy({ ...input(), actorId: "user-2" })).deployed).toBe(true);
  });

  it("denies deploy when actor lacks execute and edit permission", async () => {
    await repos.permissions.upsert({ bot_id: null, bot_owner_id: "user-3", workflow_id: "wf-test", can_view: 1, can_execute: 0, can_edit: 0 });
    const clone = vi.spyOn(gitClient, "cloneClawflows");
    await expect(service().deploy({ ...input(), actorId: "user-3" })).rejects.toMatchObject({ status: 403, code: "FORBIDDEN" });
    expect(clone).not.toHaveBeenCalled(); expect(journal()).toEqual([]); expect(historyRows()).toEqual([]);
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("skips rerun when autoRun=false", async () => {
    expect((await service().deploy({ ...input(), autoRun: false })).deployed).toBe(true);
    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("does not undo a successful release if the optional rerun fails", async () => {
    triggerRun.mockRejectedValueOnce(new Error("bot unavailable"));
    expect(await service().deploy(input())).toMatchObject({ deployed: true, messageId: null });
    expect(historyRows()).toEqual([{ deploy_number: 1, action: "deploy", is_active: 1, version: 1 }]);
    expect(journal()).toEqual([{ phase: "completed", lock_workflow_id: null }]);
  });

  it("does not repush, reallocate, or rerun a completed request replay", async () => {
    const push = vi.spyOn(deployGit, "pushPreparedDeployment");
    const request = { ...input(), requestId: "deploy-idempotent" };
    const first = await service().deploy(request), replay = await service().deploy(request);
    expect(replay).toMatchObject({ deployed: true, commit: first.commit, deployNumber: 1, candidateCommit: remote.candidateCommit });
    expect(push).toHaveBeenCalledOnce(); expect(triggerRun).toHaveBeenCalledOnce();
    expect(historyRows()).toHaveLength(1); expect(remoteGit("tag", "-l")).toBe("deploy/wf-test/#1");
  });

  it("increments deploy number across multiple deploys", async () => {
    expect((await service().deploy(input())).deployNumber).toBe(1);
    expect((await service().deploy(input())).deployNumber).toBe(2);
    expect(historyRows()).toEqual([
      { deploy_number: 1, action: "deploy", is_active: 0, version: 1 },
      { deploy_number: 2, action: "deploy", is_active: 1, version: 2 },
    ]);
  });

  it("uses the higher Git number without leaving an unfinished journal or placeholder history", async () => {
    addExternalTag();
    const result = await service().deploy(input());
    expect(result.deployNumber).toBe(6);
    expect(historyRows()).toEqual([{ deploy_number: 6, action: "deploy", is_active: 1, version: 1 }]);
    expect(journal()).toEqual([{ phase: "completed", lock_workflow_id: null }]);
    expect(await repos.history.setActive("wf-test", result.version)).toBe(true);
    expect((await repos.history.findActiveByWorkflowId("wf-test"))?.deploy_number).toBe(6);
  });

  it("journals the exact prepared tag and candidate before push, without a public reservation", async () => {
    const realPush = deployGit.pushPreparedDeployment;
    const push = vi.spyOn(deployGit, "pushPreparedDeployment").mockImplementationOnce(async (dir, prepared) => {
      const op = ctx.raw.prepare("SELECT payload_json, phase FROM clawweb_pack_operations").get() as { payload_json: string; phase: string };
      expect(op.phase).toBe("pushing"); expect(JSON.parse(op.payload_json).git).toEqual(prepared);
      expect(prepared).toMatchObject({ tagName: "deploy/wf-test/#1", candidateCommit: remote.candidateCommit, deployNumber: 1 });
      expect(historyRows()).toEqual([]); expect(triggerRun).not.toHaveBeenCalled();
      return realPush(dir, prepared);
    });
    await service().deploy(input()); expect(push).toHaveBeenCalledOnce();
  });

  it("uses each prepared number once instead of skipping ahead on redeploy", async () => {
    const prepare = vi.spyOn(deployGit, "prepareDeployCandidate"), push = vi.spyOn(deployGit, "pushPreparedDeployment");
    const first = await service().deploy(input()), second = await service().deploy(input());
    expect([first.deployNumber, second.deployNumber]).toEqual([1, 2]);
    expect(prepare.mock.calls.map(call => call[4])).toEqual([1, 2]);
    expect(push.mock.calls.map(call => call[1].candidateCommit)).toEqual([remote.candidateCommit, remote.candidateCommit]);
    expect(push.mock.calls.map(call => call[1].deployNumber)).toEqual([1, 2]);
    expect(first.commit).not.toBe(second.commit);
    expect(historyRows()).toEqual([
      { deploy_number: 1, action: "deploy", is_active: 0, version: 1 },
      { deploy_number: 2, action: "deploy", is_active: 1, version: 2 },
    ]);
  });

  it("numbers the prepared git tag gitMax+1 when external tags are ahead of the DB", async () => {
    addExternalTag();
    const prepare = vi.spyOn(deployGit, "prepareDeployCandidate"), push = vi.spyOn(deployGit, "pushPreparedDeployment");
    const result = await service().deploy(input());
    expect(prepare).toHaveBeenCalledWith(expect.any(String), "wf-test", remote.candidateCommit, "", 6, 1, gitAuthor);
    expect(push).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ deployNumber: 6, tagName: "deploy/wf-test/#6" }));
    expect(result).toMatchObject({ deployNumber: 6, tagName: "deploy/wf-test/#6" });
    expect(historyRows()).toEqual([{ deploy_number: 6, action: "deploy", is_active: 1, version: 1 }]);
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
    expect(ctx.raw.prepare("SELECT * FROM clawweb_pack_operations").all()).toEqual([]);
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
    expect(ctx.raw.prepare("SELECT * FROM clawweb_pack_operations").all()).toEqual([]);
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
  it("throws a clear error when the configured temp dir parent is missing", async () => {
    vi.stubEnv("CLAWWEB_TEMP_DIR", path.join(tempDir, "missing", "deep"));
    await expect(gitClient.createTempRepo()).rejects.toThrow("临时 git 目录的父路径不存在");
  });
});
