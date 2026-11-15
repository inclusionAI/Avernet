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
let stateJson = JSON.stringify({ workflowId: "parent", nodeStates: {} });

const db = {
  dbType: "sqlite",
  dialect: sqliteDialect,
  exec: async (sql: string, params: unknown[] = []) => {
    if (sql.includes("UPDATE flow_runs SET state_json")) stateJson = String(params[0]);
    return { affectedRows: 1 };
  },
  query: async (sql: string) => {
    if (sql.includes("SELECT state_json FROM flow_runs")) return [{ state_json: stateJson }];
    return [{
      flow_id: "parent",
      workflow_id: "cct-drill-evaluation",
      status: "running",
      state_json: stateJson,
    }];
  },
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

it("preserves committed child flow ids and ignores stale fanouts in an ordinary state PUT", async () => {
  const fanoutState = {
    workflowId: "parent",
    subworkflowFanouts: {
      "run-experiments": {
        schemaVersion: "fanout.v1",
        groupNodeId: "run-experiments",
        children: [{ itemId: "scene-a/partition-a", childFlowId: "child-1", nativeFlowId: "native-1", status: "running" }],
      },
    },
  };
  stateJson = JSON.stringify(fanoutState);
  const put = await fetch(`${baseUrl}/runs/parent/state`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ state_json: JSON.stringify({ workflowId: "updated", subworkflowFanouts: {} }) }),
  });
  expect(put.status).toBe(200);

  const get = await fetch(`${baseUrl}/runs/parent/state`);
  expect(get.status).toBe(200);
  const body = await get.json() as { data: { state_json: string } };
  const saved = JSON.parse(body.data.state_json) as typeof fanoutState;
  expect(saved.workflowId).toBe("updated");
  expect(saved.subworkflowFanouts["run-experiments"].children[0].childFlowId).toBe("child-1");
  expect(saved.subworkflowFanouts["run-experiments"].children[0].nativeFlowId).toBe("native-1");
});
