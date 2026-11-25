// @vitest-environment node
import { once } from "node:events";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import Database from "better-sqlite3";
import express from "express";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import type { DirectoryBot } from "@avernet/clawweb-shared/server/services/bot-directory";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import { createRunsRouter } from "../runs.js";

const secret = "execution-record-credential-must-not-leak";
const flow = {
  id: 1, flow_id: "fixture-flow", workflow_id: "fixture-workflow", workflow_title: "Fixture",
  status: "succeeded", params_json: ' { "values" : [1,true] }\n', input_json: "not valid {json",
  result_json: '"scalar-result"', node_count: 2, succeeded_count: 2, failed_count: 0,
  total_duration_ms: 2000000, total_token_usage: 123, triggered_by: "manual", identity_key: "identity",
  current_phase: "done", started_at: 100, completed_at: 102, credentials_json: null,
  origin_session_key: "session", origin_session_id: "session-id", origin_bot_id: "bot-A:owner-A",
  user_id: "initiator", plugin_version: "fixture-version", gmt_create: 100, gmt_modified: 102,
};
const node = {
  id: 11, flow_id: "fixture-flow", workflow_id: "fixture-workflow", node_id: "node-A",
  executor_type: "agent", status: "failed", attempt: 1, input_json: ' {"node": "input"} ',
  error_text: "first attempt failed", duration_ms: 10, token_usage_json: '{ "total" : 3 }',
  node_title: "Node A", triggered_by: "manual", branch_id: "branch-A", progress_message: "progress",
  session_key: "node-session", session_id: "node-session-id", system_context_json: "invalid JSON [",
  embedded_session_key: "embedded-session", started_at: 300, completed_at: 301,
  gmt_create: 300, gmt_modified: 301,
};
const nullNode = {
  ...node, id: 13, node_id: "node-B", executor_type: null, status: "pending", attempt: 1,
  input_json: null, error_text: null, duration_ms: null, token_usage_json: null, node_title: null,
  triggered_by: null, branch_id: null, progress_message: null, session_key: null, session_id: null,
  system_context_json: null, embedded_session_key: null, started_at: 100, completed_at: null,
  gmt_create: 100, gmt_modified: null,
};
const safeFailure = { error: "Internal Server Error", message: "Failed to load execution record" };

describe("GET /api/runs/:flowId/execution-record", () => {
  const raw = new Database(":memory:");
  const db = new SqliteDatabase(raw);
  const flowRepo = new FlowRunRepository(db);
  const nodeRepo = new NodeExecutionRepository(db);
  const directory = { listBots: vi.fn(async (_actor: string, _status: "active" | "all"): Promise<DirectoryBot[]> => []) };
  const permissionRepo = new BotWorkflowPermissionRepository(db, directory);
  let server: Server;
  let baseUrl: string;

  function insert(table: string, values: Record<string, unknown>) {
    const columns = Object.keys(values);
    raw.prepare(`INSERT INTO ${table} (${columns.join(", ")}) VALUES (${columns.map(() => "?").join(", ")})`)
      .run(...Object.values(values));
  }

  function grant(ownerId: string, botId: string | null = null) {
    insert("bot_workflow_permissions", {
      bot_id: botId, bot_owner_id: ownerId, workflow_id: "fixture-workflow", env: "fixture",
      can_view: 1, can_execute: 0, can_edit: 0, gmt_create: 1, gmt_modified: 1,
    });
  }

  function read(suffix = "", mount = "/api/runs", headers: Record<string, string> = {}, flowId = "fixture-flow") {
    return fetch(`${baseUrl}${mount}/${flowId}/execution-record${suffix}`, { headers });
  }

  beforeAll(async () => {
    raw.exec(`
      CREATE TABLE flow_runs (
        id INTEGER PRIMARY KEY, flow_id TEXT UNIQUE, workflow_id TEXT, workflow_title TEXT,
        status TEXT, params_json TEXT, input_json TEXT, result_json TEXT, node_count INTEGER,
        succeeded_count INTEGER, failed_count INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
        triggered_by TEXT, identity_key TEXT, current_phase TEXT, started_at INTEGER, completed_at INTEGER,
        credentials_json TEXT, origin_session_key TEXT, origin_session_id TEXT, origin_bot_id TEXT,
        user_id TEXT, plugin_version TEXT, gmt_create INTEGER, gmt_modified INTEGER,
        state_json TEXT, engine TEXT, workflow_version INTEGER, workflow_deploy_number INTEGER
      );
      CREATE TABLE node_executions (
        id INTEGER PRIMARY KEY, flow_id TEXT, workflow_id TEXT, node_id TEXT, executor_type TEXT,
        status TEXT, attempt INTEGER, input_json TEXT, output_json TEXT, error_text TEXT,
        duration_ms INTEGER, token_usage_json TEXT, node_title TEXT, triggered_by TEXT, branch_id TEXT,
        progress_message TEXT, session_key TEXT, session_id TEXT, system_context_json TEXT,
        embedded_session_key TEXT, started_at INTEGER, completed_at INTEGER, gmt_create INTEGER,
        gmt_modified INTEGER, resolved_prompt TEXT, version INTEGER
      );
      CREATE TABLE bot_workflow_permissions (
        id INTEGER PRIMARY KEY, bot_id TEXT, bot_owner_id TEXT, workflow_id TEXT, env TEXT,
        can_view INTEGER, can_execute INTEGER, can_edit INTEGER, gmt_create INTEGER, gmt_modified INTEGER
      );
    `);
    const app = express();
    app.set("query parser", "extended");
    app.use((req, _res, next) => { req.isAdmin = req.header("x-test-admin") === "true"; next(); });
    app.use("/api/runs", createRunsRouter(flowRepo, nodeRepo, null, null, null));
    app.use("/secure/runs", createRunsRouter(flowRepo, nodeRepo, null, null, null, null, permissionRepo));
    app.use("/missing-flow/runs", createRunsRouter(null, nodeRepo, null, null, null));
    app.use("/missing-nodes/runs", createRunsRouter(flowRepo, null, null, null, null));
    const simpleApp = express();
    simpleApp.set("query parser", "simple");
    simpleApp.use("/runs", createRunsRouter(flowRepo, nodeRepo, null, null, null));
    app.use("/simple", simpleApp);
    server = app.listen(0, "127.0.0.1");
    await once(server, "listening");
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  beforeEach(() => {
    directory.listBots.mockResolvedValue([]);
    raw.exec("DELETE FROM flow_runs; DELETE FROM node_executions; DELETE FROM bot_workflow_permissions;");
    insert("flow_runs", {
      ...flow, credentials_json: JSON.stringify({ token: secret }), state_json: secret, engine: "private-engine",
      workflow_version: 7, workflow_deploy_number: 12,
    });
    insert("node_executions", { ...node, output_json: ' { "output" : true } ', resolved_prompt: secret, version: 7 });
    insert("node_executions", { ...node, id: 12, attempt: 2, started_at: 100, status: "succeeded", output_json: "second-output" });
    insert("node_executions", { ...nullNode, output_json: null });
    insert("flow_runs", { ...flow, id: 2, flow_id: "another-flow", workflow_id: "other-workflow" });
    insert("node_executions", { ...node, id: 14, flow_id: "another-flow" });
  });

  afterEach(() => { vi.restoreAllMocks(); });
  afterAll(async () => {
    if (server?.listening) {
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
    await db.close();
  });

  it("returns exactly the callback allowlist, raw JSON strings, all attempts and stable descending order", async () => {
    const response = await read();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const text = await response.text();
    expect(text).not.toContain(secret);
    expect(text).not.toContain("private-engine");
    expect(JSON.parse(text)).toEqual({
      workflow_id: "fixture-workflow", flow_id: "fixture-flow", status: "succeeded",
      ext_info: { flow_runs: flow, node_executions: [node, nullNode, { ...node, id: 12, attempt: 2, started_at: 100, status: "succeeded" }] },
    });
  });

  it.each(["", "?includeNodeOutput=false"])("does not query credentials or node output with %s", async suffix => {
    const queries = vi.spyOn(db, "query");
    const response = await read(suffix);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.ext_info.flow_runs.credentials_json).toBeNull();
    expect(body.ext_info.node_executions.every((row: object) => !Object.hasOwn(row, "output_json"))).toBe(true);
    const flowSql = queries.mock.calls.find(([sql]) => /FROM flow_runs\b/.test(sql))?.[0];
    expect(flowSql).toMatch(/NULL\s+AS\s+credentials_json/i);
    expect(flowSql?.replace(/NULL\s+AS\s+credentials_json/i, "")).not.toMatch(/credentials_json|SELECT\s+\*|state_json|\bengine\b/i);
    const nodeSql = queries.mock.calls.find(([sql]) => /FROM node_executions\b/.test(sql))?.[0];
    expect(nodeSql).toBeDefined();
    expect(nodeSql).not.toMatch(/output_json|SELECT\s+\*|\bLIMIT\b/i);
  });

  it("includes output only for true, preserving large and null output without truncation", async () => {
    const large = ` {"large":"${"大-output-".repeat(150000)}","end":"unchanged"} `;
    raw.prepare("UPDATE node_executions SET output_json = ?, input_json = ? WHERE id = 11").run(large, large);
    const response = await read("?includeNodeOutput=true");
    expect(response.status).toBe(200);
    const rows = (await response.json()).ext_info.node_executions;
    expect(rows[0].output_json).toBe(large);
    expect(rows[0].input_json).toBe(large);
    expect(rows[1].output_json).toBeNull();
    expect(rows[2].output_json).toBe("second-output");
  });

  it("returns more than 500 attempts without a latest-only or pagination cutoff", async () => {
    raw.transaction(() => {
      for (let i = 0; i < 602; i++) {
        insert("node_executions", { ...node, id: 100 + i, attempt: i + 3 });
      }
    })();
    const response = await read();
    expect(response.status).toBe(200);
    const rows = (await response.json()).ext_info.node_executions;
    expect(rows).toHaveLength(605);
    expect(rows.map((row: { id: number }) => row.id)).toEqual([
      ...Array.from({ length: 602 }, (_, index) => 701 - index), 11, 13, 12,
    ]);
    expect(new Set(rows.filter((row: { node_id: string }) => row.node_id === "node-A").map((row: { attempt: number }) => row.attempt)).size).toBe(604);
  });

  it("returns an empty node list only when the persisted run has no node rows", async () => {
    raw.prepare("DELETE FROM node_executions WHERE flow_id = ?").run("fixture-flow");
    const response = await read();
    expect(response.status).toBe(200);
    expect((await response.json()).ext_info.node_executions).toEqual([]);
  });

  it.each(["succeeded", "failed", "cancelled", "running", "waiting", "blocked", "pending", "custom-state"])(
    "keeps persisted %s status unchanged at both levels", async status => {
      raw.prepare("UPDATE flow_runs SET status = ? WHERE flow_id = ?").run(status, "fixture-flow");
      const response = await read();
      expect(response.status).toBe(200);
      const body = await response.json();
      expect(body.status).toBe(status);
      expect(body.ext_info.flow_runs.status).toBe(status);
    },
  );

  it("preserves nullable flow and node fields as explicit nulls", async () => {
    const nullableFields = Object.keys(flow).filter(key => flow[key as keyof typeof flow] === null || [
      "workflow_title", "params_json", "input_json", "result_json", "total_duration_ms", "total_token_usage",
      "triggered_by", "identity_key", "current_phase", "origin_session_key", "origin_session_id", "origin_bot_id",
      "user_id", "plugin_version", "gmt_modified",
    ].includes(key));
    raw.prepare(`UPDATE flow_runs SET ${nullableFields.map(key => `${key} = NULL`).join(", ")} WHERE flow_id = ?`).run("fixture-flow");
    const response = await read("?includeNodeOutput=true");
    expect(response.status).toBe(200);
    const body = await response.json();
    for (const key of nullableFields) expect(body.ext_info.flow_runs, key).toHaveProperty(key, null);
    expect(body.ext_info.node_executions[1]).toEqual({ ...nullNode, output_json: null });
  });

  it("returns 404 for an unknown flow with no cached response", async () => {
    const response = await read("", "/api/runs", {}, "unknown-flow");
    expect(response.status).toBe(404);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ error: "Not Found", message: "Flow unknown-flow not found" });
  });

  it("trims a nonblank flowId and rejects a whitespace-only ID", async () => {
    expect((await read("", "/api/runs", {}, "%20fixture-flow%20")).status).toBe(200);
    const response = await read("", "/api/runs", {}, "%20%09%20");
    expect(response.status).toBe(400);
    expect(response.headers.get("cache-control")).toBe("no-store");
  });

  const invalidQueries = [
    "includeNodeOutput=", "includeNodeOutput=1", "includeNodeOutput=True", "includeNodeOutput=null",
    "includeNodeOutput=false&includeNodeOutput=true", "includeNodeOutput=true&includeNodeOutput=true",
    "includeNodeOutput[]=true", "includeNodeOutput[value]=true", "includeNodeOutput[__proto__]=true",
  ];
  it.each(invalidQueries)("rejects malformed query %s", async query => {
    for (const mount of ["/api/runs", "/simple/runs"]) {
      const response = await read(`?${query}`, mount);
      expect(response.status, mount).toBe(400);
      expect(response.headers.get("cache-control")).toBe("no-store");
    }
  });

  it.each(["/missing-flow/runs", "/missing-nodes/runs"])("returns 503 when a required repository is absent on %s", async mount => {
    const response = await read("", mount);
    expect(response.status).toBe(503);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ error: "Service Unavailable", message: "Database not configured" });
  });

  it("keeps isolated deployments readable without an actor", async () => {
    expect((await read("?botOwnerId=someone-else")).status).toBe(200);
  });

  it("requires an actor when permissions are configured, ignoring query owner identity", async () => {
    grant("owner-A");
    const lookup = vi.spyOn(db, "query");
    const response = await read("?botOwnerId=owner-A", "/secure/runs");
    expect(response.status).toBe(401);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual({ error: "Unauthorized", message: "User identity required" });
    expect(lookup).not.toHaveBeenCalled();
  });

  it("denies a workflow without grants and does not trust query botOwnerId", async () => {
    grant("owner-A");
    const scope = vi.spyOn(permissionRepo, "resolveRunViewScope");
    const response = await read("?botOwnerId=owner-A&botId=bot-A", "/secure/runs", { "x-user-id": "denied-user" });
    expect(response.status).toBe(403);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(scope).toHaveBeenCalledWith("fixture-workflow", "denied-user");
    expect(await response.json()).toEqual({ error: "Forbidden", message: "No view permission for this run" });
  });

  it("accepts all scope through the shared actor resolver including raw cookies", async () => {
    grant("reader");
    expect((await read("", "/secure/runs", { "x-staff-id": " reader " })).status).toBe(200);
    expect((await read("", "/secure/runs", { cookie: "staff_id=reader" })).status).toBe(200);
  });

  it("preserves the inherited Bot owner rather than substituting the actor or query owner", async () => {
    grant("owner-A", "bot-A");
    directory.listBots.mockResolvedValue([{
      botId: "bot-A", displayBotId: "bot-A", ownerId: "owner-A", status: "active", source: "fixture",
    }]);
    const headers = { "x-user-id": "collaborator" };
    expect((await read("?botOwnerId=owner-B", "/secure/runs", headers)).status).toBe(200);
    expect(directory.listBots).toHaveBeenCalledWith("collaborator", "all");
    raw.prepare("UPDATE flow_runs SET origin_bot_id = 'bot-A:owner-B' WHERE flow_id = ?").run("fixture-flow");
    expect((await read("?botOwnerId=owner-A", "/secure/runs", headers)).status).toBe(403);
    directory.listBots.mockResolvedValue([]);
    raw.prepare("UPDATE flow_runs SET origin_bot_id = 'bot-A:owner-A' WHERE flow_id = ?").run("fixture-flow");
    expect((await read("", "/secure/runs", headers)).status).toBe(403);
  });

  it("does not read node payloads for a denied run", async () => {
    const queries = vi.spyOn(db, "query");
    expect((await read("?includeNodeOutput=true", "/secure/runs", { "x-user-id": "reader" })).status).toBe(403);
    expect(queries.mock.calls.some(([sql]) => /FROM node_executions\b/.test(sql))).toBe(false);
  });

  it("lets server-authorized administrators bypass actor and permission lookup", async () => {
    const scope = vi.spyOn(permissionRepo, "resolveRunViewScope");
    const response = await read("", "/secure/runs", { "x-test-admin": "true" });
    expect(response.status).toBe(200);
    expect(scope).not.toHaveBeenCalled();
  });

  it.each([
    ["bot-A:owner-A", 200], ["bot-A:owner-B", 403], ["bot-A:owner-A:extra", 403],
    ["bot-AA:owner-A", 403], ["bot-A", 403], [null, 403], ["", 403],
  ])("enforces exact Bot+owner isolation for origin %s", async (origin, expectedStatus) => {
    grant("owner-A", "bot-A");
    raw.prepare("UPDATE flow_runs SET origin_bot_id = ? WHERE flow_id = ?").run(origin, "fixture-flow");
    const response = await read("?botOwnerId=owner-B", "/secure/runs", { "x-user-id": "owner-A" });
    expect(response.status).toBe(expectedStatus);
  });

  it.each([
    ["bot-A:owner-A", 200], ["bot-A:owner-B", 200], ["bot-AA:owner-A", 403],
    ["bot-A:", 403], ["bot-A:   ", 403], ["bot-A", 403], [null, 403], ["", 403],
  ])("limits wildcard owner grants to the Bot ID colon prefix for %s", async (origin, expectedStatus) => {
    grant("*", "bot-A");
    raw.prepare("UPDATE flow_runs SET origin_bot_id = ? WHERE flow_id = ?").run(origin, "fixture-flow");
    expect((await read("", "/secure/runs", { "x-user-id": "reader" })).status).toBe(expectedStatus);
  });

  it.each(["flow_runs", "node_executions", "bot_workflow_permissions"])("returns a safe 500, not 404 or empty success, on %s read failure", async table => {
    grant("reader");
    raw.exec(`ALTER TABLE ${table} RENAME TO unavailable_table`);
    try {
      const response = await read("", "/secure/runs", { "x-user-id": "reader" });
      expect(response.status).toBe(500);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(await response.json()).toEqual(safeFailure);
    } finally {
      raw.exec(`ALTER TABLE unavailable_table RENAME TO ${table}`);
    }
  });

  it("does not leak SQL or credentials even if the database exception contains them", async () => {
    vi.spyOn(db, "query").mockRejectedValue(new Error(`SELECT credentials_json FROM flow_runs; ${secret}`));
    const response = await read();
    expect(response.status).toBe(500);
    expect(await response.json()).toEqual(safeFailure);
  });

  it("leaves the existing GET /:flowId envelope and latest-node behavior unchanged", async () => {
    const response = await fetch(`${baseUrl}/api/runs/fixture-flow`);
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(Object.keys(body).sort()).toEqual(["availableInterventions", "nodes", "run"]);
    expect(body.run).toMatchObject({ flow_id: "fixture-flow", status: "succeeded", hasCredentials: true, total_duration_ms: 2000 });
    expect(body.run).not.toHaveProperty("credentials_json");
    expect(body.nodes.map((row: { id: number }) => row.id)).toEqual([12, 13]);
  });
});
