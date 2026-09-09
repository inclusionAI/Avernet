// @vitest-environment node
import { beforeAll, afterAll, describe, it, expect } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import type { Server } from "node:http";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { FlowRunRepository } from "../../../repositories/flow-run-repository.js";
import { WorkflowDeployHistoryRepository } from "../../../repositories/workflow-deploy-history-repository.js";
import { createInternalRunsRouter } from "../runs.js";
import { createInternalDeployHistoryRouter } from "../deploy-history.js";

describe("ClawMind workflow version wire contract", () => {
  const raw = new Database(":memory:");
  const db: IDatabase = {
    dbType: "sqlite", dialect: sqliteDialect,
    query: async <T>(sql: string, params: unknown[] = []) => raw.prepare(sql).all(...params) as T[],
    exec: async (sql, params = []) => { const result = raw.prepare(sql).run(...params); return { affectedRows: result.changes }; },
    transaction: async fn => fn(db),
    close: async () => raw.close(),
  };
  let server: Server;
  let baseUrl: string;
  beforeAll(async () => {
    raw.exec(`CREATE TABLE flow_runs (
      id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, workflow_id TEXT, workflow_title TEXT, status TEXT,
      params_json TEXT, input_json TEXT, result_json TEXT, node_count INTEGER, succeeded_count INTEGER,
      failed_count INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER, triggered_by TEXT,
      identity_key TEXT, current_phase TEXT, started_at INTEGER, completed_at INTEGER, credentials_json TEXT,
      origin_session_key TEXT, origin_session_id TEXT, origin_bot_id TEXT, user_id TEXT, plugin_version TEXT,
      engine TEXT, state_json TEXT, workflow_version INTEGER, workflow_deploy_number INTEGER,
      gmt_create INTEGER, gmt_modified INTEGER, evolution_analysis_status TEXT
    ); CREATE TABLE workflow_deploy_history (
      id INTEGER PRIMARY KEY, pack_id TEXT, workflow_id TEXT, deploy_number INTEGER, version INTEGER,
      tag_name TEXT, action TEXT, from_deploy_number INTEGER, spec_json TEXT, note TEXT, bot_id TEXT,
      owner_id TEXT, is_active INTEGER, gmt_create INTEGER, gmt_modified INTEGER
    );`);
    const history = new WorkflowDeployHistoryRepository(db);
    await history.insert({ packId: "pack", workflowId: "demo", deployNumber: 5, version: 2, action: "deploy", tagName: "deploy/demo/#5", specJson: '{"id":"demo"}', isActive: true });
    const app = express();
    app.use(express.json());
    app.use("/runs", createInternalRunsRouter(new FlowRunRepository(db)));
    app.use("/deploy-history", createInternalDeployHistoryRouter(history));
    await new Promise<void>(resolve => { server = app.listen(0, "127.0.0.1", resolve); });
    baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  });
  afterAll(async () => {
    await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    raw.close();
  });

  it("persists both runtime version fields through the HTTP route", async () => {
    const response = await fetch(`${baseUrl}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ flow_id: "v2-run", workflow_id: "demo", status: "running", workflow_version: 2, workflow_deploy_number: 5 }) });
    expect(response.status).toBe(201);
    const row = raw.prepare("SELECT workflow_version, workflow_deploy_number FROM flow_runs WHERE flow_id = ?").get("v2-run");
    expect(row).toEqual({ workflow_version: 2, workflow_deploy_number: 5 });
  });

  it("keeps legacy inserts without version metadata compatible", async () => {
    const response = await fetch(`${baseUrl}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ flow_id: "legacy", workflow_id: "demo", status: "running" }) });
    expect(response.status).toBe(201);
    expect(raw.prepare("SELECT workflow_version, workflow_deploy_number FROM flow_runs WHERE flow_id = 'legacy'").get()).toEqual({ workflow_version: null, workflow_deploy_number: null });
  });

  it.each([0, -1, 1.5, "2", 9007199254740992])("rejects invalid version metadata %s before persistence", async value => {
    for (const field of ["workflow_version", "workflow_deploy_number"]) {
      const response = await fetch(`${baseUrl}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ flow_id: `invalid-${field}-${value}`, workflow_id: "demo", status: "running", [field]: value }) });
      expect(response.status).toBe(400);
    }
  });

  it.each(["active", "versions/2/snapshot"])("returns a flat %s snapshot including pack identity", async endpoint => {
    const response = await fetch(`${baseUrl}/deploy-history/demo/${endpoint}`);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ found: true, workflowId: "demo", packId: "pack", version: 2, deployNumber: 5, specJson: '{"id":"demo"}' });
  });

  it("does not substitute another workflow's snapshot", async () => {
    const response = await fetch(`${baseUrl}/deploy-history/other/versions/2/snapshot`);
    expect(await response.json()).toEqual({ found: false });
  });

  it("activates the rollback target when the caller sends isActive", async () => {
    const response = await fetch(`${baseUrl}/deploy-history`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        packId: "pack", workflowId: "demo", deployNumber: 6, version: 1,
        tagName: "deploy/demo/#1", action: "rollback", fromDeployNumber: 5,
        specJson: '{"id":"demo","version":1}', isActive: true,
      }),
    });

    expect(response.status).toBe(200);
    expect(raw.prepare("SELECT version, is_active FROM workflow_deploy_history WHERE workflow_id = 'demo' ORDER BY deploy_number").all())
      .toEqual([{ version: 2, is_active: 0 }, { version: 1, is_active: 1 }]);
  });
});
