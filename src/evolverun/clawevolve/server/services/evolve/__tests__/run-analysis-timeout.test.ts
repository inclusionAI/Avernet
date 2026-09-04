import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase } from "../../../db.js";
import { EvolveRepository } from "../../../repositories/evolve-repository.js";
import { runRunAnalysisTimeoutSweep } from "../run-analysis-timeout.js";

let db: SqliteDatabase;
let repo: EvolveRepository;

beforeEach(async () => {
  db = new SqliteDatabase(new Database(":memory:"));
  repo = new EvolveRepository(db);
  await db.exec(`CREATE TABLE ce_tasks (
    task_id TEXT PRIMARY KEY, status TEXT NOT NULL, error_message TEXT, gmt_modified INTEGER
  )`);
  await db.exec(`CREATE TABLE ce_steps (
    step_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, step_type TEXT NOT NULL, command TEXT NOT NULL,
    status TEXT NOT NULL, error_code TEXT, error_message TEXT, retryable INTEGER,
    completed_at INTEGER, gmt_create INTEGER, gmt_modified INTEGER
  )`);
  await db.exec(`CREATE TABLE flow_runs (
    flow_id TEXT PRIMARY KEY, evolution_analysis_status TEXT, evolution_analyzed_at INTEGER
  )`);
  await db.exec(`CREATE TABLE workflow_evolution_analysis_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT, analysis_id TEXT NOT NULL, task_id TEXT, step_id TEXT,
    flow_id TEXT, status TEXT NOT NULL, error_code TEXT, completed_at_ms INTEGER,
    state_version INTEGER NOT NULL DEFAULT 0, gmt_modified INTEGER
  )`);
  await db.exec("INSERT INTO ce_tasks VALUES ('EV-1', 'running', NULL, 1)");
  await db.exec("INSERT INTO ce_steps VALUES ('STEP-1', 'EV-1', 'run_analysis', '[run-analysis] flow-1', 'analyzing', NULL, NULL, NULL, NULL, 1, 1)");
  await db.exec("INSERT INTO flow_runs VALUES ('flow-1', 'analyzing', 1)");
  await db.exec("INSERT INTO workflow_evolution_analysis_runs (analysis_id, task_id, step_id, flow_id, status, gmt_modified) VALUES ('AN-1', 'EV-1', 'STEP-1', 'flow-1', 'analyzing', 1)");
});

afterEach(async () => {
  await db.close();
});

describe("run analysis timeout", () => {
  it("atomically fails the analysis, CE task/step, and flow projection", async () => {
    const settled = await repo.tryTimeoutRunAnalysisStep(
      "STEP-1",
      "flow-1",
      "RUN_ANALYSIS_TIMEOUT",
      "analysis timed out",
      123456,
    );

    expect(settled).toBe(true);
    expect((await db.query<{ status: string }>("SELECT status FROM ce_steps WHERE step_id = 'STEP-1'"))[0]?.status).toBe("failed");
    expect((await db.query<{ status: string }>("SELECT status FROM ce_tasks WHERE task_id = 'EV-1'"))[0]?.status).toBe("failed");
    expect((await db.query<{ status: string; error_code: string; state_version: number }>("SELECT status, error_code, state_version FROM workflow_evolution_analysis_runs WHERE analysis_id = 'AN-1'"))[0]).toMatchObject({ status: "failed", error_code: "RUN_ANALYSIS_TIMEOUT", state_version: 1 });
    expect((await db.query<{ evolution_analysis_status: string }>("SELECT evolution_analysis_status FROM flow_runs WHERE flow_id = 'flow-1'"))[0]?.evolution_analysis_status).toBe("failed");
    expect(await repo.tryTimeoutRunAnalysisStep("STEP-1", "flow-1", "RUN_ANALYSIS_TIMEOUT", "again", 123457)).toBe(false);
  });

  it("uses the atomic transition for stale rows", async () => {
    const mockedRepo = {
      findStaleRunAnalysisSteps: vi.fn().mockResolvedValue([{ step_id: "STEP-1", task_id: "EV-1", flow_id: "flow-1", gmt_create: 1 }]),
      tryTimeoutRunAnalysisStep: vi.fn().mockResolvedValue(true),
    } as unknown as EvolveRepository;

    expect(await runRunAnalysisTimeoutSweep(mockedRepo)).toBe(1);
    expect(mockedRepo.tryTimeoutRunAnalysisStep).toHaveBeenCalledWith(
      "STEP-1",
      "flow-1",
      "RUN_ANALYSIS_TIMEOUT",
      expect.stringContaining("30 分钟"),
      expect.any(Number),
    );
  });
});
