/**
 * Tests for GET /api/workflows/accessible
 */
import express from "express";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { once } from "node:events";
import { createAccessibleWorkflowsRouter } from "../workflows-accessible.js";
import type { WorkflowSpecRepository, AccessibleWorkflowRow } from "../../repositories/workflow-spec-repository.js";

let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;

function createRepo(): WorkflowSpecRepository {
  return {
    listAccessible: vi.fn(),
    countAccessible: vi.fn(),
  } as unknown as WorkflowSpecRepository;
}

async function startRouter(repo: WorkflowSpecRepository | null, isAdmin = false) {
  const app = express();
  app.use((req, _res, next) => {
    (req as express.Request & { isAdmin?: boolean }).isAdmin = isAdmin;
    next();
  });
  app.use("/api/workflows/accessible", createAccessibleWorkflowsRouter({ workflowSpecRepo: repo }));
  const instance = app.listen(0, "127.0.0.1");
  await once(instance, "listening");
  server = instance;
  const address = instance.address();
  if (!address || typeof address === "string") throw new Error("test server did not bind a TCP port");
  baseUrl = `http://127.0.0.1:${address.port}`;
}

beforeEach(() => {
  server = null;
  baseUrl = "";
});

afterEach(async () => {
  const activeServer = server;
  server = null;
  if (activeServer) await new Promise<void>((resolve) => activeServer.close(() => resolve()));
});

describe("GET /api/workflows/accessible", () => {
  it("returns 503 when database is not configured", async () => {
    await startRouter(null);
    const res = await fetch(`${baseUrl}/api/workflows/accessible?userId=u1`);
    expect(res.status).toBe(503);
    expect(await res.json()).toMatchObject({ code: "SERVICE_UNAVAILABLE" });
  });

  it("returns 400 when both userId and botId are missing", async () => {
    const repo = createRepo();
    await startRouter(repo);
    const res = await fetch(`${baseUrl}/api/workflows/accessible`);
    expect(res.status).toBe(400);
    expect(await res.json()).toMatchObject({
      code: "INVALID_PARAMS",
      message: "at least one of [userId, botId] is required",
    });
  });

  it("returns accessible workflows for mode C", async () => {
    const repo = createRepo();
    const rows: AccessibleWorkflowRow[] = [{
      workflow_id: "wf-1",
      title: "One",
      pack_id: "pk-1",
      gmt_modified: 1787283979000,
      bot_owner_id: "u1",
      bot_id: "b1",
    }];
    vi.mocked(repo.listAccessible).mockResolvedValue(rows);
    vi.mocked(repo.countAccessible).mockResolvedValue(1);
    await startRouter(repo, false);

    const res = await fetch(`${baseUrl}/api/workflows/accessible?userId=u1&botId=b1`, {
      headers: { Cookie: "staff_id=u1" },
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      items: [{
        workflowId: "wf-1",
        title: "One",
        packId: "pk-1",
        updatedAt: 1787283979000,
        command: "wf-1",
        ownerId: "u1",
        botId: "b1",
      }],
      total: 1,
    });
  });

  it("returns 403 when non-admin queries another user", async () => {
    const repo = createRepo();
    await startRouter(repo, false);
    const res = await fetch(`${baseUrl}/api/workflows/accessible?userId=u1&botId=b1`, {
      headers: { Cookie: "staff_id=u2" },
    });
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({ code: "FORBIDDEN" });
  });

  it("allows admin to query another user", async () => {
    const repo = createRepo();
    vi.mocked(repo.listAccessible).mockResolvedValue([]);
    vi.mocked(repo.countAccessible).mockResolvedValue(0);
    await startRouter(repo, true);
    const res = await fetch(`${baseUrl}/api/workflows/accessible?userId=u1&botId=b1`, {
      headers: { Cookie: "staff_id=admin" },
    });
    expect(res.status).toBe(200);
  });

  it("returns 403 when non-admin uses botId-only mode", async () => {
    const repo = createRepo();
    await startRouter(repo, false);
    const res = await fetch(`${baseUrl}/api/workflows/accessible?botId=b1`, {
      headers: { Cookie: "staff_id=u1" },
    });
    expect(res.status).toBe(403);
    expect(await res.json()).toMatchObject({
      code: "FORBIDDEN",
      message: "botId-only mode requires admin",
    });
  });

  it("allows admin to use botId-only mode", async () => {
    const repo = createRepo();
    vi.mocked(repo.listAccessible).mockResolvedValue([]);
    vi.mocked(repo.countAccessible).mockResolvedValue(0);
    await startRouter(repo, true);
    const res = await fetch(`${baseUrl}/api/workflows/accessible?botId=b1`, {
      headers: { Cookie: "staff_id=admin" },
    });
    expect(res.status).toBe(200);
  });
});
