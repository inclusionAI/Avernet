import { afterEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import JSZip from "jszip";
import { createHash } from "node:crypto";
import { once } from "node:events";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { mkdtemp, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createSkillAssetsRouter } from "../skill-assets.js";
import { FilesystemObjectStore } from "../../services/object-storage/filesystem-object-store.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { applySkillVersion } from "../../services/evolve/skill-application.js";

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

async function startRouter(realRepository = false, spaces?: Array<{ id: string; name: string; type: "TEAM"; role: "MEMBER" }>) {
  artifactRoot = await mkdtemp(join(tmpdir(), "skill-snapshot-test-"));
  const store = new FilesystemObjectStore(artifactRoot);
  const exportLocalSkill = vi.fn();
  const replaceLocalSkill = vi.fn(async (input: { packageBytes: Buffer }) => ({
    sha256: `sha256:${createHash("sha256").update(input.packageBytes).digest("hex")}`,
  }));
  const listLocalSkills = vi.fn(async () => [{ skillId: "47", displayName: "Evidence Skill", description: "Host description" }]);
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
    hostLocalSkills: { exportLocalSkill, replaceLocalSkill, listLocalSkills, getBotMetadata } as never,
    hostSpaces: spaces ? { listAccessibleSpaces: vi.fn(async () => spaces) } : undefined,
    artifactStore: store,
  }));
  app.use((_error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    res.status(500).json({ error: "Internal Server Error" });
  });
  server = app.listen(0);
  await once(server, "listening");
  return {
    url: `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve/skill-assets/SKILL-1/versions/SKVER-1`,
    store, version, exportLocalSkill, replaceLocalSkill, listLocalSkills, getBotMetadata, repo,
    baseUrl: `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve`,
  };
}

async function zip(content: string) {
  return new JSZip().file("SKILL.md", content).generateAsync({ type: "nodebuffer" });
}

describe("Skill historical snapshot availability", () => {
  it("edits the current frozen Skill into a new immutable version", async () => {
    const test = await startRouter(true);
    const baseline = await zip("# Version 1\n");
    const baselineSha = `sha256:${createHash("sha256").update(baseline).digest("hex")}`;
    await test.store.putObject("manual/v1.zip", baseline, "application/zip");
    await test.repo.createAsset({
      assetId: "EDITABLE", versionId: "VERSION-1", ownerUserId: "owner-1", botId: "bot-1",
      externalSkillId: "47", displayName: "Editable Skill", packageRef: "oss://clawevolve-artifacts/manual/v1.zip",
      packageSha256: baselineSha,
    });
    test.exportLocalSkill.mockResolvedValue({
      packageBytes: baseline, displayName: "Editable Skill", sha256: baselineSha,
    });

    const response = await fetch(`${test.baseUrl}/skill-assets/EDITABLE/versions`, {
      method: "POST",
      headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "edit", baseVersionId: "VERSION-1",
        edits: [{ path: "SKILL.md", content: "# Version 2\n" }],
      }),
    });

    expect(response.status).toBe(201);
    const created = await response.json();
    expect(created).toMatchObject({ version: "v2", creationKind: "edit", createdBy: "owner-1" });
    expect(test.replaceLocalSkill).toHaveBeenCalledWith(expect.objectContaining({
      botId: "bot-1", skillId: "47", expectedSha256: baselineSha,
    }));
    const versions = await test.repo.listVersions("EDITABLE");
    expect(versions.map((item) => item.version_no)).toEqual([2, 1]);
    const oldContent = await fetch(`${test.baseUrl}/skill-assets/EDITABLE/versions/VERSION-1/content`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect((await oldContent.json()).selected.content).toBe("# Version 1\n");
    const newContent = await fetch(`${test.baseUrl}/skill-assets/EDITABLE/versions/${created.versionId}/content`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect((await newContent.json()).selected.content).toBe("# Version 2\n");
    const diff = await fetch(`${test.baseUrl}/skill-assets/EDITABLE/versions/${created.versionId}/diff`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect((await diff.json()).files).toEqual([{
      path: "SKILL.md", change: "modified", before: "# Version 1\n", after: "# Version 2\n",
    }]);
  });

  it("uploads a Skill package as a new version without replacing history", async () => {
    const test = await startRouter(true);
    const baseline = await zip("# Version 1\n");
    const candidate = await zip("# Uploaded Version\n");
    const baselineSha = `sha256:${createHash("sha256").update(baseline).digest("hex")}`;
    await test.store.putObject("upload/v1.zip", baseline, "application/zip");
    await test.repo.createAsset({
      assetId: "UPLOADABLE", versionId: "UPLOAD-V1", ownerUserId: "owner-1", botId: "bot-1",
      externalSkillId: "47", displayName: "Uploadable Skill", packageRef: "oss://clawevolve-artifacts/upload/v1.zip",
      packageSha256: baselineSha,
    });
    test.exportLocalSkill.mockResolvedValue({ packageBytes: baseline, displayName: "Uploadable Skill", sha256: baselineSha });
    const form = new FormData();
    form.set("mode", "upload");
    form.set("baseVersionId", "UPLOAD-V1");
    form.set("package", new Blob([candidate], { type: "application/zip" }), "skill.zip");

    const response = await fetch(`${test.baseUrl}/skill-assets/UPLOADABLE/versions`, {
      method: "POST", headers: { "X-User-Id": "owner-1" }, body: form,
    });

    expect(response.status).toBe(201);
    const created = await response.json();
    expect(created).toMatchObject({ version: "v2", creationKind: "upload" });
    expect((await test.repo.listVersions("UPLOADABLE")).map((item) => item.version_id)).toEqual([
      created.versionId, "UPLOAD-V1",
    ]);
    const content = await fetch(`${test.baseUrl}/skill-assets/UPLOADABLE/versions/${created.versionId}/content`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect((await content.json()).selected.content).toBe("# Uploaded Version\n");
  });

  it("rolls a historical version forward as a traceable new latest version", async () => {
    const test = await startRouter(true);
    const v1 = await zip("# Historical Version\n");
    const v2 = await zip("# Current Version\n");
    const v1Sha = `sha256:${createHash("sha256").update(v1).digest("hex")}`;
    const v2Sha = `sha256:${createHash("sha256").update(v2).digest("hex")}`;
    await test.store.putObject("rollback/v1.zip", v1, "application/zip");
    await test.store.putObject("rollback/v2.zip", v2, "application/zip");
    await test.repo.createAsset({
      assetId: "ROLLBACK", versionId: "ROLLBACK-V1", ownerUserId: "owner-1", botId: "bot-1",
      externalSkillId: "47", displayName: "Rollback Skill", packageRef: "oss://clawevolve-artifacts/rollback/v1.zip",
      packageSha256: v1Sha,
    });
    await test.repo.createManualVersion({
      assetId: "ROLLBACK", versionId: "ROLLBACK-V2", baseVersionId: "ROLLBACK-V1",
      packageRef: "oss://clawevolve-artifacts/rollback/v2.zip", packageSha256: v2Sha,
      creationKind: "edit", sourceVersionId: "ROLLBACK-V1", sourceVersionNo: 1, createdBy: "owner-1",
    });
    test.exportLocalSkill.mockResolvedValue({ packageBytes: v2, displayName: "Rollback Skill", sha256: v2Sha });

    const response = await fetch(`${test.baseUrl}/skill-assets/ROLLBACK/versions`, {
      method: "POST",
      headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "rollback", baseVersionId: "ROLLBACK-V2", sourceVersionId: "ROLLBACK-V1" }),
    });

    expect(response.status).toBe(201);
    const created = await response.json();
    expect(created).toMatchObject({
      version: "v3", creationKind: "rollback", createdBy: "owner-1",
      sourceVersion: { versionId: "ROLLBACK-V1", version: "v1" },
    });
    const versions = await test.repo.listVersions("ROLLBACK");
    expect(versions.map((item) => [item.version_id, item.version_no])).toEqual([
      [created.versionId, 3], ["ROLLBACK-V2", 2], ["ROLLBACK-V1", 1],
    ]);
    const content = await fetch(`${test.baseUrl}/skill-assets/ROLLBACK/versions/${created.versionId}/content`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect((await content.json()).selected.content).toBe("# Historical Version\n");
    const diff = await fetch(`${test.baseUrl}/skill-assets/ROLLBACK/versions/${created.versionId}/diff`, {
      headers: { "X-User-Id": "owner-1" },
    });
    expect((await diff.json()).files).toEqual([{
      path: "SKILL.md", change: "modified", before: "# Current Version\n", after: "# Historical Version\n",
    }]);
    const detail = await fetch(`${test.baseUrl}/skill-assets/ROLLBACK`, { headers: { "X-User-Id": "owner-1" } });
    expect((await detail.json()).versions[0]).toMatchObject({
      versionId: created.versionId, version: "v3", creationKind: "rollback", createdBy: "owner-1",
      sourceVersion: { versionId: "ROLLBACK-V1", version: "v1" },
    });
  });

  it("rejects stale or inaccessible version writes before touching the live Skill", async () => {
    const test = await startRouter(true);
    const v1 = await zip("# Version 1\n");
    const v2 = await zip("# Version 2\n");
    await test.store.putObject("conflict/v1.zip", v1, "application/zip");
    await test.store.putObject("conflict/v2.zip", v2, "application/zip");
    await test.repo.createAsset({
      assetId: "CONFLICT", versionId: "CONFLICT-V1", ownerUserId: "owner-1", botId: "bot-1",
      externalSkillId: "47", displayName: "Conflict Skill", packageRef: "oss://clawevolve-artifacts/conflict/v1.zip",
      packageSha256: "v1-sha",
    });
    await test.repo.createManualVersion({
      assetId: "CONFLICT", versionId: "CONFLICT-V2", baseVersionId: "CONFLICT-V1",
      packageRef: "oss://clawevolve-artifacts/conflict/v2.zip", packageSha256: "v2-sha",
      creationKind: "edit", sourceVersionId: "CONFLICT-V1", sourceVersionNo: 1, createdBy: "owner-1",
    });
    const request = (userId: string) => fetch(`${test.baseUrl}/skill-assets/CONFLICT/versions`, {
      method: "POST", headers: { "X-User-Id": userId, "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "edit", baseVersionId: "CONFLICT-V1", edits: [{ path: "SKILL.md", content: "# Stale\n" }] }),
    });

    expect((await request("owner-1")).status).toBe(409);
    expect((await request("another-owner")).status).toBe(404);
    expect(test.exportLocalSkill).not.toHaveBeenCalled();
    expect(test.replaceLocalSkill).not.toHaveBeenCalled();
    expect(await test.repo.listVersions("CONFLICT")).toHaveLength(2);
  });

  it("does not let a readable team-space member create Skill versions", async () => {
    const test = await startRouter(true, [{ id: "team-1", name: "Team", type: "TEAM", role: "MEMBER" }]);
    const baseline = await zip("# Team Skill\n");
    await test.store.putObject("team/v1.zip", baseline, "application/zip");
    await test.repo.createAsset({
      assetId: "TEAM-SKILL", versionId: "TEAM-V1", ownerUserId: "owner-1", botId: "bot-1",
      spaceId: "team-1", spaceType: "TEAM", spaceName: "Team", externalSkillId: "47",
      displayName: "Team Skill", packageRef: "oss://clawevolve-artifacts/team/v1.zip", packageSha256: "sha",
    });
    const response = await fetch(`${test.baseUrl}/skill-assets/TEAM-SKILL/versions`, {
      method: "POST", headers: { "X-User-Id": "member-1", "Content-Type": "application/json" },
      body: JSON.stringify({
        mode: "edit", baseVersionId: "TEAM-V1",
        edits: [{ path: "SKILL.md", content: "# Changed\n" }],
      }),
    });
    expect(response.status).toBe(404);
    expect(test.exportLocalSkill).not.toHaveBeenCalled();
    expect(test.replaceLocalSkill).not.toHaveBeenCalled();
  });

  it('presents a diagnosis business event with its exact frozen baseline version', async () => {
    const test = await startRouter(true);
    await test.repo.createAsset({ assetId: 'ASSET', versionId: 'BASE', ownerUserId: 'owner-1', botId: 'bot-1',
      externalSkillId: '47', displayName: 'Skill', packageRef: 'base', packageSha256: 'base-sha' });
    const tasks = new EvolveRepository(database!);
    await tasks.createTask({ taskId: 'TASK-DIAGNOSE', taskType: 'diagnose', taskName: 'Diagnose Skill',
      userId: 'owner-1', botId: 'bot-1', createdBy: 'owner-1', configJson: JSON.stringify({
        targetSkill: { assetId: 'ASSET', baseline: { sha256: 'base-sha', versionId: 'BASE', versionNo: 1 } },
      }) });
    const response = await fetch(`${test.baseUrl}/skill-events`, { headers: { 'X-User-Id': 'owner-1' } });
    expect(response.status).toBe(200);
    const { items } = await response.json();
    expect(items[0]).toMatchObject({ type: 'diagnosis', status: 'running',
      versionFrom: { version: 'v1', versionId: 'BASE' } });
    const history = await fetch(`${test.baseUrl}/skill-assets/ASSET/history`, { headers: { 'X-User-Id': 'owner-1' } });
    expect(history.status).toBe(200);
    expect((await history.json()).events).toMatchObject([
      { eventId: items[0].eventId, type: 'diagnosis', taskId: 'TASK-DIAGNOSE' },
      { type: 'registered', taskId: null },
    ]);
  });

  it('projects only the frozen Test Bench association, never private event detail or live reports', async () => {
    const test = await startRouter(true);
    await test.repo.createAsset({ assetId: 'ASSET', versionId: 'BASE', ownerUserId: 'owner-1', botId: 'bot-1',
      externalSkillId: '47', displayName: 'Skill', packageRef: 'base', packageSha256: 'base' });
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
        ownerUserId: "owner-1", botId: "bot-1", externalSkillId: skillId, displayName: skillId,
        description: "old ZIP-derived description", packageRef: "oss://clawevolve-artifacts/missing.zip", packageSha256: "old" });
    }
    const readObject = vi.spyOn(test.store, "getObject");
    const response = await fetch(`${test.baseUrl}/skill-assets`, { headers: { "X-User-Id": "owner-1" } });
    expect(response.status).toBe(200);
    const { items } = await response.json();
    expect(items).toHaveLength(2);
    expect(items.find((item: { skillId: string }) => item.skillId === "47")).toMatchObject({ ownerId: "bot-owner", description: "Host description" });
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

  it("registers the Host description even when ZIP frontmatter says something different", async () => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockResolvedValue({ packageBytes: await zip("---\ndescription: wrong ZIP text\n---\n"),
      displayName: "Metadata", sha256: "checksum", description: "Host authoritative description" });
    const response = await fetch(`${test.baseUrl}/skill-assets`, { method: "POST",
      headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" }, body: JSON.stringify({botId: "bot-1", skillId: "47"}) });
    expect(response.status).toBe(201);
    const item = await response.json();
    expect(item).toMatchObject({ownerId: "bot-owner", description: "Host authoritative description"});
    expect((await test.repo.findAsset(item.assetId))?.description).toBe("Host authoritative description");
  });

  it.each([
    [413, "HOST_LOCAL_SKILL_TOO_LARGE", "Skill 太大：目录文件总大小超过 100 MiB，无法注册"],
    [502, "HOST_LOCAL_SKILL_SIZE_UNAVAILABLE", "无法预检查 Skill 大小：目录列表读取失败"],
  ])("shows directory precheck failure %s without registering an asset", async (statusCode, code, message) => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockRejectedValue(Object.assign(new Error(String(message)), { statusCode, code }));
    const response = await fetch(`${test.baseUrl}/skill-assets`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ botId: "bot-1", skillId: "47" }),
    });
    expect(response.status).toBe(statusCode);
    expect(await response.json()).toEqual({ code, error: message });
  });

  it.each([502, 503])("returns an explainable %s when Host export is unavailable", async (status) => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockRejectedValue(Object.assign(new Error("宿主 Skill 服务暂时不可用，请稍后重试"), {
      code: "HOST_LOCAL_SKILL_UNAVAILABLE", status, statusCode: status, upstreamStatus: 500,
    }));
    const response = await fetch(`${test.baseUrl}/skill-assets`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ botId: "bot-1", skillId: "47" }),
    });
    expect(response.status).toBe(status);
    expect(await response.json()).toEqual({ code: "HOST_LOCAL_SKILL_UNAVAILABLE", error: "宿主 Skill 服务暂时不可用，请稍后重试" });
  });

  it.each([401, 403, 409])("preserves Host request rejection %s instead of turning it into 500", async (statusCode) => {
    const test = await startRouter(true);
    test.exportLocalSkill.mockRejectedValue(Object.assign(new Error("宿主 Skill 请求未获允许或内容已变更"), {
      code: "HOST_LOCAL_SKILL_REQUEST_FAILED", statusCode,
    }));
    const response = await fetch(`${test.baseUrl}/skill-assets`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ botId: "bot-1", skillId: "47" }),
    });
    expect(response.status).toBe(statusCode);
    expect(await response.json()).toEqual({ code: "HOST_LOCAL_SKILL_REQUEST_FAILED", error: "宿主 Skill 请求未获允许或内容已变更" });
  });

  it.each([
    { code: "UNKNOWN_Host_ERROR", status: 503 },
    { code: "HOST_LOCAL_SKILL_UNAVAILABLE", status: 500 },
    { code: "HOST_LOCAL_SKILL_REQUEST_FAILED", status: 503 },
    { code: "HOST_LOCAL_SKILL_REQUEST_FAILED", status: "403" },
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

  it("lists one asset with live Host description and Bot owner even when it has multiple versions", async () => {
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
    expect(items[0]).toMatchObject({ assetId, name: "Evidence Skill", description: "Host description", ownerId: "bot-owner", botId: "bot-1", currentVersion: "v2" });
    expect(items[0].createdAt).toBeTruthy();
    expect(items[0].updatedAt).toBeTruthy();
    expect(test.exportLocalSkill).toHaveBeenCalledTimes(1);
    expect(readObject).not.toHaveBeenCalled();
  });

  it("reads persisted operation events, never reconstructing applied events from version rows", async () => {
    const test = await startRouter(true);
    test.listLocalSkills.mockResolvedValue([{ skillId: "SKILL-1", displayName: "Skill", description: "Live Host skill description" }]);
    for (const [assetId, ownerUserId] of [["SKILL-1", "owner-1"], ["SKILL-OTHER", "owner-2"]]) {
      await test.repo.createAsset({ assetId, versionId: `BASE-${assetId}`, ownerUserId, botId: "bot-1", externalSkillId: assetId,
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
    expect(items[0]).toMatchObject({ assetId: "SKILL-1", ownerId: "bot-owner", botId: "bot-1",
      versionFrom: { version: "v1", versionId: 'BASE-SKILL-1' }, type: "registered", status: 'completed',
      outcome: 'registered', taskId: null, actorId: 'owner-1', actorType: 'user' });
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

  it("reports a missing historical package without substituting the current 宿主 Skill", async () => {
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
    test.exportLocalSkill.mockRejectedValue(new Error("current Host content must not be read"));
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
      assetId: "SKILL-1", versionId: "SKVER-1", ownerUserId: "owner-1", botId: "bot-1", externalSkillId: "44",
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

describe("durable manual Skill application", () => {
  async function applicationFixture() {
    const test = await startRouter(true);
    const baseline = await zip("# Original\n");
    const candidate = await zip("# Candidate\n");
    await test.repo.createAsset({ assetId: "INTENT", versionId: "V1", ownerUserId: "owner-1",
      botId: "bot-1", externalSkillId: "47", displayName: "Intent", packageRef: "base", packageSha256: "base-sha" });
    test.exportLocalSkill.mockResolvedValue({ packageBytes: baseline, sha256: "base-sha" });
    const input: Parameters<typeof applySkillVersion>[0] = {
      repo: test.repo, host: { exportLocalSkill: test.exportLocalSkill, replaceLocalSkill: test.replaceLocalSkill } as never,
      operationId: "task:EV-1", readPackage: vi.fn(async () => candidate),
      version: { kind: "task", data: { assetId: "INTENT", versionId: "V2", sourceTaskId: "EV-1",
        packageRef: "candidate", packageSha256: `sha256:${createHash("sha256").update(candidate).digest("hex")}`,
        baselinePackageRef: "base", baselinePackageSha256: "base-sha" } },
      replace: { botId: "bot-1", skillId: "47", ownerUserId: "owner-1", identity: {} as never, expectedSha256: "base-sha" },
      baselineBytes: baseline,
    };
    return { ...test, input };
  }

  it("returns a completed task application when a delayed second accept reaches reservation", async () => {
    const test = await applicationFixture();
    const first = await applySkillVersion(test.input);
    const second = await applySkillVersion(test.input);
    expect(second.version.version_id).toBe(first.version.version_id);
    expect(test.replaceLocalSkill).toHaveBeenCalledTimes(1);
    expect(await test.repo.listVersions("INTENT")).toHaveLength(2);
    expect((await test.repo.findAsset("INTENT"))?.pending_application_json).toBeNull();
  });

  it.each(["package_read", "digest", "baseline_read"])("releases a first application after %s fails before writing", async failure => {
    const test = await applicationFixture();
    if (failure === "package_read") test.input.readPackage = async () => { throw new Error("storage unavailable"); };
    if (failure === "digest") test.input.readPackage = async () => Buffer.from("corrupt");
    if (failure === "baseline_read") test.exportLocalSkill.mockRejectedValue(new Error("provider read unavailable"));
    await expect(applySkillVersion(test.input)).rejects.toThrow();
    expect(test.replaceLocalSkill).not.toHaveBeenCalled();
    expect((await test.repo.findAsset("INTENT"))?.pending_application_json).toBeNull();
  });

  it("retains an uncertain earlier application when a recovery read fails", async () => {
    const test = await applicationFixture();
    test.replaceLocalSkill.mockRejectedValue(new Error("response lost"));
    await expect(applySkillVersion(test.input)).rejects.toThrow("response lost");
    test.input.readPackage = async () => { throw new Error("storage unavailable"); };
    await expect(applySkillVersion(test.input)).rejects.toThrow("storage unavailable");
    expect((await test.repo.findAsset("INTENT"))?.pending_application_json).toBeTruthy();
  });

  it("does not release another caller's reservation when the original caller fails before writing", async () => {
    const test = await applicationFixture();
    test.input.readPackage = async () => {
      await test.repo.reserveApplication("INTENT", { operationId: test.input.operationId,
        packageRef: "candidate", packageSha256: test.input.version.data.packageSha256 });
      throw new Error("first caller read failed");
    };
    await expect(applySkillVersion(test.input)).rejects.toThrow("first caller read failed");
    expect((await test.repo.findAsset("INTENT"))?.pending_application_json).toBeTruthy();
  });

  it.each([true, false])("releases only a proven pre-write failure (writeNotStarted=%s)", async writeNotStarted => {
    const test = await startRouter(true);
    const baseline = await zip("# Original\n");
    await test.store.putObject("intent/base.zip", baseline, "application/zip");
    await test.repo.createAsset({ assetId: "INTENT", versionId: "INTENT-V1", ownerUserId: "owner-1",
      botId: "bot-1", externalSkillId: "47", displayName: "Intent", packageRef: "oss://clawevolve-artifacts/intent/base.zip", packageSha256: "base-sha" });
    test.exportLocalSkill.mockResolvedValue({ packageBytes: baseline, sha256: "base-sha", displayName: "Intent" });
    test.replaceLocalSkill.mockRejectedValue(Object.assign(new Error("provider unavailable"), { writeNotStarted }));
    const submit = (content: string) => fetch(`${test.baseUrl}/skill-assets/INTENT/versions`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "edit", baseVersionId: "INTENT-V1", edits: [{ path: "SKILL.md", content }] }),
    });
    expect((await submit("# Candidate\n")).status).toBe(500);
    expect(Boolean((await test.repo.findAsset("INTENT"))?.pending_application_json)).toBe(!writeNotStarted);
    test.replaceLocalSkill.mockResolvedValue({ sha256: "new-sha" });
    expect((await submit("# Another candidate\n")).status).toBe(writeNotStarted ? 201 : 409);
  });

  it.each(["response_lost", "database_failed"])("recovers %s without applying twice or losing version history", async failure => {
    const test = await startRouter(true);
    const baseline = await zip("# Original\n");
    await test.store.putObject("recover/base.zip", baseline, "application/zip");
    await test.repo.createAsset({ assetId: "RECOVER", versionId: "RECOVER-V1", ownerUserId: "owner-1",
      botId: "bot-1", externalSkillId: "47", displayName: "Recovery", packageRef: "oss://clawevolve-artifacts/recover/base.zip", packageSha256: "base-sha" });
    let live = baseline;
    test.exportLocalSkill.mockImplementation(async () => ({ packageBytes: live,
      sha256: createHash("sha256").update(live).digest("hex"), displayName: "Recovery" }));
    test.replaceLocalSkill.mockImplementation(async ({ packageBytes }) => {
      live = packageBytes;
      if (failure === "response_lost") throw Object.assign(new Error("response lost"), { status: 503 });
      return { sha256: createHash("sha256").update(live).digest("hex") };
    });
    if (failure === "database_failed") vi.spyOn(test.repo, "createManualVersion").mockRejectedValueOnce(new Error("database unavailable"));
    const submit = () => fetch(`${test.baseUrl}/skill-assets/RECOVER/versions`, {
      method: "POST", headers: { "X-User-Id": "owner-1", "Content-Type": "application/json" },
      body: JSON.stringify({ mode: "edit", baseVersionId: "RECOVER-V1", edits: [{ path: "SKILL.md", content: "# Candidate\n" }] }),
    });
    const first = await submit();
    if (failure === "database_failed") {
      expect(first.status).toBe(500);
      expect((await test.repo.findAsset("RECOVER"))?.pending_application_json).toBeTruthy();
      expect(await test.repo.listVersions("RECOVER")).toHaveLength(1);
      expect((await submit()).status).toBe(201);
    } else expect(first.status).toBe(201);
    expect((await submit()).status).toBe(200);
    expect(test.replaceLocalSkill).toHaveBeenCalledTimes(1);
    const versions = await test.repo.listVersions("RECOVER");
    expect(versions).toHaveLength(2);
    expect(versions[0]).toMatchObject({ version_no: 2, creation_kind: "edit", source_version_id: "RECOVER-V1" });
    expect((await test.repo.findAsset("RECOVER"))?.pending_application_json).toBeNull();
  });
});
