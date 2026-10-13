// @vitest-environment node
import { afterAll, beforeAll, expect, it } from "vitest";
import express from "express";
import type { Server } from "node:http";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { FlowRunRepository } from "../../../repositories/flow-run-repository.js";
import { createInternalRunsRouter } from "../runs.js";

let server: Server;
let baseUrl: string;
let inserted: { sql: string; params: unknown[] } | undefined;

const db = {
  dbType: "sqlite",
  dialect: sqliteDialect,
  exec: async (sql: string, params: unknown[] = []) => {
    inserted = { sql, params };
    return { affectedRows: 1 };
  },
  query: async () => [{ flow_id: "flow", state_json: inserted?.params[4] ?? null }],
  transaction: async (fn: (database: IDatabase) => Promise<unknown>) => fn(db as IDatabase),
  close: async () => {},
} as IDatabase;

beforeAll(async () => {
  const app = express();
  app.use(express.json());
  app.use("/runs", createInternalRunsRouter(new FlowRunRepository(db)));
  await new Promise<void>(resolve => { server = app.listen(0, "127.0.0.1", resolve); });
  baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});

afterAll(async () => {
  await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
});

it("writes the initial state in the run INSERT", async () => {
  const stateJson = JSON.stringify({ workflowId: "demo", nodeStates: {} });
  const response = await fetch(`${baseUrl}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ flow_id: "flow", workflow_id: "demo", status: "running", state_json: stateJson }) });
  expect(response.status).toBe(201);
  expect(inserted?.sql).toContain("status, state_json, params_json");
  expect(inserted?.params[4]).toBe(stateJson);
});

it("rejects double-encoded state before inserting", async () => {
  inserted = undefined;
  const response = await fetch(`${baseUrl}/runs`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ flow_id: "flow", workflow_id: "demo", status: "running", state_json: JSON.stringify('{"workflowId":"demo"}') }) });
  expect(response.status).toBe(400);
  expect(inserted).toBeUndefined();
});
