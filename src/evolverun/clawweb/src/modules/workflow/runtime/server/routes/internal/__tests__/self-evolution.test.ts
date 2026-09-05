/**
 * Tests for createInternalSelfEvolutionRouter — ClawMind → clawweb decoupled
 * writes for the self-evolution experience loop. Routes are mounted under
 * /api/internal/self-evolution and protected by the parent signature middleware.
 *
 * Endpoints covered:
 *   POST /lessons              — upsert a (draft|validated) lesson
 *   GET  /lessons/:sig/recall  — recall a validated/live lesson above threshold
 *   POST /lessons/:id/outcome  — record an outcome (improved|neutral|regressed)
 *   POST /diagnosis-cards      — persist a diagnosis card (L1/L2/L3 observed failure)
 *   PATCH /diagnosis-cards/:id — update outcome (recovered|not_recovered|escalated)
 *   POST /repair-history       — record a repair attempt ledger row
 *   POST /suggestion-outcomes  — record an outcome aligned with a lesson
 *
 * Test strategy: build a real Express app with all 4 repos wired into an
 * in-memory SQLite DB, listen on a random port, and hit it via Node fetch.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach } from "vitest";
import express from "express";
import type { Server } from "node:http";
import type { IDatabase } from "../../../db.js";
import { sqliteDialect } from "../../../db/dialect.js";
import { LessonRepository } from "../../../repositories/lesson-repository.js";
import { DiagnosisCardRepository } from "../../../repositories/diagnosis-card-repository.js";
import { RepairHistoryRepository } from "../../../repositories/repair-history-repository.js";
import { SuggestionOutcomeRepository } from "../../../repositories/suggestion-outcome-repository.js";
import { createInternalSelfEvolutionRouter } from "../self-evolution.js";
import Database from "better-sqlite3";

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
    CREATE UNIQUE INDEX uk_lessons_sig_type ON lessons (failure_signature, repair_type);

    CREATE TABLE diagnosis_cards (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      flow_id VARCHAR(128) NOT NULL, workflow_id VARCHAR(255) NOT NULL, node_id VARCHAR(255) NOT NULL,
      failure_signature VARCHAR(256) NOT NULL, error_text TEXT NOT NULL,
      input_snapshot TEXT, output_snapshot TEXT, step_traces_snapshot TEXT,
      analysis_reasoning TEXT,
      suggested_repair_type VARCHAR(32), suggested_repair_content TEXT,
      matched_lesson_id INTEGER, outcome VARCHAR(16) NOT NULL DEFAULT 'not_recovered',
      attempt_count INTEGER NOT NULL DEFAULT 1, diagnosis_level VARCHAR(8),
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );

    CREATE TABLE repair_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      flow_id VARCHAR(128) NOT NULL, node_id VARCHAR(255) NOT NULL, failure_signature VARCHAR(256) NOT NULL,
      lesson_id INTEGER, diagnosis_card_id INTEGER, suggestion_outcome_id INTEGER,
      repair_type VARCHAR(32) NOT NULL, repair_content TEXT,
      applied_by VARCHAR(16) NOT NULL, retry_success INTEGER, level VARCHAR(8),
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );

    CREATE TABLE suggestion_outcomes (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      lesson_id INTEGER NOT NULL, workflow_id VARCHAR(255) NOT NULL, node_id VARCHAR(255),
      failure_signature VARCHAR(256) NOT NULL, adopted INTEGER NOT NULL DEFAULT 0,
      applied_version VARCHAR(64), metrics_before TEXT, metrics_after TEXT,
      verdict VARCHAR(16) NOT NULL, source VARCHAR(32) NOT NULL,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
  `);
  return {
    dbType: "sqlite", dialect: sqliteDialect,
    query: async <T>(sql: string, params?: unknown[]) => {
      const stmt = raw.prepare(sql);
      const rows = params ? stmt.all(...(params as never[])) : stmt.all();
      return rows as T[];
    },
    exec: async (sql: string, params?: unknown[]) => {
      const stmt = raw.prepare(sql);
      const r = params ? stmt.run(...(params as never[])) : stmt.run();
      return { affectedRows: r.changes, insertId: r.lastInsertRowid as number };
    },
    transaction: async <T>(fn: (db: IDatabase) => Promise<T>) => raw.transaction(() => fn(createTestDb()))(),
    close: async () => { raw.close(); },
  };
}

function listenOn(app: express.Express): Promise<{ server: Server; baseUrl: string }> {
  return new Promise((resolve) => {
    const server = app.listen(0, () => {
      const addr = server.address();
      const port = typeof addr === "object" && addr ? addr.port : 0;
      resolve({ server, baseUrl: `http://127.0.0.1:${port}` });
    });
  });
}

function close(server: Server): Promise<void> {
  return new Promise((resolve) => server.close(() => resolve()));
}

async function postJson(baseUrl: string, path: string, body: unknown) {
  return fetch(`${baseUrl}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}
async function patchJson(baseUrl: string, path: string, body: unknown) {
  return fetch(`${baseUrl}${path}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

describe("createInternalSelfEvolutionRouter", () => {
  let server: Server;
  let baseUrl: string;
  let lessonRepo: LessonRepository;
  let diagRepo: DiagnosisCardRepository;

  beforeAll(async () => {
    const db = createTestDb();
    lessonRepo = new LessonRepository(db);
    diagRepo = new DiagnosisCardRepository(db);
    const repairRepo = new RepairHistoryRepository(db);
    const outcomeRepo = new SuggestionOutcomeRepository(db);
    const app = express();
    app.use(express.json({ limit: "10mb" }));
    app.use("/api/internal/self-evolution", createInternalSelfEvolutionRouter({
      lessonRepo, diagnosisCardRepo: diagRepo, repairHistoryRepo: repairRepo, suggestionOutcomeRepo: outcomeRepo,
    }));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv;
    baseUrl = url;
  });

  afterAll(async () => { await close(server); });

  beforeEach(async () => {
    // sanity: ensure repos start clean (in-memory db is per-suite, so nothing to clear)
  });

  it("POST /lessons upserts a draft lesson and returns 201 with id, sig, status", async () => {
    const sig = "param-type-mismatch · mcp-call · fetch_data";
    const res = await postJson(baseUrl, "/api/internal/self-evolution/lessons", {
      failure_signature: sig,
      error_class: "param-type-mismatch",
      executor_type: "mcp-call",
      tool_or_node: "fetch_data",
      repair_type: "kb_hint",
      repair_content: "domain_id must be int; cast via int(domain_id)",
      confidence_score: 0.5,
      status: "draft",
      source: "auto_heal",
      evidence_run_ids: JSON.stringify(["flow-1"]),
    });
    expect(res.status).toBe(201);
    const body = await res.json() as { id: number; failure_signature: string; status: string };
    expect(body.id).toBeGreaterThan(0);
    expect(body.failure_signature).toBe(sig);
    expect(body.status).toBe("draft");
  });

  it("POST /lessons is idempotent on (signature, repair_type)", async () => {
    const sig = "timeout · cli-script · curl";
    const first = await postJson(baseUrl, "/api/internal/self-evolution/lessons", {
      failure_signature: sig, repair_type: "kb_hint", repair_content: "use --max-time 30",
      confidence_score: 0.5, status: "draft", source: "manual",
    });
    const second = await postJson(baseUrl, "/api/internal/self-evolution/lessons", {
      failure_signature: sig, repair_type: "kb_hint", repair_content: "use --max-time 60",
      confidence_score: 0.5, status: "draft", source: "manual",
    });
    expect(second.status).toBe(201);
    expect((await first.json() as any).id).toBe((await second.json() as any).id);
  });

  it("returns 400 when failure_signature or repair_type missing on POST /lessons", async () => {
    const res = await postJson(baseUrl, "/api/internal/self-evolution/lessons", {
      failure_signature: "missing-repair-type",
      // repair_type intentionally omitted
      repair_content: "x",
      confidence_score: 0.5,
      status: "draft",
      source: "manual",
    });
    expect(res.status).toBe(400);
    const body = await res.json();
    expect((body as any).error).toBe("bad_request");
    expect(String((body as any).message)).toContain("failure_signature");
    expect(String((body as any).message)).toContain("repair_type");
  });

  it("GET /lessons/:sig/recall returns the highest-confidence validated/live lesson above threshold", async () => {
    const sig = "recall-test-sig";
    await lessonRepo.insert({ failure_signature: sig, repair_type: "kb_hint", repair_content: "low",
      confidence_score: 0.4, status: "validated", source: "manual" });
    await lessonRepo.insert({ failure_signature: sig, repair_type: "prompt_patch", repair_content: "high",
      confidence_score: 0.9, status: "validated", source: "manual" });
    await lessonRepo.insert({ failure_signature: sig, repair_type: "arg_template_fix", repair_content: "low-status",
      confidence_score: 0.95, status: "draft", source: "manual" }); // below status filter

    const res = await fetch(`${baseUrl}/api/internal/self-evolution/lessons/${encodeURIComponent(sig)}/recall?min_confidence=0.6`);
    expect(res.status).toBe(200);
    const body = await res.json() as { lesson: any };
    expect(body.lesson).not.toBeNull();
    expect(body.lesson.failure_signature).toBe(sig);
    expect(body.lesson.repair_type).toBe("prompt_patch"); // highest-validated
    expect(body.lesson.confidence_score).toBeCloseTo(0.9, 5);
  });

  it("GET /lessons/:sig/recall returns 404 when no validated/live lesson above threshold", async () => {
    const res = await fetch(`${baseUrl}/api/internal/self-evolution/lessons/${encodeURIComponent("no-such-sig")}/recall?min_confidence=0.9`);
    expect(res.status).toBe(404);
  });

  it("POST /lessons/:id/outcome records improved verdict and returns 201", async () => {
    const id = await lessonRepo.insert({ failure_signature: "out-sig", repair_type: "kb_hint",
      repair_content: "x", confidence_score: 0.5, status: "validated", source: "manual" });
    const res = await postJson(baseUrl, `/api/internal/self-evolution/lessons/${id}/outcome`, {
      workflow_id: "wf-x", node_id: "fetch", failure_signature: "out-sig",
      adopted: true, verdict: "improved", source: "runtime_retry",
    });
    expect(res.status).toBe(201);
    const body = await res.json() as { id: number };
    expect(body.id).toBeGreaterThan(0);
    // lesson's confidence should have trended up after success
    const rows = await lessonRepo.listBySignature("out-sig");
    expect(rows[0].confidence_score).toBeGreaterThan(0.5);
  });

  it("POST /lessons/:id/outcome uses the lesson's OWN signature (looked up by id), not body.failure_signature", async () => {
    // Insert a lesson whose actual failure_signature is "real-sig".
    const id = await lessonRepo.insert({ failure_signature: "real-sig", repair_type: "kb_hint",
      repair_content: "x", confidence_score: 0.5, status: "validated", source: "manual" });
    // POST an outcome with a WRONG (or missing) failure_signature — the server
    // must use the lesson row's own signature, so the subsequent outcome row
    // should still land against "real-sig".
    const res = await postJson(baseUrl, `/api/internal/self-evolution/lessons/${id}/outcome`, {
      workflow_id: "wf-x", node_id: "fetch",
      // failure_signature intentionally NOT sent — api-mode behavior
      adopted: true, verdict: "improved", source: "runtime_retry",
    });
    expect(res.status).toBe(201);
    // Re-list by the real signature and verify the confidence trended up,
    // which only happens if the server applied outcome to the correct row.
    const rows = await lessonRepo.listBySignature("real-sig");
    expect(rows[0].confidence_score).toBeGreaterThan(0.5);
  });

  it("POST /lessons/:id/outcome returns 404 when lesson id does not exist", async () => {
    const res = await postJson(baseUrl, `/api/internal/self-evolution/lessons/9999999/outcome`, {
      workflow_id: "wf-x", node_id: "n", failure_signature: "x",
      adopted: true, verdict: "improved", source: "runtime_retry",
    });
    expect(res.status).toBe(404);
  });

  it("POST /diagnosis-cards inserts a card and returns 201 with id", async () => {
    const res = await postJson(baseUrl, "/api/internal/self-evolution/diagnosis-cards", {
      flow_id: "flow-1", workflow_id: "wf-1", node_id: "fetch",
      failure_signature: "diag-sig", error_text: "boom",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      analysis_reasoning: "L2 saw it was ambiguous",
      suggested_repair_type: "prompt_patch", suggested_repair_content: "be explicit",
      matched_lesson_id: null, outcome: "not_recovered", attempt_count: 2,
      diagnosis_level: "L2",
    });
    expect(res.status).toBe(201);
    const body = await res.json() as { id: number };
    expect(body.id).toBeGreaterThan(0);
  });

  it("POST /diagnosis-cards returns 400 on missing required fields", async () => {
    const res = await postJson(baseUrl, "/api/internal/self-evolution/diagnosis-cards", {
      flow_id: "f", workflow_id: "wf", node_id: "n",
      // failure_signature + error_text missing
      outcome: "not_recovered", attempt_count: 1,
    });
    expect(res.status).toBe(400);
  });

  it("PATCH /diagnosis-cards/:id updates outcome and matched_lesson_id, returns 200", async () => {
    // Insert the card via the SAME repo the router uses (outer-scope diagRepo) so
    // the PATCH actually hits an existing row; the old test created the row in a
    // different in-memory db so the row never existed from the router's POV.
    const id = await diagRepo.insert({
      flow_id: "f-2", workflow_id: "wf-2", node_id: "n-2",
      failure_signature: "patch-sig", error_text: "e",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      analysis_reasoning: null, suggested_repair_type: null, suggested_repair_content: null,
      outcome: "not_recovered", attempt_count: 1, diagnosis_level: "L1",
    });
    const res = await patchJson(baseUrl, `/api/internal/self-evolution/diagnosis-cards/${id}`, {
      outcome: "recovered",
      matched_lesson_id: 42,
    });
    expect(res.status).toBe(200);
    const body = await res.json() as { id: number; outcome: string };
    expect(body.id).toBe(id);
    expect(body.outcome).toBe("recovered");
  });

  it("PATCH /diagnosis-cards/:id returns 404 when the card id does not exist (not a phantom 200)", async () => {
    // updateOutcome returns affectedRows=0 for a missing id; the route uses
    // that to emit a proper 404 instead of a 200 with body.id echoed back.
    const res = await patchJson(baseUrl, `/api/internal/self-evolution/diagnosis-cards/9999999`, {
      outcome: "recovered",
    });
    expect(res.status).toBe(404);
    const body = await res.json() as { error: string };
    expect(body.error).toBe("not_found");
  });

  it("POST /repair-history records a guardian ledger row and returns 201", async () => {
    const lessonId = await lessonRepo.insert({ failure_signature: "rh-sig", repair_type: "kb_hint",
      repair_content: "k", confidence_score: 0.5, status: "draft", source: "manual" });
    const res = await postJson(baseUrl, "/api/internal/self-evolution/repair-history", {
      flow_id: "flow-rh", node_id: "fetch", failure_signature: "rh-sig",
      lesson_id: lessonId, diagnosis_card_id: null, suggestion_outcome_id: null,
      repair_type: "kb_hint", repair_content: "k",
      applied_by: "guardian", retry_success: 1, level: "L1",
    });
    expect(res.status).toBe(201);
    const body = await res.json() as { id: number };
    expect(body.id).toBeGreaterThan(0);
  });

  it("POST /repair-history returns 400 when flow_id or failure_signature missing", async () => {
    const res = await postJson(baseUrl, "/api/internal/self-evolution/repair-history", {
      node_id: "fetch", repair_type: "kb_hint", applied_by: "manual", level: "L1",
    });
    expect(res.status).toBe(400);
  });

  it("POST /suggestion-outcomes inserts and returns 201", async () => {
    const id = await lessonRepo.insert({ failure_signature: "so-sig", repair_type: "kb_hint",
      repair_content: "k", confidence_score: 0.5, status: "validated", source: "manual" });
    const res = await postJson(baseUrl, `/api/internal/self-evolution/suggestion-outcomes`, {
      lesson_id: id, workflow_id: "wf-x", node_id: "n", failure_signature: "so-sig",
      adopted: true, applied_version: null, metrics_before: null, metrics_after: null,
      verdict: "improved", source: "batch_patch",
    });
    expect(res.status).toBe(201);
    const body = await res.json() as { id: number };
    expect(body.id).toBeGreaterThan(0);
  });
});