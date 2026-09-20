// @vitest-environment node
/**
 * Rerun route lifecycle_stage propagation tests.
 *
 * Verifies that POST /:flowId/rerun extracts the STAGE field from
 * credentials_json and passes it as lifecycleStage to sendIntervention,
 * and that missing/invalid STAGE leaves lifecycleStage undefined (backward compat).
 */
import { beforeAll, afterAll, describe, it, expect, vi, beforeEach } from "vitest";
import express from "express";
import Database from "better-sqlite3";
import type { Server } from "node:http";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import { FlowEventRepository } from "../../repositories/event-repository.js";

// Capture sendIntervention calls
const sendInterventionMock = vi.fn();

// Must mock before importing the route module
vi.mock("@avernet/clawevolve/server/services/baas-intervention", () => ({
  sendIntervention: (...args: unknown[]) => sendInterventionMock(...args),
}));

// Import after mock is set up
const { createRunsRouter } = await import("../runs.js");

describe("POST /:flowId/rerun — lifecycle_stage propagation", () => {
  const raw = new Database(":memory:");
  const db: IDatabase = {
    dbType: "sqlite",
    dialect: sqliteDialect,
    query: async <T>(sql: string, params: unknown[] = []) =>
      raw.prepare(sql).all(...params) as T[],
    exec: async (sql, params = []) => {
      const result = raw.prepare(sql).run(...params);
      return { affectedRows: result.changes };
    },
    transaction: async (fn) => fn(db),
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
    ); CREATE TABLE flow_events (
      id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT, flow_id TEXT, workflow_id TEXT,
      node_id TEXT, event_type TEXT, attempt INTEGER, time INTEGER, data_json TEXT,
      error_text TEXT, gmt_create INTEGER, gmt_modified INTEGER
    );`);

    const flowRunRepo = new FlowRunRepository(db);
    const eventRepo = new FlowEventRepository(db);

    const app = express();
    app.use(express.json());
    app.use("/runs", createRunsRouter(flowRunRepo, null, eventRepo, null, null, null, null, null, null));
    await new Promise<void>((resolve) => {
      server = app.listen(0, "127.0.0.1", resolve);
    });
    baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
  });

  afterAll(async () => {
    await new Promise<void>((resolve, reject) =>
      server.close((error) => (error ? reject(error) : resolve())),
    );
    raw.close();
  });

  beforeEach(() => {
    sendInterventionMock.mockReset();
    sendInterventionMock.mockResolvedValue({
      ok: true,
      messageId: "msg-new-1",
      sessionId: "session-new-1",
    });
    // Clean up rows between tests
    raw.prepare("DELETE FROM flow_runs").run();
    raw.prepare("DELETE FROM flow_events").run();
  });

  function insertRun(
    flowId: string,
    opts: {
      credentialsJson: string | null;
      originBotId?: string;
      inputJson?: string | null;
    },
  ) {
    raw
      .prepare(
        `INSERT INTO flow_runs
         (flow_id, workflow_id, workflow_title, status, input_json, credentials_json,
          origin_bot_id, origin_session_key, started_at, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        flowId,
        "test-workflow",
        "Test Workflow",
        "completed",
        opts.inputJson ?? JSON.stringify({ command: "/kf-direct", message: "hello" }),
        opts.credentialsJson,
        opts.originBotId ?? "test-bot:160855",
        "agent:main:dashboard:original",
        Math.floor(Date.now() / 1000),
        Math.floor(Date.now() / 1000),
      );
  }

  it("passes lifecycleStage=draft when credentials_json has STAGE=draft", async () => {
    insertRun("flow-draft", {
      credentialsJson: JSON.stringify({
        TOKEN: "xxx",
        CLIENT_ID: "yyy",
        BOT_ID: "20260629_g3xxlxd3",
        OWNER_ID: "160855",
        STAGE: "draft",
      }),
    });

    const res = await fetch(`${baseUrl}/runs/flow-draft/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body.ok).toBe(true);

    expect(sendInterventionMock).toHaveBeenCalledOnce();
    const callArg = sendInterventionMock.mock.calls[0][0] as Record<string, unknown>;
    expect(callArg.lifecycleStage).toBe("draft");
  });

  it("passes lifecycleStage=verify when credentials_json has STAGE=verify", async () => {
    insertRun("flow-verify", {
      credentialsJson: JSON.stringify({ STAGE: "verify" }),
    });

    const res = await fetch(`${baseUrl}/runs/flow-verify/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(res.status).toBe(200);

    expect(sendInterventionMock).toHaveBeenCalledOnce();
    const callArg = sendInterventionMock.mock.calls[0][0] as Record<string, unknown>;
    expect(callArg.lifecycleStage).toBe("verify");
  });

  it("passes lifecycleStage=online when credentials_json has STAGE=online", async () => {
    insertRun("flow-online", {
      credentialsJson: JSON.stringify({ STAGE: "online" }),
    });

    const res = await fetch(`${baseUrl}/runs/flow-online/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(res.status).toBe(200);

    expect(sendInterventionMock).toHaveBeenCalledOnce();
    const callArg = sendInterventionMock.mock.calls[0][0] as Record<string, unknown>;
    expect(callArg.lifecycleStage).toBe("online");
  });

  it("leaves lifecycleStage undefined when credentials_json has no STAGE", async () => {
    insertRun("flow-nostage", {
      credentialsJson: JSON.stringify({
        TOKEN: "xxx",
        OWNER_ID: "160855",
        BOT_ID: "default",
      }),
    });

    const res = await fetch(`${baseUrl}/runs/flow-nostage/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(res.status).toBe(200);

    expect(sendInterventionMock).toHaveBeenCalledOnce();
    const callArg = sendInterventionMock.mock.calls[0][0] as Record<string, unknown>;
    expect(callArg.lifecycleStage).toBeUndefined();
  });

  it("leaves lifecycleStage undefined when credentials_json is null", async () => {
    insertRun("flow-null-creds", {
      credentialsJson: null,
    });

    const res = await fetch(`${baseUrl}/runs/flow-null-creds/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(res.status).toBe(200);

    expect(sendInterventionMock).toHaveBeenCalledOnce();
    const callArg = sendInterventionMock.mock.calls[0][0] as Record<string, unknown>;
    expect(callArg.lifecycleStage).toBeUndefined();
  });

  it("leaves lifecycleStage undefined when credentials_json is invalid JSON", async () => {
    insertRun("flow-bad-json", {
      credentialsJson: "{not valid json",
    });

    const res = await fetch(`${baseUrl}/runs/flow-bad-json/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(res.status).toBe(200);

    expect(sendInterventionMock).toHaveBeenCalledOnce();
    const callArg = sendInterventionMock.mock.calls[0][0] as Record<string, unknown>;
    expect(callArg.lifecycleStage).toBeUndefined();
  });

  it("leaves lifecycleStage undefined when STAGE has an unrecognized value", async () => {
    insertRun("flow-weird-stage", {
      credentialsJson: JSON.stringify({ STAGE: "production" }),
    });

    const res = await fetch(`${baseUrl}/runs/flow-weird-stage/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    expect(res.status).toBe(200);

    expect(sendInterventionMock).toHaveBeenCalledOnce();
    const callArg = sendInterventionMock.mock.calls[0][0] as Record<string, unknown>;
    expect(callArg.lifecycleStage).toBeUndefined();
  });
});
