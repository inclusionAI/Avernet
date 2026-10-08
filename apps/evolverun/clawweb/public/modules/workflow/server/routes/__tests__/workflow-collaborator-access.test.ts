// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import express from "express";
import { once } from "node:events";
import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import type { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import { createWorkflowsRouter } from "../workflows.js";

const cleanup: Array<() => Promise<void>> = [];
let sequence = 0;
afterEach(async () => { for (const close of cleanup.splice(0)) await close(); vi.restoreAllMocks(); });

async function start() {
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
  const spec = { id: "shared", version: "1", title: "Shared", nodes: [
    { id: "step", title: "Step", executor: { type: "shell", command: "echo test" } },
  ] };
  const row = { workflow_id: "shared", pack_id: null, title: "Shared", owner_id: owner,
    spec_json: JSON.stringify(spec), gmt_modified: 1 };
  const specs = {
    listSummaries: async () => [row],
    findPage: async () => ({ rows: [row], total: 1 }),
    existsByWorkflowId: async (id: string) => id === "shared",
    findByWorkflowId: async (id: string) => id === "shared" ? row : null,
    upsert: async (_id: string, _pack: unknown, json: string) => { row.spec_json = json; return row; },
  } as unknown as WorkflowSpecRepository;
  const app = express();
  app.use(express.json());
  app.use("/api/workflows", createWorkflowsRouter(specs, null, permissions, null, null));
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
  return { request, permissions, directory, bots, owner, member, spec };
}

describe("workflow collaborator HTTP access", () => {
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
