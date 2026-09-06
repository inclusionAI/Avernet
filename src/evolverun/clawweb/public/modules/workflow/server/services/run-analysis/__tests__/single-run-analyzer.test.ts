import { describe, it, expect } from "vitest";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import Database from "better-sqlite3";
import { SingleRunAnalyzer } from "../single-run-analyzer.js";
import { DiagnosisCardRepository } from "../../../repositories/diagnosis-card-repository.js";
import { LessonRepository } from "../../../repositories/lesson-repository.js";

function createTestDb(): IDatabase {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE diagnosis_cards (
      id INTEGER PRIMARY KEY AUTOINCREMENT, flow_id VARCHAR(255) NOT NULL,
      workflow_id VARCHAR(255) NOT NULL, node_id VARCHAR(255) NOT NULL,
      failure_signature VARCHAR(256) NOT NULL, error_text TEXT NOT NULL,
      input_snapshot TEXT, output_snapshot TEXT, step_traces_snapshot TEXT,
      analysis_reasoning TEXT, suggested_repair_type VARCHAR(32),
      suggested_repair_content TEXT, matched_lesson_id BIGINT,
      outcome VARCHAR(16) NOT NULL, attempt_count INT NOT NULL DEFAULT 0,
      diagnosis_level VARCHAR(8),
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE lessons (
      id INTEGER PRIMARY KEY AUTOINCREMENT, failure_signature VARCHAR(256) NOT NULL,
      error_class VARCHAR(64), executor_type VARCHAR(64), tool_or_node VARCHAR(128),
      repair_type VARCHAR(32) NOT NULL, repair_content TEXT NOT NULL,
      confidence_score DECIMAL(5,4) NOT NULL DEFAULT 0.5, hit_count INTEGER DEFAULT 0,
      success_count INTEGER DEFAULT 0, fail_count INTEGER DEFAULT 0, status VARCHAR(16) DEFAULT 'draft',
      evidence_run_ids TEXT, source VARCHAR(32), related_workflow_ids TEXT,
      metrics_before TEXT, metrics_after TEXT, bench_domain_id BIGINT,
      last_hit_at INTEGER, last_hit_success INTEGER,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
  `);
  return { dbType: "sqlite", dialect: sqliteDialect,
    query: async <T>(sql: string, params?: unknown[]) => params ? raw.prepare(sql).all(...params as []) as T[] : raw.prepare(sql).all() as T[],
    exec: async (sql: string, params?: unknown[]) => { const s = raw.prepare(sql); const r = params ? s.run(...params as []) : s.run(); return { affectedRows: r.changes, insertId: r.lastInsertRowid as number }; },
    transaction: async <T>(fn: (db: IDatabase) => Promise<T>) => raw.transaction(() => fn(createTestDb()))(),
    close: async () => { raw.close(); },
  };
}

describe("SingleRunAnalyzer", () => {
  it("persists a diagnosis card when a flow fails", async () => {
    const db = createTestDb();
    const diagnosisCards = new DiagnosisCardRepository(db);
    const lessons = new LessonRepository(db);
    const analyzer = new SingleRunAnalyzer(db, diagnosisCards, lessons);
    await analyzer.analyzeAndPersist({
      flow_id: "f-99", workflow_id: "wf-99", node_id: "fetch", failure_signature: "sig-x",
      error_text: "expected number but got string", attempt_count: 3,
      outcome: "not_recovered",
      analysis_reasoning: "domain_id must be int",
      suggested_repair_type: "arg_template_fix",
      suggested_repair_content: "{\"domain_id\":\"{{int(domain_id)}}\"}",
      diagnosis_level: "L3",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      matched_lesson_id: null,
    });
    const rows = await db.query<{ failure_signature: string }>(`SELECT failure_signature FROM diagnosis_cards`);
    expect(rows.length).toBe(1);
    expect(rows[0].failure_signature).toBe("sig-x");
  });

  it("promotes a draft lesson when no validated lesson matches yet and a suggested fix exists", async () => {
    const db = createTestDb();
    const diagnosisCards = new DiagnosisCardRepository(db);
    const lessons = new LessonRepository(db);
    const analyzer = new SingleRunAnalyzer(db, diagnosisCards, lessons);
    await analyzer.analyzeAndPersist({
      flow_id: "f-100", workflow_id: "wf-100", node_id: "fetch",
      failure_signature: "param-type-mismatch · mcp-call", error_text: "expected number but got string",
      attempt_count: 2, outcome: "not_recovered",
      suggested_repair_type: "arg_template_fix",
      suggested_repair_content: "{\"domain_id\":\"{{int(domain_id)}}\"}",
      diagnosis_level: "L2",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      matched_lesson_id: null,
      error_class_raw: "param-type-mismatch",
    });
    const drafts = await db.query<{ repair_type: string; status: string }>(
      `SELECT repair_type, status FROM lessons WHERE failure_signature = 'param-type-mismatch · mcp-call'`,
    );
    expect(drafts).toHaveLength(1);
    expect(drafts[0].repair_type).toBe("arg_template_fix");
    expect(drafts[0].status).toBe("draft");
  });

  it("does not duplicate a draft lesson when one already exists for the signature", async () => {
    const db = createTestDb();
    const diagnosisCards = new DiagnosisCardRepository(db);
    const lessons = new LessonRepository(db);
    const analyzer = new SingleRunAnalyzer(db, diagnosisCards, lessons);
    const input = {
      flow_id: "f-101", workflow_id: "wf-101", node_id: "fetch",
      failure_signature: "timeout · cli-script", error_text: "timed out after 30000ms",
      attempt_count: 3, outcome: "not_recovered",
      suggested_repair_type: "kb_hint" as const,
      suggested_repair_content: "use --max-time 60",
      diagnosis_level: "L3" as const,
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      matched_lesson_id: null,
      error_class_raw: "timeout",
    };
    await analyzer.analyzeAndPersist(input);
    await analyzer.analyzeAndPersist(input);
    const rows = await db.query<{ id: number }>(`SELECT id FROM lessons WHERE failure_signature = 'timeout · cli-script'`);
    expect(rows).toHaveLength(1);
  });
});