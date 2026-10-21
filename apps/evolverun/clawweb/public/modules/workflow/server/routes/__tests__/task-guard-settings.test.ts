import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "../../repositories/app-config-repository.js";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { createTaskGuardSettingsRouter } from "../task-guard-settings.js";
import { createAutoAnalysisSettings } from "../../services/task-guard/auto-analysis-settings.js";

const records = vi.fn();
let db: SqliteDatabase;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;

beforeEach(async () => {
  records.mockReset().mockResolvedValue({jobs: [], nextCursor: null});
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
  app.use("/api/task-guard", createTaskGuardSettingsRouter({ settings, permissionRepo, automation: () => ({ records }) as never }));
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
  it('persists notification recipients without enabling analysis or configuring a daily schedule', async () => {
    const response = await fetch(`${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-User-Id': 'editor-1' },
      body: JSON.stringify({ enabled: false, autoDraft: false, dailyTime: '09:00', dailyConfigured: false, expectedVersion: 0, expectedNotificationVersion: 0, notificationRecipients: [' alice ', 'bob', 'alice'] }),
    });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ enabled: false, version: 0, notificationVersion: 1, dailyConfigured: false, notificationRecipients: ['alice', 'bob'] });
    const read = await fetch(`${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`, { headers: { 'X-User-Id': 'editor-1' } });
    expect(await read.json()).toMatchObject({ notificationRecipients: ['alice', 'bob'], dailyConfigured: false });
    const stale = await fetch(`${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-User-Id': 'editor-1' },
      body: JSON.stringify({ enabled: false, autoDraft: false, dailyTime: '09:00', expectedVersion: 0, expectedNotificationVersion: 0, notificationRecipients: ['different-person'] }),
    });
    expect(stale.status).toBe(409);
    const toggle = await fetch(`${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-User-Id': 'editor-1' }, body: JSON.stringify({ enabled: true }),
    });
    expect(await toggle.json()).toMatchObject({ enabled: true, notificationRecipients: ['alice', 'bob'], dailyConfigured: false });
  });

  it.each([{ notificationRecipients: ['bad/id'] }, { notificationRecipients: Array.from({ length: 21 }, (_, i) => `person-${i}`) }, { notificationRecipients: 'not-an-array' }])("rejects malformed recipients without changing persisted policy: %j", async ({ notificationRecipients }) => {
    const response = await fetch(`${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json', 'X-User-Id': 'editor-1' },
      body: JSON.stringify({ enabled: false, autoDraft: false, dailyTime: '09:00', expectedVersion: 0, expectedNotificationVersion: 0, notificationRecipients }),
    });
    expect(response.status).toBe(400);
    const stored = await new AppConfigRepository(db).listAll();
    expect(stored).toHaveLength(0);
  });
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

it('saves one versioned daily policy and rejects stale or invalid writes', async () => {
  const url = `${baseUrl}/api/task-guard/workflows/wf-1/auto-analysis`;
  const headers = { 'Content-Type': 'application/json', 'X-User-Id': 'editor-1' };
  const policy = { enabled: true, autoDraft: true, dailyTime: '10:30', expectedVersion: 0 };
  const saved = await fetch(url, { method: 'PUT', headers, body: JSON.stringify(policy) });
  expect(saved.status).toBe(200);
  expect(await saved.json()).toMatchObject({ enabled: true, autoDraft: true, dailyTime: '10:30', timezone: 'Asia/Shanghai', version: 1, dailyConfigured: true });
  expect((await fetch(url, { method: 'PUT', headers, body: JSON.stringify(policy) })).status).toBe(409);
  expect((await fetch(url, { method: 'PUT', headers, body: JSON.stringify({ ...policy, dailyTime: '24:00', expectedVersion: 1 }) })).status).toBe(400);
  const off = await fetch(url, { method: 'PUT', headers, body: JSON.stringify({ ...policy, enabled: false, expectedVersion: 1 }) });
  expect(await off.json()).toMatchObject({ enabled: false, autoDraft: true, version: 2 });
});

it('authorizes history separately and validates bounded query parameters', async () => {
  const url = `${baseUrl}/api/task-guard/workflows/wf-1/automation-records`;
  expect((await fetch(url, { headers: { 'X-User-Id': 'viewer-1' } })).status).toBe(403);
  expect(records).not.toHaveBeenCalled();
  const headers = { 'X-User-Id': 'editor-1' };
  for (const params of ['limit=100000', 'limit=0', 'limit=8&limit=20', 'status=invalid', 'period=invalid']) {
    expect((await fetch(`${url}?${params}`, { headers })).status).toBe(400);
  }
  expect(records).not.toHaveBeenCalled();
  const response = await fetch(`${url}?period=7d&status=blocked&limit=20`, { headers });
  expect(response.status).toBe(200);
  expect(records).toHaveBeenCalledWith('wf-1', { period: '7d', status: 'blocked', limit: 20 });
  expect(await response.json()).toEqual({ jobs: [], nextCursor: null });
});
