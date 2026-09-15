import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import type { OcbSpace, OcbSpacePort } from "../../internal/module-api.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import type { EvolveHostExtension } from "../../services/evolve/host-extensions.js";
import { createSkillTaskDefaultsRouter } from "../skill-task-defaults.js";

const actor = "reader";
const team: OcbSpace = { id: "team-alpha", name: "Alpha", type: "TEAM", role: "MEMBER" };
const personal: OcbSpace = { id: "personal-reader", name: "Personal", type: "PERSONAL", role: "ADMIN" };
let db: SqliteDatabase;
let skills: SkillAssetRepository;
let stages: StageSkillRepository;
let spaces: OcbSpace[];
let extensions: EvolveHostExtension[];
let server: ReturnType<express.Application["listen"]> | undefined;
let url: string;

const hostExtension: EvolveHostExtension = {
  id: "host.test",
  resolveSkillTaskPreset(context) {
    if (context.targetSkill.spaceType !== "TEAM" || context.targetSkill.spaceId !== team.id) return null;
    const implementation = context.availableStageImplementations
      .filter((item) => item.stageSkillId === "host-stage" && item.spaceId === team.id
        && item.status === "registered" && item.integrationTestStatus === "test_passed")
      .sort((left, right) => right.versionNo - left.versionNo)[0];
    return implementation ? {
      stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: implementation.implementationId } } },
      launchDescription: `Host ${context.action}`,
    } : { unavailableReason: "Host requirement is unavailable" };
  },
};

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  skills = new SkillAssetRepository(db);
  stages = new StageSkillRepository(db);
  spaces = [team, personal];
  extensions = [hostExtension];
  const port: OcbSpacePort = { listAccessibleSpaces: vi.fn(async ({ identity }) => identity.userId === actor ? spaces : []) };
  const app = express();
  app.use("/api/evolve", createSkillTaskDefaultsRouter({ skills, stages, spaces: port, hostExtensions: extensions }));
  const started = await new Promise<ReturnType<express.Application["listen"]>>((resolve, reject) => {
    const instance = app.listen(0, "127.0.0.1", () => resolve(instance));
    instance.once("error", reject);
  });
  server = started;
  url = `http://127.0.0.1:${(started.address() as { port: number }).port}/api/evolve/skill-assets`;
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
  server = undefined;
  await db?.close();
});

async function seedAsset(space: OcbSpace = team) {
  return skills.createAsset({ assetId: "target", versionId: "target-v1", ownerUserId: actor,
    spaceId: space.id, spaceType: space.type, spaceName: space.name, botId: "target-bot", ocbSkillId: "target-skill",
    displayName: "Evidence Skill", packageRef: "fixture:skill", packageSha256: "fixture-checksum" });
}

async function seedStage(implementationId = "eligible", versionNo = 1) {
  await stages.createImplementation({ implementationId, stageSkillId: "host-stage", ownerUserId: "publisher",
    spaceId: team.id, spaceType: "TEAM", spaceName: team.name, displayName: "Host stage",
    stage: "diagnose", mode: "preprocess", versionNo, packageRef: "fixture:stage",
    packageSha256: "fixture-checksum", staticValidation: { status: "passed" } });
  await stages.registerImplementation(implementationId);
  await stages.updateIntegrationTest(implementationId, `test-${implementationId}`, "test_passed");
}

async function getDefaults(user = actor) {
  return fetch(`${url}/target/task-defaults`, { headers: { "X-User-Id": user } });
}

describe("GET Skill task defaults", () => {
  it("requires an authenticated reader with access to the Skill", async () => {
    await seedAsset();
    expect((await fetch(`${url}/target/task-defaults`)).status).toBe(401);
    expect((await getDefaults("outsider")).status).toBe(404);
  });

  it("applies one host contribution to all Skill task presets", async () => {
    await seedAsset();
    await seedStage("old", 1);
    await seedStage("newest", 2);
    const response = await getDefaults();
    expect(response.status, await response.clone().text()).toBe(200);
    const result = await response.json();
    for (const action of [result.diagnose, result.hardening, result.optimize]) {
      expect(action).toMatchObject({ unavailableReason: null, launchDescription: expect.stringContaining("Host"),
        stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: "newest" } } } });
    }
  });

  it("fails closed when a matching host requirement cannot be satisfied", async () => {
    await seedAsset();
    const result = await (await getDefaults()).json();
    for (const action of [result.diagnose, result.hardening, result.optimize]) {
      expect(action).toMatchObject({ stageExtensions: null, unavailableReason: "Host requirement is unavailable" });
    }
  });

  it.each(["private", "no-host"])("keeps complete standalone defaults for %s", async scenario => {
    await seedAsset(personal);
    if (scenario === "no-host") extensions.splice(0);
    await seedStage();
    const result = await (await getDefaults()).json();
    for (const action of [result.diagnose, result.hardening, result.optimize]) {
      expect(action).toMatchObject({ stageExtensions: null, unavailableReason: null, launchDescription: null });
    }
    expect(result.hardening).toMatchObject({ taskType: "hardening", goal: expect.stringContaining("加固") });
  });
});
