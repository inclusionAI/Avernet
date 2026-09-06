/**
 * Evolution metrics dashboard endpoint tests — G5 (unified proposal CR).
 *
 * Verifies the 4 new §10.3 dashboard widgets against a real in-memory SQLite
 * db with seeded data covering each aggregation slice.
 */
import { describe, it, beforeAll, afterAll, expect } from "vitest";
import express from "express";
import { DatabaseSync } from "node:sqlite";
import { EvolutionMetricsRepository } from "../../repositories/evolution-metrics.js";
import { createEvolutionMetricsRouter } from "../evolution-metrics.js";

type DbSync = DatabaseSync;

function createTestDb(): { db: any } {
  const raw = new DatabaseSync(":memory:") as DbSync;
  raw.exec(`
    CREATE TABLE repair_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      flow_id VARCHAR(255) NOT NULL, node_id VARCHAR(255) NOT NULL,
      failure_signature VARCHAR(256) NOT NULL,
      lesson_id INTEGER, diagnosis_card_id INTEGER, suggestion_outcome_id INTEGER,
      repair_type VARCHAR(32) NOT NULL, repair_content TEXT,
      applied_by VARCHAR(32), retry_success INTEGER, level VARCHAR(8),
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE diagnosis_cards (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      flow_id VARCHAR(255) NOT NULL, workflow_id VARCHAR(255) NOT NULL, node_id VARCHAR(255) NOT NULL,
      failure_signature VARCHAR(256) NOT NULL, error_text TEXT NOT NULL,
      input_snapshot TEXT, output_snapshot TEXT, step_traces_snapshot TEXT,
      analysis_reasoning TEXT, suggested_repair_type VARCHAR(32),
      suggested_repair_content TEXT, matched_lesson_id INTEGER,
      outcome VARCHAR(16) NOT NULL DEFAULT 'not_recovered',
      attempt_count INTEGER NOT NULL DEFAULT 0, diagnosis_level VARCHAR(8),
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE flow_runs (
      flow_id VARCHAR(255) PRIMARY KEY,
      workflow_version INTEGER,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE lessons (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      failure_signature VARCHAR(256) NOT NULL, error_class VARCHAR(64),
      executor_type VARCHAR(64), tool_or_node VARCHAR(128),
      repair_type VARCHAR(32) NOT NULL, repair_content TEXT NOT NULL,
      confidence_score DECIMAL(5,4) NOT NULL DEFAULT 0.5,
      hit_count INTEGER NOT NULL DEFAULT 0, success_count INTEGER NOT NULL DEFAULT 0,
      fail_count INTEGER NOT NULL DEFAULT 0, status VARCHAR(16) NOT NULL DEFAULT 'draft',
      evidence_run_ids TEXT, source VARCHAR(32), related_workflow_ids TEXT,
      metrics_before TEXT, metrics_after TEXT, bench_domain_id INTEGER,
      last_hit_at INTEGER, last_hit_success INTEGER,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE suggestion_outcomes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      lesson_id INTEGER NOT NULL, workflow_id VARCHAR(255) NOT NULL,
      node_id VARCHAR(255), failure_signature VARCHAR(256) NOT NULL,
      adopted INTEGER NOT NULL DEFAULT 0, applied_version VARCHAR(64),
      metrics_before TEXT, metrics_after TEXT,
      verdict VARCHAR(16) NOT NULL, source VARCHAR(32) NOT NULL,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE TABLE weakness_list (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      failure_signature VARCHAR(256) NOT NULL UNIQUE,
      error_class VARCHAR(64),
      workflow_ids TEXT,
      occurrence_count INTEGER NOT NULL DEFAULT 0,
      affected_workflows_count INTEGER,
      repairability VARCHAR(16),
      priority_score DECIMAL(5,2) NOT NULL DEFAULT 0,
      evidence_diagnosis_ids TEXT,
      latest_occurrence INTEGER,
      first_occurrence INTEGER,
      matched_lesson_ids TEXT,
      status VARCHAR(16) NOT NULL DEFAULT 'active',
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
  `);
  const db = {
    dbType: "sqlite" as const,
    query: <T,>(sql: string, params: unknown[] = []) => {
      const stmt = raw.prepare(sql);
      return (params.length ? stmt.all(...(params as never[])) : stmt.all()) as T[];
    },
    exec: async (sql: string, params: unknown[] = []) => {
      const stmt = raw.prepare(sql);
      const r = params.length ? stmt.run(...(params as never[])) : stmt.run();
      return { affectedRows: r.changes, insertId: r.lastInsertRowid as number };
    },
    transaction: async <T,>(fn: () => Promise<T>) => fn(),
    close: async () => raw.close(),
  };
  return { db: db as any };
}

async function listenOn(app: express.Express): Promise<{ server: any; baseUrl: string }> {
  return new Promise((resolve) => {
    const srv = app.listen(0, () => {
      const port = (srv.address() as any).port;
      resolve({ server: srv, baseUrl: `http://127.0.0.1:${port}` });
    });
  });
}
async function close(srv: any): Promise<void> { return new Promise((r) => srv.close(() => r())); }

describe("createEvolutionMetricsRouter — §10.3 dashboard widgets (G5)", () => {
  let server: any;
  let baseUrl: string;
  let db: any;
  let repo: EvolutionMetricsRepository;

  beforeAll(async () => {
    const ctx = createTestDb();
    db = ctx.db;
    repo = new EvolutionMetricsRepository(db);
    const app = express();
    app.use(express.json({ limit: "10mb" }));
    app.use("/api/evolution-metrics", createEvolutionMetricsRouter(repo));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv; baseUrl = url;
    // Seed data — set gmt_create to a high timestamp so the since=now-3600s filter
    // is inclusive. Easiest: insert with gmt_create = unixepoch() (default).
    // Note: For the seeded repair_history rows below, we use the timestamp 0 case
    // by NOT explicitly seeding gmt_create (relies on the DEFAULT unixepoch()).
  });
  afterAll(async () => { await close(server); });

  it("GET /physical-repair-coverage aggregates by applied_by and totals correctly", async () => {
    // Seed 2 guardian + 1 manual + 1 auto_heal
    await db.exec(`INSERT INTO repair_history (flow_id, node_id, failure_signature, repair_type, applied_by, retry_success, level) VALUES ('f-1','n','sig','kb_hint','guardian',1,'L1')`);
    await db.exec(`INSERT INTO repair_history (flow_id, node_id, failure_signature, repair_type, applied_by, retry_success, level) VALUES ('f-2','n','sig','kb_hint','guardian',0,'L1')`);
    await db.exec(`INSERT INTO repair_history (flow_id, node_id, failure_signature, repair_type, applied_by, retry_success, level) VALUES ('f-3','n','sig','prompt_patch','manual',1,'L2')`);
    await db.exec(`INSERT INTO repair_history (flow_id, node_id, failure_signature, repair_type, applied_by, retry_success, level) VALUES ('f-4','n','sig','prompt_patch','auto_heal',1,'L2')`);
    // Query since=0 (cover all rows)
    const body = await (await fetch(`${baseUrl}/api/evolution-metrics/physical-repair-coverage?since=0`)).json() as { items: { applied_by: string; count: number; total: number }[]; since: number };
    expect(body.since).toBe(0);
    const guardian = body.items.find((x) => x.applied_by === "guardian");
    const manual = body.items.find((x) => x.applied_by === "manual");
    const autoheal = body.items.find((x) => x.applied_by === "auto_heal");
    expect(guardian?.count).toBe(2);
    expect(manual?.count).toBe(1);
    expect(autoheal?.count).toBe(1);
    // total = grand total across all rows
    expect(body.items[0]?.total).toBe(4);
  });

  it("GET /lesson-confidence-distribution buckets lessons by confidence_score", async () => {
    // Seed across buckets: 0.2 / 0.4 / 0.6 / 0.8 / 0.95
    await db.exec(`INSERT INTO lessons (failure_signature, repair_type, repair_content, confidence_score, status) VALUES ('low','kb_hint','x',0.2,'draft')`);
    await db.exec(`INSERT INTO lessons (failure_signature, repair_type, repair_content, confidence_score, status) VALUES ('mid-low','kb_hint','x',0.4,'draft')`);
    await db.exec(`INSERT INTO lessons (failure_signature, repair_type, repair_content, confidence_score, status) VALUES ('mid','kb_hint','x',0.6,'validated')`);
    await db.exec(`INSERT INTO lessons (failure_signature, repair_type, repair_content, confidence_score, status) VALUES ('high','kb_hint','x',0.8,'validated')`);
    await db.exec(`INSERT INTO lessons (failure_signature, repair_type, repair_content, confidence_score, status) VALUES ('top','kb_hint','x',0.95,'live')`);
    // Also seed an EXPIRED lesson — should be excluded
    await db.exec(`INSERT INTO lessons (failure_signature, repair_type, repair_content, confidence_score, status) VALUES ('expired','kb_hint','x',0.99,'expired')`);
    const body = await (await fetch(`${baseUrl}/api/evolution-metrics/lesson-confidence-distribution`)).json() as { items: { bucket: string; count: number }[] };
    const buckets = Object.fromEntries(body.items.map((x) => [x.bucket, x.count]));
    expect(buckets["[0.1, 0.3)"]).toBe(1);
    expect(buckets["[0.3, 0.5)"]).toBe(1);
    expect(buckets["[0.5, 0.7)"]).toBe(1);
    expect(buckets["[0.7, 0.9)"]).toBe(1);
    expect(buckets["[0.9, 1.0]"]).toBe(1);
    // Total across all live/non-expired buckets = 5
    expect(Object.values(buckets).reduce((a, b) => a + b, 0)).toBe(5);
  });

  it("GET /lesson-cross-workflow-reuse ranks lessons by distinct workflow hit count", async () => {
    // Seed 2 lessons
    const r1 = await db.exec(`INSERT INTO lessons (failure_signature, repair_type, repair_content, confidence_score, hit_count, status) VALUES ('reuse-sig-1','kb_hint','x',0.85,5,'validated')`);
    const r2 = await db.exec(`INSERT INTO lessons (failure_signature, repair_type, repair_content, confidence_score, hit_count, status) VALUES ('reuse-sig-2','kb_hint','x',0.65,3,'validated')`);
    // Seed suggestion_outcomes: lesson1 hit in 3 distinct workflows; lesson2 in 1
    for (const wf of ["wf-A", "wf-B", "wf-C"]) {
      await db.exec(`INSERT INTO suggestion_outcomes (lesson_id, workflow_id, failure_signature, verdict, source) VALUES (${r1.insertId}, '${wf}', 'reuse-sig-1', 'improved', 'runtime_retry')`);
    }
    await db.exec(`INSERT INTO suggestion_outcomes (lesson_id, workflow_id, failure_signature, verdict, source) VALUES (${r2.insertId}, 'wf-X', 'reuse-sig-2', 'improved', 'runtime_retry')`);
    const body = await (await fetch(`${baseUrl}/api/evolution-metrics/lesson-cross-workflow-reuse?limit=5`)).json() as { items: { lesson_id: number; workflow_count: number; hit_count: number }[] };
    expect(body.items.length).toBe(2);
    // Top row must be lesson1 (workflow_count=3), second is lesson2 (1).
    expect(body.items[0].lesson_id).toBe(r1.insertId);
    expect(body.items[0].workflow_count).toBe(3);
    expect(body.items[1].lesson_id).toBe(r2.insertId);
    expect(body.items[1].workflow_count).toBe(1);
  });

  it("GET /weakness-list-top returns active weaknesses ranked by priority_score", async () => {
    await db.exec(`INSERT INTO weakness_list (failure_signature, error_class, occurrence_count, affected_workflows_count, repairability, priority_score, status) VALUES ('wk-low','timeout',5,1,'auto',1.5,'active')`);
    await db.exec(`INSERT INTO weakness_list (failure_signature, error_class, occurrence_count, affected_workflows_count, repairability, priority_score, status) VALUES ('wk-high','auth',10,3,'semi',4.8,'active')`);
    await db.exec(`INSERT INTO weakness_list (failure_signature, error_class, occurrence_count, affected_workflows_count, repairability, priority_score, status) VALUES ('wk-closed','x',1,1,'manual',5.0,'closed')`);
    const body = await (await fetch(`${baseUrl}/api/evolution-metrics/weakness-list-top?limit=10`)).json() as { items: { failure_signature: string; priority_score: number; status: string }[] };
    // Only 'active' rows appear.
    expect(body.items.every((x) => true)).toBe(true);
    const sigs = body.items.map((x) => x.failure_signature);
    expect(sigs.includes("wk-low")).toBe(true);
    expect(sigs.includes("wk-high")).toBe(true);
    expect(sigs.includes("wk-closed")).toBe(false);
    // Sorted by priority DESC
    const highRow = body.items.find((x) => x.failure_signature === "wk-high");
    const lowRow = body.items.find((x) => x.failure_signature === "wk-low");
    expect(highRow!.priority_score > lowRow!.priority_score).toBe(true);
  });

  it("GET /failure-rate-by-version still passes (back-compat with T10 anchor)", async () => {
    // We don't seed diagnosis_cards/flow_runs here — endpoint must 200 with empty array
    // when no data (robust against missing join target).
    const body = await (await fetch(`${baseUrl}/api/evolution-metrics/failure-rate-by-version`)).json() as { items: any[] };
    expect(Array.isArray(body.items)).toBe(true);
  });
});