// @vitest-environment node
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { FlowRunRepository } from "../flow-run-repository.js";
import express from "express";
import { once } from "node:events";
import { createRunsRouter } from "../../routes/runs.js";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";

describe("run history keyword filtering", () => {
  it("searches identifiers and callers across history with consistent counts and pagination", async () => {
    const raw = new Database(":memory:");
    const db = new SqliteDatabase(raw);
    const repo = new FlowRunRepository(db);
    raw.exec(`CREATE TABLE flow_runs (
      id INTEGER PRIMARY KEY, flow_id TEXT, workflow_id TEXT, status TEXT, triggered_by TEXT,
      origin_bot_id TEXT, started_at INTEGER, workflow_title TEXT, node_count INTEGER,
      succeeded_count INTEGER, failed_count INTEGER, total_duration_ms INTEGER, total_token_usage INTEGER,
      completed_at INTEGER, identity_key TEXT, current_phase TEXT, origin_session_key TEXT,
      origin_session_id TEXT, user_id TEXT, plugin_version TEXT, engine TEXT,
      workflow_version INTEGER, workflow_deploy_number INTEGER, gmt_create INTEGER,
      gmt_modified INTEGER, evolution_analysis_status TEXT);
      INSERT INTO flow_runs (flow_id,workflow_id,status,triggered_by,origin_bot_id,started_at) VALUES
      ('old-match','wf','failed','gateway-client','bot_one:owner',1),
      ('new-match','wf','failed','gateway-client','bot_two:owner',2),
      ('success','wf','succeeded','gateway-client','bot_three:owner',3),
      ('foreign','other','failed','gateway-client','bot_one:owner',4);`);
    try {
      const filters = { workflowId: "wf", status: "failed", query: "gateway-client" };
      expect(await repo.countRuns(filters)).toBe(2);
      expect((await repo.findRuns({ ...filters, limit: 1 })).map(r => r.flow_id)).toEqual(["new-match"]);
      expect((await repo.findRuns({ ...filters, limit: 1, offset: 1 })).map(r => r.flow_id)).toEqual(["old-match"]);
      expect(await repo.countRuns({ workflowId: "wf", query: "old-match" })).toBe(1);
      expect(await repo.countRuns({ workflowId: "wf", query: "bot_one" })).toBe(1);
      expect(await repo.countRuns({ workflowId: "wf", query: "%" })).toBe(0);
      expect(await repo.countRuns({ workflowId: "wf", query: "' OR 1=1 --" })).toBe(0);

      let viewableIds = new Set(["wf"]);
      const permissions = {
        getViewByIdsForOwner: async () => ({ restrictedIds: new Set(["wf", "other"]), viewableIds }),
      } as unknown as BotWorkflowPermissionRepository;
      const app = express();
      app.use((req, _res, next) => { req.isAdmin = req.headers["x-test-admin"] === "true"; next(); });
      app.use("/runs", createRunsRouter(repo, null, null, null, null, null, permissions));
      const server = app.listen(0, "127.0.0.1");
      await once(server, "listening");
      const base = `http://127.0.0.1:${(server.address() as import("node:net").AddressInfo).port}/runs`;
      const read = async (query: string, admin = false) => {
        const response = await fetch(base + query, { headers: { "x-user-id": "owner", "x-test-admin": String(admin) } });
        expect(response.status).toBe(200);
        return response.json();
      };
      try {
        for (const query of ["?query=foreign", "?workflowId=other", "?query=bot_one&workflowId=other"]) {
          const hidden = await read(query);
          expect(hidden.total).toBe(0);
          expect(hidden.runs).toEqual([]);
          if (hidden.statusCounts) expect(hidden.statusCounts).toEqual({});
        }
        const first = await read("?query=gateway-client&status=failed&limit=1");
        expect(first.total).toBe(2);
        expect(first.runs.map((r: { flow_id: string }) => r.flow_id)).toEqual(["new-match"]);
        const second = await read("?query=gateway-client&status=failed&limit=1&offset=1");
        expect(second.runs.map((r: { flow_id: string }) => r.flow_id)).toEqual(["old-match"]);
        expect((await read("")).statusCounts).toEqual({ failed: 2, succeeded: 1 });
        viewableIds = new Set();
        expect(await read("?query=gateway-client")).toMatchObject({ total: 0, runs: [] });
        expect(await read("")).toMatchObject({ total: 0, runs: [], statusCounts: {} });
        expect(await read("?query=foreign", true)).toMatchObject({ total: 1 });
        expect((await read("", true)).statusCounts).toEqual({ failed: 3, succeeded: 1 });
      } finally {
        server.closeAllConnections();
        await new Promise<void>((resolve, reject) => server.close(error => error ? reject(error) : resolve()));
      }
    } finally { await db.close(); }
  });
});
