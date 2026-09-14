import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import type { OcbSpace, OcbSpacePort } from "../../internal/module-api.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import type { SpacePresentationPolicy } from "../../services/evolve/space-presentation.js";
import { createSkillTaskDefaultsRouter } from "../skill-task-defaults.js";

const actor = "reader";
const team: OcbSpace = { id: "actual-space-208", name: "97", type: "TEAM", role: "MEMBER" };
const other: OcbSpace = { id: "actual-space-309", name: "97", type: "TEAM", role: "MEMBER" };
const personal: OcbSpace = { id: "personal-reader", name: "Personal", type: "PERSONAL", role: "ADMIN" };
const policy: SpacePresentationPolicy = { spaceId: team.id, kind: "skill_hardening", diagnosePreprocessStageSkillId: "hardening-stage" };
let db: SqliteDatabase;
let skills: SkillAssetRepository;
let stages: StageSkillRepository;
let spaces: OcbSpace[];
let policies: SpacePresentationPolicy[];
let server: ReturnType<express.Application["listen"]> | undefined;
let url: string;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  skills = new SkillAssetRepository(db);
  stages = new StageSkillRepository(db);
  spaces = [team, other, personal];
  policies = [{ ...policy }];
  const port: OcbSpacePort = { listAccessibleSpaces: vi.fn(async ({ identity }) => identity.userId === actor ? spaces : []) };
  const app = express();
  app.use("/api/evolve", createSkillTaskDefaultsRouter({ skills, stages, spaces: port, policies }));
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

type StageInput = Partial<Parameters<StageSkillRepository["createImplementation"]>[0]> & {
  register?: boolean;
  testStatus?: "untested" | "testing" | "test_failed" | "test_passed";
  deleted?: boolean;
};
async function seedStage(implementationId = "eligible", options: StageInput = {}) {
  const { register = true, testStatus = "test_passed", deleted = false, ...overrides } = options;
  const row = await stages.createImplementation({ implementationId, stageSkillId: "hardening-stage", ownerUserId: "publisher",
    spaceId: team.id, spaceType: "TEAM", spaceName: "97", displayName: "Hardening", stage: "diagnose", mode: "preprocess",
    versionNo: 1, packageRef: "fixture:stage", packageSha256: "fixture-checksum", staticValidation: { status: "passed" }, ...overrides });
  if (register) await stages.registerImplementation(implementationId);
  if (testStatus !== "untested") await stages.updateIntegrationTest(implementationId, `test-${implementationId}`, testStatus);
  if (deleted) await stages.deleteImplementation(implementationId, row.owner_user_id);
}

async function getDefaults(user = actor, asset = "target") {
  return fetch(`${url}/${asset}/task-defaults`, { headers: { "X-User-Id": user } });
}
async function defaults() {
  const response = await getDefaults();
  expect(response.status, await response.clone().text()).toBe(200);
  return response.json();
}

describe("GET Skill task defaults (real repositories)", () => {
  it.each(["missing", "nonmember", "owner-left"])("returns 404 for %s without revealing defaults", async scenario => {
    await seedAsset();
    await seedStage();
    if (scenario === "owner-left") spaces = [personal];
    const response = await getDefaults(scenario === "nonmember" ? "outsider" : actor, scenario === "missing" ? "missing" : "target");
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "Skill 不存在" });
  });

  it("requires authenticated identity", async () => {
    await seedAsset();
    expect((await fetch(`${url}/target/task-defaults`)).status).toBe(401);
  });

  it("selects the newest registered AND test_passed diagnostic preprocessor, ignoring newer untested versions", async () => {
    await seedAsset();
    await seedStage("old", { versionNo: 1 });
    await seedStage("eligible", { versionNo: 2 });
    await seedStage("untested-new", { versionNo: 3, testStatus: "untested" });
    await seedStage("unregistered-new", { versionNo: 4, register: false });
    const result = await defaults();
    expect(result).toMatchObject({ assetId: "target", botId: "target-bot", userId: actor,
      diagnose: { taskType: "diagnose" }, optimize: { taskType: "full", unavailableReason: null,
        stageExtensions: { diagnose: { preprocess: { enabled: true, implementationId: "eligible" } } } } });
    expect(result.diagnose).not.toHaveProperty("stageExtensions");
  });

  it.each([
    { reason: "validated only", register: false, testStatus: "untested" },
    { reason: "test_passed without registration", register: false },
    { reason: "registered but untested", testStatus: "untested" },
    { reason: "registered but testing", testStatus: "testing" },
    { reason: "registered but test_failed", testStatus: "test_failed" },
    { reason: "deleted", deleted: true },
    { reason: "wrong Stage", stage: "plan" },
    { reason: "wrong mode", mode: "postprocess" },
    { reason: "wrong Stage Skill ID", stageSkillId: "not-the-policy-stage" },
    { reason: "same space name, different space ID", spaceId: other.id },
    { reason: "PERSONAL implementation", spaceType: "PERSONAL" },
  ] satisfies Array<StageInput & { reason: string }>)("does not fabricate a binding for $reason", async ({ reason: _reason, ...options }) => {
    await seedAsset();
    await seedStage("ineligible", options);
    expect((await defaults()).optimize).toMatchObject({ stageExtensions: null, unavailableReason: expect.any(String) });
  });

  it("removes the default when Stage permission is revoked even if the caller owns the Stage", async () => {
    await seedAsset(personal);
    await seedStage("owned-stage", { ownerUserId: actor });
    expect((await defaults()).optimize.stageExtensions.diagnose.preprocess.implementationId).toBe("owned-stage");
    spaces = [personal];
    expect((await defaults()).optimize).toMatchObject({ stageExtensions: null, unavailableReason: expect.any(String) });
  });

  it.each(["no policy", "no implementation", "space name is not an ID"])("returns an explicit unavailable result for %s", async reason => {
    await seedAsset();
    if (reason !== "no implementation") await seedStage();
    if (reason === "no policy") policies.splice(0);
    if (reason === "space name is not an ID") policies[0] = { ...policy, spaceId: "97" };
    expect((await defaults()).optimize).toMatchObject({ stageExtensions: null, unavailableReason: expect.any(String) });
  });

  it("uses actual IDs after a rename and prefers the target space's policy over another accessible team", async () => {
    await seedAsset({ ...team, name: "Renamed team" });
    policies.unshift({ ...policy, spaceId: other.id, diagnosePreprocessStageSkillId: "other-stage" });
    await seedStage("other-eligible", { spaceId: other.id, stageSkillId: "other-stage", versionNo: 50 });
    await seedStage("target-eligible", { spaceName: "Renamed team" });
    spaces = [{ ...team, name: "Renamed again" }, other, personal];
    expect((await defaults()).optimize.stageExtensions).toEqual({ diagnose: { preprocess: { enabled: true, implementationId: "target-eligible" } } });
  });
});
