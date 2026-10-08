import { afterEach, expect, it, vi } from "vitest";
import express from "express";
import { once } from "node:events";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { createClawevolveModule } from "../../create-module.js";

let server: ReturnType<express.Application["listen"]>;
let db: SqliteDatabase;
afterEach(async () => { if (server) await new Promise<void>(resolve => server.close(() => resolve())); await db?.close(); });

it("Singlebox advertises unavailable integrations, rejects direct access, and preserves native routes", async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  const dispatch = vi.fn();
  const module = createClawevolveModule({ db, dispatch, version: "openversion" });
  const app = express(); app.use(express.json()); app.use("/api/evolve", module.publicRouter);
  server = app.listen(0); await once(server, "listening");
  const base = `http://127.0.0.1:${(server.address() as { port: number }).port}/api/evolve`;
  const capabilities = await fetch(`${base}/capabilities`);
  expect(await capabilities.json()).toEqual({ skillManagement: false, stageCustomization: false });
  for (const path of ["skill-assets", "skill-events", "stage-skills", "stage-developments", "stage-catalog", "spaces", "skill-assets/x/task-defaults", "STAGE-CATALOG", "Stage-Skills", "Skill-Assets"]) {
    const response = await fetch(`${base}/${path}`, { headers: { "X-User-Id": "owner" } });
    expect(response.status, path).toBe(404);
    expect(await response.json()).toMatchObject({ code: "EVOLVE_CAPABILITY_UNAVAILABLE" });
  }
  expect((await fetch(`${base}/model-options`)).status).toBe(200);
  expect((await fetch(`${base}/task-definitions`)).status).toBe(200);
  expect(dispatch).not.toHaveBeenCalled();
});
