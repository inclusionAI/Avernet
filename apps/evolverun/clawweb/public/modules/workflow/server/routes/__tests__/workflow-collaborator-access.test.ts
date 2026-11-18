// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import express from "express";
import { once } from "node:events";
import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import type { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { workflowsCache } from "../workflows-list.js";
import { createWorkflowsRouter } from "../workflows.js";

const cleanup: Array<() => Promise<void>> = [];
let sequence = 0;
afterEach(async () => { for (const close of cleanup.splice(0)) await close(); vi.restoreAllMocks(); });

async function start(permissionsOverride?: BotWorkflowPermissionRepository | null) {
  const owner = `owner-${++sequence}`;
  const member = `member-${sequence}`;
  const raw = new Database(":memory:");
  raw.exec(`CREATE TABLE bot_workflow_permissions (
    id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL, env TEXT NOT NULL,
    can_view INTEGER NOT NULL, can_execute INTEGER NOT NULL, can_edit INTEGER NOT NULL,
    gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
  )`);
  const bots = [{ botId: "shared-bot", ownerId: owner, displayBotId: "shared-bot", status: "active", source: "test" }];
  const directory = { listBots: vi.fn(async (actor: string) => actor === member ? bots : []) };
  const permissions = new BotWorkflowPermissionRepository(new SqliteDatabase(raw), directory);
  await permissions.upsert({ bot_id: "shared-bot", bot_owner_id: owner, workflow_id: "shared",
    can_view: 1, can_edit: 1, can_execute: 1 });
  const effectivePermissions = permissionsOverride === undefined ? permissions : permissionsOverride;
  const spec = { id: "shared", version: "1", title: "Shared", nodes: [
    { id: "step", title: "Step", executor: { type: "shell", command: "echo test" } },
  ] };
  const row = { workflow_id: "shared", pack_id: null, title: "Shared", owner_id: owner,
    spec_json: JSON.stringify(spec), gmt_modified: 1 };
  const rows = [row];
  const addWorkflow = (workflowId: string) => {
    const extra = { ...row, workflow_id: workflowId, title: workflowId };
    rows.push(extra);
    return extra;
  };
  const specs = {
    listSummaries: async () => rows,
    findPage: async () => ({ rows, total: rows.length }),
    existsByWorkflowId: async (id: string) => id === "shared",
    findByWorkflowId: async (id: string) => id === "shared" ? row : null,
    upsert: async (_id: string, _pack: unknown, json: string) => { row.spec_json = json; return row; },
  } as unknown as WorkflowSpecRepository;
  const app = express();
  app.use(express.json());
  app.use((req, _res, next) => {
    req.isAdmin = req.header("X-Test-Admin") === "true";
    next();
  });
  app.use("/api/workflows", createWorkflowsRouter(specs, null, effectivePermissions, null, null));
  const server = app.listen(0, "127.0.0.1");
  await once(server, "listening");
  const address = server.address();
  if (!address || typeof address === "string") throw new Error("server did not bind");
  cleanup.push(async () => {
    server.closeAllConnections();
    await new Promise<void>(resolve => server.close(() => resolve()));
    raw.close();
  });
  const request = (path: string, actor = member, init?: RequestInit) => fetch(
    `http://127.0.0.1:${address.port}/api/workflows${path}`,
    { ...init, headers: { "X-User-Id": actor, "Content-Type": "application/json", ...init?.headers } },
  );
  return { request, permissions, directory, bots, owner, member, spec, addWorkflow };
}

describe("workflow collaborator HTTP access", () => {
  it.each([
    ["/", false],
    ["/", true],
    ["/list", false],
    ["/list", true],
  ])("keeps %s unrestricted when the permission table is empty (Bot scope: %s)", async (path, botScoped) => {
    const { request, permissions, owner } = await start();
    await permissions.delete("shared-bot", owner, "shared");
    const query = botScoped ? `?botOwnerId=${owner}&botId=shared-bot` : "";
    const response = await request(`${path}${query}`, "stranger");
    expect(response.status).toBe(200);
    const body = await response.json();
    const rows = path === "/list" ? body.data : body;
    expect(rows.map((row: { workflowId: string }) => row.workflowId)).toEqual(["shared"]);
  });

  it("returns the listed Bot workflow's details using the caller's existing identity", async () => {
    const { request, owner } = await start();
    const list = await request(`?botOwnerId=${owner}&botId=shared-bot`);
    expect(list.status).toBe(200);
    expect(list.headers.get("cache-control")).toBe("no-store");
    expect((await list.json()).map((row: { workflowId: string }) => row.workflowId)).toEqual(["shared"]);
    const detail = await request("/shared");
    expect(detail.status).toBe(200);
    expect((await detail.json()).id).toBe("shared");
  });

  it("accepts the existing staff cookie without adding Bot context to the detail request", async () => {
    const { request, member } = await start();
    expect((await request("/shared", "", { headers: { Cookie: `staff_id=${member}` } })).status).toBe(200);
  });

  it("does not let a logged-in stranger use another Owner's query parameters", async () => {
    const { request, owner } = await start();
    expect(await (await request(`?botOwnerId=${owner}&botId=shared-bot`, "stranger")).json()).toEqual([]);
    const page = await request(`/list?botOwnerId=${owner}&botId=shared-bot`, "stranger");
    expect((await page.json()).data).toEqual([]);
    expect((await request("/shared", "stranger")).status).toBe(403);
  });

  it.each([
    ["/", ""],
    ["/list", "/list"],
  ])("rejects an anonymous caller even when Bot-owner query parameters are present on %s", async (path, prefix) => {
    const { request, owner } = await start();
    const response = await request(`${prefix}?botOwnerId=${owner}&botId=shared-bot`, "");
    expect(response.status).toBe(401);
    expect((await response.json()).error).toBe("Unauthorized");
  });

  it.each([
    ["/", ""],
    ["/list", "/list"],
  ])("keeps execute requests deny-by-default when the permission table is empty on %s", async (path, prefix) => {
    const { request, permissions, owner } = await start();
    await permissions.delete("shared-bot", owner, "shared");
    const response = await request(`${prefix}?permission=execute&botOwnerId=${owner}&botId=shared-bot`);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(path === "/" ? body : body.data).toEqual([]);
  });

  it.each([
    ["/", ""],
    ["/list", "/list"],
  ])("does not give a stranger execute access through another Owner's query parameters on %s", async (path, prefix) => {
    const { request, owner } = await start();
    const response = await request(`${prefix}?permission=execute&botOwnerId=${owner}&botId=shared-bot`, "stranger");
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(path === "/" ? body : body.data).toEqual([]);
  });

  it.each([
    ["/", ""],
    ["/list", "/list"],
  ])("fails execute requests closed when no permission repository is configured on %s", async (path, prefix) => {
    const { request, owner } = await start(null);
    const response = await request(`${prefix}?permission=execute&botOwnerId=${owner}&botId=shared-bot`);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(path === "/" ? body : body.data).toEqual([]);
  });

  it.each([
    ["/", ""],
    ["/list", "/list"],
  ])("shows a globally executable workflow inside the selected Bot scope on %s", async (path, prefix) => {
    const { request, permissions, owner, addWorkflow } = await start();
    await permissions.upsert({ workflow_id: "shared", bot_id: "shared-bot", bot_owner_id: owner,
      can_view: 1, can_edit: 1, can_execute: 0 });
    addWorkflow("global-execute");
    // The HTTP save path invalidates the shared summary cache; this fixture mutates the repository directly.
    workflowsCache.invalidate();
    await permissions.upsert({ workflow_id: "global-execute", bot_id: null, bot_owner_id: "*",
      can_view: 0, can_edit: 0, can_execute: 1 });

    const response = await request(`${prefix}?permission=execute&botOwnerId=${owner}&botId=shared-bot`);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect((path === "/" ? body : body.data).map((row: { workflowId: string }) => row.workflowId))
      .toEqual(["global-execute"]);
  });

  it("retains the administrator bypass for execute lists", async () => {
    const { request, permissions, owner } = await start();
    await permissions.upsert({ workflow_id: "shared", bot_id: "shared-bot", bot_owner_id: owner,
      can_view: 0, can_edit: 0, can_execute: 0 });
    const response = await request("?permission=execute", "stranger", {
      headers: { "X-Test-Admin": "true" },
    });
    expect(response.status).toBe(200);
    expect((await response.json()).map((row: { workflowId: string }) => row.workflowId)).toEqual(["shared"]);
  });

  it.each([
    ["/", ""],
    ["/list", "/list"],
  ])("returns only executable workflows when permission=execute is requested on %s", async (path, prefix) => {
    const { request, permissions, owner } = await start();
    await permissions.upsert({ workflow_id: "shared", bot_id: "shared-bot", bot_owner_id: owner,
      can_view: 1, can_edit: 1, can_execute: 0 });
    const denied = await request(`${prefix}?permission=execute&botOwnerId=${owner}&botId=shared-bot`);
    expect(denied.status).toBe(200);
    const deniedBody = await denied.json();
    expect(path === "/" ? deniedBody : deniedBody.data).toEqual([]);

    await permissions.upsert({ workflow_id: "shared", bot_id: "shared-bot", bot_owner_id: owner,
      can_view: 0, can_edit: 0, can_execute: 1 });
    const allowed = await request(`${prefix}?permission=execute&botOwnerId=${owner}&botId=shared-bot`);
    expect(allowed.status).toBe(200);
    const allowedBody = await allowed.json();
    expect((path === "/" ? allowedBody : allowedBody.data).map((row: { workflowId: string }) => row.workflowId)).toEqual(["shared"]);
  });

  it("rechecks membership on a cached list and preserves direct access during directory failure", async () => {
    const { request, owner, member, directory, permissions } = await start();
    const path = `?botOwnerId=${owner}&botId=shared-bot`;
    expect((await (await request(path)).json()).length).toBe(1);
    directory.listBots.mockRejectedValue(new Error("unavailable"));
    vi.spyOn(console, "warn").mockImplementation(() => {});
    expect(await (await request(path)).json()).toEqual([]);
    expect((await request("/shared")).status).toBe(403);
    await permissions.upsert({ workflow_id: "shared", bot_id: null, bot_owner_id: member,
      can_view: 1, can_edit: 0, can_execute: 0 });
    expect((await (await request(path)).json()).length).toBe(1);
    expect((await request("/shared")).status).toBe(200);
  });

  it("does not persist a direct grant when a collaborator edits an existing workflow", async () => {
    const { request, permissions, owner, member, spec, bots } = await start();
    const saved = await request("/save", member, { method: "POST", body: JSON.stringify({
      workflowId: "shared", originalWorkflowId: "shared", spec,
      botId: "shared-bot", botOwnerId: owner,
    }) });
    expect(saved.status).toBe(200);
    expect((await permissions.findByWorkflowId("shared")).some(row => row.bot_owner_id === member)).toBe(false);
    bots.splice(0);
    expect((await request("/shared")).status).toBe(403);
  });

  it("does not let a view-only collaborator edit", async () => {
    const { request, permissions, owner, member, spec } = await start();
    await permissions.upsert({ workflow_id: "shared", bot_id: "shared-bot", bot_owner_id: owner,
      can_view: 1, can_edit: 0, can_execute: 1 });
    expect((await request("/shared")).status).toBe(200);
    expect((await request("/save", member, { method: "POST", body: JSON.stringify({
      workflowId: "shared", originalWorkflowId: "shared", spec,
    }) })).status).toBe(403);
  });

  it("keeps a direct user view grant when the selected Bot's view bit is zero", async () => {
    const { request, permissions, owner, member } = await start();
    await permissions.upsert({ workflow_id: "shared", bot_id: "shared-bot", bot_owner_id: owner,
      can_view: 0, can_edit: 0, can_execute: 1 });
    await permissions.upsert({ workflow_id: "shared", bot_id: null, bot_owner_id: member,
      can_view: 1, can_edit: 0, can_execute: 0 });
    const list = await request(`?botOwnerId=${owner}&botId=shared-bot`);
    expect((await list.json()).map((row: { workflowId: string }) => row.workflowId)).toEqual(["shared"]);
    expect((await request("/shared")).status).toBe(200);
  });
});
