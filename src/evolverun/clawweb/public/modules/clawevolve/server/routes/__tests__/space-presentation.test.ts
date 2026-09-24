import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { createHash } from "node:crypto";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import type { BotSkillGateway } from "../../contracts/bot-skill-gateway.js";
import type { AccessibleSpace, SpaceDirectory } from "../../contracts/space-directory.js";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import type { EvolveExtension } from "../../contracts/evolve-extension.js";
import { FilesystemObjectStore } from "../../services/object-storage/filesystem-object-store.js";
import { createEvolveRouter } from "../evolve.js";

const actor = "task-owner";
const team: AccessibleSpace = { id: "team-alpha", name: "Alpha", type: "TEAM", role: "MEMBER" };
const personal: AccessibleSpace = { id: "personal-owner", name: "Personal", type: "PERSONAL", role: "ADMIN" };
const implementationId = "host-implementation";
let db: SqliteDatabase;
let tasks: EvolveRepository;
let skills: SkillAssetRepository;
let stages: StageSkillRepository;
let server: ReturnType<express.Application["listen"]> | undefined;
let artifactRoot: string;
let url: string;

const extension: EvolveExtension = {
  id: "host.test",
  resolveTaskPresentation({ targetSkill, stageExtensions }) {
    const binding = stageExtensions.diagnose?.preprocess;
    return targetSkill?.spaceType === "TEAM" && targetSkill.spaceId === team.id
      && binding?.enabled && binding.spaceId === team.id && binding.stageSkillId === "host-stage"
      ? { data: { implementationId: binding.implementationId } }
      : null;
  },
};

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  tasks = new EvolveRepository(db);
  skills = new SkillAssetRepository(db);
  stages = new StageSkillRepository(db);
  artifactRoot = await mkdtemp(join(tmpdir(), "host-presentation-test-"));
  const store = new FilesystemObjectStore(artifactRoot);
  const bytes = await new JSZip().file("SKILL.md", "# evidence-skill\nRead evidence.\n").generateAsync({ type: "nodebuffer" });
  const hostLocalSkills: BotSkillGateway = {
    listLocalSkills: vi.fn(async () => []),
    exportLocalSkill: vi.fn(async () => ({ packageBytes: bytes,
      sha256: createHash("sha256").update(bytes).digest("hex"), displayName: "evidence-skill" })),
    replaceLocalSkill: vi.fn(async () => { throw new Error("must not replace live Skill"); }),
  };
  const hostSpaces: SpaceDirectory = { listAccessibleSpaces: vi.fn(async () => [team, personal]) };
  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createEvolveRouter(tasks, {
    skillAssetRepo: skills, stageSkillRepo: stages, hostSpaces, hostLocalSkills,
    artifactStore: store, artifactUrlStore: store, hostExtensions: [extension],
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
  await db?.close();
  if (artifactRoot) await rm(artifactRoot, { recursive: true, force: true });
});

async function seed(targetSpace: AccessibleSpace = team) {
  await skills.createAsset({ assetId: "target", versionId: "target-v1", ownerUserId: actor,
    spaceId: targetSpace.id, spaceType: targetSpace.type, spaceName: targetSpace.name,
    botId: "target-bot", externalSkillId: "local-skill", displayName: "evidence-skill",
    packageRef: "fixture:baseline", packageSha256: "fixture-checksum" });
  await stages.createImplementation({ implementationId, stageSkillId: "host-stage", ownerUserId: "publisher",
    spaceId: team.id, spaceType: "TEAM", spaceName: team.name, displayName: "Host stage",
    stage: "diagnose", mode: "preprocess", versionNo: 1, packageRef: "fixture:stage",
    packageSha256: "c".repeat(64), staticValidation: { status: "passed" } });
  await stages.registerImplementation(implementationId);
  await stages.updateIntegrationTest(implementationId, "stage-test", "test_passed");
}

async function createTask(extra: Record<string, unknown> = {}) {
  const response = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json", "X-User-Id": actor },
    body: JSON.stringify({ taskType: "full", taskName: "Task", userId: actor, botId: "target-bot",
      targetSkillAssetId: "target", model: "GLM-5.2", judgeBackend: "subagent",
      diagnoseIntent: "Check evidence", goal: "Improve reliability",
      stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId } } }, ...extra }),
  });
  expect(response.status, await response.clone().text()).toBe(201);
  return response.json();
}

describe("host task presentation through task creation", () => {
  it("persists one opaque host presentation after the target and Stage are frozen", async () => {
    await seed();
    const task = await createTask();
    expect(task.config.presentation).toEqual({ extensionId: "host.test", data: { implementationId } });
    expect(task.config.stageExtensions.diagnose.preprocess).toMatchObject({ spaceId: team.id, stageSkillId: "host-stage" });
    const stored = await tasks.findTask(task.task_id);
    expect(JSON.parse(stored!.config_json ?? "{}").presentation).toEqual(task.config.presentation);
  });

  it("keeps the default presentation for a private target and ignores request-supplied presentation", async () => {
    await seed(personal);
    const task = await createTask({ presentation: { extensionId: "forged" }, stageExtensions: {} });
    expect(task.config).not.toHaveProperty("presentation");
  });
});
