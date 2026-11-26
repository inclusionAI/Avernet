// @vitest-environment node
import { once } from "node:events";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import Database from "better-sqlite3";
import express from "express";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import type { RunViewScope } from "@avernet/clawweb-shared/server/services/run-view-permissions";
import { FanoutReadError } from "../../contracts/run-subworkflows.js";
import { RunSubworkflowsRepository } from "../../repositories/run-subworkflows-repository.js";
import { RunSubworkflowsService } from "../../services/run-subworkflows-service.js";
import type { RunLaunchMetadata } from "../../../shared/run-launch-metadata.js";
import { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import { createRunsRouter } from "../runs.js";

const launchParams = String.raw`{ "mode": " original ", "count": 2, "enabled": false, "nested": {"items": [null, "启动\n输入"]} }`;
const launchInput = String.raw`{ "message": "  launch text\n第二行  ", "command": "/fixture original", "params": {"mode": "later-value"}, "parent_flow_id": "not-db-parent", "root_flow_id": "not-db-root" }`;
const credentialValue = "fixture-only-must-not-be-returned";
const credentialsJson = JSON.stringify({ PRIVATE_FIELD: credentialValue, CLIENT_ID: "fixture-device" });

const schemas = [
  { schema: "lineage columns", hasLineage: true },
  { schema: "legacy table without lineage columns", hasLineage: false },
];

describe.each(schemas)("GET /api/runs/:flowId launch input ($schema)", ({ hasLineage }) => {
  const raw = new Database(":memory:");
  const db = new SqliteDatabase(raw);
  let server: Server;
  let runsUrl: string;

  beforeAll(async () => {
    raw.exec(`
      CREATE TABLE flow_runs (
        flow_id TEXT PRIMARY KEY, workflow_id TEXT DEFAULT 'fixture-workflow', workflow_title TEXT,
        status TEXT, started_at INTEGER, completed_at INTEGER,
        total_duration_ms INTEGER, origin_bot_id TEXT, origin_session_key TEXT,
        credentials_json TEXT, params_json TEXT, input_json TEXT
        ${hasLineage ? ", parent_flow_id TEXT, root_flow_id TEXT" : ""}
      );
      CREATE TABLE node_executions (
        id INTEGER PRIMARY KEY, flow_id TEXT, node_id TEXT, attempt INTEGER, started_at INTEGER
      );
      INSERT INTO node_executions VALUES (1, 'fixture-flow', 'fixture-node', 1, 100);
      INSERT INTO flow_runs (flow_id, params_json, input_json)
      VALUES ('another-flow', '{"mode":"another-run"}', '{"message":"another-run"}');
    `);
    const app = express();
    app.use("/api/runs", createRunsRouter(
      new FlowRunRepository(db), new NodeExecutionRepository(db), null, null, null,
    ));
    server = app.listen(0, "127.0.0.1");
    await once(server, "listening");
    runsUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/runs`;
  });

  beforeEach(() => {
    raw.prepare(`
      INSERT OR REPLACE INTO flow_runs
        (flow_id, status, started_at, completed_at, total_duration_ms,
         origin_bot_id, origin_session_key, credentials_json, params_json, input_json)
      VALUES ('fixture-flow', 'running', 100, NULL, NULL, 'fixture-bot', 'fixture-session', ?, ?, ?)
    `).run(credentialsJson, launchParams, launchInput);
    if (hasLineage) {
      raw.prepare(`
        UPDATE flow_runs SET parent_flow_id = ?, root_flow_id = ? WHERE flow_id = 'fixture-flow'
      `).run("db-parent-flow", "db-root-flow");
    }
  });

  afterAll(async () => {
    if (server?.listening) {
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    }
    await db.close();
  });

  async function readDetail() {
    const response = await fetch(`${runsUrl}/fixture-flow`);
    expect(response.status).toBe(200);
    const body = await response.text();
    expect(body).not.toContain("credentials_json");
    expect(body).not.toContain("PRIVATE_FIELD");
    expect(body).not.toContain(credentialValue);
    expect(body).not.toContain("fixture-device");
    const detail = JSON.parse(body);
    expect(detail.run).not.toHaveProperty("parent_flow_id");
    expect(detail.run).not.toHaveProperty("root_flow_id");
    expect(body).not.toContain("db-parent-flow");
    expect(body).not.toContain("db-root-flow");
    expect(detail.run.hasCredentials).toBe(true);
    expect(detail.run).not.toHaveProperty("device_id");
    expect(detail.run).not.toHaveProperty("bot_type");
    expect(detail.run).not.toHaveProperty("stage");
    return detail;
  }

  it("passes stored launch JSON strings through unchanged while redacting database lineage", async () => {
    if (hasLineage) {
      expect(raw.prepare("SELECT parent_flow_id, root_flow_id FROM flow_runs WHERE flow_id = 'fixture-flow'").get())
        .toEqual({ parent_flow_id: "db-parent-flow", root_flow_id: "db-root-flow" });
    }
    const detail = await readDetail();
    const metadata: Pick<RunLaunchMetadata, "params_json" | "input_json"> = {
      params_json: launchParams, input_json: launchInput,
    };
    expect(detail).toEqual({
      run: {
        flow_id: "fixture-flow", workflow_id: "fixture-workflow", workflow_title: null,
        status: "running", started_at: 100, completed_at: null, total_duration_ms: null,
        origin_bot_id: "fixture-bot", origin_session_key: "fixture-session",
        ...metadata, hasCredentials: true,
      },
      nodes: [{ id: 1, flow_id: "fixture-flow", node_id: "fixture-node", attempt: 1, started_at: 100 }],
      availableInterventions: [],
    });
  });

  it("keeps launch params independent from an overwritten execution input summary", async () => {
    const completedInput = String.raw`{ "message": "execution summary", "params": {"mode": "completed-value"}, "fileCount": 0 }`;
    raw.prepare(`
      UPDATE flow_runs SET status = 'succeeded', completed_at = 102, input_json = ?
      WHERE flow_id = 'fixture-flow'
    `).run(completedInput);
    const { run } = await readDetail();
    expect(run.status).toBe("succeeded");
    expect(run.params_json).toBe(launchParams);
    expect(run.input_json).toBe(completedInput);
  });

  it.each<[string, string | null, string | null]>([
    ["SQL nulls", null, null],
    ["missing launch params with later execution params", null, launchInput],
    ["missing input with launch params", launchParams, null],
    ["empty strings", "", ""],
    ["unparsed JSON", '{"unfinished":', '"execution summary"'],
  ])("preserves %s without inventing a launch fallback", async (_case, paramsJson, inputJson) => {
    raw.prepare("UPDATE flow_runs SET params_json = ?, input_json = ? WHERE flow_id = 'fixture-flow'")
      .run(paramsJson, inputJson);
    const { run } = await readDetail();
    expect(run.params_json).toBe(paramsJson);
    expect(run.input_json).toBe(inputJson);
  });

  it("does not infer null or absent lineage from the current flow or input JSON", async () => {
    if (hasLineage) {
      raw.exec("UPDATE flow_runs SET parent_flow_id = NULL, root_flow_id = NULL WHERE flow_id = 'fixture-flow'");
    }
    const { run } = await readDetail();
    expect(run.input_json).toBe(launchInput);
    expect(run).not.toHaveProperty("parent_flow_id");
    expect(run).not.toHaveProperty("root_flow_id");
  });
});

// Unlike the readerless fixtures above, these exercise the real authorized
// inspection, persisted-column SELECT and HTTP projection together.
describe("reader-backed ordinary detail lineage through SQLite", () => {
  const raw = new Database(":memory:");
  const db = new SqliteDatabase(raw);
  const flowRunRepo = new FlowRunRepository(db);
  const reader = new RunSubworkflowsService(new RunSubworkflowsRepository(db), {
    resolveRunViewScope: async (workflowId, actorId) => actorId === "viewer" ? scopes.get(workflowId) ?? "deny" : "deny",
    listRunViewScopes: async actorId => actorId === "viewer" ? scopes : new Map(),
  });
  let scopes: Map<string, RunViewScope>;
  let server: Server;
  let runsUrl: string;

  beforeAll(async () => {
    await runMigrations(db, "sqlite");
    const app = express();
    app.use("/api/runs", createRunsRouter(flowRunRepo, new NodeExecutionRepository(db), null, null, null,
      null, null, null, null, null, reader));
    server = app.listen(0, "127.0.0.1");
    await once(server, "listening");
    runsUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}/api/runs`;
  });

  beforeEach(() => {
    raw.exec("BEGIN");
    scopes = new Map([
      ["fixture-workflow", { bots: [{ botId: "fixture-bot", ownerId: "owner" }] }],
      ["parent-workflow", "all"], ["root-workflow", "all"],
    ]);
    raw.exec(`
      INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, origin_bot_id, state_json)
      VALUES ('fixture-flow', 'fixture-workflow', 'running', 100, 'fixture-bot:owner', '{}'),
        ('db-parent-flow', 'parent-workflow', 'succeeded', 98, 'parent-bot:owner', '{}'),
        ('db-root-flow', 'root-workflow', 'succeeded', 97, 'root-bot:owner', '{}');
      INSERT INTO node_executions (flow_id, workflow_id, node_id, status, attempt, started_at)
      VALUES ('fixture-flow', 'fixture-workflow', 'fixture-node', 'running', 1, 100);
    `);
    raw.prepare(`UPDATE flow_runs SET credentials_json = ?, params_json = ?, input_json = ?,
      parent_flow_id = 'db-parent-flow', root_flow_id = 'db-root-flow' WHERE flow_id = 'fixture-flow'`)
      .run(credentialsJson, launchParams, launchInput);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });

  afterEach(() => { raw.exec("ROLLBACK"); vi.restoreAllMocks(); });
  afterAll(async () => {
    if (server?.listening) {
      server.closeAllConnections();
      await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    }
    await db.close();
  });

  const read = (suffix = "/fixture-flow", actorId = "viewer", headers: Record<string, string> = {}) =>
    fetch(runsUrl + suffix, { headers: { ...(actorId ? { "x-user-id": actorId } : {}), ...headers } });
  async function detail(suffix = "/fixture-flow") {
    const response = await read(suffix);
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("private, no-store");
    const text = await response.text();
    for (const forbidden of ["credentials_json", "PRIVATE_FIELD", credentialValue, "fixture-device"]) {
      expect(text).not.toContain(forbidden);
    }
    return JSON.parse(text);
  }

  it("returns real independently authorized parent/root IDs, exact launch JSON and no credentials or fallback read", async () => {
    const inspect = vi.spyOn(reader, "inspectRun");
    const fullRead = vi.spyOn(flowRunRepo, "findFullByFlowId");
    const queries = vi.spyOn(db, "query");
    const body = await detail();
    expect(body.run).toMatchObject({ params_json: launchParams, input_json: launchInput,
      parent_flow_id: "db-parent-flow", root_flow_id: "db-root-flow" });
    expect(body.nodes).toMatchObject([{ node_id: "fixture-node" }]);
    expect(body.availableInterventions).toEqual([]);
    expect(body.run).not.toHaveProperty("hasCredentials");
    expect(body.run).not.toHaveProperty("device_id");
    expect(inspect.mock.calls).toEqual(["fixture-flow", "db-parent-flow", "db-root-flow"]
      .map(id => [{ actorId: "viewer", isAdmin: false }, id]));
    expect(fullRead).not.toHaveBeenCalled();
    expect(queries.mock.calls.every(([sql]) => /^SELECT\b/i.test(sql) && !sql.includes("credentials_json"))).toBe(true);
  });

  it("preserves independently authorized stored strings without trimming, encoding or normalization", async () => {
    const parent = " parent /?根 "; const root = "ROOT#%e\u0301";
    raw.prepare("UPDATE flow_runs SET flow_id = ? WHERE flow_id = 'db-parent-flow'").run(parent);
    raw.prepare("UPDATE flow_runs SET flow_id = ? WHERE flow_id = 'db-root-flow'").run(root);
    raw.prepare("UPDATE flow_runs SET parent_flow_id = ?, root_flow_id = ? WHERE flow_id = 'fixture-flow'").run(parent, root);
    expect((await detail()).run).toMatchObject({ parent_flow_id: parent, root_flow_id: root });
  });

  it("deduplicates identical parent/root without recursively traversing ancestor metadata", async () => {
    raw.exec(`UPDATE flow_runs SET root_flow_id = parent_flow_id WHERE flow_id = 'fixture-flow';
      UPDATE flow_runs SET parent_flow_id = 'never-traverse', root_flow_id = 'never-traverse' WHERE flow_id = 'db-parent-flow'`);
    const inspect = vi.spyOn(reader, "inspectRun");
    expect((await detail()).run).toMatchObject({ parent_flow_id: "db-parent-flow", root_flow_id: "db-parent-flow" });
    expect(inspect.mock.calls.map(([, id]) => id)).toEqual(["fixture-flow", "db-parent-flow"]);
  });

  it.each(["parent", "root"] as const)("omits the denied %s while retaining the other authorized ancestor", async field => {
    scopes.set(`${field}-workflow`, "deny");
    const { run } = await detail();
    expect(run).not.toHaveProperty(`${field}_flow_id`);
    const other = field === "parent" ? "root" : "parent";
    expect(run[`${other}_flow_id`]).toBe(`db-${other}-flow`);
  });

  it.each(["other-bot:owner", "fixture-bot:other-owner", null])("denies same-workflow ancestor with origin %s", async origin => {
    raw.prepare("UPDATE flow_runs SET workflow_id = 'fixture-workflow', origin_bot_id = ? WHERE flow_id = 'db-parent-flow'").run(origin);
    const { run } = await detail();
    expect(run).not.toHaveProperty("parent_flow_id");
    expect(run.root_flow_id).toBe("db-root-flow");
  });

  it("withholds both ancestors when only the child is visible, then respects a revoked grant on the next request", async () => {
    expect((await detail()).run).toHaveProperty("parent_flow_id", "db-parent-flow");
    scopes.set("parent-workflow", "deny"); scopes.set("root-workflow", "deny");
    const { run } = await detail();
    expect(run).not.toHaveProperty("parent_flow_id"); expect(run).not.toHaveProperty("root_flow_id");
  });

  it.each(["parent", "root"] as const)("omits a missing %s row independently", async field => {
    raw.prepare("DELETE FROM flow_runs WHERE flow_id = ?").run(`db-${field}-flow`);
    const { run } = await detail();
    expect(run).not.toHaveProperty(`${field}_flow_id`);
    expect(run[field === "parent" ? "root_flow_id" : "parent_flow_id"]).toBe(field === "parent" ? "db-root-flow" : "db-parent-flow");
  });

  it("preserves null lineage and does not infer from launch params, input or state", async () => {
    raw.prepare("UPDATE flow_runs SET parent_flow_id = NULL, root_flow_id = NULL, state_json = ? WHERE flow_id = 'fixture-flow'")
      .run('{"subworkflowMeta":{"parentFlowId":"not-db-parent"},"root_flow_id":"not-db-root"}');
    const inspect = vi.spyOn(reader, "inspectRun");
    expect((await detail()).run).toMatchObject({ parent_flow_id: null, root_flow_id: null, input_json: launchInput });
    expect(inspect).toHaveBeenCalledTimes(1);
  });

  it.each([
    { columns: ["parent_flow_id"] }, { columns: ["root_flow_id"] },
    { columns: ["parent_flow_id", "root_flow_id"] },
  ])("supports legacy schemas missing $columns without fabricating those fields", async ({ columns }) => {
    for (const column of columns) raw.exec(`ALTER TABLE flow_runs DROP COLUMN ${column}`);
    // Exercise compatibility with the older optional metadata projections too.
    raw.exec("DROP INDEX idx_flow_runs_evolution_status; ALTER TABLE flow_runs DROP COLUMN ordinary_run_marker; ALTER TABLE flow_runs DROP COLUMN evolution_analysis_status");
    const { run } = await detail();
    expect(run.params_json).toBe(launchParams); expect(run.input_json).toBe(launchInput);
    for (const field of ["parent_flow_id", "root_flow_id"]) {
      if (columns.includes(field)) expect(run).not.toHaveProperty(field);
      else expect(run[field]).toBe(field === "parent_flow_id" ? "db-parent-flow" : "db-root-flow");
    }
  });

  it("does not reveal a collation-substituted ancestor identity", async () => {
    raw.exec("UPDATE flow_runs SET flow_id = 'DB-PARENT-FLOW' WHERE flow_id = 'db-parent-flow'");
    const query = db.query.bind(db);
    // Model a case-insensitive DB collation while keeping real SQL and reader authorization.
    vi.spyOn(db, "query").mockImplementation((sql, params) => query(
      sql.includes("WHERE flow_id = ?") ? sql.replace("WHERE flow_id = ?", "WHERE flow_id = ? COLLATE NOCASE") : sql, params));
    const { run } = await detail();
    expect(run).not.toHaveProperty("parent_flow_id"); expect(run.root_flow_id).toBe("db-root-flow");
  });

  it("ignores client-supplied lineage and flags rather than using them as lookup IDs", async () => {
    raw.exec("UPDATE flow_runs SET parent_flow_id = NULL, root_flow_id = NULL WHERE flow_id = 'fixture-flow'");
    const inspect = vi.spyOn(reader, "inspectRun");
    const response = await read("/fixture-flow?parent_flow_id=db-parent-flow&root_flow_id=db-root-flow&includeLineage=true&botOwnerId=owner", "viewer", {
      "x-parent-flow-id": "db-parent-flow", "x-root-flow-id": "db-root-flow", "x-include-lineage": "true",
    });
    expect(response.status).toBe(200);
    expect((await response.json()).run).toMatchObject({ parent_flow_id: null, root_flow_id: null });
    expect(inspect).toHaveBeenCalledTimes(1);
  });

  it("keeps lineage out of reader-backed lists even when detail is authorized", async () => {
    const response = await read(); expect(response.status).toBe(200);
    const listResponse = await read("?workflowId=fixture-workflow"); expect(listResponse.status).toBe(200);
    const list = await listResponse.json(); expect(list.runs).toHaveLength(1);
    expect(list.runs[0]).not.toHaveProperty("parent_flow_id"); expect(list.runs[0]).not.toHaveProperty("root_flow_id");
  });

  it("keeps managed/restricted fanout parents and sensitive children redacted without ancestor lookups", async () => {
    scopes.set("fixture-workflow", "all");
    const group = { schemaVersion: "fanout.v1", kind: "subworkflowFanoutState", parentFlowId: "fixture-flow",
      groupNodeId: "fanout", groupExecutionId: "generation", revision: 1, status: "succeeded", concurrency: 1,
      successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all",
      dependencySnapshot: { workflowId: "parent-workflow", publishedVersion: 1 }, children: [], error: null };
    for (const state of [{ subworkflowFanouts: { fanout: group } }, { fanoutOrigin: { parentFlowId: "hidden" } }]) {
      raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id = 'fixture-flow'").run(JSON.stringify(state));
      const inspect = vi.spyOn(reader, "inspectRun"); inspect.mockClear();
      const { run } = await detail();
      expect(run).not.toHaveProperty("parent_flow_id"); expect(run).not.toHaveProperty("root_flow_id");
      expect(run.params_json).toBeNull(); expect(run.input_json).toBeNull();
      expect(inspect.mock.calls.map(([, id]) => id)).toEqual(["fixture-flow"]);
    }
    scopes.set("parent-workflow", "deny");
    raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id = 'fixture-flow'").run(JSON.stringify({ subworkflowFanouts: { fanout: group } }));
    expect((await detail()).run).not.toHaveProperty("parent_flow_id");
  });

  it.each([["", 401], ["no-access", 403]] as const)("preserves child authorization failure for actor %s", async (actorId, status) => {
    const inspect = vi.spyOn(reader, "inspectRun");
    const response = await read("/fixture-flow", actorId); expect(response.status).toBe(status);
    expect(inspect.mock.calls.map(([, id]) => id)).toEqual(["fixture-flow"]);
    expect(await response.text()).not.toMatch(/db-parent-flow|db-root-flow|credentials_json/);
  });

  it.each([
    [new Error("storage failure"), 500, "read_failed"],
    [new FanoutReadError("unavailable", "Storage unavailable"), 503, "unavailable"],
    [new FanoutReadError("unauthenticated", "Identity expired"), 401, "unauthenticated"],
    [new FanoutReadError("invalid_state", "Stored state invalid"), 500, "invalid_state"],
  ] as const)("propagates ancestor query failure %s to the existing handler", async (error, status, code) => {
    const query = db.query.bind(db);
    vi.spyOn(db, "query").mockImplementation((sql, params) => {
      if (sql.includes("FROM flow_runs WHERE flow_id = ?") && params?.[0] === "db-parent-flow") return Promise.reject(error);
      return query(sql, params);
    });
    const response = await read(); expect(response.status).toBe(status);
    expect(await response.json()).toMatchObject({ error: code });
  });

  it("does not turn corrupt persisted ancestor state into an absent lineage field", async () => {
    raw.exec("UPDATE flow_runs SET state_json = '{bad' WHERE flow_id = 'db-parent-flow'");
    const response = await read(); expect(response.status).toBe(500);
    expect(await response.json()).toMatchObject({ error: "invalid_state" });
  });
});
