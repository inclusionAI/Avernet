// @vitest-environment node
import { once } from "node:events";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import Database from "better-sqlite3";
import express from "express";
import { afterAll, beforeAll, describe, expect, it } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import type { RunRuntimeMetadata } from "../../../shared/run-runtime-metadata.js";
import { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import { RunSubworkflowsRepository } from "../../repositories/run-subworkflows-repository.js";
import { RunSubworkflowsService } from "../../services/run-subworkflows-service.js";
import { createRunsRouter } from "../runs.js";

const missingMetadata: RunRuntimeMetadata = { device_id: null, bot_type: null, stage: null };
const unrelatedValue = "should-not-appear-in-run-detail";
const populatedCredentials = JSON.stringify({
  CLIENT_ID: "device-fixture",
  BOT_TYPE: " bot-fixture ",
  STAGE: "stage-fixture",
  UNRELATED_FIELD: unrelatedValue,
});

describe("GET /api/runs/:flowId runtime metadata", () => {
  const raw = new Database(":memory:");
  const db = new SqliteDatabase(raw);
  let server: Server;
  let runsUrl: string;
  let defaultRunsUrl: string;
  let inspectedRunsUrl: string;

  beforeAll(async () => {
    await runMigrations(db, "sqlite");
    raw.exec(`
      INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, credentials_json)
      VALUES ('another-flow', 'fixture-workflow', 'running', 99,
        '{"CLIENT_ID":"another-device","BOT_TYPE":"another-bot","STAGE":"another-stage"}');
      INSERT INTO flow_runs (flow_id, workflow_id, env, tag, status, started_at, completed_at,
        total_duration_ms, origin_bot_id, origin_session_key)
      VALUES ('fixture-flow', 'fixture-workflow', 'fixture-env', 'fixture-tag', 'waiting', 100, 102,
        2000000, 'fixture-bot:fixture-owner', 'fixture-session');
      INSERT INTO node_executions (flow_id, workflow_id, node_id, status, attempt, started_at, output_json)
      VALUES ('fixture-flow', 'fixture-workflow', 'fixture-node', 'succeeded', 1, 100, '{"fixture":true}');
    `);
    const app = express();
    const flowRunRepo = new FlowRunRepository(db);
    const nodeExecRepo = new NodeExecutionRepository(db);
    app.use("/api/runs", createRunsRouter(flowRunRepo, nodeExecRepo, null, null, null));
    app.use("/opt-in/api/runs", createRunsRouter(
      flowRunRepo, nodeExecRepo, null, null, null, null, null, null, null, null, null,
      { exposeRuntimeMetadata: true },
    ));
    const reader = new RunSubworkflowsService(new RunSubworkflowsRepository(db), {
      resolveRunViewScope: async () => "all",
      listRunViewScopes: async () => new Map(),
    });
    // Stand in for a verified admin principal on this test host.
    app.use("/inspected/api/runs", (req, _res, next) => { req.isAdmin = true; next(); }, createRunsRouter(
      flowRunRepo, nodeExecRepo, null, null, null, null, null, null, null, null, reader,
      { exposeRuntimeMetadata: true },
    ));
    server = app.listen(0, "127.0.0.1");
    await once(server, "listening");
    const baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
    runsUrl = `${baseUrl}/opt-in/api/runs`;
    defaultRunsUrl = `${baseUrl}/api/runs`;
    inspectedRunsUrl = `${baseUrl}/inspected/api/runs`;
  });

  afterAll(async () => {
    if (server?.listening) {
      await new Promise<void>((resolve, reject) => server.close((error) => error ? reject(error) : resolve()));
    }
    await db.close();
  });

  async function readDetail(credentialsJson: string | null, url = runsUrl, stateJson: string | null = null) {
    raw.prepare("UPDATE flow_runs SET credentials_json = ?, state_json = ? WHERE flow_id = 'fixture-flow'")
      .run(credentialsJson, stateJson);
    const response = await fetch(`${url}/fixture-flow`);
    expect(response.status).toBe(200);
    const body = await response.text();
    expect(body).not.toContain("credentials_json");
    expect(body).not.toContain("UNRELATED_FIELD");
    expect(body).not.toContain(unrelatedValue);
    return JSON.parse(body);
  }

  it("omits all runtime metadata by default even with populated credentials", async () => {
    const { run } = await readDetail(populatedCredentials, defaultRunsUrl);
    expect(run).toMatchObject({ flow_id: "fixture-flow", hasCredentials: true });
    expect(run).not.toHaveProperty("device_id");
    expect(run).not.toHaveProperty("bot_type");
    expect(run).not.toHaveProperty("stage");
  });

  it("exposes only the three allowlisted values as flat run fields when opted in", async () => {
    const baseline = await readDetail(populatedCredentials, defaultRunsUrl);
    expect(baseline).toMatchObject({
      run: {
        flow_id: "fixture-flow", workflow_id: "fixture-workflow", env: "fixture-env", tag: "fixture-tag",
        status: "waiting", started_at: 100, completed_at: 102,
        total_duration_ms: 2000, origin_bot_id: "fixture-bot:fixture-owner", origin_session_key: "fixture-session",
        hasCredentials: true,
      },
      nodes: [{ flow_id: "fixture-flow", node_id: "fixture-node", attempt: 1, started_at: 100 }],
      availableInterventions: ["confirm", "skip"],
    });
    const detail = await readDetail(populatedCredentials);
    expect(detail).toEqual({
      ...baseline,
      run: { ...baseline.run, device_id: "device-fixture", bot_type: " bot-fixture ", stage: "stage-fixture" },
    });
  });

  it.each<[string, string | null, boolean]>([
    ["SQL null", null, false],
    ["an empty column", "", false],
    ["a whitespace-only column", " \t", true],
    ["missing entries", "{}", true],
    ["explicit null and blank values", '{"CLIENT_ID":null,"BOT_TYPE":"","STAGE":"  \\t"}', true],
    ["malformed JSON", `{"UNRELATED_FIELD":"${unrelatedValue}"`, true],
    ["JSON null", "null", true],
    ["a JSON string", '"device-fixture"', true],
    ["a JSON array", '[{"CLIENT_ID":"nested","BOT_TYPE":"nested","STAGE":"nested"}]', true],
    ["non-string values", '{"CLIENT_ID":123,"BOT_TYPE":false,"STAGE":["stage-fixture"]}', true],
    ["nested and differently cased keys", '{"nested":{"CLIENT_ID":"nested","BOT_TYPE":"nested","STAGE":"nested"},"client_id":"lowercase"}', true],
  ])("returns null fields for %s without changing hasCredentials", async (_case, credentialsJson, hasCredentials) => {
    const { run } = await readDetail(credentialsJson);
    expect(run).toMatchObject({ ...missingMetadata, hasCredentials });
  });

  it("reads each field independently", async () => {
    const { run } = await readDetail('{"BOT_TYPE":{"value":"nested"},"STAGE":"stage-fixture"}');
    expect(run).toMatchObject({ ...missingMetadata, stage: "stage-fixture", hasCredentials: true });
  });

  it("does not fetch credentials behind the credential-free subworkflow reader", async () => {
    const { run } = await readDetail(populatedCredentials, inspectedRunsUrl);
    expect(run).toMatchObject({ flow_id: "fixture-flow", ...missingMetadata });
    expect(run).not.toHaveProperty("hasCredentials");
  });

  it.each([
    ["managed parent", { subworkflowFanouts: { group: {
      schemaVersion: "fanout.v1", kind: "subworkflowFanoutState", parentFlowId: "fixture-flow",
      groupNodeId: "group", groupExecutionId: "generation", revision: 1, status: "running",
      concurrency: 1, successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all",
      dependencySnapshot: { workflowId: "fixture-child-workflow", publishedVersion: 1 }, children: [],
    } } }],
    ["sensitive child", { fanoutOrigin: {} }],
  ])("omits metadata for the %s with or without the reader", async (_kind, state) => {
    const stateJson = JSON.stringify(state);
    const baseline = await readDetail(populatedCredentials, defaultRunsUrl, stateJson);
    const withoutReader = await readDetail(populatedCredentials, runsUrl, stateJson);
    expect(withoutReader).toEqual(baseline);
    expect(withoutReader.run.hasCredentials).toBe(true);
    for (const field of Object.keys(missingMetadata)) expect(withoutReader.run).not.toHaveProperty(field);

    const { run, nodes, availableInterventions } = await readDetail(populatedCredentials, inspectedRunsUrl, stateJson);
    expect(run).toMatchObject({ flow_id: "fixture-flow", origin_session_key: null });
    for (const field of Object.keys(missingMetadata)) expect(run).not.toHaveProperty(field);
    expect(nodes).toHaveLength(1);
    expect(nodes[0]).not.toHaveProperty("output_json");
    expect(availableInterventions).toEqual([]);
  });

  it.each(["{", "null", "[]", '"string"', " \t"])(
    "omits only the new metadata for malformed stored state %j without the reader",
    async (stateJson) => {
      const baseline = await readDetail(populatedCredentials, defaultRunsUrl, stateJson);
      expect(await readDetail(populatedCredentials, runsUrl, stateJson)).toEqual(baseline);
    },
  );

  it.each(["", '{"nested":{"fanoutOrigin":{},"subworkflowFanouts":{}},"FanoutOrigin":{},"SubworkflowFanouts":{}}'])(
    "still projects metadata for ordinary stored state %j without the reader",
    async (stateJson) => {
      const { run } = await readDetail(populatedCredentials, runsUrl, stateJson);
      expect(run).toMatchObject({ device_id: "device-fixture", bot_type: " bot-fixture ", stage: "stage-fixture" });
    },
  );

  it("does not project runtime metadata on the opted-in run list", async () => {
    await readDetail(populatedCredentials);
    const response = await fetch(runsUrl);
    expect(response.status).toBe(200);
    const { runs } = await response.json();
    expect(runs[0]).toMatchObject({ flow_id: "fixture-flow" });
    for (const field of [...Object.keys(missingMetadata), "credentials_json"]) {
      expect(runs[0]).not.toHaveProperty(field);
    }
  });

  it("keeps the run-not-found response", async () => {
    const response = await fetch(`${runsUrl}/unknown-flow`);
    expect(response.status).toBe(404);
    expect(await response.json()).toEqual({ error: "Not Found", message: "Flow unknown-flow not found" });
  });

  it("keeps /workflow-types ahead of /:flowId", async () => {
    const response = await fetch(`${runsUrl}/workflow-types`);
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ workflows: [{ workflow_id: "fixture-workflow" }] });
  });
});
