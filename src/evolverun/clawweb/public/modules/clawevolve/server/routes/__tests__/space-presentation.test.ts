import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createHash } from "node:crypto";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import type { OcbLocalSkillPort, OcbSpace, OcbSpacePort } from "../../internal/module-api.js";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { FilesystemObjectStore } from "../../services/object-storage/filesystem-object-store.js";
import type { SpacePresentationPolicy } from "../../services/evolve/space-presentation.js";
import { createEvolveRouter } from "../evolve.js";
import { createSkillTaskDefaultsRouter } from "../skill-task-defaults.js";

// Verify taskSpacePresentation at POST /tasks, after the real target/Stage
// authorization and freezing, and check the persisted task through its HTTP GET.
const actor = "task-owner";
const teamId = "actual-team-208";
const otherTeamId = "actual-team-309";
const personalId = "personal-task-owner";
const stageSkillId = "registered-hardening-stage";
const implementationId = "hardening-v1";
let db: SqliteDatabase;
let tasks: EvolveRepository;
let skills: SkillAssetRepository;
let stages: StageSkillRepository;
let policies: SpacePresentationPolicy[];
let server: ReturnType<express.Application["listen"]> | undefined;
let artifactRoot: string;
let url: string;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  tasks = new EvolveRepository(db);
  skills = new SkillAssetRepository(db);
  stages = new StageSkillRepository(db);
  artifactRoot = await mkdtemp(join(tmpdir(), "space-presentation-test-"));
  const store = new FilesystemObjectStore(artifactRoot);
  const bytes = await new JSZip().file("SKILL.md", "# evidence-skill\nRead evidence and report grounded results.\n")
    .generateAsync({ type: "nodebuffer" });
  const ocbLocalSkills: OcbLocalSkillPort = {
    listLocalSkills: vi.fn(async () => []),
    exportLocalSkill: vi.fn(async () => ({ packageBytes: bytes,
      sha256: createHash("sha256").update(bytes).digest("hex"), displayName: "evidence-skill" })),
    replaceLocalSkill: vi.fn(async () => { throw new Error("Task creation must not replace a live Skill"); }),
  };
  const spaces: OcbSpace[] = [
    { id: teamId, name: "Renamed hardening team", type: "TEAM", role: "MEMBER" },
    { id: otherTeamId, name: "97", type: "TEAM", role: "MEMBER" },
    { id: personalId, name: "97", type: "PERSONAL", role: "ADMIN" },
  ];
  const ocbSpaces: OcbSpacePort = { listAccessibleSpaces: vi.fn(async ({ identity }) => identity.userId === actor ? spaces : []) };
  policies = [{ spaceId: teamId, kind: "skill_hardening", diagnosePreprocessStageSkillId: stageSkillId }];
  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createSkillTaskDefaultsRouter({ skills, stages, spaces: ocbSpaces, policies }));
  app.use("/api/evolve", createEvolveRouter(tasks, {
    skillAssetRepo: skills, stageSkillRepo: stages, ocbSpaces, ocbLocalSkills,
    artifactStore: store, artifactUrlStore: store, spacePresentationPolicies: policies,
    dispatch: vi.fn(async () => ({ runId: "test-run", sessionId: "test-session", platformResponse: {} })),
  }));
  const started = await new Promise<ReturnType<express.Application["listen"]>>((resolve, reject) => {
    const instance = app.listen(0, "127.0.0.1", () => resolve(instance));
    instance.once("error", reject);
  });
  server = started;
  url = `http://127.0.0.1:${(started.address() as { port: number }).port}/api/evolve/tasks`;
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
  server = undefined;
  await db?.close();
  if (artifactRoot) await rm(artifactRoot, { recursive: true, force: true });
});

type Scenario = {
  name: string;
  targetType?: "TEAM" | "PERSONAL" | null;
  targetSpace?: string;
  bindingSpace?: string;
  bindingStageSkill?: string;
  binding?: "enabled" | "disabled" | "none";
  mode?: "preprocess" | "postprocess";
  policySpace?: string;
  noPolicy?: boolean;
};

async function seed(input: Scenario) {
  const targetType = input.targetType === undefined ? "TEAM" : input.targetType;
  await skills.createAsset({ assetId: "target", versionId: "target-v1", ownerUserId: actor,
    spaceId: targetType === null ? null : input.targetSpace ?? (targetType === "PERSONAL" ? personalId : teamId),
    spaceType: targetType, spaceName: "97", botId: "target-bot", ocbSkillId: "local-skill",
    displayName: "evidence-skill", packageRef: "fixture:baseline", packageSha256: "fixture-checksum" });
  await stages.createImplementation({ implementationId,
    stageSkillId: input.bindingStageSkill ?? stageSkillId, ownerUserId: "stage-publisher",
    spaceId: input.bindingSpace ?? teamId, spaceType: "TEAM", spaceName: "97",
    displayName: "Hardening preprocessor", stage: "diagnose", mode: input.mode ?? "preprocess", versionNo: 1,
    packageRef: "oss://clawevolve-artifacts/fixture/stage.zip", packageSha256: "c".repeat(64), staticValidation: { status: "passed" } });
  await stages.registerImplementation(implementationId);
  await stages.updateIntegrationTest(implementationId, "stage-test", "test_passed");
  if (input.policySpace) policies[0] = { ...policies[0]!, spaceId: input.policySpace };
  if (input.noPolicy) policies.splice(0);
}

async function createTask(input: Scenario, extra: Record<string, unknown> = {}) {
  const extensions = input.binding === "none" ? {} : {
    stageExtensions: { diagnose: { [input.mode ?? "preprocess"]: {
      enabled: input.binding !== "disabled", implementationId,
      // Request-supplied claims must not replace frozen repository facts.
      spaceId: teamId, stageSkillId, ownerUserId: actor,
    } } },
  };
  const response = await fetch(url, { method: "POST",
    headers: { "Content-Type": "application/json", "X-User-Id": actor },
    body: JSON.stringify({ taskType: "full", taskName: "Skill reliability regression", userId: actor,
      botId: "target-bot", targetSkillAssetId: "target", model: "GLM-5.2", judgeBackend: "subagent",
      diagnoseIntent: "Check actual session evidence", goal: "Improve reliability while preserving business semantics",
      ...extensions, ...extra }),
  });
  expect(response.status, await response.clone().text()).toBe(201);
  return response.json();
}

async function assertPersistedPresentation(taskId: string, expected: unknown) {
  const response = await fetch(`${url}/${taskId}`, { headers: { "X-User-Id": actor } });
  expect(response.status, await response.clone().text()).toBe(200);
  const body = await response.json();
  expect(body.config.presentation).toEqual(expected);
  const stored = await tasks.findTask(taskId);
  expect(JSON.parse(stored!.config_json ?? "{}").presentation).toEqual(expected);
}

describe("Space presentation through task creation HTTP", () => {
  it.each([true, false])("consumes real HTTP defaults without inventing a binding or presentation (eligible=%s)", async eligible => {
    await seed({ name: "defaults-to-task", noPolicy: !eligible });
    const response = await fetch(`${url.replace(/\/tasks$/, "")}/skill-assets/target/task-defaults`, {
      headers: { "X-User-Id": actor },
    });
    expect(response.status, await response.clone().text()).toBe(200);
    const defaults = await response.json();
    const task = await createTask({ name: "defaults-to-task", binding: "none" }, {
      userId: defaults.userId, botId: defaults.botId, targetSkillAssetId: defaults.assetId,
      ...defaults.optimize,
    });
    const expected = eligible
      ? { kind: "skill_hardening", spaceId: teamId, hardeningImplementationId: implementationId }
      : undefined;
    expect(task.config.presentation).toEqual(expected);
    if (eligible) {
      expect(task.config.stageExtensions.diagnose.preprocess).toMatchObject({ enabled: true, implementationId, stageSkillId, spaceId: teamId });
    } else {
      expect(defaults.optimize.stageExtensions).toBeNull();
      expect(task.config).not.toHaveProperty("stageExtensions");
    }
    await assertPersistedPresentation(task.task_id, expected);
  });

  it("enables presentation for a TEAM target and same-space policy Stage, independently of the space name", async () => {
    await seed({ name: "matching" });
    const task = await createTask({ name: "matching" });
    const expected = { kind: "skill_hardening", spaceId: teamId, hardeningImplementationId: implementationId };
    expect(task.config.presentation).toEqual(expected);
    expect(task.config.targetSkill).toMatchObject({ spaceId: teamId, spaceType: "TEAM" });
    expect(task.config.stageExtensions.diagnose.preprocess).toMatchObject({
      spaceId: teamId, stageSkillId, implementationId, ownerUserId: "stage-publisher",
    });
    await assertPersistedPresentation(task.task_id, expected);
  });

  it.each([
    { name: "PERSONAL target", targetType: "PERSONAL" },
    { name: "historical private target", targetType: null },
    { name: "different TEAM target with the same name", targetSpace: otherTeamId },
    { name: "different binding space despite forged request claims", bindingSpace: otherTeamId },
    { name: "Stage Skill does not match policy despite forged request claims", bindingStageSkill: "ordinary-stage" },
    { name: "no enabled binding", binding: "none" },
    { name: "disabled binding", binding: "disabled" },
    { name: "postprocess instead of preprocess", mode: "postprocess" },
    { name: "space name used instead of actual ID", policySpace: "97" },
    { name: "no configured policy", noPolicy: true },
  ] satisfies Scenario[])("keeps the normal task presentation for $name", async scenario => {
    await seed(scenario);
    const task = await createTask(scenario, {
      presentation: { kind: "skill_hardening", spaceId: teamId, hardeningImplementationId: implementationId },
    });
    expect(task.config).not.toHaveProperty("presentation");
    await assertPersistedPresentation(task.task_id, undefined);
  });

  it.each(["none", "enabled"] as const)("does not change an ordinary Bot diagnosis form with %s Stage binding", async binding => {
    await seed({ name: "ordinary" });
    const task = await createTask({ name: "ordinary", binding }, {
      taskType: "diagnose", targetSkillAssetId: undefined, taskName: "Ordinary diagnosis", remark: "Keep ordinary form",
      stageSelection: { diagnose: true, plan: false, optimize: false },
    });
    expect(task.config).not.toHaveProperty("targetSkill");
    expect(task.config).not.toHaveProperty("presentation");
    expect(task.taskName ?? task.task_name).toBe("Ordinary diagnosis");
    await assertPersistedPresentation(task.task_id, undefined);
  });
});
