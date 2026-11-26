// @vitest-environment node
import { once } from "node:events";
import type { Server } from "node:http";
import type { AddressInfo } from "node:net";
import Database from "better-sqlite3";
import express from "express";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase, runMigrations } from "@avernet/clawweb-shared/server/db";
import { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import { RunSubworkflowsRepository } from "../../repositories/run-subworkflows-repository.js";
import { RunSubworkflowsService } from "../../services/run-subworkflows-service.js";
import type { TaskGuardApiDiagnostic } from "../error-log-format.js";
import { createRunsRouter } from "../runs.js";

const secret = "<PRIVATE_EXECUTION_RECORD_CREDENTIAL>";
const privatePayload = "<PRIVATE_FANOUT_PAYLOAD>";
const fanoutDenied = { error: "Forbidden", message: "Execution records are unavailable for fanout runs" };
const conflict = { error: "Conflict", message: "Run identity changed; retry the request" };
const failed = { error: "Internal Server Error", message: "Failed to load execution record" };
const mounts = ["reader-only", "reader-and-repo", "legacy-reader"] as const;

// Real readers remain installed for other routes. Execution records must not call them or resolve view ACL.
// Headers exercise the inherited identity gate, not host verification of a login/session.
describe("execution record identity and structural payload guards", () => {
  const raw = new Database(":memory:");
  const db = new SqliteDatabase(raw);
  const flowRepo = new FlowRunRepository(db);
  const nodeRepo = new NodeExecutionRepository(db);
  const directory = { listBots: vi.fn(async () => []) };
  const permissions = new BotWorkflowPermissionRepository(db, directory);
  const store = new RunSubworkflowsRepository(db);
  const reader = new RunSubworkflowsService(store, permissions);
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
    directory.listBots.mockReset().mockResolvedValue([]);
    vi.spyOn(console, "error").mockImplementation(() => {});
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(reader, "inspectRun");
    vi.spyOn(reader, "isFanoutOriginChild");
    vi.spyOn(store, "readRun");
    vi.spyOn(permissions, "resolveRunViewScope");
    vi.spyOn(permissions, "listRunViewScopes");
    vi.spyOn(flowRepo, "findExecutionRecordByFlowIdStrict");
    vi.spyOn(nodeRepo, "findExecutionRecordsByFlowIdStrict");
    vi.spyOn(db, "query");
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
  function expectNoAccessLookups() {
    expect(reader.inspectRun).not.toHaveBeenCalled();
    expect(reader.isFanoutOriginChild).not.toHaveBeenCalled();
    expect(store.readRun).not.toHaveBeenCalled();
    expect(permissions.resolveRunViewScope).not.toHaveBeenCalled();
    expect(permissions.listRunViewScopes).not.toHaveBeenCalled();
    expect(directory.listBots).not.toHaveBeenCalled();
  }
  function expectNoNodeReads() {
    expect(nodeRepo.findExecutionRecordsByFlowIdStrict).not.toHaveBeenCalled();
    expect(vi.mocked(db.query).mock.calls.some(([sql]) => /FROM node_executions\b/.test(sql))).toBe(false);
    expectNoAccessLookups();
  }
  function expectOneSnapshot() {
    expect(flowRepo.findExecutionRecordByFlowIdStrict).toHaveBeenCalledExactlyOnceWith("record-flow");
    const selections = vi.mocked(db.query).mock.calls.filter(([sql]) => /FROM flow_runs\b/.test(sql));
    expect(selections).toHaveLength(1);
    expect(selections[0][0]).toMatch(/\bstate_json\b/);
    expect(selections[0][0]).toMatch(/NULL AS credentials_json/);
    expect(selections[0][0].replace(/NULL AS credentials_json/, "")).not.toMatch(/credentials_json|SELECT\s+\*/);
    expectNoAccessLookups();
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
  function persistState(state: unknown) {
    raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id = ?").run(JSON.stringify(state), "record-flow");
  }
  function persistFanout(kind: "parent" | "child") {
    const state = kind === "child" ? { fanoutOrigin: { parentFlowId: "parent", workflowId: "record-workflow" } }
      : { subworkflowFanouts: { group: { schemaVersion: "fanout.v1", kind: "subworkflowFanoutState",
        parentFlowId: "record-flow", groupNodeId: "group", groupExecutionId: "generation", revision: 1,
        status: "running", config: { concurrency: 1, successPolicy: "all_done", finishMode: "wait_all", resultPolicy: "collect_all" },
        childWorkflow: { id: "child-workflow" }, children: [] } } };
    persistState(state);
    raw.prepare("UPDATE flow_runs SET input_json = ?, result_json = ? WHERE flow_id = ?")
      .run(privatePayload, privatePayload, "record-flow");
    raw.prepare("UPDATE node_executions SET input_json = ?, output_json = ?").run(privatePayload, privatePayload);
  }

  describe.each(mounts)("%s mount", mount => {
    it.each([
      { flowId: "%20%09%20", suffix: "" },
      { flowId: "record-flow", suffix: "?includeNodeOutput=invalid" },
      { flowId: "record-flow", suffix: "?includeNodeOutput=true&includeNodeOutput=false" },
      { flowId: "record-flow", suffix: "?includeNodeOutput[value]=true" },
    ])("validates before all reads for $flowId$suffix", async ({ flowId, suffix }) => {
      const response = await read(mount, suffix, { "x-user-id": "reader" }, flowId);
      expect(response.status).toBe(400);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(flowRepo.findExecutionRecordByFlowIdStrict).not.toHaveBeenCalled();
      expect(db.query).not.toHaveBeenCalled();
      expectNoNodeReads();
    });

    it.each([{}, { "x-user-id": "   " }])("requires identity regardless of query identity (%j)", async headers => {
      const response = await read(mount, "?botOwnerId=reader&botId=bot&userId=reader&isAdmin=true", headers);
      expect(response.status).toBe(401);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(await response.json()).toEqual({ error: "Unauthorized", message: "User identity required" });
      expect(flowRepo.findExecutionRecordByFlowIdStrict).not.toHaveBeenCalled();
      expect(db.query).not.toHaveBeenCalled();
      expectNoNodeReads();
    });

    it.each(["no grant", "explicit deny"])("returns an ordinary record with %s without permission or directory lookup", async grant => {
      if (grant === "no grant") raw.exec("DELETE FROM bot_workflow_permissions");
      else raw.exec("UPDATE bot_workflow_permissions SET can_view = 0");
      // These dependencies must be unreachable, not caught as forbidden and then ignored.
      vi.mocked(reader.inspectRun).mockRejectedValue(new Error("Must not inspect run"));
      vi.mocked(permissions.resolveRunViewScope).mockRejectedValue(new Error("Must not resolve view ACL"));
      directory.listBots.mockRejectedValue(new Error("Must not read directory"));
      const response = await read(mount, "?includeNodeOutput=true&botOwnerId=someone-else&botId=unrelated");
      expect(response.status).toBe(200);
      const body = await response.json();
      expect(body.ext_info.flow_runs).toMatchObject({ flow_id: "record-flow", credentials_json: null });
      expect(body.ext_info.node_executions[0].output_json).toBe("node-output");
      expectOneSnapshot();
    });

    it.each(["unrelated-bot:reader", "bot:another-owner", null, ""])("ignores view Bot/owner scope for ordinary origin %s", async origin => {
      raw.exec("UPDATE bot_workflow_permissions SET bot_id = 'bot'");
      raw.prepare("UPDATE flow_runs SET origin_bot_id = ? WHERE flow_id = ?").run(origin, "record-flow");
      const response = await read(mount, "?botOwnerId=owner&botId=bot");
      expect(response.status).toBe(200);
      expect((await response.json()).ext_info.flow_runs.origin_bot_id).toBe(origin);
      expectOneSnapshot();
    });

    it("returns a fixed not-found response from the strict selection without reading nodes", async () => {
      const response = await read(mount, "", { "x-user-id": "denied-reader" }, "missing-flow");
      expect(response.status).toBe(404);
      expect(response.headers.get("cache-control")).toBe("no-store");
      expect(await response.json()).toEqual({ error: "Not Found", message: "Flow missing-flow not found" });
      expect(flowRepo.findExecutionRecordByFlowIdStrict).toHaveBeenCalledExactlyOnceWith("missing-flow");
      expectNoNodeReads();
      expect(console.error).not.toHaveBeenCalled();
    });

    it.each(["", "?includeNodeOutput=false", "?includeNodeOutput=true"])("keeps ordinary records callback-compatible for %s", async suffix => {
      persistState({ flowId: "record-flow", subworkflowFanouts: {}, private: secret });
      const response = await read(mount, suffix, { "x-user-id": "reader" }, "%20record-flow%20");
      expect(response.status).toBe(200);
      expect(response.headers.get("cache-control")).toBe("no-store");
      const text = await response.text();
      expect(text).not.toContain(secret);
      const body = JSON.parse(text);
      expect(Object.keys(body).sort()).toEqual(["ext_info", "flow_id", "status", "workflow_id"]);
      expect(body).toMatchObject({ flow_id: "record-flow", workflow_id: "record-workflow", status: "running",
        ext_info: { flow_runs: { credentials_json: null, params_json: " [1] ", input_json: "invalid {",
          result_json: "null", total_duration_ms: 2000000 }, node_executions: [{ input_json: "node-input" }] } });
      expect(body.ext_info.flow_runs).not.toHaveProperty("state_json");
      if (suffix.endsWith("=true")) expect(body.ext_info.node_executions[0].output_json).toBe("node-output");
      else expect(body.ext_info.node_executions[0]).not.toHaveProperty("output_json");
      expectOneSnapshot();
      expect(nodeRepo.findExecutionRecordsByFlowIdStrict).toHaveBeenCalledExactlyOnceWith("record-flow", suffix.endsWith("=true"));
    });

    it("preserves the server-authorized administrator exception to the actor gate without using the reader", async () => {
      expect((await read(mount, "", { "x-test-admin": "true" })).status).toBe(200);
      expectOneSnapshot();
    });

    it.each(["parent", "child"] as const)("denies real fanout %s payloads independently of caller and grants, including admins", async kind => {
      persistFanout(kind);
      raw.exec("DELETE FROM bot_workflow_permissions");
      const identities: Record<string, string>[] = [{ "x-user-id": "reader" }, { "x-user-id": "denied-reader" }, { "x-test-admin": "true" }];
      for (const headers of identities) {
        const response = await read(mount, "?includeNodeOutput=true", headers);
        expect(response.status).toBe(403);
        expect(response.headers.get("cache-control")).toBe("no-store");
        const text = await response.text();
        expect(JSON.parse(text)).toEqual(fanoutDenied);
        expect(text).not.toMatch(/PRIVATE_/);
      }
      expect(flowRepo.findExecutionRecordByFlowIdStrict).toHaveBeenCalledTimes(identities.length);
      expectNoNodeReads();
    });

    it.each([
      { fanoutOrigin: null }, { fanoutOrigin: false }, { fanoutOrigin: "" },
      { subworkflowFanouts: { group: null } }, { subworkflowFanouts: { group: {} } },
      JSON.parse('{"subworkflowFanouts":{"__proto__":null}}'),
    ])("conservatively denies own child markers or nonempty maps without validating group entries (%j)", async state => {
      persistState(state);
      const response = await read(mount, "?includeNodeOutput=true", { "x-test-admin": "true" });
      expect(response.status).toBe(403);
      expect(await response.json()).toEqual(fanoutDenied);
      expectOneSnapshot();
      expectNoNodeReads();
      expect(console.error).not.toHaveBeenCalled();
      expect(diagnostics).toEqual([undefined]);
    });

    it.each([null, "", "{}", '{"subworkflowFanouts":{}}', '{"nested":{"fanoutOrigin":null}}',
      '{"__proto__":{"fanoutOrigin":null}}'])("permits ordinary/absent state without inherited or nested marker inference (%s)", async state => {
      raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id = ?").run(state, "record-flow");
      expect((await read(mount)).status).toBe(200);
      expectOneSnapshot();
    });

    it.each([secret, "null", "[]", "true", '"scalar"', "123", " ",
      '{"subworkflowFanouts":null}', '{"subworkflowFanouts":[]}', '{"subworkflowFanouts":true}'])(
      "rejects malformed root/map state with safe 500 diagnostics (%s)", async state => {
        raw.prepare("UPDATE flow_runs SET state_json = ? WHERE flow_id = ?").run(state, "record-flow");
        const response = await read(mount, "?includeNodeOutput=true");
        expect(response.status).toBe(500);
        expect(response.headers.get("cache-control")).toBe("no-store");
        expect(await response.json()).toEqual(failed);
        expectOneSnapshot();
        expectNoNodeReads();
        expectSafeDiagnostic();
      },
    );

    it.each([{}, { fanoutOrigin: null }, { subworkflowFanouts: { group: null } }])(
      "rejects same-snapshot embedded/request flowId conflicts before payload reads (%j)", async state => {
        persistState({ ...state, flowId: "another-flow" });
        const response = await read(mount, "?includeNodeOutput=true");
        expect(response.status).toBe(409);
        expect(await response.json()).toEqual(conflict);
        expectOneSnapshot();
        expectNoNodeReads();
        expect(console.error).not.toHaveBeenCalled();
        expect(diagnostics).toEqual([undefined]);
      },
    );

    it("rejects a selected/request identity mismatch before reading node payloads", async () => {
      vi.mocked(flowRepo.findExecutionRecordByFlowIdStrict).mockImplementationOnce(async flowId => {
        const selected = await FlowRunRepository.prototype.findExecutionRecordByFlowIdStrict.call(flowRepo, flowId);
        if (!selected) throw new Error("Expected seeded execution record");
        return { ...selected, flow_id: "another-flow" };
      });
      const response = await read(mount);
      expect(response.status).toBe(409);
      expect(await response.json()).toEqual(conflict);
      expectOneSnapshot();
      expectNoNodeReads();
    });

    it.each([false, true])("uses selected state without a second snapshot (fanout=%s)", async fanout => {
      const fanoutState = JSON.stringify({ flowId: "record-flow", fanoutOrigin: null });
      raw.prepare("UPDATE flow_runs SET state_json = ?").run(fanout ? fanoutState : "{}");
      vi.mocked(flowRepo.findExecutionRecordByFlowIdStrict).mockImplementationOnce(async flowId => {
        const selected = await FlowRunRepository.prototype.findExecutionRecordByFlowIdStrict.call(flowRepo, flowId);
        raw.prepare("UPDATE flow_runs SET state_json = ?").run(fanout ? "{}" : fanoutState);
        return selected;
      });
      const response = await read(mount, "?includeNodeOutput=true");
      expect(response.status).toBe(fanout ? 403 : 200);
      expectOneSnapshot();
      if (fanout) expectNoNodeReads();
      else expect((await response.json()).ext_info.flow_runs).not.toHaveProperty("state_json");
    });

    it.each(["flow", "nodes"] as const)("records only safe observation diagnostics on a %s read failure", async phase => {
      const error = Object.assign(new Error(`SELECT credentials_json FROM flow_runs ${secret}`), {
        stack: `original-private-stack ${secret}`, config: { headers: { Authorization: secret } },
      });
      if (phase === "flow") vi.mocked(flowRepo.findExecutionRecordByFlowIdStrict).mockRejectedValueOnce(error);
      else vi.mocked(nodeRepo.findExecutionRecordsByFlowIdStrict).mockRejectedValueOnce(error);
      const response = await read(mount);
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual(failed);
      expectSafeDiagnostic();
      expectNoAccessLookups();
      if (phase === "flow") expectNoNodeReads();
    });

    it("does not inspect message, stack, coercion or extension properties on an unexpected throw", async () => {
      const error = { get message() { throw new Error(secret); }, get stack() { throw new Error(secret); },
        get config() { throw new Error(secret); }, toString() { throw new Error(secret); } };
      vi.mocked(flowRepo.findExecutionRecordByFlowIdStrict).mockRejectedValueOnce(error);
      const response = await read(mount);
      expect(response.status).toBe(500);
      expect(await response.json()).toEqual(failed);
      expectSafeDiagnostic();
      expectNoNodeReads();
    });

    it.each(["no grant", "explicit deny", "wrong Bot", "wrong owner", "missing origin"])(
      "keeps ordinary list/detail/node ACL enforced by the real reader (%s)", async kind => {
        if (kind === "no grant") raw.exec("DELETE FROM bot_workflow_permissions");
        else if (kind === "explicit deny") raw.exec("UPDATE bot_workflow_permissions SET can_view = 0");
        else {
          raw.exec("UPDATE bot_workflow_permissions SET bot_id = 'bot'");
          const origin = kind === "wrong Bot" ? "unrelated-bot:reader" : kind === "wrong owner" ? "bot:another-owner" : null;
          raw.prepare("UPDATE flow_runs SET origin_bot_id = ?").run(origin);
        }
        expect((await read(mount)).status).toBe(200);
        expectOneSnapshot();
        const headers = { "x-user-id": "reader" };
        const list = await fetch(`${baseUrl}/${mount}?workflowId=record-workflow&botOwnerId=owner`, { headers });
        expect(list.status).toBe(200);
        expect(await list.json()).toMatchObject({ runs: [], total: 0 });
        for (const path of ["record-flow", "record-flow/nodes"]) {
          const detail = await fetch(`${baseUrl}/${mount}/${path}?botOwnerId=owner`, { headers });
          expect(detail.status).toBe(403);
          expect(await detail.json()).toEqual({ error: "forbidden", message: "Run view permission required" });
        }
        expect(reader.inspectRun).toHaveBeenCalledTimes(2);
        expect(permissions.resolveRunViewScope).toHaveBeenCalledTimes(2);
        expect(permissions.listRunViewScopes).toHaveBeenCalledTimes(3);
        expect(directory.listBots).toHaveBeenCalled();
      },
    );

    it("retains successful real-reader ordinary list/detail/node reads for a granted actor", async () => {
      const headers = { "x-user-id": "reader" };
      const list = await fetch(`${baseUrl}/${mount}?workflowId=record-workflow`, { headers });
      expect(list.status).toBe(200);
      expect(await list.json()).toMatchObject({ runs: [{ flow_id: "record-flow" }], total: 1 });
      const detail = await fetch(`${baseUrl}/${mount}/record-flow`, { headers });
      expect(detail.status).toBe(200);
      expect(await detail.json()).toMatchObject({ run: { flow_id: "record-flow" }, nodes: [{ node_id: "node" }] });
      const nodes = await fetch(`${baseUrl}/${mount}/record-flow/nodes`, { headers });
      expect(nodes.status).toBe(200);
      expect(await nodes.json()).toMatchObject([{ node_id: "node" }]);
      expect(reader.inspectRun).toHaveBeenCalledTimes(2);
      expect(permissions.resolveRunViewScope).toHaveBeenCalledTimes(2);
      expect(flowRepo.findExecutionRecordByFlowIdStrict).not.toHaveBeenCalled();
    });
  });
});
