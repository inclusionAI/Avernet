import Database from "better-sqlite3";
import express from "express";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { EvolveRepository } from "../../repositories/evolve-repository.js";
import { createEvolveRouter } from "../evolve.js";

let db: SqliteDatabase;
let server: ReturnType<express.Application["listen"]> | null;
let baseUrl: string;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  await db.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL UNIQUE, request_key TEXT NOT NULL UNIQUE,
    scope_type TEXT NOT NULL, scope_json TEXT NOT NULL, flow_id TEXT, workflow_id TEXT, status TEXT NOT NULL,
    evidence_status TEXT, evidence_snapshot_ref TEXT, evidence_snapshot_digest TEXT, evidence_manifest_json TEXT,
    task_id TEXT, step_id TEXT, analysis_version TEXT NOT NULL, result_json TEXT, result_digest TEXT,
    diagnosis_count INTEGER NOT NULL DEFAULT 0, error_code TEXT, requested_by TEXT, requested_at_ms INTEGER NOT NULL,
    completed_at_ms INTEGER, state_version INTEGER NOT NULL DEFAULT 0, gmt_create INTEGER DEFAULT 0, gmt_modified INTEGER DEFAULT 0
  )`);
  await db.exec(`CREATE TABLE ce_tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT NOT NULL UNIQUE, task_name TEXT NOT NULL,
    remark TEXT, task_type TEXT NOT NULL, user_id TEXT NOT NULL, bot_id TEXT NOT NULL,
    status TEXT NOT NULL, config_json TEXT NOT NULL, error_message TEXT, created_by TEXT NOT NULL,
    gmt_create INTEGER DEFAULT 0, gmt_modified INTEGER DEFAULT 0
  )`);
  await db.exec(`CREATE TABLE ce_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT, step_id TEXT NOT NULL UNIQUE, task_id TEXT NOT NULL,
    step_type TEXT NOT NULL, step_no INTEGER NOT NULL, round_no INTEGER, command TEXT NOT NULL,
    status TEXT NOT NULL, bot_run_id TEXT, bot_session_id TEXT, bot_response_json TEXT,
    output_json TEXT, summary TEXT, error_code TEXT, error_message TEXT, retryable INTEGER,
    started_at INTEGER, completed_at INTEGER, gmt_create INTEGER DEFAULT 0, gmt_modified INTEGER DEFAULT 0
  )`);
  await db.exec(`INSERT INTO ce_tasks
    (task_id, task_name, task_type, user_id, bot_id, status, config_json, created_by)
    VALUES ('EV-PROGRESS-1', '运行分析', 'run_analysis', 'owner-1', 'bot-1', 'running', '{}', 'owner-1')`);
  await db.exec(`INSERT INTO ce_steps
    (step_id, task_id, step_type, step_no, command, status, summary, output_json)
    VALUES ('STEP-PROGRESS-1', 'EV-PROGRESS-1', 'run_analysis', 1, 'analyze flow-1', 'analyzing',
      'Agent 正在分析', '{"analysisProgress":{"phase":"agent_analyzing","message":"Agent 正在分析","elapsedMs":42000,"updatedAtMs":1000,"inputSummary":{"evidenceTotal":12,"evidenceIncluded":10,"nodeCount":3,"failedNodeCount":1,"traceCount":5,"warnErrorLogCount":2,"truncated":true}}}')`);
  await db.exec(`INSERT INTO workflow_evolution_analysis_runs
    (analysis_id, request_key, scope_type, scope_json, flow_id, workflow_id, status, task_id, step_id,
     analysis_version, requested_at_ms)
    VALUES ('AN-PROGRESS-1', '${"p".repeat(64)}', 'single_run', '{"flowIds":["flow-1"]}',
      'flow-1', 'wf-1', 'analyzing', 'EV-PROGRESS-1', 'STEP-PROGRESS-1', 'workflow-evolution/v1', 1000)`);

  const app = express();
  app.use(express.json());
  app.use("/api/evolve", createEvolveRouter(new EvolveRepository(db), {
    db,
    artifactUrlStore: { createSignedUrl: async () => "https://example.test/artifact" },
  }));
  server = await new Promise<ReturnType<express.Application["listen"]>>((resolve) => {
    const instance = app.listen(0, () => resolve(instance));
  });
  baseUrl = `http://127.0.0.1:${(server.address() as { port: number }).port}`;
});

afterEach(async () => {
  const active = server;
  server = null;
  if (active) await new Promise<void>((resolve) => active.close(() => resolve()));
  await db.close();
});

describe("Task Guard analysis progress", () => {
  it("returns the latest managed analysis phase and safe input summary", async () => {
    const response = await fetch(`${baseUrl}/api/evolve/runs/flow-1/analysis-progress`);

    expect(response.status).toBe(200);
    expect(await response.json()).toMatchObject({
      analysisId: "AN-PROGRESS-1",
      status: "analyzing",
      progress: {
        phase: "agent_analyzing",
        message: "Agent 正在分析",
        elapsedMs: 42000,
        inputSummary: { evidenceTotal: 12, evidenceIncluded: 10, failedNodeCount: 1, truncated: true },
      },
    });
  });
});
