import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "../../repositories/app-config-repository.js";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { createTaskGuardSettingsRouter } from "../task-guard-settings.js";
import { createAutoAnalysisSettings } from "../../services/task-guard/auto-analysis-settings.js";

let db: SqliteDatabase;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await runMigrations(db, "sqlite");
  const permissionRepo = new BotWorkflowPermissionRepository(db);
  await permissionRepo.upsert({
    bot_id: null,
    bot_owner_id: "editor-1",
    workflow_id: "wf-1",
    can_view: 1,
    can_execute: 1,
    can_edit: 1,
  });
  const settings = createAutoAnalysisSettings({
    repo: new AppConfigRepository(db),
    environmentDefault: "wf-from-env",
  });
  const app = express();
  app.use(express.json());
  app.use("/api/task-guard", createTaskGuardSettingsRouter({ settings, permissionRepo }));
  server = await new Promise((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});

afterEach(async () => {
  const active = server;
  server = null;
  if (active) await new Promise<void>((resolve) => active.close(() => resolve()));
  await db.close();
});

describe("Task Guard automatic analysis settings", () => {
  it("persists a per-workflow switch and returns the database value without a restart", async () => {
    const update = await fetch(`${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-User-Id": "editor-1" },
      body: JSON.stringify({ enabled: true }),
    });
    expect(update.status).toBe(200);
    expect(await update.json()).toMatchObject({ workflowId: "wf-1", enabled: true, source: "database" });

    const read = await fetch(`${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`, {
      headers: { "X-User-Id": "editor-1" },
    });
    expect(read.status).toBe(200);
    expect(await read.json()).toMatchObject({ workflowId: "wf-1", enabled: true, source: "database" });
  });

  it("uses the environment only until a database value is explicitly saved", async () => {
    const settings = createAutoAnalysisSettings({
      repo: new AppConfigRepository(db),
      environmentDefault: "wf-from-env",
    });
    expect(await settings.get("wf-from-env")).toMatchObject({ enabled: true, source: "environment" });

    await settings.set("wf-from-env", false, "editor-1");
    expect(await settings.get("wf-from-env")).toMatchObject({ enabled: false, source: "database" });
  });

  it("rejects updates from users without workflow edit permission", async () => {
    const response = await fetch(`${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`, {
      method: "PUT",
      headers: { "Content-Type": "application/json", "X-User-Id": "viewer-1" },
      body: JSON.stringify({ enabled: true }),
    });
    expect(response.status).toBe(403);
  });
});
