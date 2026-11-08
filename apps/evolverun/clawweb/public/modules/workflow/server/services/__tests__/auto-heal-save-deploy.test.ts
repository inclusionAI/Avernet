/**
 * AutoHeal save + deploy service tests.
 *
 * Uses an in-memory SQLite DB and mocks git operations to verify:
 *   1. Save: permission checks, validation, git commit/branch, DB upsert.
 *   2. Deploy: deploy_number computation, git tag, deploy_history insert,
 *      setActive, version sync, and auto-run trigger.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { mkdtempSync, rmSync, statSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import Database from "better-sqlite3";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { AutoHealSaveService } from "../auto-heal-save-service.js";
import { AutoHealDeployService } from "../auto-heal-deploy-service.js";
import * as gitClient from "../clawflows-git-client.js";
import type { WorkflowSpec } from "../../workflow.js";

// Capture the real implementation before any beforeEach mocks it.
const realCreateTempRepo = gitClient.createTempRepo;

const TEST_SPEC: WorkflowSpec = {
  id: "wf-test",
  title: "Test Workflow",
  nodes: [
    {
      id: "start",
      type: "start",
      title: "Start",
      config: {},
      executor: { type: "start" },
      dependsOn: [],
    },
    {
      id: "end",
      type: "end",
      title: "End",
      config: {},
      executor: { type: "end" },
      dependsOn: ["start"],
    },
  ],
};

function createTestDb() {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE workflow_specs (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      workflow_id VARCHAR(128) NOT NULL UNIQUE,
      pack_id VARCHAR(128),
      spec_json TEXT NOT NULL,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch()),
      title VARCHAR(256),
      version INTEGER,
      owner_id VARCHAR(64)
    );
    CREATE TABLE workflow_deploy_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      pack_id VARCHAR(128) NOT NULL,
      workflow_id VARCHAR(128) NOT NULL,
      deploy_number INTEGER NOT NULL,
      version INTEGER NOT NULL,
      tag_name VARCHAR(256),
      action VARCHAR(32) NOT NULL,
      from_deploy_number INTEGER,
      spec_json TEXT,
      bot_id VARCHAR(64),
      owner_id VARCHAR(64),
      is_active INTEGER DEFAULT 0,
      note TEXT,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE bot_workflow_permissions (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      bot_id VARCHAR(128),
      bot_owner_id VARCHAR(128) NOT NULL,
      workflow_id VARCHAR(128) NOT NULL,
      can_view INTEGER DEFAULT 0,
      can_execute INTEGER DEFAULT 0,
      can_edit INTEGER DEFAULT 0,
      env VARCHAR(32) NOT NULL DEFAULT 'test',
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
  `);

  const db = {
    dbType: "sqlite" as const,
    dialect: sqliteDialect,
    query: <T,>(sql: string, params: unknown[] = []) => {
      const stmt = raw.prepare(sql);
      return (params.length ? stmt.all(...(params as never[])) : stmt.all()) as T[];
    },
    exec: async (sql: string, params: unknown[] = []) => {
      const stmt = raw.prepare(sql);
      const r = params.length ? stmt.run(...(params as never[])) : stmt.run();
      return { affectedRows: r.changes, insertId: r.lastInsertRowid as number };
    },
    transaction: async <T,>(fn: (tx: typeof db) => Promise<T>) => fn(db),
    close: async () => raw.close(),
  };
  return { db: db as never };
}

const gitConfig = {
  remoteUrl: "https://git.example.com/clawflows.git",
  username: "test",
  email: "test@example.com",
  token: "token",
};

const gitAuthor = { name: "Test", email: "test@example.com" };

describe("AutoHealSaveService", () => {
  let ctx: ReturnType<typeof createTestDb>;
  let specRepo: WorkflowSpecRepository;
  let deployHistoryRepo: WorkflowDeployHistoryRepository;
  let botPermRepo: BotWorkflowPermissionRepository;

  beforeEach(() => {
    ctx = createTestDb();
    deployHistoryRepo = new WorkflowDeployHistoryRepository(ctx.db as never);
    specRepo = new WorkflowSpecRepository(ctx.db as never, deployHistoryRepo);
    botPermRepo = new BotWorkflowPermissionRepository(ctx.db as never);
    vi.spyOn(gitClient, "cloneClawflows").mockResolvedValue();
    vi.spyOn(gitClient, "writeWorkflowSpecToRepo").mockResolvedValue();
    vi.spyOn(gitClient, "ensurePackManifest").mockResolvedValue();
    vi.spyOn(gitClient, "createTempRepo").mockResolvedValue("/tmp/cw-autoheal-xxx");
    vi.spyOn(gitClient, "removeTempRepo").mockResolvedValue();
    vi.spyOn(gitClient, "commitAndPushSaved").mockResolvedValue({ branch: "saved/wf-test", commit: "abc123" });
    vi.spyOn(gitClient, "prepareBranchBase").mockResolvedValue("default");
    vi.spyOn(gitClient, "pruneLegacyPackCopy").mockResolvedValue(false);
  });

  it("denies save when the actor lacks edit permission", async () => {
    const service = new AutoHealSaveService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
    );
    await expect(service.save({
      workflowId: "wf-test",
      spec: TEST_SPEC,
      actorId: "user-1",
    })).rejects.toThrow("No edit permission");
  });

  it("saves a valid spec and returns git branch/commit", async () => {
    await botPermRepo.upsert({
      bot_id: null,
      bot_owner_id: "user-1",
      workflow_id: "wf-test",
      can_view: 1,
      can_execute: 1,
      can_edit: 1,
    });

    const service = new AutoHealSaveService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
    );

    const result = await service.save({
      workflowId: "wf-test",
      spec: TEST_SPEC,
      packId: "pack-1",
      actorId: "user-1",
    });

    expect(result.saved).toBe(true);
    expect(result.workflowId).toBe("wf-test");
    expect(result.packId).toBe("pack-1");
    expect(result.branch).toBe("saved/wf-test");
    expect(result.commit).toBe("abc123");
    // The snapshot must be based on the previous save/deploy content (scripts
    // inheritance) — never on a fresh default-branch base.
    expect(gitClient.prepareBranchBase).toHaveBeenCalledWith("/tmp/cw-autoheal-xxx", "saved/wf-test", "wf/wf-test");
    expect(gitClient.pruneLegacyPackCopy).toHaveBeenCalledWith("/tmp/cw-autoheal-xxx", "pack-1");

    const row = await specRepo.findByWorkflowId("wf-test");
    expect(row).not.toBeNull();
    expect(row?.pack_id).toBe("pack-1");
    expect(row?.owner_id).toBe("user-1");
  });
});

describe("AutoHealDeployService", () => {
  let ctx: ReturnType<typeof createTestDb>;
  let specRepo: WorkflowSpecRepository;
  let deployHistoryRepo: WorkflowDeployHistoryRepository;
  let botPermRepo: BotWorkflowPermissionRepository;
  let triggerRun: ReturnType<typeof vi.fn>;

  beforeEach(async () => {
    ctx = createTestDb();
    deployHistoryRepo = new WorkflowDeployHistoryRepository(ctx.db as never);
    specRepo = new WorkflowSpecRepository(ctx.db as never, deployHistoryRepo);
    botPermRepo = new BotWorkflowPermissionRepository(ctx.db as never);
    triggerRun = vi.fn().mockResolvedValue("message-123");

    vi.spyOn(gitClient, "cloneClawflows").mockResolvedValue();
    vi.spyOn(gitClient, "writeWorkflowSpecToRepo").mockResolvedValue();
    vi.spyOn(gitClient, "ensurePackManifest").mockResolvedValue();
    vi.spyOn(gitClient, "createTempRepo").mockResolvedValue("/tmp/cw-autoheal-xxx");
    vi.spyOn(gitClient, "removeTempRepo").mockResolvedValue();
    vi.spyOn(gitClient, "getMaxDeployNumberFromGitTags").mockResolvedValue(0);
    vi.spyOn(gitClient, "commitAndPushDeploy").mockResolvedValue({
      branch: "wf/wf-test",
      tagName: "deploy/wf-test/#1",
      commit: "def456",
      deployNumber: 1,
    });
    vi.spyOn(gitClient, "prepareBranchBase").mockResolvedValue("default");
    vi.spyOn(gitClient, "pruneLegacyPackCopy").mockResolvedValue(false);

    await botPermRepo.upsert({
      bot_id: null,
      bot_owner_id: "user-1",
      workflow_id: "wf-test",
      can_view: 1,
      can_execute: 1,
      can_edit: 1,
    });

    await specRepo.upsert("wf-test", "pack-1", JSON.stringify({
      content: "id: wf-test\ntitle: Test Workflow\nnodes:\n  - id: start\n    type: start\n    config: {}\n    executor:\n      type: start\n    dependsOn: []\n  - id: end\n    type: end\n    config: {}\n    executor:\n      type: end\n    dependsOn: [start]\n",
      ...TEST_SPEC,
    }), "user-1");
  });

  it("deploys the active spec and triggers rerun by default", async () => {
    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    const result = await service.deploy({
      workflowId: "wf-test",
      packId: "pack-1",
      actorId: "user-1",
    });

    expect(result.deployed).toBe(true);
    expect(result.deployNumber).toBe(1);
    expect(result.version).toBe(1);
    expect(result.tagName).toBe("deploy/wf-test/#1");
    expect(result.messageId).toBe("message-123");

    const history = await deployHistoryRepo.listHistory("wf-test", 10);
    expect(history.length).toBe(1);
    expect(history[0].action).toBe("deploy");
    expect(history[0].deploy_number).toBe(1);
    expect(history[0].is_active).toBe(1);
    expect(history[0].tag_name).toBe("deploy/wf-test/#1");
    // The deploy must be based on the previous deploy content (scripts
    // inheritance) — never on a fresh default-branch base.
    expect(gitClient.prepareBranchBase).toHaveBeenCalledWith("/tmp/cw-autoheal-xxx", "wf/wf-test");
    expect(gitClient.pruneLegacyPackCopy).toHaveBeenCalledWith("/tmp/cw-autoheal-xxx", "pack-1");

    const row = await specRepo.findByWorkflowId("wf-test");
    expect(row?.version).toBe(1);
    // No source run supplied, so the re-run cannot resolve a target Bot.
    expect(triggerRun).toHaveBeenCalledWith("wf-test", "user-1", null);
  });

  it("forwards the source run so the re-run targets its own Bot", async () => {
    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    await service.deploy({
      workflowId: "wf-test",
      packId: "pack-1",
      sourceFlowId: "60c5aff6-2a07-42ac-8d93-dbd8bbbaabb6",
      actorId: "user-1",
    });

    // The re-run must reach the Bot that ran the source, never the diagnosis Bot.
    expect(triggerRun).toHaveBeenCalledWith(
      "wf-test",
      "user-1",
      "60c5aff6-2a07-42ac-8d93-dbd8bbbaabb6",
    );
  });

  it("allows deploy with execute permission (no edit)", async () => {
    await botPermRepo.upsert({
      bot_id: null,
      bot_owner_id: "user-2",
      workflow_id: "wf-test",
      can_view: 1,
      can_execute: 1,
      can_edit: 0,
    });

    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    const result = await service.deploy({
      workflowId: "wf-test",
      actorId: "user-2",
    });

    expect(result.deployed).toBe(true);
  });

  it("denies deploy when actor lacks execute and edit permission", async () => {
    await botPermRepo.upsert({
      bot_id: null,
      bot_owner_id: "user-3",
      workflow_id: "wf-test",
      can_view: 1,
      can_execute: 0,
      can_edit: 0,
    });

    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    await expect(service.deploy({
      workflowId: "wf-test",
      actorId: "user-3",
    })).rejects.toThrow("No execute permission");
  });

  it("skips rerun when autoRun=false", async () => {
    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    await service.deploy({
      workflowId: "wf-test",
      packId: "pack-1",
      autoRun: false,
      actorId: "user-1",
    });

    expect(triggerRun).not.toHaveBeenCalled();
  });

  it("increments deploy number across multiple deploys", async () => {
    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    vi.spyOn(gitClient, "commitAndPushDeploy")
      .mockResolvedValueOnce({ branch: "wf/wf-test", tagName: "deploy/wf-test/#1", commit: "def456", deployNumber: 1 })
      .mockResolvedValueOnce({ branch: "wf/wf-test", tagName: "deploy/wf-test/#2", commit: "ghi789", deployNumber: 2 });

    const first = await service.deploy({ workflowId: "wf-test", actorId: "user-1" });
    expect(first.deployNumber).toBe(1);

    const second = await service.deploy({ workflowId: "wf-test", actorId: "user-1" });
    expect(second.deployNumber).toBe(2);

    const history = await deployHistoryRepo.listHistory("wf-test", 10);
    expect(history.map((h) => h.deploy_number).sort((a, b) => a - b)).toEqual([1, 2]);
  });

  it("leaves an inactive placeholder when git claims a higher deploy number", async () => {
    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    vi.spyOn(gitClient, "getMaxDeployNumberFromGitTags").mockResolvedValueOnce(5);
    vi.spyOn(gitClient, "commitAndPushDeploy").mockResolvedValueOnce({
      branch: "wf/wf-test",
      tagName: "deploy/wf-test/#6",
      commit: "def456",
      deployNumber: 6,
    });

    const result = await service.deploy({ workflowId: "wf-test", actorId: "user-1" });
    expect(result.deployNumber).toBe(6);

    const history = await deployHistoryRepo.listHistory("wf-test", 10);
    const placeholder = history.find((h) => h.deploy_number === 1);
    const real = history.find((h) => h.deploy_number === 6);
    expect(placeholder?.is_active).toBe(0);
    // The reserve step must never write a null tag_name: managed MySQL declares
    // the column NOT NULL and the v65 relaxation never ran there, so a null
    // would abort the deploy with "Column 'tag_name' cannot be null".
    expect(placeholder?.tag_name).toBe("deploy/wf-test/#1");
    expect(real?.is_active).toBe(1);
    expect(real?.tag_name).toBe("deploy/wf-test/#6");
  });

  it("reserves a non-null tag name before the git push reveals the real tag", async () => {
    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    // Capture the row as it exists between reserve and activation.
    let reservedTagName: string | null | undefined;
    let reservedIsActive: number | undefined;
    vi.spyOn(gitClient, "commitAndPushDeploy").mockImplementationOnce(async () => {
      const rows = await ctx.db.query<{ tag_name: string; is_active: number }>(
        "SELECT tag_name, is_active FROM workflow_deploy_history WHERE workflow_id = ? AND deploy_number = ?",
        ["wf-test", 1],
      );
      reservedTagName = rows[0]?.tag_name;
      reservedIsActive = rows[0]?.is_active;
      return { branch: "wf/wf-test", tagName: "deploy/wf-test/#1", commit: "abc123", deployNumber: 1 };
    });

    await service.deploy({ workflowId: "wf-test", actorId: "user-1" });

    expect(reservedTagName).toBe("deploy/wf-test/#1");
    expect(reservedIsActive).toBe(0);
  });

  it("reuses the reserved deploy number per deploy instead of skipping ahead", async () => {
    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    // Echo the number the service computed, as the real commitAndPushDeploy does
    // (it tags with the passed number and returns it). The old fixed-return mocks
    // never saw the computed value, which is how the double increment slipped by.
    const echoPush = vi.fn(async (_repoDir: string, _workflowId: string, deployNumber: number) => ({
      branch: "wf/wf-test",
      tagName: `deploy/wf-test/#${deployNumber}`,
      commit: "def456",
      deployNumber,
    }));
    vi.spyOn(gitClient, "commitAndPushDeploy").mockImplementation(echoPush);

    const first = await service.deploy({ workflowId: "wf-test", actorId: "user-1" });
    expect(first.deployNumber).toBe(1);
    const second = await service.deploy({ workflowId: "wf-test", actorId: "user-1" });
    expect(second.deployNumber).toBe(2);
    expect(second.tagName).toBe("deploy/wf-test/#2");

    // Reserved number = dbMax + 1; a git max that is not ahead must NOT bump the
    // tag past it: one history row per deploy, no inactive placeholders, no gaps.
    expect(echoPush.mock.calls.map((call) => call[2])).toEqual([1, 2]);
    const history = await deployHistoryRepo.listHistory("wf-test", 10);
    expect(history.map((h) => h.deploy_number).sort((a, b) => a - b)).toEqual([1, 2]);
    const firstRow = history.find((h) => h.deploy_number === 1);
    const secondRow = history.find((h) => h.deploy_number === 2);
    expect(firstRow?.is_active).toBe(0);
    expect(secondRow?.is_active).toBe(1);
    expect(secondRow?.tag_name).toBe("deploy/wf-test/#2");
  });

  it("numbers the git tag gitMax+1 when external tags are ahead of the DB", async () => {
    const service = new AutoHealDeployService(
      ctx.db as never,
      specRepo,
      deployHistoryRepo,
      botPermRepo,
      gitConfig,
      gitAuthor,
      triggerRun,
    );

    vi.spyOn(gitClient, "getMaxDeployNumberFromGitTags").mockResolvedValueOnce(5);
    let pushedDeployNumber = 0;
    vi.spyOn(gitClient, "commitAndPushDeploy").mockImplementationOnce(
      async (_repoDir: string, _workflowId: string, deployNumber: number) => {
        pushedDeployNumber = deployNumber;
        return { branch: "wf/wf-test", tagName: `deploy/wf-test/#${deployNumber}`, commit: "def456", deployNumber };
      },
    );

    const result = await service.deploy({ workflowId: "wf-test", actorId: "user-1" });

    // dbMax=0 → reserved=1; external tags already reach #5 → the tag must be
    // gitMax + 1 (#6), keeping the reserved #1 as an inactive placeholder.
    expect(pushedDeployNumber).toBe(6);
    expect(result.deployNumber).toBe(6);
    expect(result.tagName).toBe("deploy/wf-test/#6");

    const history = await deployHistoryRepo.listHistory("wf-test", 10);
    expect(history.map((h) => h.deploy_number).sort((a, b) => a - b)).toEqual([1, 6]);
    const placeholder = history.find((h) => h.deploy_number === 1);
    const real = history.find((h) => h.deploy_number === 6);
    expect(placeholder?.is_active).toBe(0);
    expect(real?.is_active).toBe(1);
    expect(real?.tag_name).toBe("deploy/wf-test/#6");
  });
});

describe("createTempRepo", () => {
  let originalClawwebTemp: string | undefined;
  let tempDir: string | undefined;

  beforeEach(() => {
    originalClawwebTemp = process.env.CLAWWEB_TEMP_DIR;
    tempDir = mkdtempSync(path.join(tmpdir(), "cw-autoheal-test-"));
    process.env.CLAWWEB_TEMP_DIR = tempDir;
    // Restore the real implementation for these tests.
    vi.spyOn(gitClient, "createTempRepo").mockImplementation(realCreateTempRepo);
  });

  afterEach(() => {
    if (tempDir) {
      rmSync(tempDir, { recursive: true, force: true });
    }
    if (originalClawwebTemp === undefined) {
      delete process.env.CLAWWEB_TEMP_DIR;
    } else {
      process.env.CLAWWEB_TEMP_DIR = originalClawwebTemp;
    }
  });

  it("creates a temp directory under CLAWWEB_TEMP_DIR", async () => {
    const repo = await gitClient.createTempRepo();
    expect(repo.startsWith(tempDir!)).toBe(true);
    expect(statSync(repo).isDirectory()).toBe(true);
    rmSync(repo, { recursive: true, force: true });
  });

  it("throws a clear error when the configured temp dir parent is missing", async () => {
    process.env.CLAWWEB_TEMP_DIR = path.join(tempDir!, "missing", "deep");
    await expect(gitClient.createTempRepo()).rejects.toThrow("临时 git 目录的父路径不存在");
  });
});
