// @vitest-environment node
import { once } from "node:events";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import Database from "better-sqlite3";
import express from "express";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { FanoutReadError } from "../../contracts/run-subworkflows.js";
import { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import { RunSubworkflowsRepository } from "../../repositories/run-subworkflows-repository.js";
import { RunSubworkflowsService } from "../../services/run-subworkflows-service.js";
import type { TaskGuardApiDiagnostic } from "../error-log-format.js";
import { createRunsRouter } from "../runs.js";

const secret = "<PRIVATE_EXECUTION_RECORD_CREDENTIAL>";
const privatePayload = "PRIVATE_FANOUT_PAYLOAD";
const denied = { error: "Forbidden", message: "No view permission for this run" };
const fanoutDenied = { error: "Forbidden", message: "Execution records are unavailable for fanout runs" };
const conflict = { error: "Conflict", message: "Run identity changed; retry the request" };
const failed = { error: "Internal Server Error", message: "Failed to load execution record" };
const unavailable = { error: "Service Unavailable", message: "Execution record unavailable" };
const mounts = ["reader-only", "reader-and-repo", "legacy-reader"] as const;

// Exercise the production reader, repositories and permission resolver against the migrated schema.
describe("execution record reader authorization", () => {
  const raw = new Database(":memory:");
  const db = new SqliteDatabase(raw);
  const flowRepo = new FlowRunRepository(db);
  const nodeRepo = new NodeExecutionRepository(db);
  const permissions = new BotWorkflowPermissionRepository(db, { listBots: async () => [] });
  const reader = new RunSubworkflowsService(new RunSubworkflowsRepository(db), permissions);
  const diagnostics: unknown[] = [];
  let server: Server;
  let baseUrl: string;

  beforeAll(async () => {
    await runMigrations(db, "sqlite");
    const app = express();
    app.use((req, res, next) => {
      req.isAdmin = req.header("x-test-admin") === "true";
      res.once("finish", () => { diagnostics.push(res.locals.taskGuardApiDiagnostic); });
      next();
    });
    app.use("/reader-only", createRunsRouter(flowRepo, nodeRepo, null, null, null, null,
      null, null, null, null, reader));
    app.use("/reader-and-repo", createRunsRouter(flowRepo, nodeRepo, null, null, null, null,
      permissions, null, null, null, reader));
    // Compatibility composition: the reader occupies the historical facadeRepo argument.
    app.use("/legacy-reader", createRunsRouter(flowRepo, nodeRepo, null, null, null, null,
      null, null, null, reader));
    server = app.listen(0, "127.0.0.1");
    await once(server, "listening");
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });

  beforeEach(() => {
    diagnostics.length = 0;
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(reader, "inspectRun");
    vi.spyOn(flowRepo, "findExecutionRecordByFlowIdStrict");
    vi.spyOn(nodeRepo, "findExecutionRecordsByFlowIdStrict");
    raw.exec("DELETE FROM node_executions; DELETE FROM flow_runs; DELETE FROM bot_workflow_permissions;");
    raw.prepare(`INSERT INTO flow_runs (flow_id, workflow_id, status, started_at, origin_bot_id,
      state_json, credentials_json, params_json, input_json, result_json, total_duration_ms)
      VALUES (?, ?, 'running', 100, 'bot:owner', '{}', ?, ' [1] ', 'invalid {', 'null', 2000000)`)
      .run("record-flow", "record-workflow", JSON.stringify({ token: secret }));
    raw.prepare(`INSERT INTO node_executions (flow_id, workflow_id, node_id, status, attempt,
      started_at, input_json, output_json) VALUES (?, ?, 'node', 'succeeded', 1, 100, 'node-input', 'node-output')`)
      .run("record-flow", "record-workflow");
    raw.prepare(`INSERT INTO bot_workflow_permissions (bot_id, bot_owner_id, workflow_id, env,
      can_view, can_execute, can_edit, gmt_create, gmt_modified) VALUES (NULL, 'reader', ?, 'prod', 1, 0, 0, 1, 1)`)
      .run("record-workflow");
  });
  afterEach(() => { vi.restoreAllMocks(); });
  afterAll(async () => {
    if (server?.listening) await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await db.close();
  });

  function read(mount: string, suffix = "", headers: Record<string, string> = { "x-user-id": "reader" }, flowId = "record-flow") {
    return fetch(`${baseUrl}/${mount}/${flowId}/execution-record${suffix}`, { headers });
  }
  function expectNoPayloadReads() {
    expect(flowRepo.findExecutionRecordByFlowIdStrict).not.toHaveBeenCalled();
    expect(nodeRepo.findExecutionRecordsByFlowIdStrict).not.toHaveBeenCalled();
  }
  function expectSafeDiagnostic() {
    expect(diagnostics).toHaveLength(1);
    const diagnostic = diagnostics[0] as TaskGuardApiDiagnostic;
    expect(diagnostic).toEqual({ message: "Protected workflow operation failed",
      stack: expect.stringContaining("Workflow protected operation failure observed"), stackSource: "observation" });
    expect(diagnostic.stack).toMatch(/\n\s+at /);
    expect(console.error).toHaveBeenCalledExactlyOnceWith("[runs] execution record failed:", {
      stack: diagnostic.stack, stackSource: "observation",
    });
    expect(console.warn).not.toHaveBeenCalled();
    expect(JSON.stringify({ diagnostics, logs: vi.mocked(console.error).mock.calls }))
      .not.toMatch(/PRIVATE_|SELECT|credentials_json|Authorization|original-private-stack/);
  }
  function persistFanout(kind: "parent" | "child") {
    const state = kind === "child" ? { fanoutOrigin: { parentFlowId: "parent", workflowId: "record-workflow" } }
      : { subworkflowFanouts: { group: { schemaVersion: "fanout.v1", kind: "subworkflowFanoutState",
        parentFlowId: "record-flow", groupNodeId: "group", groupExecutionId: "generation", revision: 1,
        status: "running", config: { concurrency: 1, successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all" },
        childWorkflow: { id: "child-workflow" }, children: [] } } };
    raw.prepare("UPDATE flow_runs SET state_json = ?, input_json = ?, result_json = ? WHERE flow_id = ?")
      .run(JSON.stringify(state), privatePayload, privatePayload, "record-flow");
    raw.prepare("UPDATE node_executions SET input_json = ?, output_json = ?").run(privatePayload, privatePayload);
  }

  describe.each(mounts)("%s mount", mount => {
    it.each([
      { flowId: "%20%09%20", suffix: "" },
      { flowId: "record-flow", suffix: "?includeNodeOutput=invalid" },
    ])("validates before inspection for $flowId$suffix", async ({ flowId, suffix }) => {
      const response = await read(mount, suffix, { "x-user-id": "reader" }, flowId);
      expect(response.status).toBe(400);
      expect(reader.inspectRun).not.toHaveBeenCalled();
      expectNoPayloadReads();
    });

    it("requires identity even when the router permission repository is absent", async () => {
      const response = await read(mount, "?botOwnerId=reader", {});
      expect(response.status).toBe(401);
      expect(await response.json()).toEqual({ error: "Unauthorized", message: "User identity required" });
      expectNoPayloadReads();
    });

    it.each([
      { actor: "denied-reader", flowId: "record-flow", status: 403, body: denied },
      { actor: "reader", flowId: "missing-flow", status: 404, body: { error: "Not Found", message: "Run not found" } },
    ])("preserves real reader $status before payload reads", async ({ actor, flowId, status, body }) => {
      const response = await read(mount, "?includeNodeOutput=true", { "x-user-id": actor }, flowId);
      expect(response.status).toBe(status);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(await response.json()).toEqual(body);
      expectNoPayloadReads();
      expect(console.error).not.toHaveBeenCalled();
    });

    it.each(["", "?includeNodeOutput=false", "?includeNodeOutput=true"])("keeps ordinary records callback-compatible for %s", async suffix => {
      const response = await read(mount, suffix, { "x-user-id": "reader" }, "%20record-flow%20");
      expect(response.status).toBe(200);
      const text = await response.text();
      expect(text).not.toContain(secret);
      const body = JSON.parse(text);
      expect(Object.keys(body).sort()).toEqual(["ext_info", "flow_id", "status", "workflow_id"]);
      expect(body).toMatchObject({ flow_id: "record-flow", workflow_id: "record-workflow", status: "running",
        ext_info: { flow_runs: { credentials_json: null, params_json: " [1] ", input_json: "invalid {",
          result_json: "null", total_duration_ms: 2000000 }, node_executions: [{ input_json: "node-input" }] } });
      if (suffix.endsWith("=true")) expect(body.ext_info.node_executions[0].output_json).toBe("node-output");
      else expect(body.ext_info.node_executions[0]).not.toHaveProperty("output_json");
      expect(reader.inspectRun).toHaveBeenCalledExactlyOnceWith({ actorId: "reader", isAdmin: false }, "record-flow");
      expect(flowRepo.findExecutionRecordByFlowIdStrict).toHaveBeenCalledExactlyOnceWith("record-flow");
      expect(nodeRepo.findExecutionRecordsByFlowIdStrict).toHaveBeenCalledExactlyOnceWith("record-flow", suffix.endsWith("=true"));
    });

    it.each(["parent", "child"] as const)("denies real fanout %s payloads to readers and administrators", async kind => {
      persistFanout(kind);
      const identities: Record<string, string>[] = [{ "x-user-id": "reader" }, { "x-test-admin": "true" }];
      for (const headers of identities) {
        const response = await read(mount, "?includeNodeOutput=true", headers);
        expect(response.status).toBe(403);
        const text = await response.text();
        expect(JSON.parse(text)).toEqual(fanoutDenied);
        expect(text).not.toMatch(/PRIVATE_/);
      }
      expectNoPayloadReads();
    });

    it.each(["managed", "restricted", "sensitiveChild"] as const)("fails closed for the independent %s inspection flag", async flag => {
      const inspection = await reader.inspectRun({ actorId: "reader", isAdmin: false }, "record-flow");
      vi.mocked(reader.inspectRun).mockResolvedValue({ ...inspection, [flag]: true });
      const response = await read(mount, "", { "x-test-admin": "true" });
      expect(response.status).toBe(403);
      expect(await response.json()).toEqual(fanoutDenied);
      expectNoPayloadReads();
    });

    it.each([
      ["flow_id", "another-flow"], ["workflow_id", "another-workflow"],
      ["origin_bot_id", "bot:another-owner"], ["origin_bot_id", null],
    ] as const)("rejects an authorized/raw %s mismatch (%s) before node payload reads", async (field, value) => {
      vi.mocked(flowRepo.findExecutionRecordByFlowIdStrict).mockImplementationOnce(async flowId => {
        const selected = await FlowRunRepository.prototype.findExecutionRecordByFlowIdStrict.call(flowRepo, flowId);
        if (!selected) throw new Error("Expected seeded execution record");
        return { ...selected, [field]: value };
      });
      const response = await read(mount);
      expect(response.status).toBe(409);
      expect(await response.json()).toEqual(conflict);
      expect(nodeRepo.findExecutionRecordsByFlowIdStrict).not.toHaveBeenCalled();
    });

    it("rejects inspection of a different flow even when the selected row matches the request", async () => {
      const inspection = await reader.inspectRun({ actorId: "reader", isAdmin: false }, "record-flow");
      vi.mocked(reader.inspectRun).mockResolvedValue({ ...inspection, run: { ...inspection.run, flow_id: "another-flow" } });
      const response = await read(mount);
      expect(response.status).toBe(409);
      expect(await response.json()).toEqual(conflict);
      expect(nodeRepo.findExecutionRecordsByFlowIdStrict).not.toHaveBeenCalled();
    });

    it.each([
      ["unauthenticated", 401, { error: "Unauthorized", message: "User identity required" }],
      ["forbidden", 403, denied], ["not_found", 404, { error: "Not Found", message: "Run not found" }],
      ["invalid_query", 400, { error: "Bad Request", message: "Invalid execution record request" }],
      ["identity_conflict", 409, conflict], ["snapshot_changed", 409, conflict],
      ["invalid_state", 500, failed], ["unavailable", 503, unavailable],
    ] as const)("maps reader %s to fixed safe status %s", async (code, status, body) => {
      const error = new FanoutReadError(code, `SELECT credentials_json ${secret}`);
      Object.defineProperty(error, "stack", { get() { throw new Error("Must not inspect the caught stack"); } });
      Object.defineProperty(error, "message", { get() { throw new Error("Must not inspect the caught message"); } });
      vi.mocked(reader.inspectRun).mockRejectedValueOnce(error);
      const response = await read(mount);
      expect(response.status).toBe(status);
      expect(await response.json()).toEqual(body);
      expectNoPayloadReads();
      if (status >= 500) expectSafeDiagnostic();
      else {
        expect(console.error).not.toHaveBeenCalled();
        expect(diagnostics).toEqual([undefined]);
        expect(JSON.stringify(vi.mocked(console.warn).mock.calls)).not.toContain(secret);
      }
    });

    it.each(["reader", "flow", "nodes"] as const)("records only safe observation diagnostics on a %s failure", async phase => {
      const error = Object.assign(new Error(`SELECT credentials_json FROM flow_runs ${secret}`), {
        stack: `original-private-stack ${secret}`, config: { headers: { Authorization: secret } },
      });
      if (phase === "reader") vi.mocked(reader.inspectRun).mockRejectedValueOnce(error);
      if (phase === "flow") vi.mocked(flowRepo.findExecutionRecordByFlowIdStrict).mockRejectedValueOnce(error);
      if (phase === "nodes") vi.mocked(nodeRepo.findExecutionRecordsByFlowIdStrict).mockRejectedValueOnce(error);
      const response = await read(mount);
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual(failed);
      expectSafeDiagnostic();
      if (phase === "reader") expectNoPayloadReads();
    });

    it("does not inspect message, stack, coercion or extension properties on an unexpected throw", async () => {
      const error = { get message() { throw new Error(secret); }, get stack() { throw new Error(secret); },
        get config() { throw new Error(secret); }, toString() { throw new Error(secret); } };
      vi.mocked(reader.inspectRun).mockRejectedValueOnce(error);
      const response = await read(mount);
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual(failed);
      expectSafeDiagnostic();
      expectNoPayloadReads();
    });
  });

  it("preserves the permission repository check in addition to successful reader inspection", async () => {
    vi.spyOn(permissions, "resolveRunViewScope").mockResolvedValueOnce("all").mockResolvedValueOnce("deny");
    const response = await read("reader-and-repo");
    expect(response.status).toBe(403);
    expect(await response.json()).toEqual(denied);
    expect(reader.inspectRun).toHaveBeenCalledOnce();
    expect(flowRepo.findExecutionRecordByFlowIdStrict).toHaveBeenCalledOnce();
    expect(nodeRepo.findExecutionRecordsByFlowIdStrict).not.toHaveBeenCalled();
  });
});
