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

/**
 * Command resolution on the clawweb re-run route.
 *
 * A cron/panel run records no input_json.command, so the route falls back to the
 * facade binding. That binding holds only the workflow name, and a bare slash
 * command makes the Bot answer with help text instead of starting a run, so the
 * replayed trigger must always carry a task description.
 */
describe("POST /:flowId/rerun — command resolution", () => {
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
    // facade_bindings stores the bare name; only ids-log-self-audit is bound here.
    const facadeRepo = {
      findByWorkflowId: async (workflowId: string) =>
        workflowId === "ids-log-self-audit" ? [{ command: "ids-log-self-audit" }] : [],
    };

    const app = express();
    app.use(express.json());
    app.use("/runs", createRunsRouter(
      flowRunRepo, null, eventRepo, null, null, null, null, null, null, facadeRepo as never,
    ));
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
    sendInterventionMock.mockResolvedValue({ ok: true, messageId: "msg-new-1", sessionId: "session-new-1" });
    raw.prepare("DELETE FROM flow_runs").run();
    raw.prepare("DELETE FROM flow_events").run();
  });

  function insertRun(flowId: string, workflowId: string, inputJson: string | null) {
    raw
      .prepare(
        `INSERT INTO flow_runs
         (flow_id, workflow_id, workflow_title, status, input_json, credentials_json,
          origin_bot_id, origin_session_key, started_at, gmt_create)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      )
      .run(
        flowId, workflowId, "Test Workflow", "completed", inputJson,
        JSON.stringify({ BOT_TYPE: "personal", BOT_ID: "20260629_g3xxlxd3", OWNER_ID: "160855" }),
        "test-bot:160855", "agent:main:cron:original",
        Math.floor(Date.now() / 1000), Math.floor(Date.now() / 1000),
      );
  }

  async function rerun(flowId: string) {
    const res = await fetch(`${baseUrl}/runs/${flowId}/rerun`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
    });
    return { status: res.status, body: await res.json() as Record<string, unknown> };
  }

  function sentTrigger(): string {
    const callArg = sendInterventionMock.mock.calls.at(-1)![0] as { message: string };
    return callArg.message.split("\n").at(-1) as string;
  }

  it("replays the command recorded on the run, verbatim", async () => {
    insertRun("flow-recorded", "social-appeal", JSON.stringify({ command: "/social-appeal 2088242692000193" }));

    const { status } = await rerun("flow-recorded");
    expect(status).toBe(200);
    expect(sentTrigger()).toBe("/social-appeal 2088242692000193");
  });

  it("falls back to /workflow run for a run that records no message", async () => {
    insertRun("flow-cron", "ids-log-self-audit", JSON.stringify({ message: null, params: { enable_yuque_sync: "true" } }));

    const { status } = await rerun("flow-cron");
    expect(status).toBe(200);
    // Bare "/ids-log-self-audit" would make the Bot print help instead of running.
    expect(sentTrigger()).toBe("/workflow run ids-log-self-audit");
  });

  it("keeps the run's own message when the binding supplies the command", async () => {
    insertRun("flow-cron-msg", "ids-log-self-audit", JSON.stringify({ message: "什么是tc?" }));

    const { status } = await rerun("flow-cron-msg");
    expect(status).toBe(200);
    expect(sentTrigger()).toBe("/ids-log-self-audit 什么是tc?");
  });

  it("still names an unbound workflow by id when the run records no message", async () => {
    insertRun("flow-unbound", "no-such-workflow", JSON.stringify({ message: null }));

    const { status } = await rerun("flow-unbound");
    expect(status).toBe(200);
    expect(sentTrigger()).toBe("/workflow run no-such-workflow");
  });

  it("rejects a run that names no workflow at all", async () => {
    insertRun("flow-blank", "", JSON.stringify({ message: null }));

    const { status, body } = await rerun("flow-blank");
    expect(status).toBe(422);
    expect(String(body.message)).toContain("无法重跑");
    expect(sendInterventionMock).not.toHaveBeenCalled();
  });
});
