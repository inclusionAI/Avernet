import { describe, it, expect } from "vitest";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { BatchRunAnalyzer } from "../batch-run-analyzer.js";
import { DiagnosisCardRepository } from "../../../repositories/diagnosis-card-repository.js";
import { WeaknessListRepository } from "../../../repositories/weakness-list-repository.js";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";
import Database from "better-sqlite3";

// build a DB seeded with 3 diagnosis_cards across 2 workflows for the same signature
function createTestDb(): IDatabase {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE diagnosis_cards (
      id INTEGER PRIMARY KEY AUTOINCREMENT, flow_id VARCHAR(255), workflow_id VARCHAR(255),
      node_id VARCHAR(255), failure_signature VARCHAR(256), error_text TEXT,
      analysis_reasoning TEXT, suggested_repair_type VARCHAR(32), suggested_repair_content TEXT,
      matched_lesson_id BIGINT, outcome VARCHAR(16), attempt_count INT,
      diagnosis_level VARCHAR(8),
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE weakness_list (
      id INTEGER PRIMARY KEY AUTOINCREMENT, failure_signature VARCHAR(256) NOT NULL UNIQUE,
      error_class VARCHAR(64), workflow_ids TEXT, occurrence_count INT,
      affected_workflows_count INT, repairability VARCHAR(16), priority_score DECIMAL(5,2),
      evidence_diagnosis_ids TEXT, latest_occurrence INTEGER, first_occurrence INTEGER,
      matched_lesson_ids TEXT, status VARCHAR(16) DEFAULT 'active',
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE ce_tasks (
      task_id VARCHAR(64) PRIMARY KEY, task_type VARCHAR(32), task_name VARCHAR(255), remark TEXT,
      user_id VARCHAR(255), bot_id VARCHAR(255), status VARCHAR(32),
      config_json TEXT, created_by VARCHAR(255),
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
  `);
  const seedRows: Array<{ flowId: string; wfId: string }> = [
    { flowId: "f-1", wfId: "wf-A" },
    { flowId: "f-2", wfId: "wf-A" },
    { flowId: "f-3", wfId: "wf-B" },
  ];
  for (const r of seedRows) {
    raw.prepare(`INSERT INTO diagnosis_cards (flow_id, workflow_id, node_id, failure_signature, error_text, outcome, attempt_count) VALUES (?, ?, ?, ?, ?, 'not_recovered', 3)`).run(r.flowId, r.wfId, "fetch", "sig-X", "expected number but got string");
  }
  return { dbType: "sqlite", dialect: sqliteDialect,
    query: async <T>(sql: string, params?: unknown[]) => params ? raw.prepare(sql).all(...params as []) as T[] : raw.prepare(sql).all() as T[],
    exec: async (sql: string, params?: unknown[]) => { const s = raw.prepare(sql); const r = params ? s.run(...params as []) : s.run(); return { affectedRows: r.changes, insertId: r.lastInsertRowid as number }; },
    transaction: async <T>(fn: (db: IDatabase) => Promise<T>) => raw.transaction(() => fn(createTestDb()))(),
    close: async () => { raw.close(); },
  };
}

describe("BatchRunAnalyzer", () => {
  it("aggregates diagnosis_cards by signature into weakness_list with priority_score", async () => {
    const db = createTestDb();
    const diagnosisRepo = new DiagnosisCardRepository(db);
    const weaknessRepo = new WeaknessListRepository(db);
    const analyzer = new BatchRunAnalyzer(db, diagnosisRepo, weaknessRepo, 7);
    await analyzer.runOnce();
    const rows = await db.query<{ failure_signature: string; occurrence_count: number; affected_workflows_count: number }>(`SELECT failure_signature, occurrence_count, affected_workflows_count FROM weakness_list`);
    expect(rows).toHaveLength(1);
    expect(rows[0].failure_signature).toBe("sig-X");
    expect(rows[0].occurrence_count).toBe(3);
    expect(rows[0].affected_workflows_count).toBe(2);
  });

  it("G11 — auto-dispatches a ce_task when evolveRepo provided and priority >= threshold", async () => {
    const db = createTestDb();
    const diagnosisRepo = new DiagnosisCardRepository(db);
    const weaknessRepo = new WeaknessListRepository(db);
    // Stub EvolveRepository: capture the createTask payload and reflect back.
    const dispatched: Array<{ taskId: string; taskType: string; taskName: string; configJson: string; createdBy: string }> = [];
    const evolveRepoStub = {
      createTask: async (input: { taskId: string; taskType: string; taskName: string; configJson: string; createdBy: string }) => {
        dispatched.push(input);
      },
      createTaskWithStep: async () => { throw new Error("must not call createTaskWithStep for stub"); },
    } as unknown as EvolveRepository;
    const analyzer = new BatchRunAnalyzer(db, diagnosisRepo, weaknessRepo, 7, evolveRepoStub, 2.0);
    await analyzer.runOnce();

    // priority = log(3+1) * 2 ≈ 2.77 → above threshold → must dispatch.
    expect(dispatched).toHaveLength(1);
    expect(dispatched[0].taskType).toBe("weakness_evolve");
    expect(dispatched[0].createdBy).toBe("batch-run-analyzer-g11");
    expect(dispatched[0].taskName).toContain("弱点进化:");
    const cfg = JSON.parse(dispatched[0].configJson) as { failure_signature: string; occurrence_count: number; affected_workflows_count: number; priority_score: number };
    expect(cfg.failure_signature).toBe("sig-X");
    expect(cfg.occurrence_count).toBe(3);
    expect(cfg.affected_workflows_count).toBe(2);
    expect(cfg.priority_score).toBeGreaterThan(2.0);
  });

  it("G11 — does NOT dispatch when evolveRepo is null (pre-G11 back-compat)", async () => {
    const db = createTestDb();
    const diagnosisRepo = new DiagnosisCardRepository(db);
    const weaknessRepo = new WeaknessListRepository(db);
    const analyzer = new BatchRunAnalyzer(db, diagnosisRepo, weaknessRepo, 7, null, 2.0);
    await analyzer.runOnce();
    // Should NOT throw and should NOT write any ce_tasks rows (table empty).
    const ceTasks = await db.query<{ task_id: string }>(`SELECT task_id FROM ce_tasks`);
    expect(ceTasks).toHaveLength(0);
    // weakness_list upsert path still works (regression guard).
    const weak = await db.query<{ failure_signature: string }>(`SELECT failure_signature FROM weakness_list`);
    expect(weak).toHaveLength(1);
  });

  it("G11 — dispatch is idempotent across runOnce calls with same sig", async () => {
    const db = createTestDb();
    const diagnosisRepo = new DiagnosisCardRepository(db);
    const weaknessRepo = new WeaknessListRepository(db);
    const dispatched: string[] = [];
    // Stub evolveRepo: persist each createTask into the test ce_tasks table
    // so the idempotency seeq finds it on re-runs.
    const evolveRepoStub = {
      createTask: async (input: { taskId: string; taskType: string; taskName: string; remark: string | null; userId: string; botId: string; configJson: string; createdBy: string }) => {
        dispatched.push(input.taskId);
        await db.exec(
          `INSERT INTO ce_tasks (task_id, task_type, task_name, remark, user_id, bot_id, status, config_json, created_by)
           VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, ?)`,
          [input.taskId, input.taskType, input.taskName, input.remark, input.userId, input.botId, input.configJson, input.createdBy],
        );
      },
      createTaskWithStep: async () => { throw new Error("must not call createTaskWithStep for stub"); },
    } as unknown as EvolveRepository;
    const analyzer = new BatchRunAnalyzer(db, diagnosisRepo, weaknessRepo, 7, evolveRepoStub, 2.0);
    await analyzer.runOnce();
    await analyzer.runOnce(); // second invocation with same data
    expect(dispatched).toHaveLength(1); // only the first run dispatched
  });
});