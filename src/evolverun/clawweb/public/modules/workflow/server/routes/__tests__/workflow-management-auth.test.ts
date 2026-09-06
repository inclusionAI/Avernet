import { afterEach, describe, expect, it, vi } from "vitest";
import express from "express";
import { once } from "node:events";
import type { BotWorkflowPermissionRepository } from "../../repositories/bot-workflow-permission-repository.js";
import type { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import type { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { createWorkflowsRouter } from "../workflows.js";

let server: ReturnType<express.Application["listen"]> | null = null;

afterEach(async () => {
  const active = server;
  server = null;
  if (active) await new Promise<void>((resolve) => active.close(() => resolve()));
});

async function start() {
  const permissions = [{
    id: 1,
    bot_id: null,
    bot_owner_id: "editor-1",
    workflow_id: "wf-1",
    env: "pre",
    can_view: 1,
    can_execute: 1,
    can_edit: 1,
    gmt_create: 1,
    gmt_modified: 1,
  }];
  const botPermRepo = {
    hasEditPermission: vi.fn(async (_workflowId: string, userId: string) => userId === "editor-1"),
    getViewByIdsForOwner: vi.fn(async (userId: string) => ({
      restrictedIds: new Set(["wf-1"]),
      viewableIds: new Set(userId === "viewer-1" || userId === "editor-1" ? ["wf-1"] : []),
    })),
    findByWorkflowId: vi.fn(async () => permissions),
    upsert: vi.fn(async () => permissions[0]),
    delete: vi.fn(async () => true),
    deleteById: vi.fn(async () => true),
  } as unknown as BotWorkflowPermissionRepository;
  const workflowSpecRepo = {
    delete: vi.fn(async () => true),
  } as unknown as WorkflowSpecRepository;
  const historyRepo = {
    listHistory: vi.fn(async () => []),
  } as unknown as WorkflowDeployHistoryRepository;

  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    req.isAdmin = req.header("X-Test-Admin") === "true";
    next();
  });
  app.use("/api/workflows", createWorkflowsRouter(
    workflowSpecRepo,
    null,
    botPermRepo,
    null,
    historyRepo,
    null,
  ));
  const instance = app.listen(0, "127.0.0.1");
  await once(instance, "listening");
  server = instance;
  const address = instance.address();
  if (!address || typeof address === "string") throw new Error("test server did not bind");
  return `http://127.0.0.1:${address.port}`;
}

describe("workflow management authorization", () => {
  it("allows a workflow editor to read and change permission settings", async () => {
    const baseUrl = await start();
    const headers = { "Content-Type": "application/json", "X-User-Id": "editor-1" };

    const list = await fetch(`${baseUrl}/api/workflows/wf-1/bot-permissions`, { headers });
    expect(list.status).toBe(200);

    const update = await fetch(`${baseUrl}/api/workflows/wf-1/bot-permissions`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ botOwnerId: "viewer-2", botId: null, canView: 1, canExecute: 0, canEdit: 0 }),
    });
    expect(update.status).toBe(200);

    const remove = await fetch(`${baseUrl}/api/workflows/wf-1/bot-permissions?permissionId=1`, {
      method: "DELETE",
      headers,
    });
    expect(remove.status).toBe(200);
  });

  it("allows a workflow editor to read history and delete the workflow", async () => {
    const baseUrl = await start();
    const headers = { "X-User-Id": "editor-1" };

    expect((await fetch(`${baseUrl}/api/workflows/wf-1/history`, { headers })).status).toBe(200);
    expect((await fetch(`${baseUrl}/api/workflows/wf-1`, { method: "DELETE", headers })).status).toBe(200);
  });

  it("denies management access to a view-only user", async () => {
    const baseUrl = await start();
    const headers = { "X-User-Id": "viewer-1" };

    expect((await fetch(`${baseUrl}/api/workflows/wf-1/bot-permissions`, { headers })).status).toBe(403);
    expect((await fetch(`${baseUrl}/api/workflows/wf-1/history`, { headers })).status).toBe(200);
    expect((await fetch(`${baseUrl}/api/workflows/wf-1`, { method: "DELETE", headers })).status).toBe(403);
  });
});
