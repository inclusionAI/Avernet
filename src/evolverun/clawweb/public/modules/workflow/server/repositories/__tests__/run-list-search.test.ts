// @vitest-environment node
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { FlowRunRepository } from "../flow-run-repository.js";

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
    } finally { await db.close(); }
  });
});
