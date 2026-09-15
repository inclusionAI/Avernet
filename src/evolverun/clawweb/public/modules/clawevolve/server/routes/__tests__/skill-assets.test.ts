import { afterEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { createHash } from "node:crypto";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createSkillAssetsRouter } from "../skill-assets.js";
import { FilesystemObjectStore } from "../../services/object-storage/filesystem-object-store.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { EvolveRepository } from "../../repositories/evolve-repository.js";

let server: ReturnType<express.Application["listen"]> | undefined;
let artifactRoot: string | undefined;
let database: SqliteDatabase | undefined;

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
  server = undefined;
  if (database) await database.close();
  database = undefined;
  if (artifactRoot) await rm(artifactRoot, { recursive: true, force: true });
  artifactRoot = undefined;
});

async function startRouter(realRepository = false) {
  artifactRoot = await mkdtemp(join(tmpdir(), "skill-snapshot-test-"));
  const store = new FilesystemObjectStore(artifactRoot);
  const exportLocalSkill = vi.fn();
  const listLocalSkills = vi.fn(async () => [{ skillId: "47", displayName: "Evidence Skill", description: "OCB description" }]);
  const getBotMetadata = vi.fn(async () => ({ ownerId: "bot-owner" as string | null }));
  const asset = { asset_id: "SKILL-1", owner_user_id: "owner-1" };
  const version = {
    version_id: "SKVER-1",
    package_ref: "oss://clawevolve-artifacts/versions/v1/package.zip",
    baseline_package_ref: null as string | null,
  };
  if (realRepository) {
    database = new SqliteDatabase(new Database(":memory:"));
    await runMigrations(database, "sqlite");
  }
  const repo = database ? new SkillAssetRepository(database) : {
    findAsset: async () => asset,
    findVersion: async () => version,
  } as unknown as SkillAssetRepository;
  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createSkillAssetsRouter({
    repo,
    ocbLocalSkills: { exportLocalSkill, listLocalSkills, getBotMetadata } as never,
    artifactStore: store,
  }));
  app.use((_error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    res.status(500).json({ error: "Internal Server Error" });
  });
  server = await new Promise<ReturnType<express.Application["listen"]>>((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  return {
    url: `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve/skill-assets/SKILL-1/versions/SKVER-1`,
    store, version, exportLocalSkill, listLocalSkills, getBotMetadata, repo,
    baseUrl: `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve`,
  };
}

async function zip(content: string) {
  return new JSZip().file("SKILL.md", content).generateAsync({ type: "nodebuffer" });
}

describe("Skill historical snapshot availability", () => {
  it('presents legacy Skill diagnosis events with their exact frozen baseline version', async () => {
    const test = await startRouter(true);
    await test.repo.createAsset({ assetId: 'ASSET', versionId: 'BASE', ownerUserId: 'owner-1', botId: 'bot-1',
      ocbSkillId: '47', displayName: 'Skill', packageRef: 'base', packageSha256: 'base-sha' });
    const tasks = new EvolveRepository(database!);
    await tasks.createTask({ taskId: 'TASK-DIAGNOSE', taskType: 'diagnose', taskName: 'Diagnose Skill',
      userId: 'owner-1', botId: 'bot-1', createdBy: 'owner-1', configJson: JSON.stringify({
        targetSkill: { assetId: 'ASSET', baseline: { sha256: 'base-sha' } },
      }) });
    await database!.exec(
      "UPDATE ce_skill_audit_events SET event_type = 'evolution_started', version_id = NULL, version_no = NULL WHERE task_id = ?",
      ['TASK-DIAGNOSE'],
    );
    const response = await fetch(`${test.baseUrl}/skill-events`, { headers: { 'X-User-Id': 'owner-1' } });
    expect(response.status).toBe(200);
    const { items } = await response.json();
    expect(items[0]).toMatchObject({ type: 'diagnosis_started', version: 'v1', versionId: 'BASE' });
  });

  it('projects only the frozen Test Bench association, never private event detail or live reports', async () => {
    const test = await startRouter(true);
    await test.repo.createAsset({ assetId: 'ASSET', versionId: 'BASE', ownerUserId: 'owner-1', botId: 'bot-1',
      ocbSkillId: '47', displayName: 'Skill', packageRef: 'base', packageSha256: 'base' });
    const tasks = new EvolveRepository(database!);
    await tasks.createTask({ taskId: 'TASK', taskType: 'full', taskName: 'Audit projection', userId: 'owner-1', botId: 'bot-1', createdBy: 'owner-1',
      configJson: JSON.stringify({ targetSkill: { assetId: 'ASSET' } }) });
    await tasks.createStep({ taskId: 'TASK', stepId: 'OPTIMIZE', stepType: 'optimize', stepNo: 1, roundNo: 1, command: 'test' });
    await tasks.updateStepStatus('OPTIMIZE', { status: 'succeeded', output: { privateOutput: 'never-public',
      scoreComparison: { name: 'test_score', baseline: null, candidate: 0, delta: null, privateMetric: 'never-public' } } });
    await tasks.freezeSkillTestBenchSelection('TASK', 'OPTIMIZE');
    await tasks.recordSkillOperation('TASK', { key: 'failure', type: 'version_apply_failed', actorType: 'system', result: 'conflict',
      detail: { signedUrl: 'never-public', errorCode: 'private-code' } });
    await tasks.updateStepStatus('OPTIMIZE', { status: 'succeeded', output: { scoreComparison: { candidate: 999 } } });
    const response = await fetch(`${test.baseUrl}/skill-events`, { headers: { 'X-User-Id': 'owner-1' } });
    const body = await response.json();
    expect(response.status).toBe(200);
    expect(body.items[0].testBench).toEqual({ taskId: 'TASK', stepId: 'OPTIMIZE', round: 1,
      scoreComparison: { name: 'test_score', baseline: null, candidate: 0, delta: null } });
    expect(body.items.slice(1).every((item: { testBench: unknown }) => item.testBench === null)).toBe(true);
    expect(JSON.stringify(body)).not.toMatch(/never-public|private-code|detail_json|999/);
    const other = await fetch(`${test.baseUrl}/skill-events`, { headers: { 'X-User-Id': 'owner-2' } });
    expect(await other.json()).toEqual({ items: [] });
  });

  it("enriches old assets once per Bot without ZIP reads or changing registrar authorization", async () => {
    const test = await startRouter(true);
    for (const skillId of ["47", "48"]) {
      await test.repo.createAsset({ assetId: `OLD-${skillId}`, versionId: `V-${skillId}`,
        ownerUserId: "owner-1", botId: "bot-1", ocbSkillId: skillId, displayName: skillId,
        description: "old ZIP-derived description", packageRef: "oss://clawevolve-artifacts/missing.zip", packageSha256: "old" });
    }
    const readObject = vi.spyOn(test.store, "getObject");
    const response = await fetch(`${test.baseUrl}/skill-assets`, { headers: { "X-User-Id": "owner-1" } });
    expect(response.status).toBe(200);
    const { items } = await response.json();
    expect(items).toHaveLength(2);
    expect(items.find((item: { skillId: string }) => item.skillId === "47")).toMatchObject({ ownerId: "bot-owner", description: "OCB description" });
    expect(items.find((item: { skillId: string }) => item.skillId === "48")).toMatchObject({ ownerId: "bot-owner", description: null });
    expect(test.getBotMetadata).toHaveBeenCalledTimes(1);
    expect(test.listLocalSkills).toHaveBeenCalledTimes(1);
    expect(test.exportLocalSkill).not.toHaveBeenCalled();
    expect(readObject).not.toHaveBeenCalled();
    expect((await test.repo.findAsset("OLD-47"))?.owner_user_id).toBe("owner-1");
    const denied = await fetch(`${test.baseUrl}/skill-assets/OLD-47`, { headers: { "X-User-Id": "bot-owner" } });
    expect(denied.status).toBe(404);
    test.getBotMetadata.mockRejectedValueOnce(new Error("unreachable"));
    test.listLocalSkills.mockRejectedValueOnce(new Error("unreachable"));
    const unknown = await fetch(`${test.baseUrl}/skill-assets/OLD-47`, { headers: { "X-User-Id": "owner-1" } });
    expect(await unknown.json()).toMatchObject({ ownerId: null, description: null });
  });

  it("registers the OCB description even when ZIP frontmatter says something different", async () => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockResolvedValue({ packageBytes: await zip("---\ndescription: wrong ZIP text\n---\n"),
      displayName: "Metadata", sha256: "checksum", description: "OCB authoritative description" });
    const response = await fetch(`${test.baseUrl}/skill-assets`, { method: "POST",
      headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" }, body: JSON.stringify({botId: "bot-1", skillId: "47"}) });
    expect(response.status).toBe(201);
    const item = await response.json();
    expect(item).toMatchObject({ownerId: "bot-owner", description: "OCB authoritative description"});
    expect((await test.repo.findAsset(item.assetId))?.description).toBe("OCB authoritative description");
  });

  it.each([502, 503])("returns an explainable %s when OCB export is unavailable", async (status) => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockRejectedValue(Object.assign(new Error("OCB Skill 服务暂时不可用，请稍后重试"), {
      code: "OCB_LOCAL_SKILL_UNAVAILABLE", status, statusCode: status, upstreamStatus: 500,
    }));
    const response = await fetch(`${test.baseUrl}/skill-assets`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ botId: "bot-1", skillId: "47" }),
    });
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ code: "OCB_LOCAL_SKILL_UNAVAILABLE", error: "OCB Skill 服务暂时不可用，请稍后重试" });
  });

  it.each([401, 403, 409])("preserves OCB request rejection %s instead of turning it into 500", async (statusCode) => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockRejectedValue(Object.assign(new Error("OCB Skill 请求未获允许或内容已变更"), {
      code: "OCB_LOCAL_SKILL_REQUEST_FAILED", statusCode,
    }));
    const response = await fetch(`${test.baseUrl}/skill-assets`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ botId: "bot-1", skillId: "47" }),
    });
    expect(response.status).toBe(statusCode);
    expect(await response.json()).toEqual({ code: "OCB_LOCAL_SKILL_REQUEST_FAILED", error: "OCB Skill 请求未获允许或内容已变更" });
  });

  it.each([
    { code: "UNKNOWN_OCB_ERROR", status: 503 },
    { code: "OCB_LOCAL_SKILL_UNAVAILABLE", status: 500 },
    { code: "OCB_LOCAL_SKILL_REQUEST_FAILED", status: 503 },
    { code: "OCB_LOCAL_SKILL_REQUEST_FAILED", status: "403" },
  ])("does not expose or remap an unknown error contract: %j", async (fields) => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockRejectedValue(Object.assign(new Error("Unclassified internal details"), fields));
    const response = await fetch(`${test.baseUrl}/skill-assets`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ botId: "bot-1", skillId: "47" }),
    });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "Internal Server Error" });
  });

  it("lists one asset with live OCB description and Bot owner even when it has multiple versions", async () => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockResolvedValue({
      packageBytes: await zip("---\nname: evidence-skill\ndescription: Diagnose actual session evidence.\n---\n# Skill\n"),
      displayName: "Evidence Skill", sha256: "registered-sha",
    });
    const registration = await fetch(`${test.baseUrl}/skill-assets`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ botId: "bot-1", skillId: "47" }),
    });
    expect(registration.status).toBe(201);
    const { assetId } = await registration.json();
    const readObject = vi.spyOn(test.store, "getObject");
    await test.repo.createAcceptedVersion({
      assetId, versionId: "SKVER-2", sourceTaskId: "TASK-ACCEPTED",
      packageRef: "oss://clawevolve-artifacts/registered.zip", packageSha256: "registered-sha",
      baselinePackageRef: "oss://clawevolve-artifacts/registered.zip", baselinePackageSha256: "registered-sha",
    });
    const response = await fetch(`${test.baseUrl}/skill-assets`, { headers: { "X-User-Id": "owner-1" } });
    expect(response.status).toBe(200);
    const { items } = await response.json();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ assetId, name: "Evidence Skill", description: "OCB description", ownerId: "bot-owner", botId: "bot-1", currentVersion: "v2" });
    expect(items[0].createdAt).toBeTruthy();
    expect(items[0].updatedAt).toBeTruthy();
    expect(test.exportLocalSkill).toHaveBeenCalledTimes(1);
    expect(readObject).not.toHaveBeenCalled();
  });

  it("reads persisted operation events, never reconstructing applied events from version rows", async () => {
    const test = await startRouter(true);
    test.listLocalSkills.mockResolvedValue([{ skillId: "SKILL-1", displayName: "Skill", description: "Live OCB skill description" }]);
    for (const [assetId, ownerUserId] of [["SKILL-1", "owner-1"], ["SKILL-OTHER", "owner-2"]]) {
      await test.repo.createAsset({ assetId, versionId: `BASE-${assetId}`, ownerUserId, botId: "bot-1", ocbSkillId: assetId,
        displayName: assetId, description: 'Description at registration', packageRef: "oss://clawevolve-artifacts/baseline.zip", packageSha256: "base-sha" });
    }
    const accepted = { assetId: "SKILL-1", versionId: "ACCEPTED-1", sourceTaskId: "REAL-SOURCE-TASK",
      packageRef: "oss://clawevolve-artifacts/candidate.zip", packageSha256: "candidate-sha",
      baselinePackageRef: "oss://clawevolve-artifacts/task-baseline.zip", baselinePackageSha256: "baseline-sha" };
    await test.repo.createAcceptedVersion(accepted);
    await test.repo.createAcceptedVersion(accepted);
    const response = await fetch(`${test.baseUrl}/skill-events`, { headers: { "X-User-Id": "owner-1" } });
    expect(response.status).toBe(200);
    const { items } = await response.json();
    expect(items).toHaveLength(1);
    expect(items[0]).toMatchObject({ assetId: "SKILL-1", ownerId: "bot-owner", botId: "bot-1", version: "v1", versionId: 'BASE-SKILL-1', type: "registered", taskId: null, actorId: 'owner-1', actorType: 'user', result: 'succeeded' });
    expect(items[0].description).toBe('Description at registration');
    expect(test.listLocalSkills).not.toHaveBeenCalled();
    expect(items.every((item: Record<string, unknown>) => !('score' in item) && !('improved' in item))).toBe(true);
    expect(test.exportLocalSkill).not.toHaveBeenCalled();
    expect((await fetch(`${test.baseUrl}/skill-events`)).status).toBe(401);
    const empty = await fetch(`${test.baseUrl}/skill-events`, { headers: { "X-User-Id": "no-records-owner" } });
    expect(await empty.json()).toEqual({ items: [] });
  });

  it.each(["# No description\n", "---\ndescription: [not, text]\n---\n", "---\ndescription: [malformed\n---\n"])("does not invent a description from absent or unreadable metadata: %s", async (content) => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockResolvedValue({ packageBytes: await zip(content), displayName: "No metadata", sha256: "package-sha" });
    const response = await fetch(`${test.baseUrl}/skill-assets`, { method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" }, body: JSON.stringify({ botId: "bot-1", skillId: "47" }) });
    expect(response.status).toBe(201);
    expect(await response.json()).toMatchObject({ description: null });
  });

  it("reports a missing historical package without substituting the current OCB Skill", async () => {
    const test = await startRouter();
    const response = await fetch(`${test.url}/content`, { headers: { "X-User-Id": "owner-1" } });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({
      code: "SKILL_SNAPSHOT_UNAVAILABLE",
      error: "该版本的历史快照在当前存储中不可用，无法查看内容或差异；不会以当前 Skill 内容替代。",
    });
    expect(test.exportLocalSkill).not.toHaveBeenCalled();
  });

  it("reports a missing frozen baseline instead of comparing with a different version", async () => {
    const test = await startRouter();
    test.version.baseline_package_ref = "oss://clawevolve-artifacts/tasks/source-task/baseline.zip";
    await test.store.putObject("versions/v1/package.zip", await zip("# Candidate"), "application/zip");
    const response = await fetch(`${test.url}/diff`, { headers: { "X-User-Id": "owner-1" } });
    expect(response.status).toBe(404);
    expect(await response.json()).toMatchObject({ code: "SKILL_SNAPSHOT_UNAVAILABLE" });
    expect(test.exportLocalSkill).not.toHaveBeenCalled();
  });

  it("registers an actual frozen file and serves its content and initial diff through HTTP", async () => {
    const test = await startRouter(true);
    const packageBytes = await zip("# Frozen at registration\n");
    test.exportLocalSkill.mockResolvedValue({
      packageBytes, displayName: "historical-skill",
      sha256: `sha256:${createHash("sha256").update(packageBytes).digest("hex")}`,
    });
    const headers = { "X-User-Id": "owner-1", "Content-Type": "application/json" };
    const registration = await fetch(`${test.baseUrl}/skill-assets`, {
      method: "POST", headers, body: JSON.stringify({ botId: "bot-1", skillId: "44" }),
    });
    expect(registration.status).toBe(201);
    const asset = await registration.json();
    const detail = await fetch(`${test.baseUrl}/skill-assets/${asset.assetId}`, { headers });
    const { versions } = await detail.json();
    const url = `${test.baseUrl}/skill-assets/${asset.assetId}/versions/${versions[0].versionId}`;
    test.exportLocalSkill.mockRejectedValue(new Error("current OCB content must not be read"));
    const content = await fetch(`${url}/content`, { headers });
    expect(content.status).toBe(200);
    expect(await content.json()).toMatchObject({ selected: { path: "SKILL.md", content: "# Frozen at registration\n" } });
    const diff = await fetch(`${url}/diff`, { headers });
    expect(diff.status).toBe(200);
    expect(await diff.json()).toEqual({ baseline: null, files: [] });
    expect(test.exportLocalSkill).toHaveBeenCalledTimes(1);
  });

  it("compares an accepted version to its frozen task baseline, not the previous version", async () => {
    const test = await startRouter(true);
    await test.store.putObject("v1.zip", await zip("# Registered v1\n"), "application/zip");
    await test.store.putObject("task-baseline.zip", await zip("# External change before task\n"), "application/zip");
    await test.store.putObject("candidate.zip", await zip("# Accepted candidate\n"), "application/zip");
    await test.repo.createAsset({
      assetId: "SKILL-1", versionId: "SKVER-1", ownerUserId: "owner-1", botId: "bot-1", ocbSkillId: "44",
      displayName: "historical-skill", packageRef: "oss://clawevolve-artifacts/v1.zip", packageSha256: "v1-sha",
    });
    await test.repo.createAcceptedVersion({
      assetId: "SKILL-1", versionId: "SKVER-2", sourceTaskId: "EV-SOURCE",
      packageRef: "oss://clawevolve-artifacts/candidate.zip", packageSha256: "candidate-sha",
      baselinePackageRef: "oss://clawevolve-artifacts/task-baseline.zip", baselinePackageSha256: "task-sha",
    });
    const response = await fetch(`${test.baseUrl}/skill-assets/SKILL-1/versions/SKVER-2/diff`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({
      baseline: { sha256: "task-sha" },
      files: [{ path: "SKILL.md", change: "modified", before: "# External change before task\n", after: "# Accepted candidate\n" }],
    });
  });

  it("does not expose snapshot availability to another owner", async () => {
    const test = await startRouter();
    const response = await fetch(`${test.url}/content`, { headers: { "X-User-Id": "another-owner" } });
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "Skill 不存在" });
  });

  it("does not misclassify an unknown storage failure as a missing snapshot", async () => {
    const test = await startRouter();
    vi.spyOn(test.store, "getObject").mockRejectedValue(Object.assign(new Error("storage denied"), { code: "EACCES" }));
    const response = await fetch(`${test.url}/content`, { headers: { "X-User-Id": "owner-1" } });
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual({ error: "Internal Server Error" });
  });
});
