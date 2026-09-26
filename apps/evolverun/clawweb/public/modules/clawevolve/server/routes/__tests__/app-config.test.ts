import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "../../repositories/app-config-repository.js";
import { createAppConfigRouter } from "../app-config.js";

let db: SqliteDatabase;
let repo: AppConfigRepository;
let server: ReturnType<express.Application["listen"]>;
let url: string;
const value = { config_key: "example", config_json: '{"items":[1,true]}', description: "Example" };

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  repo = new AppConfigRepository(db);
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    // The public module consumes a trusted host decision, not request fields.
    if (req.header("X-User-Id") === "admin") req.isAdmin = true;
    if (req.header("X-User-Id") === "evolve-admin") req.isClawEvolveAdmin = true;
    next();
  });
  app.use("/api/evolve/app-config", createAppConfigRouter(repo));
  app.use((_error: unknown, _req: express.Request, res: express.Response, _next: express.NextFunction) => {
    res.status(500).json({ error: "Persistence failed" });
  });
  server = await new Promise((resolve, reject) => {
    const instance = app.listen(0, "127.0.0.1", () => resolve(instance));
    instance.once("error", reject);
  });
  url = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve/app-config`;
});
afterEach(async () => {
  await new Promise<void>(resolve => server.close(() => resolve()));
  vi.restoreAllMocks();
  await db.close();
});
function request(method: string, path = "", body?: unknown, user = "admin") {
  return fetch(url + path, { method, headers: {
    "Content-Type": "application/json", ...(user ? { "X-User-Id": user } : {}),
    "X-Is-Admin": "true",
  }, ...(body === undefined ? {} : { body: JSON.stringify(body) }) });
}

describe("Evolve application configuration management", () => {
  it.each(["", "reader", "evolve-admin"])("denies every read/write operation for non-admin %s", async user => {
    await repo.create(value);
    for (const [method, path, body] of [
      ["GET", "", undefined], ["GET", "/example", undefined],
      ["POST", "", value], ["PUT", "/example", { enabled: 0 }], ["DELETE", "/example", undefined],
    ] as const) expect((await request(method, path, body, user)).status).toBe(403);
    expect(await repo.listAll()).toHaveLength(1);
    expect(await repo.findByKey("example")).toMatchObject({ version: 1, enabled: 1 });
  });

  it("creates, reads, updates, filters and deletes generic JSON config through HTTP", async () => {
    const created = await request("POST", "", value);
    expect(created.status).toBe(201);
    expect(await created.json()).toMatchObject({ configKey: "example", configJson: value.config_json,
      version: 1, enabled: true, updatedBy: "admin" });
    expect((await request("POST", "", value)).status).toBe(409);
    expect(await (await request("GET", "/example")).json()).toMatchObject({ configJson: value.config_json });
    const updated = await request("PUT", "/example", { config_json: '["arbitrary",null]', enabled: 0 });
    expect(await updated.json()).toMatchObject({ configJson: '["arbitrary",null]', enabled: false, version: 2 });
    expect(await (await request("GET")).json()).toHaveLength(1);
    expect(await (await request("GET", "?enabled=true")).json()).toEqual([]);
    expect(await (await request("PUT", "/example", { description: "Revised", enabled: 1 })).json())
      .toMatchObject({ version: 3, enabled: true, description: "Revised" });
    expect(await (await request("DELETE", "/example")).json()).toEqual({ affected: true });
    expect((await request("GET", "/example")).status).toBe(404);
    expect((await request("PUT", "/example", { enabled: 0 })).status).toBe(404);
    expect((await request("DELETE", "/example")).status).toBe(404);
  });

  it.each([
    {}, { config_key: "x" }, { ...value, config_key: "" }, { ...value, config_key: "x".repeat(65) },
    { ...value, config_key: " padded" }, { ...value, config_json: "invalid" },
    { ...value, config_json: {} }, { ...value, description: 1 }, { ...value, updated_by: "spoof" },
  ])("rejects invalid creation without persisting it: %j", async body => {
    expect((await request("POST", "", body)).status).toBe(400);
    expect(await repo.listAll()).toEqual([]);
  });

  it.each([{}, { enabled: true }, { enabled: 2 }, { config_json: "{" }, { description: null },
    { updated_by: "spoof" }, { config_key: "renamed" }])("rejects invalid update without changing existing config: %j", async body => {
    await repo.create(value);
    expect((await request("PUT", "/example", body)).status).toBe(400);
    expect(await repo.findByKey("example")).toMatchObject({ ...value, version: 1 });
  });

  it("propagates database write failure without returning success", async () => {
    await repo.create(value);
    vi.spyOn(repo, "update").mockRejectedValueOnce(new Error("write failed"));
    expect((await request("PUT", "/example", { enabled: 0 })).status).toBe(500);
    expect(await repo.findByKey("example")).toMatchObject({ version: 1, enabled: 1 });
  });
});
