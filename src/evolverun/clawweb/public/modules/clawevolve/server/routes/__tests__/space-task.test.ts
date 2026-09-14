import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import type { OcbSpace, OcbSpacePort } from "../../internal/module-api.js";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { createEvolveRouter } from "../evolve.js";

let db: SqliteDatabase;
let repo: EvolveRepository;
let stages: StageSkillRepository;
let server: ReturnType<express.Application["listen"]>;
let baseUrl: string;
let accessibleSpaces: OcbSpace[];
const dispatch = vi.fn();
const createSignedUrl = vi.fn();
const listAccessibleSpaces = vi.fn<OcbSpacePort["listAccessibleSpaces"]>();
const implementationId = "IMPL-SPACE-TASK";
const teamSpace: OcbSpace = { id: "208", name: "97", type: "TEAM", role: "MEMBER" };

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new EvolveRepository(db);
  stages = new StageSkillRepository(db);
  accessibleSpaces = [teamSpace];
  listAccessibleSpaces.mockReset().mockImplementation(async ({ identity }) =>
    identity.userId === "member" ? accessibleSpaces : []);
  dispatch.mockReset().mockResolvedValue({ runId: "test-run", sessionId: "test-session" });
  createSignedUrl.mockReset().mockResolvedValue("https://objects.example.test/authorized-stage-package");
  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createEvolveRouter(repo, {
    dispatch, stageSkillRepo: stages, ocbSpaces: { listAccessibleSpaces },
    artifactUrlStore: { createSignedUrl },
  }));
  server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve`;
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server.close(() => resolve()));
  await db?.close();
});

async function seedImplementation(owner = "publisher", spaceType: "TEAM" | "PERSONAL" | null = "TEAM") {
  await stages.createImplementation({
    implementationId, stageSkillId: "STAGE-SPACE-TASK", ownerUserId: owner,
    spaceId: spaceType ? "208" : null, spaceType, spaceName: spaceType ? "97" : null,
    displayName: "共享诊断前置", stage: "diagnose", mode: "preprocess", versionNo: 1,
    packageRef: "oss://clawevolve-artifacts/evolve/stage-implementations/IMPL-SPACE-TASK/v1/package.zip",
    packageSha256: "c".repeat(64), staticValidation: { status: "passed" },
  });
  await stages.registerImplementation(implementationId);
  await stages.updateIntegrationTest(implementationId, "TEST-SPACE-TASK", "test_passed");
}

async function createTask(extra: Record<string, unknown> = {}) {
  return fetch(`${baseUrl}/tasks`, {
    method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": "member" },
    body: JSON.stringify({
      taskType: "diagnose", taskName: "共享Stage任务权限", userId: "member", botId: "member-bot",
      model: "GLM-5.2", judgeBackend: "subagent", diagnoseIntent: "检查用户提供的真实记录",
      stageSelection: { diagnose: true, plan: false, optimize: false },
      stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId } } },
      ...extra,
    }),
  });
}

async function acceptedTask(extra: Record<string, unknown> = {}) {
  const response = await createTask(extra);
  expect(response.status, await response.clone().text()).toBe(201);
  const task = await response.json();
  expect(task.steps[0].stepType).toBe("stage_extension");
  return task;
}

function stepInput(task: { task_id: string; steps: Array<{ stepId: string }> }) {
  // Runtime calls independently of the browser: no X-User-Id/cookie/member claim.
  return fetch(`${baseUrl}/internal/tasks/${task.task_id}/steps/${task.steps[0].stepId}/input`);
}

describe("shared Stage task authorization", () => {
  it("freezes the real TEAM publisher and space for a member and serves the independent Step package", async () => {
    await seedImplementation();
    const task = await acceptedTask({
      stageExtensions: { diagnose: { preprocess: {
        enabled: true, implementationId, ownerUserId: "forged-owner", spaceId: "forged-space", displayName: "伪造名称",
      } } },
    });
    expect(listAccessibleSpaces).toHaveBeenCalledWith(expect.objectContaining({ identity: expect.objectContaining({ userId: "member" }) }));
    expect(task.config.stageExtensions.diagnose.preprocess).toMatchObject({
      enabled: true, implementationId, ownerUserId: "publisher", spaceId: "208", displayName: "共享诊断前置",
    });
    // The running task uses its creation-time authorization, not caller-supplied membership.
    accessibleSpaces = [];
    const response = await stepInput(task);
    expect(response.status, await response.clone().text()).toBe(200);
    const input = await response.json();
    expect(input.implementation).toMatchObject({
      implementationId, packageSha256: "c".repeat(64),
      package: { method: "GET", url: "https://objects.example.test/authorized-stage-package" },
    });
    expect(createSignedUrl).toHaveBeenCalledWith(
      "evolve/stage-implementations/IMPL-SPACE-TASK/v1/package.zip", "GET", expect.any(Number),
    );
    const denied = await createTask();
    expect(denied.status).toBe(403);
    expect(await denied.json()).toEqual({ error: "无权使用所选 Stage Skill" });
    expect((await repo.listTasks()).length).toBe(1);
  });

  it.each([false, true])("rejects a nonmember before task creation, including forged body grants (%s)", async (forged) => {
    await seedImplementation();
    accessibleSpaces = [];
    const response = await createTask(forged ? {
      spaceId: "208", spaceType: "TEAM", role: "ADMIN", member: true,
      accessibleSpaces: [teamSpace], ownerUserId: "publisher",
      stageExtensions: { diagnose: { preprocess: {
        enabled: true, implementationId, ownerUserId: "publisher", spaceId: "208", spaceType: "TEAM", authorized: true,
      } } },
    } : {});
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "无权使用所选 Stage Skill" });
    expect(dispatch).not.toHaveBeenCalled();
    expect(createSignedUrl).not.toHaveBeenCalled();
    expect(await repo.listTasks()).toEqual([]);
  });

  it.each(["PERSONAL", null] as const)("does not share another owner's %s implementation by a matching TEAM claim", async (type) => {
    await seedImplementation("publisher", type);
    const response = await createTask();
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ error: "无权使用所选 Stage Skill" });
    expect(dispatch).not.toHaveBeenCalled();
    expect(await repo.listTasks()).toEqual([]);
  });

  it("preserves the owner's historical private Stage and independent input access", async () => {
    await seedImplementation("member", null);
    accessibleSpaces = [];
    const task = await acceptedTask();
    const response = await stepInput(task);
    expect(response.status, await response.clone().text()).toBe(200);
    expect((await response.json()).implementation.implementationId).toBe(implementationId);
  });

  it.each(["owner_user_id", "space_id"] as const)("rejects a changed frozen %s before signing the Step package", async (column) => {
    await seedImplementation();
    const task = await acceptedTask();
    // Test fixture mutation models a persisted implementation changing after authorization.
    await db.exec(`UPDATE ce_stage_skill_implementations SET ${column} = ? WHERE implementation_id = ?`, ["other", implementationId]);
    createSignedUrl.mockClear();
    const response = await stepInput(task);
    expect(response.status).toBe(409);
    expect(createSignedUrl).not.toHaveBeenCalled();
  });

  it.each(["member", "publisher"])("keeps old bindings private to the task owner (%s)", async (owner) => {
    await seedImplementation(owner, owner === "member" ? null : "TEAM");
    const task = await acceptedTask();
    const config = structuredClone(task.config);
    config.stageExtensions.diagnose.preprocess = { enabled: true, implementationId };
    await db.exec("UPDATE ce_tasks SET config_json = ? WHERE task_id = ?", [JSON.stringify(config), task.task_id]);
    createSignedUrl.mockClear();
    const response = await stepInput(task);
    expect(response.status).toBe(owner === "member" ? 200 : 409);
    if (owner !== "member") expect(createSignedUrl).not.toHaveBeenCalled();
  });
});
