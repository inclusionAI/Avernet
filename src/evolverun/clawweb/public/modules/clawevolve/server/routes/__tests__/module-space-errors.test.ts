import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { createClawevolveModule, type ClawevolveModule } from "../../create-module.js";
import type { OcbLocalSkillPort, OcbSpacePort } from "../../internal/module-api.js";
import { SkillAssetRepository } from "../../repositories/skill-asset-repository.js";

const actor = "module-error-user";
const privateDetail = "PRIVATE-UPSTREAM-DETAIL-DO-NOT-EXPOSE";
const genericBody = { error: "Internal Server Error" };
let db: SqliteDatabase;
let module: ClawevolveModule | undefined;
let server: ReturnType<express.Application["listen"]> | undefined;
let baseUrl: string;
const listAccessibleSpaces = vi.fn<OcbSpacePort["listAccessibleSpaces"]>();
const exportLocalSkill = vi.fn<OcbLocalSkillPort["exportLocalSkill"]>();
const dispatch = vi.fn(async () => ({ runId: "must-not-dispatch", sessionId: null, platformResponse: {} }));
const putObject = vi.fn(async () => ({ etag: "must-not-write" }));
const getObject = vi.fn(async () => ({ content: Buffer.from("unused"), contentType: null, etag: null }));
const createSignedUrl = vi.fn(async () => "https://objects.example.test/unused");
const hostErrors = vi.fn();
const businessTables = [
  "ce_tasks", "ce_steps", "ce_stage_developments", "ce_stage_skill_implementations",
  "ce_stage_extension_runs", "ce_stage_interactions", "ce_skill_assets", "ce_skill_versions", "ce_skill_audit_events",
];
const endpoints = ["/stage-developments", "/skill-assets", "/spaces", "/skill-assets/target/task-defaults"] as const;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  listAccessibleSpaces.mockReset().mockResolvedValue([
    { id: "personal", name: "Personal", type: "PERSONAL", role: "ADMIN" },
  ]);
  exportLocalSkill.mockReset().mockResolvedValue({ packageBytes: Buffer.from("unused package"), sha256: "unused", displayName: "unused" });
  for (const mock of [dispatch, putObject, getObject, createSignedUrl, hostErrors]) mock.mockClear();
  // A real target ensures the defaults route reaches space authorization on
  // an existing asset. Side-effect checks compare against this seeded baseline.
  await new SkillAssetRepository(db).createAsset({ assetId: "target", versionId: "target-v1", ownerUserId: actor,
    botId: "bot", ocbSkillId: "existing", displayName: "Existing", packageRef: "fixture:existing", packageSha256: "fixture-checksum" });
});

afterEach(async () => {
  if (server) await new Promise<void>((resolve) => server!.close(() => resolve()));
  server = undefined;
  await module?.stop();
  module = undefined;
  await db?.close();
});

async function startHost(configured = true) {
  module = createClawevolveModule({ db, dispatch,
    artifactStore: { putObject, getObject, createSignedUrl },
    ocbLocalSkills: {
      exportLocalSkill, listLocalSkills: vi.fn(async () => []),
      replaceLocalSkill: vi.fn(async () => { throw new Error("Unexpected live Skill write"); }),
    },
    ...(configured ? { ocbSpaces: { listAccessibleSpaces } } : {}),
  });
  const app = express();
  app.use(express.json());
  // Do not mount individual routers or import/install spaceAccessErrorHandler:
  // the production composition root alone must preserve safe status codes.
  app.use("/api/evolve", module.publicRouter);
  app.use((error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    hostErrors(error);
    res.status(500).json(genericBody);
  });
  const started = await new Promise<ReturnType<express.Application["listen"]>>((resolve, reject) => {
    const instance = app.listen(0, "127.0.0.1", () => resolve(instance));
    instance.once("error", reject);
  });
  server = started;
  baseUrl = `http://127.0.0.1:${(started.address() as { port: number }).port}/api/evolve`;
}

async function snapshot() {
  return Promise.all(businessTables.map(table => db.query(`SELECT * FROM ${table} ORDER BY rowid`)));
}

async function request(endpoint: typeof endpoints[number]) {
  const headers = { "X-User-Id": actor, "Content-Type": "application/json" };
  if (endpoint === "/stage-developments") {
    return fetch(`${baseUrl}${endpoint}`, { method: "POST", headers,
      body: JSON.stringify({ flow: "bot_evolution", stage: "diagnose", mode: "preprocess", spaceId: "forbidden-team" }) });
  }
  if (endpoint === "/skill-assets") {
    return fetch(`${baseUrl}${endpoint}`, { method: "POST", headers,
      body: JSON.stringify({ botId: "bot", skillId: "new-skill", spaceId: "forbidden-team" }) });
  }
  return fetch(`${baseUrl}${endpoint}`, { headers });
}

async function expectUnchanged(before: Awaited<ReturnType<typeof snapshot>>) {
  expect(await snapshot()).toEqual(before);
  expect(dispatch).not.toHaveBeenCalled();
  expect(exportLocalSkill).not.toHaveBeenCalled();
  expect(putObject).not.toHaveBeenCalled();
  expect(getObject).not.toHaveBeenCalled();
  expect(createSignedUrl).not.toHaveBeenCalled();
}

describe("ClawEvolve module space errors under a generic-500 host", () => {
  it.each(["/stage-developments", "/skill-assets"] as const)("preserves local forbidden-space 403 for %s without persistence or dispatch", async endpoint => {
    await startHost();
    const before = await snapshot();
    const response = await request(endpoint);
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual({ code: "EVOLVE_SPACE_ACCESS_REJECTED", error: "无权访问所选空间" });
    expect(hostErrors).not.toHaveBeenCalled();
    await expectUnchanged(before);
  });

  it.each(["/stage-developments", "/skill-assets"] as const)("preserves explicit-space 503 when OCB spaces are unconfigured for %s", async endpoint => {
    await startHost(false);
    const before = await snapshot();
    const response = await request(endpoint);
    expect(response.status).toBe(503);
    expect(await response.json()).toEqual({ code: "EVOLVE_SPACE_ACCESS_REJECTED", error: "OCB 空间服务未配置" });
    expect(hostErrors).not.toHaveBeenCalled();
    expect(listAccessibleSpaces).not.toHaveBeenCalled();
    await expectUnchanged(before);
  });

  it.each([
    { code: "OCB_SPACE_UNAVAILABLE", status: 503, field: "status" },
    { code: "OCB_SPACE_REQUEST_FAILED", status: 403, field: "statusCode" },
    { code: "OCB_SPACE_INVALID_RESPONSE", status: 502, field: "status" },
    { code: "OCB_PERSONAL_SPACE_NOT_INITIALIZED", status: 409, field: "statusCode" },
  ])("preserves safe $code/$status across all mounted public space routes", async ({ code, status, field }) => {
    listAccessibleSpaces.mockRejectedValue(Object.assign(new Error("可安全展示的 OCB 空间错误"), {
      code, [field]: status, internalDetails: privateDetail, upstreamBody: { debug: privateDetail },
    }));
    await startHost();
    const before = await snapshot();
    for (const endpoint of endpoints) {
      const response = await request(endpoint);
      expect(response.status, endpoint).toBe(status);
      expect(await response.json()).toEqual({ code, error: "可安全展示的 OCB 空间错误" });
    }
    expect(hostErrors).not.toHaveBeenCalled();
    await expectUnchanged(before);
  });

  it.each([
    { name: "plain error", fields: {} },
    { name: "unknown code with status 403", fields: { code: "UNKNOWN_OCB_ERROR", status: 403 } },
    { name: "safe code but string status", fields: { code: "OCB_SPACE_UNAVAILABLE", status: "503" } },
    { name: "safe code but non-error status", fields: { code: "OCB_SPACE_UNAVAILABLE", status: 200 } },
    { name: "safe code but out-of-range status", fields: { code: "OCB_SPACE_UNAVAILABLE", status: 600 } },
    { name: "safe code but fractional status", fields: { code: "OCB_SPACE_UNAVAILABLE", status: 503.5 } },
    { name: "safe code but non-string message", fields: { code: "OCB_SPACE_UNAVAILABLE", status: 503, message: { privateDetail } } },
  ])("passes $name to the host's generic 500 without leaking upstream details", async ({ fields }) => {
    listAccessibleSpaces.mockRejectedValue(Object.assign(new Error(privateDetail), fields));
    await startHost();
    const before = await snapshot();
    for (const endpoint of endpoints) {
      const response = await request(endpoint);
      expect(response.status, endpoint).toBe(500);
      const body = await response.json();
      expect(body).toEqual(genericBody);
      expect(JSON.stringify(body)).not.toContain(privateDetail);
    }
    expect(hostErrors).toHaveBeenCalledTimes(endpoints.length);
    await expectUnchanged(before);
  });
});
