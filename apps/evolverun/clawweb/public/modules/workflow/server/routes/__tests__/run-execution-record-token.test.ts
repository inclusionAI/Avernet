// @vitest-environment node
import { createHash } from "node:crypto";
import { once } from "node:events";
import { request, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import Database from "better-sqlite3";
import express from "express";
import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { AppConfigRepository } from "../../repositories/app-config-repository.js";
import type { FlowRunRepository } from "../../repositories/flow-run-repository.js";
import type { NodeExecutionRepository } from "../../repositories/node-execution-repository.js";
import { createRunsRouter } from "../runs.js";
import {
  executionRecordTestToken, executionRecordTokenHeaders, executionRecordUnauthorized,
  executionRecordAuthUnavailable, executionRecordLegacyWorkflow, seedExecutionRecordToken, tokenConfigYaml,
} from "./execution-record-token.fixture.js";

const digest = createHash("sha256").update(executionRecordTestToken, "utf8").digest("hex");
const rotatedToken = "cd".repeat(32);
const run = { flow_id: "token-flow", workflow_id: "token-workflow", status: "running", state_json: "{}" };

describe("execution record dedicated Bearer token authentication", () => {
  const raw = new Database(":memory:");
  const db = new SqliteDatabase(raw);
  const configRepo = new AppConfigRepository(db);
  const findRecord = vi.fn();
  const readWorkflow = vi.fn();
  const findNodes = vi.fn();
  const findFullRun = vi.fn();
  const findLatestNodes = vi.fn();
  const flowRepo = { findExecutionRecordByFlowIdStrict: findRecord, readExecutionRecordWorkflow: readWorkflow,
    findFullByFlowId: findFullRun } as unknown as FlowRunRepository;
  const nodeRepo = { findExecutionRecordsByFlowIdStrict: findNodes, findLatestByFlowId: findLatestNodes } as unknown as NodeExecutionRepository;
  let server: Server;
  let baseUrl: string;

  beforeAll(async () => {
    const app = express();
    app.use((req, _res, next) => {
      req.isAdmin = req.header("x-test-admin") === "true";
      req.cookies = { staff_id: req.header("x-test-cookie-user") };
      next();
    });
    app.use("/runs", createRunsRouter(flowRepo, nodeRepo, null, null, null, null, null, null, null, null, null,
      { executionRecordConfigRepo: configRepo }));
    app.use("/unconfigured", createRunsRouter(flowRepo, nodeRepo, null, null, null));
    app.use("/null-config", createRunsRouter(flowRepo, nodeRepo, null, null, null, null, null, null, null, null, null,
      { executionRecordConfigRepo: null }));
    server = app.listen(0, "127.0.0.1");
    await once(server, "listening");
    baseUrl = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
  });
  beforeEach(() => {
    seedExecutionRecordToken(raw);
    findRecord.mockReset().mockResolvedValue(run);
    readWorkflow.mockReset().mockResolvedValue(executionRecordLegacyWorkflow);
    findNodes.mockReset().mockResolvedValue([]);
    findFullRun.mockReset().mockResolvedValue(run);
    findLatestNodes.mockReset().mockResolvedValue([]);
    vi.spyOn(console, "warn").mockImplementation(() => {});
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => {
    try {
      const logs = JSON.stringify([vi.mocked(console.warn).mock.calls, vi.mocked(console.error).mock.calls]);
      for (const value of [executionRecordTestToken, rotatedToken, digest, "execution_record_sha256", "private-driver-error"]) {
        expect(logs).not.toContain(value);
      }
    } finally { vi.restoreAllMocks(); }
  });
  afterAll(async () => {
    if (server?.listening) await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
    await db.close();
  });

  function read(headers: Record<string, string> = executionRecordTokenHeaders, mount = "/runs", query = "") {
    return fetch(`${baseUrl}${mount}/token-flow/execution-record${query}`, { headers });
  }
  function expectNoRecordReads() {
    expect(findRecord).not.toHaveBeenCalled();
    expect(readWorkflow).not.toHaveBeenCalled();
    expect(findNodes).not.toHaveBeenCalled();
    expect(findFullRun).not.toHaveBeenCalled();
    expect(findLatestNodes).not.toHaveBeenCalled();
  }
  async function expectRejected(response: Response, status: 401 | 503) {
    expect(response.status).toBe(status);
    expect(response.headers.get("cache-control")).toBe("no-store");
    expect(await response.json()).toEqual(status === 401 ? executionRecordUnauthorized : executionRecordAuthUnavailable);
    expectNoRecordReads();
  }

  it.each(["Bearer", "bearer", "bEaReR", "BEARER"])("accepts %s with a valid raw token and no actor", async scheme => {
    const query = vi.spyOn(db, "query");
    const response = await read({ authorization: `${scheme} ${executionRecordTestToken}` });
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toBe("no-store");
    const body = await response.text();
    for (const value of [executionRecordTestToken, digest, "execution_record_sha256"]) expect(body).not.toContain(value);
    expect(findRecord).toHaveBeenCalledExactlyOnceWith("token-flow");
    expect(readWorkflow).toHaveBeenCalledExactlyOnceWith(run);
    expect(findNodes).toHaveBeenCalledExactlyOnceWith("token-flow", false);
    expect(query).toHaveBeenCalledTimes(1);
    expect(query.mock.calls[0][0]).toMatch(/FROM cm_app_config WHERE config_key = \?/);
    expect(query.mock.calls[0][1]).toEqual(["api_token"]);
    expect(query.mock.calls[0][0]).not.toMatch(/ce_app_config/);
  });

  it("forwards exact executed-workflow metadata at the top level without expanding callback ext_info", async () => {
    const yaml = "# exact fixture source\nid: token-workflow\nnodes: []\n";
    const workflow = { workflow_yaml: yaml, workflow_snapshot: { status: "resolved", snapshot: {
      kind: "deployment", workflowId: "token-workflow", workflowVersion: 3, deployNumber: 7,
      configDigest: createHash("sha256").update(yaml, "utf8").digest("hex"),
    } } };
    readWorkflow.mockResolvedValueOnce(workflow);
    const response = await read();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body).toMatchObject(workflow);
    expect(Object.keys(body.ext_info).sort()).toEqual(["flow_runs", "node_executions"]);
    expect(body.ext_info.flow_runs).not.toHaveProperty("workflow_yaml");
    expect(body.ext_info.flow_runs).not.toHaveProperty("workflow_snapshot");
  });

  it.each([
    undefined, "", "Bearer", "Basic fixture", `Bearer ${rotatedToken}`,
    `Bearer ${executionRecordTestToken.toUpperCase()}`, `Bearer ${"a".repeat(63)}`,
    `Bearer ${"a".repeat(65)}`, `Bearer ${"g".repeat(64)}`, `Bearer  ${executionRecordTestToken}`,
    `Bearer\t${executionRecordTestToken}`, `Bearer ${executionRecordTestToken} extra`,
    `Bearer ${executionRecordTestToken}, Bearer ${executionRecordTestToken}`, `Bearer ${digest}`,
  ])("rejects absent, incorrect, malformed or digest-as-token Authorization (%s)", async authorization => {
    await expectRejected(await read(authorization === undefined ? {} : { authorization }), 401);
  });

  it.each([
    { "x-test-admin": "true" }, { "x-user-id": "owner" }, { "x-staff-id": "owner" },
    { cookie: "staff_id=owner" }, { "x-test-cookie-user": "owner" },
    { "x-test-admin": "true", authorization: `Bearer ${rotatedToken}` },
  ])("does not allow admin/session/identity spoofing to bypass authentication (%j)", async headers => {
    await expectRejected(await read(headers, "/runs", "?botOwnerId=owner&botId=bot&isAdmin=true"), 401);
  });

  it("does not accept query or cookie credentials", async () => {
    const credentialQuery = new URLSearchParams();
    for (const key of ["token", "access_token", "api_token"]) credentialQuery.set(key, executionRecordTestToken);
    await expectRejected(await read({ cookie: `api_token=${executionRecordTestToken}; Authorization=Bearer%20${executionRecordTestToken}` },
      "/runs", `?${credentialQuery}`), 401);
  });

  it("rejects duplicate original Authorization headers even when Node drops one normalized value", async () => {
    const response = await new Promise<{ status: number; body: string; cache: string | string[] | undefined }>((resolve, reject) => {
      const req = request(`${baseUrl}/runs/token-flow/execution-record`, {
        headers: ["Host", new URL(baseUrl).host, "Authorization", executionRecordTokenHeaders.authorization,
          "aUtHoRiZaTiOn", executionRecordTokenHeaders.authorization],
      }, res => {
        let body = "";
        res.setEncoding("utf8");
        res.on("data", chunk => { body += chunk; });
        res.on("end", () => resolve({ status: res.statusCode!, body, cache: res.headers["cache-control"] }));
      });
      req.on("error", reject);
      req.end();
    });
    expect(response).toEqual({ status: 401, body: JSON.stringify(executionRecordUnauthorized), cache: "no-store" });
    expectNoRecordReads();
  });

  it.each(["/unconfigured", "/null-config"])("fails closed without an injected config repository on %s", async mount => {
    await expectRejected(await read(executionRecordTokenHeaders, mount), 503);
  });

  it("fails closed for a missing config, without reading the similarly named Evolve table", async () => {
    raw.exec("DELETE FROM cm_app_config; CREATE TABLE ce_app_config (config_key TEXT, config_yaml TEXT, enabled INTEGER)");
    try {
      raw.prepare("INSERT INTO ce_app_config VALUES (?, ?, 1)").run("api_token", tokenConfigYaml());
      await expectRejected(await read(), 503);
    } finally { raw.exec("DROP TABLE ce_app_config"); }
  });

  it.each([0, 2, -1])("fails closed for enabled=%s", async enabled => {
    raw.prepare("UPDATE cm_app_config SET enabled = ? WHERE config_key = ?").run(enabled, "api_token");
    await expectRejected(await read(), 503);
  });

  it.each([
    "", "null", "[]", '"scalar"', "123", "{}", `- execution_record_sha256: "${digest}"`,
    `execution_record_sha256: "${digest}"\nother: true`,
    `execution_record_sha256: "${digest}"\nexecution_record_sha256: "${digest}"`,
    `execution_record_sha256: "${digest}"\n"execution_record_sha256": "${digest}"`,
    "execution_record_sha256: [broken", "execution_record_sha256: null", "execution_record_sha256: true",
    `execution_record_sha256: ${"1".repeat(64)}`, 'execution_record_sha256: "a"',
    `execution_record_sha256: "${"g".repeat(64)}"`, `execution_record_sha256: "${digest} "`,
    `execution_record_sha256: "${digest}"\n---\nexecution_record_sha256: "${digest}"`,
    `execution_record_sha256: !private "${digest}"`, `execution_record_sha256: {value: "${digest}"}`,
    `execution_record_sha256: *missing`, `&root {execution_record_sha256: *root}`,
    `__proto__: {execution_record_sha256: "${digest}"}`, `<<: {execution_record_sha256: "${digest}"}`,
  ])("fails closed for malformed or noncanonical token configuration (%s)", async yaml => {
    raw.prepare("UPDATE cm_app_config SET config_yaml = ? WHERE config_key = ?").run(yaml, "api_token");
    await expectRejected(await read(), 503);
  });

  it("accepts uppercase expected digest hex without changing raw token case", async () => {
    raw.prepare("UPDATE cm_app_config SET config_yaml = ?").run(`execution_record_sha256: "${digest.toUpperCase()}"`);
    expect((await read()).status).toBe(200);
  });

  it.each([{ config_key: "API_TOKEN" }, { enabled: "1" }, { config_yaml: null }])("validates the repository row envelope strictly (%j)", async patch => {
    const row = await configRepo.findByKey("api_token");
    vi.spyOn(configRepo, "findByKey").mockResolvedValue({ ...row, ...patch } as never);
    await expectRejected(await read(), 503);
  });

  it("returns a safe 503 for actual config database failure", async () => {
    raw.exec("ALTER TABLE cm_app_config RENAME TO unavailable_token_config");
    try { await expectRejected(await read(), 503); }
    finally { raw.exec("ALTER TABLE unavailable_token_config RENAME TO cm_app_config"); }
  });

  it("does not inspect or expose opaque config lookup errors", async () => {
    const opaque = { get message() { throw new Error("private-driver-error"); }, get stack() { throw new Error("private-driver-error"); },
      get config() { throw new Error("private-driver-error"); }, toString() { throw new Error("private-driver-error"); } };
    vi.spyOn(configRepo, "findByKey").mockRejectedValue(opaque);
    await expectRejected(await read(), 503);
  });

  it("completes config authentication before any runtime/snapshot/node lookup", async () => {
    const row = await configRepo.findByKey("api_token");
    let finish: (value: typeof row) => void;
    const pending = new Promise<typeof row>(resolve => { finish = resolve; });
    const config = vi.spyOn(configRepo, "findByKey").mockReturnValueOnce(pending);
    const response = read();
    await vi.waitFor(() => expect(config).toHaveBeenCalledExactlyOnceWith("api_token"));
    expectNoRecordReads();
    finish!(row);
    expect((await response).status).toBe(200);
  });

  it("reads fresh config for rotation, disabling, deletion and re-creation without restarting", async () => {
    const config = vi.spyOn(configRepo, "findByKey");
    expect((await read()).status).toBe(200);
    await configRepo.update("api_token", { config_yaml: tokenConfigYaml(rotatedToken) });
    findRecord.mockClear(); readWorkflow.mockClear(); findNodes.mockClear();
    await expectRejected(await read(), 401);
    expect((await read({ authorization: `Bearer ${rotatedToken}` })).status).toBe(200);
    await configRepo.update("api_token", { enabled: 0 });
    findRecord.mockClear(); readWorkflow.mockClear(); findNodes.mockClear();
    await expectRejected(await read({ authorization: `Bearer ${rotatedToken}` }), 503);
    await configRepo.delete("api_token");
    await expectRejected(await read({ authorization: `Bearer ${rotatedToken}` }), 503);
    seedExecutionRecordToken(raw);
    expect((await read()).status).toBe(200);
    expect(config.mock.calls.every(([key]) => key === "api_token")).toBe(true);
  });

  it.each(["/runs", "/unconfigured"])("does not add token authentication to unrelated detail routes on %s", async mount => {
    const config = vi.spyOn(configRepo, "findByKey");
    const response = await fetch(`${baseUrl}${mount}/token-flow`, { headers: { authorization: "not a Bearer token" } });
    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({ run: { flow_id: "token-flow" }, nodes: [] });
    expect(config).not.toHaveBeenCalled();
    expect(findFullRun).toHaveBeenCalledExactlyOnceWith("token-flow");
    expect(findRecord).not.toHaveBeenCalled();
  });
});
