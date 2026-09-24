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
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";
import { StageSkillRepository } from "../../repositories/stage-skill-repository.js";
import { FilesystemObjectStore } from "../../services/object-storage/filesystem-object-store.js";
import { createSkillAssetsRouter } from "../skill-assets.js";
import { createStageSkillsRouter } from "../stage-skills.js";

// Exercise HTTP -> real repositories -> real SQLite migrations (including 126).
// Only external Host ports are mocked; ZIP validation and artifact IO stay real.
const owner = "registrar";
const member = "teammate";
const outsider = "outsider";
const team: AccessibleSpace = { id: "team-97", name: "Aurora Research", type: "TEAM", role: "MEMBER" };
const otherTeam: AccessibleSpace = { id: "team-other", name: "Other Team", type: "TEAM", role: "ADMIN" };
const personalView = { spaceId: `personal-${owner}`, spaceType: "PERSONAL", spaceName: `${owner} Personal` };
const teamView = { spaceId: "team-97", spaceType: "TEAM", spaceName: "Aurora Research" };
const noSpaceView = { spaceId: null, spaceType: null, spaceName: null };

let db: SqliteDatabase;
let assets: SkillAssetRepository;
let stages: StageSkillRepository;
let store: FilesystemObjectStore;
let artifactRoot: string;
let server: ReturnType<express.Application["listen"]> | undefined;
let baseUrl: string;
let packageBytes: Buffer;
let members: Set<string>;
let hostSpaces: SpaceDirectory;
let hostLocalSkills: BotSkillGateway;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  assets = new SkillAssetRepository(db);
  stages = new StageSkillRepository(db);
  artifactRoot = await mkdtemp(join(tmpdir(), "space-registration-test-"));
  store = new FilesystemObjectStore(artifactRoot);
  packageBytes = await new JSZip().file("SKILL.md",
    "# Evidence processor\n\nRead supplied evidence and return a structured result.\n")
    .generateAsync({ type: "nodebuffer" });
  members = new Set([owner, member]);
  hostSpaces = {
    listAccessibleSpaces: vi.fn(async ({ identity }) => [
      // TEAM intentionally comes first: omitted spaceId must select PERSONAL,
      // not whichever space Host happens to return first.
      ...(members.has(identity.userId) ? [team] : []),
      ...(identity.userId === owner ? [otherTeam] : []),
      { id: `personal-${identity.userId}`, name: `${identity.userId} Personal`,
        type: "PERSONAL" as const, role: "ADMIN" as const },
    ]),
  };
  hostLocalSkills = {
    getBotMetadata: vi.fn(async () => ({ ownerId: owner })),
    listLocalSkills: vi.fn(async () => [
      { skillId: "local-skill", displayName: "Evidence Skill", description: "Host description" },
    ]),
    exportLocalSkill: vi.fn(async () => ({ packageBytes,
      sha256: createHash("sha256").update(packageBytes).digest("hex"),
      displayName: "Evidence Skill", description: "Host description" })),
    replaceLocalSkill: vi.fn(async () => { throw new Error("Registration must not replace an 宿主 Skill"); }),
  };
  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createSkillAssetsRouter({ repo: assets, artifactStore: store, hostLocalSkills, hostSpaces }));
  app.use("/api/evolve", createStageSkillsRouter({ repo: stages, artifactStore: store, hostSpaces }));
  // Leave Express's real status propagation in place, including thrown 403s.
  const started = await new Promise<ReturnType<express.Application["listen"]>>((resolve, reject) => {
    const instance = app.listen(0, "127.0.0.1", () => resolve(instance));
    instance.once("error", reject);
  });
  server = started;
  baseUrl = `http://127.0.0.1:${(started.address() as { port: number }).port}/api/evolve`;
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve, reject) => server!.close(error => error ? reject(error) : resolve()));
  server = undefined;
  if (db) await db.close();
  if (artifactRoot) await rm(artifactRoot, { recursive: true, force: true });
});

async function get(path: string, userId = owner) {
  return fetch(`${baseUrl}${path}`, { headers: { "X-User-Id": userId } });
}

async function post(path: string, body: Record<string, unknown>, userId = owner) {
  return fetch(`${baseUrl}${path}`, { method: "POST",
    headers: { "X-User-Id": userId, "Content-Type": "application/json" }, body: JSON.stringify(body) });
}

async function bodyOf(response: Response, status = 200) {
  const text = await response.text();
  expect(response.status, text).toBe(status);
  return JSON.parse(text);
}

async function registerAsset(spaceId?: string, userId = owner) {
  return post("/skill-assets", { botId: "bot-fixture", skillId: "local-skill", ...(spaceId ? { spaceId } : {}) }, userId);
}

async function develop(spaceId?: string, userId = owner) {
  return post("/stage-developments", {
    flow: "bot_evolution", stage: "diagnose", mode: "preprocess",
    displayName: "  Evidence Preprocessor  ", ...(spaceId ? { spaceId } : {}),
  }, userId);
}

async function upload(stageSkillId: string, userId = owner, fields: Record<string, string> = {}) {
  const form = new FormData();
  for (const [key, value] of Object.entries({ stageSkillId, stage: "diagnose", mode: "preprocess", ...fields })) {
    form.set(key, value);
  }
  form.set("package", new Blob([new Uint8Array(packageBytes)], { type: "application/zip" }), "implementation.zip");
  return fetch(`${baseUrl}/stage-skills/uploads`, { method: "POST", headers: { "X-User-Id": userId }, body: form });
}

async function createTeamStage() {
  const development = await bodyOf(await develop(team.id), 201);
  const implementation = await bodyOf(await upload(development.stageSkillId), 201);
  return { development, implementation };
}

describe("Space registration with real repositories", () => {
  it.each(["/stage-skills", "/stage-developments"])("forwards browser context for %s space checks", async path => {
    const referer = "https://workbench.example/evolve/new";
    const origin = "https://workbench.example";
    const response = await fetch(`${baseUrl}${path}`, {
      headers: { "X-User-Id": owner, Referer: referer, Origin: origin },
    });
    expect(await bodyOf(response)).toEqual({ items: [] });
    expect(hostSpaces.listAccessibleSpaces).toHaveBeenCalledWith({ identity: expect.objectContaining({
      userId: owner, referer, origin,
    }) });
  });

  it.each([
    { selection: "default PERSONAL", spaceId: undefined, expected: personalView },
    { selection: "explicit TEAM", spaceId: team.id, expected: teamView },
  ])("registers Skill assets in $selection and persists the returned space", async ({ spaceId, expected }) => {
    const registered = await bodyOf(await registerAsset(spaceId), 201);
    expect(registered).toMatchObject({ ...expected, name: "Evidence Skill", skillId: "local-skill" });
    expect(await assets.findAsset(registered.assetId)).toMatchObject({
      space_id: expected.spaceId, space_type: expected.spaceType, space_name: expected.spaceName,
      owner_user_id: owner,
    });
    expect(await bodyOf(await get(`/skill-assets/${registered.assetId}`))).toMatchObject(expected);
    expect((await bodyOf(await get("/skill-assets"))).items).toEqual([
      expect.objectContaining({ assetId: registered.assetId, ...expected }),
    ]);
  });

  it.each([
    { selection: "default PERSONAL", spaceId: undefined, expected: personalView },
    { selection: "explicit TEAM", spaceId: team.id, expected: teamView },
  ])("creates Stage development in $selection with its chosen displayName", async ({ spaceId, expected }) => {
    const development = await bodyOf(await develop(spaceId), 201);
    expect(development).toMatchObject({ ...expected, displayName: "Evidence Preprocessor", ownerId: owner });
    expect(await stages.findDevelopment(development.stageSkillId)).toMatchObject({
      space_id: expected.spaceId, space_type: expected.spaceType, space_name: expected.spaceName,
      display_name: "Evidence Preprocessor",
    });
    expect(await bodyOf(await get(`/stage-developments/${development.stageSkillId}`))).toMatchObject(expected);
  });

  it.each(["Skill", "Stage"] as const)("rejects non-member %s registration with 403 before any side effect", async kind => {
    const putObject = vi.spyOn(store, "putObject");
    const response = kind === "Skill"
      ? await registerAsset(team.id, outsider)
      : await develop(team.id, outsider);
    expect(response.status).toBe(403);
    await response.text();
    expect(await assets.listAssets(outsider)).toEqual([]);
    expect(await assets.listEvents(outsider)).toEqual([]);
    expect(await stages.listDevelopments(outsider)).toEqual([]);
    expect(await stages.listImplementations(outsider)).toEqual([]);
    expect(hostLocalSkills.exportLocalSkill).not.toHaveBeenCalled();
    expect(putObject).not.toHaveBeenCalled();
  });

  it.each([
    { selection: "PERSONAL", spaceId: undefined, expected: personalView },
    { selection: "TEAM", spaceId: team.id, expected: teamView },
  ])("upload inherits $selection development identity despite conflicting multipart fields", async ({ spaceId, expected }) => {
    const development = await bodyOf(await develop(spaceId), 201);
    const uploaded = await bodyOf(await upload(development.stageSkillId, owner, {
      spaceId: otherTeam.id, spaceType: "TEAM", spaceName: "Forged space", displayName: "Forged name",
    }), 201);
    expect(uploaded).toMatchObject({ ...expected, displayName: "Evidence Preprocessor",
      stageSkillId: development.stageSkillId, ownerId: owner, version: "v1" });
    expect(await stages.findImplementation(uploaded.implementationId)).toMatchObject({
      space_id: expected.spaceId, space_type: expected.spaceType, space_name: expected.spaceName,
      display_name: "Evidence Preprocessor", owner_user_id: owner,
    });
    expect(await bodyOf(await get(`/stage-skills/${uploaded.implementationId}`))).toMatchObject(expected);
    const registered = await bodyOf(await post(`/stage-skills/${uploaded.implementationId}/register`, {}));
    expect(registered).toMatchObject({ ...expected, status: "registered" });
    const upgraded = await bodyOf(await upload(development.stageSkillId, owner, { spaceId: otherTeam.id }), 201);
    expect(upgraded).toMatchObject({ ...expected, displayName: "Evidence Preprocessor", version: "v2" });
  });

  it("lets a team member search by space name and read Stage content, but not upload another user's record", async () => {
    const { development, implementation } = await createTeamStage();
    // Neither Stage displayName nor its generated ID contains this search text.
    const matched = await bodyOf(await get("/stage-skills?search=%20aUrOrA%20", member));
    expect(matched.items).toEqual([expect.objectContaining({
      ...teamView, implementationId: implementation.implementationId, ownerId: owner,
    })]);
    expect((await bodyOf(await get("/stage-skills?search=unmatched-space", member))).items).toEqual([]);
    expect((await bodyOf(await get("/stage-skills?search=Aurora", outsider))).items).toEqual([]);
    expect(await bodyOf(await get(`/stage-skills/${implementation.implementationId}`, member))).toMatchObject(teamView);
    const content = await bodyOf(await get(`/stage-skills/${implementation.implementationId}/content`, member));
    expect(content.files).toEqual(expect.arrayContaining([expect.objectContaining({ path: "SKILL.md" })]));
    const putObject = vi.spyOn(store, "putObject");
    expect((await upload(development.stageSkillId, member)).status).toBe(404);
    expect(await stages.nextVersion(development.stageSkillId)).toBe(2);
    expect(putObject).not.toHaveBeenCalled();
    expect((await post(`/stage-skills/${implementation.implementationId}/register`, {}, member)).status).toBe(404);
    expect((await stages.findImplementation(implementation.implementationId))?.status).toBe("validated");
  });

  it("keeps newly registered PERSONAL assets and Stages private from other team members", async () => {
    const asset = await bodyOf(await registerAsset(), 201);
    const development = await bodyOf(await develop(), 201);
    const implementation = await bodyOf(await upload(development.stageSkillId), 201);
    expect((await bodyOf(await get("/skill-assets", member))).items).toEqual([]);
    expect((await bodyOf(await get("/stage-skills", member))).items).toEqual([]);
    expect((await get(`/skill-assets/${asset.assetId}`, member)).status).toBe(404);
    expect((await get(`/stage-skills/${implementation.implementationId}`, member)).status).toBe(404);
  });

  it.each([owner, member])("revokes team asset and Stage visibility immediately when %s leaves (including the owner)", async departing => {
    const asset = await bodyOf(await registerAsset(team.id), 201);
    const { development, implementation } = await createTeamStage();
    const assetDetail = await bodyOf(await get(`/skill-assets/${asset.assetId}`, departing));
    const versionId = assetDetail.versions[0].versionId;
    expect(assetDetail).toMatchObject(teamView);
    expect((await bodyOf(await get("/skill-assets", departing))).items).toHaveLength(1);
    expect((await bodyOf(await get("/stage-skills?search=Aurora", departing))).items).toHaveLength(1);
    await bodyOf(await get(`/stage-skills/${implementation.implementationId}/content`, departing));

    members.delete(departing);

    expect((await bodyOf(await get("/skill-assets", departing))).items).toEqual([]);
    expect((await bodyOf(await get("/stage-skills", departing))).items).toEqual([]);
    expect((await bodyOf(await get("/stage-skills?search=Aurora", departing))).items).toEqual([]);
    for (const path of [
      `/skill-assets/${asset.assetId}`,
      `/skill-assets/${asset.assetId}/versions/${versionId}/content`,
      `/stage-skills/${implementation.implementationId}`,
      `/stage-skills/${implementation.implementationId}/content`,
    ]) {
      expect((await get(path, departing)).status, path).toBe(404);
    }
    const putObject = vi.spyOn(store, "putObject");
    expect((await upload(development.stageSkillId, departing)).status).toBe(404);
    expect(putObject).not.toHaveBeenCalled();
    expect((await post(`/stage-skills/${implementation.implementationId}/register`, {}, departing)).status).toBe(404);
    // Revoking access must not remove the team's persisted records.
    const remaining = departing === owner ? member : owner;
    expect(await bodyOf(await get(`/stage-skills/${implementation.implementationId}`, remaining))).toMatchObject(teamView);
    expect(await bodyOf(await get(`/skill-assets/${asset.assetId}`, remaining))).toMatchObject(teamView);
  });

  it.each(["list", "detail", "developer-package"] as const)("hides team development %s from its owner after leaving the team", async surface => {
    const development = await bodyOf(await develop(team.id), 201);
    const path = surface === "list" ? "/stage-developments"
      : surface === "detail" ? `/stage-developments/${development.stageSkillId}`
      : `/stage-skills/developer-package?developmentId=${development.stageSkillId}`;
    const allowed = await get(path);
    expect(allowed.status).toBe(200);
    await allowed.arrayBuffer();

    members.delete(owner);

    const denied = await get(path);
    if (surface === "list") {
      expect((await bodyOf(denied)).items).toEqual([]);
    } else {
      expect(denied.status, path).toBe(404);
      await denied.arrayBuffer();
    }
    expect(await stages.findDevelopment(development.stageSkillId)).not.toBeNull();
  });

  it("preserves owner-only access for historical records with no space, even without current memberships", async () => {
    // Old private records become NULL-space rows under migration 126. Use real
    // repository defaults to seed that state without assigning a new space.
    await assets.createAsset({ assetId: "legacy-asset", versionId: "legacy-version", ownerUserId: owner,
      botId: "bot-fixture", externalSkillId: "legacy-skill", displayName: "Historical Skill",
      packageRef: "fixture:legacy-package", packageSha256: "legacy-checksum" });
    const development = await stages.createDevelopment({ ownerUserId: owner,
      displayName: "Historical Stage", flow: "bot_evolution", stage: "diagnose", mode: "preprocess" });
    await stages.createImplementation({ stageSkillId: String(development.id), implementationId: "legacy-implementation",
      ownerUserId: owner, displayName: "Historical Stage", stage: "diagnose", mode: "preprocess", versionNo: 1,
      packageRef: "fixture:legacy-package", packageSha256: "legacy-checksum", staticValidation: {} });
    vi.mocked(hostSpaces.listAccessibleSpaces).mockResolvedValue([]);

    const paths = ["/skill-assets/legacy-asset", "/stage-skills/legacy-implementation", `/stage-developments/${development.id}`];
    for (const path of paths) {
      expect(await bodyOf(await get(path))).toMatchObject(noSpaceView);
      expect((await get(path, member)).status, path).toBe(404);
    }
    for (const path of ["/skill-assets", "/stage-skills", "/stage-developments"]) {
      expect((await bodyOf(await get(path))).items).toEqual([expect.objectContaining(noSpaceView)]);
      expect((await bodyOf(await get(path, member))).items).toEqual([]);
    }
  });

  it.each([
    { kind: "draft", user: owner },
    { kind: "implementation", user: owner },
    { kind: "draft", user: member },
    { kind: "implementation", user: member },
  ])("rejects deletion of team $kind by departed owner or non-owning member ($user) without changing data", async ({ kind, user }) => {
    const development = await bodyOf(await develop(team.id), 201);
    const implementation = kind === "implementation"
      ? await bodyOf(await upload(development.stageSkillId), 201) : null;
    const beforeDevelopment = await stages.findDevelopment(development.stageSkillId);
    const beforeImplementation = implementation ? await stages.findImplementation(implementation.implementationId) : null;
    if (user === owner) members.delete(owner);
    const path = implementation ? `/stage-skills/${implementation.implementationId}` : `/stage-developments/${development.stageSkillId}`;
    const response = await fetch(`${baseUrl}${path}`, { method: "DELETE", headers: { "X-User-Id": user } });
    await response.text();
    // Draft deletion historically uses 409 for a denied operation; either
    // explicit denial or a concealed resource must preserve the original rows.
    expect([403, 404, 409]).toContain(response.status);
    expect(await stages.findDevelopment(development.stageSkillId)).toEqual(beforeDevelopment);
    if (implementation) expect(await stages.findImplementation(implementation.implementationId)).toEqual(beforeImplementation);
  });

  it.each(["draft", "implementation"])("still allows the current team owner to delete their own %s", async kind => {
    const development = await bodyOf(await develop(team.id), 201);
    const implementation = kind === "implementation"
      ? await bodyOf(await upload(development.stageSkillId), 201) : null;
    const path = implementation ? `/stage-skills/${implementation.implementationId}` : `/stage-developments/${development.stageSkillId}`;
    const response = await fetch(`${baseUrl}${path}`, { method: "DELETE", headers: { "X-User-Id": owner } });
    expect(await bodyOf(response)).toEqual({ deleted: true });
    if (implementation) expect((await stages.findImplementation(implementation.implementationId))?.status).toBe("deleted");
    else expect(await stages.findDevelopment(development.stageSkillId)).toBeNull();
  });
});
