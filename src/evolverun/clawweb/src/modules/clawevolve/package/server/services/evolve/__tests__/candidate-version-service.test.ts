import { describe, it, expect } from "vitest";
import type { IDatabase } from "../../../db.js";
import { sqliteDialect } from "../../../db/dialect.js";
import Database from "better-sqlite3";
import { CandidateVersionService, type BenchRunnerPort, type DeployerPort } from "../candidate-version-service.js";
import { LessonRepository } from "../../../repositories/lesson-repository.js";
import { SuggestionOutcomeRepository } from "../../../repositories/suggestion-outcome-repository.js";
import { WorkflowSpecRepository } from "../../../repositories/workflow-spec-repository.js";

function createTestDb(): IDatabase {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE lessons (
      id INTEGER PRIMARY KEY AUTOINCREMENT, failure_signature VARCHAR(256) NOT NULL,
      error_class VARCHAR(64), executor_type VARCHAR(64), tool_or_node VARCHAR(128),
      repair_type VARCHAR(32) NOT NULL, repair_content TEXT NOT NULL,
      confidence_score DECIMAL(5,4) NOT NULL DEFAULT 0.5, hit_count INTEGER DEFAULT 0,
      success_count INTEGER DEFAULT 0, fail_count INTEGER DEFAULT 0, status VARCHAR(16) DEFAULT 'draft',
      evidence_run_ids TEXT, source VARCHAR(32), related_workflow_ids TEXT,
      metrics_before TEXT, metrics_after TEXT, bench_domain_id INTEGER,
      last_hit_at INTEGER, last_hit_success INTEGER,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE suggestion_outcomes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      lesson_id INTEGER NOT NULL, workflow_id VARCHAR(255) NOT NULL, node_id VARCHAR(255),
      failure_signature VARCHAR(256) NOT NULL, adopted INTEGER NOT NULL DEFAULT 0,
      applied_version VARCHAR(64), metrics_before TEXT, metrics_after TEXT,
      verdict VARCHAR(16) NOT NULL, source VARCHAR(32) NOT NULL,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE workflow_specs (
      id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id VARCHAR(255) NOT NULL,
      pack_id VARCHAR(255), spec_json TEXT NOT NULL, title VARCHAR(255),
      version INTEGER DEFAULT 1,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE cm_bench_candidate_versions (
      id INTEGER PRIMARY KEY AUTOINCREMENT, workflow_id VARCHAR(255) NOT NULL,
      lesson_id INTEGER NOT NULL, candidate_version VARCHAR(64) NOT NULL,
      score_vs_baseline DECIMAL(8,4), passed_baseline INTEGER NOT NULL DEFAULT 0,
      overfit_detected INTEGER NOT NULL DEFAULT 0, deployed INTEGER NOT NULL DEFAULT 0,
      deploy_number INTEGER,
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

const SPEC_YAML = "nodes:\n  - id: fetch\n    executor:\n      type: mcp-call\n      prompt: old prompt\n";

describe("CandidateVersionService.autoEvolve", () => {
  it("auto-deploys a confidence>=0.9 validated lesson when bench passes and no overfit", async () => {
    const db = createTestDb();
    const lessons = new LessonRepository(db);
    const outcomes = new SuggestionOutcomeRepository(db);
    const specRepo = new WorkflowSpecRepository(db);
    const bench: BenchRunnerPort = { runFor: async () => ({ scoreVsBaseline: 0.2, scoreFluctuationAcrossRounds: 0.01 }) };
    const deployer: DeployerPort = { deploy: async () => ({ success: true, deploy_number: 42 }) };
    const svc = new CandidateVersionService(db, lessons, outcomes, specRepo, null, bench, deployer);

    const lessonId = await lessons.insert({
      failure_signature: "sig-deploy", error_class: "param-type-mismatch",
      repair_type: "prompt_patch", repair_content: "Output strict JSON only",
      confidence_score: 0.91, status: "validated", source: "manual",
      tool_or_node: "fetch",
      related_workflow_ids: JSON.stringify(["wf-deploy"]),
    });
    await specRepo.upsert("wf-deploy", "pk", SPEC_YAML);

    const r = await svc.autoEvolve(lessonId);
    expect(r.deployed).toBe(true);
    expect(r.deploy_number).toBe(42);
    const updated = await specRepo.findByWorkflowId("wf-deploy");
    expect(updated?.spec_json).toContain("Output strict JSON only");
    const lesson = await lessons.getById(lessonId);
    expect(lesson?.status).toBe("live");
  });

  it("rolls back spec and decrements confidence when bench fails", async () => {
    const db = createTestDb();
    const lessons = new LessonRepository(db);
    const outcomes = new SuggestionOutcomeRepository(db);
    const specRepo = new WorkflowSpecRepository(db);
    const bench: BenchRunnerPort = { runFor: async () => ({ scoreVsBaseline: -0.1, scoreFluctuationAcrossRounds: 0.01 }) };
    const deployer: DeployerPort = { deploy: async () => ({ success: true, deploy_number: 99 }) };
    const svc = new CandidateVersionService(db, lessons, outcomes, specRepo, null, bench, deployer);

    const lessonId = await lessons.insert({
      failure_signature: "sig-rollback", error_class: "param-type-mismatch",
      repair_type: "prompt_patch", repair_content: "Output strict JSON only",
      confidence_score: 0.92, status: "validated", source: "manual",
      tool_or_node: "fetch",
      related_workflow_ids: JSON.stringify(["wf-rollback"]),
    });
    await specRepo.upsert("wf-rollback", "pk", SPEC_YAML);

    const r = await svc.autoEvolve(lessonId);
    expect(r.deployed).toBe(false);
    expect(r.reason).toBe("bench_baseline_not_passed_or_overfit");
    // spec rolled back
    const updated = await specRepo.findByWorkflowId("wf-rollback");
    expect(updated?.spec_json).toContain("old prompt");
    expect(updated?.spec_json).not.toContain("Output strict JSON only");
    // confidence decremented (applyOutcome false → -0.15, clamped at [0.10, 1.00])
    const lesson = await lessons.getById(lessonId);
    expect(lesson?.confidence_score).toBeCloseTo(0.92 - 0.15, 5);
  });

  it("refuses to auto-deploy dependency-down (transient) lessons", async () => {
    const db = createTestDb();
    const lessons = new LessonRepository(db);
    const outcomes = new SuggestionOutcomeRepository(db);
    const specRepo = new WorkflowSpecRepository(db);
    const bench: BenchRunnerPort = { runFor: async () => ({ scoreVsBaseline: 0.3, scoreFluctuationAcrossRounds: 0.0 }) };
    const deployer: DeployerPort = { deploy: async () => ({ success: true, deploy_number: 1 }) };
    const svc = new CandidateVersionService(db, lessons, outcomes, specRepo, null, bench, deployer);

    const lessonId = await lessons.insert({
      failure_signature: "sig-down", error_class: "dependency-down",
      repair_type: "prompt_patch", repair_content: "retry later",
      confidence_score: 0.95, status: "validated", source: "manual",
      tool_or_node: "fetch",
      related_workflow_ids: JSON.stringify(["wf-down"]),
    });

    const r = await svc.autoEvolve(lessonId);
    expect(r.deployed).toBe(false);
    expect(r.reason).toBe("transient_class_not_auto_deployable");
  });
});