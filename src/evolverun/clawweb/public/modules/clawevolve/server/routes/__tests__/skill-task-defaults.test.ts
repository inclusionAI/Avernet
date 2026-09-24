import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import type { AccessibleSpace, SpaceDirectory } from "../../contracts/space-directory.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import type { EvolveExtension } from "../../contracts/evolve-extension.js";
import { AppConfigRepository } from "../../repositories/app-config-repository.js";
import { createSkillTaskDefaultsRouter } from "../skill-task-defaults.js";

const actor = "reader";
const team: AccessibleSpace = { id: "team-alpha", name: "Alpha", type: "TEAM", role: "MEMBER" };
const personal: AccessibleSpace = { id: "personal-reader", name: "Personal", type: "PERSONAL", role: "ADMIN" };
let db: SqliteDatabase;
let skills: SkillAssetRepository;
let config: AppConfigRepository;
let stages: StageSkillRepository;
let spaces: AccessibleSpace[];
let extensions: EvolveExtension[];
let server: ReturnType<express.Application["listen"]> | undefined;
let url: string;

const hostExtension: EvolveExtension = {
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
  config = new AppConfigRepository(db);
  stages = new StageSkillRepository(db);
  spaces = [team, personal];
  extensions = [hostExtension];
  const port: SpaceDirectory = { listAccessibleSpaces: vi.fn(async ({ identity }) => identity.userId === actor ? spaces : []) };
  const app = express();
  app.use("/api/evolve", createSkillTaskDefaultsRouter({ config, skills, stages, spaces: port, hostExtensions: extensions }));
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

async function seedAsset(space: AccessibleSpace = team) {
  return skills.createAsset({ assetId: "target", versionId: "target-v1", ownerUserId: actor,
    spaceId: space.id, spaceType: space.type, spaceName: space.name, botId: "target-bot", externalSkillId: "target-skill",
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
  it("reads database bindings afresh and respects disable without changing frozen results", async () => {
    extensions.splice(0);
    await seedAsset();
    await seedStage();
    await db.exec("UPDATE ce_stage_skill_implementations SET stage_skill_id = 'STAGESKILL-EXAMPLE'");
    const value = (action: string) => JSON.stringify({ bindings: [{ spaceType: "TEAM", spaceId: team.id,
      action, stage: "diagnose", mode: "preprocess", stageSkillId: "STAGESKILL-EXAMPLE" }] });
    await config.create({ config_key: "skill_task_stage_bindings", config_json: value("optimize") });
    const first = await (await getDefaults()).json();
    expect(first.optimize.stageExtensions.diagnose.preprocess.implementationId).toBe("eligible");
    expect(first.diagnose.stageExtensions).toBeNull();
    expect(first.hardening.stageExtensions).toBeNull();
    await config.update("skill_task_stage_bindings", { config_json: value("diagnose") });
    const second = await (await getDefaults()).json();
    expect(second.optimize.stageExtensions).toBeNull();
    expect(second.diagnose.stageExtensions.diagnose.preprocess.implementationId).toBe("eligible");
    // Previously returned selections remain unchanged; task creation owns freezing.
    expect(first.optimize.stageExtensions.diagnose.preprocess.implementationId).toBe("eligible");
    await config.update("skill_task_stage_bindings", { enabled: 0 });
    expect((await (await getDefaults()).json()).diagnose.stageExtensions).toBeNull();
  });

  it("rejects invalid stored configuration and reports unavailable configured implementations", async () => {
    extensions.splice(0);
    await seedAsset();
    await config.create({ config_key: "skill_task_stage_bindings", config_json: '{"unknown":true}' });
    expect((await getDefaults()).status).toBe(503);
    await config.update("skill_task_stage_bindings", { config_json: JSON.stringify({ bindings: [{
      spaceType: "TEAM", spaceId: team.id, action: "hardening", stage: "hardening", mode: "replace",
      stageSkillId: "STAGESKILL-MISSING",
    }] }) });
    const result = await (await getDefaults()).json();
    expect(result.hardening.unavailableReason).toContain("没有可访问");
    expect(result.hardening.stageExtensions).toBeNull();
  });

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
