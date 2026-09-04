/**
 * External /api/diagnosis-cards route tests — G3 (unified proposal CR).
 *
 * Covers GET /, GET /:id against a real in-memory SQLite db + real
 * DiagnosisCardRepository + RepairHistoryRepository.
 */
import { describe, it, beforeAll, afterAll, expect } from "vitest";
import express from "express";
import { DatabaseSync } from "node:sqlite";
import { DiagnosisCardRepository } from "../../repositories/diagnosis-card-repository.js";
import { RepairHistoryRepository } from "../../repositories/repair-history-repository.js";
import { createDiagnosisCardRouter } from "../diagnosis-cards.js";

type DbSync = DatabaseSync;

function createTestDb(): { db: any } {
  const raw = new DatabaseSync(":memory:") as DbSync;
  raw.exec(`
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

describe("createDiagnosisCardRouter (external /api/diagnosis-cards)", () => {
  let server: any;
  let baseUrl: string;
  let diagRepo: DiagnosisCardRepository;
  let repairRepo: RepairHistoryRepository;

  beforeAll(async () => {
    const { db } = createTestDb();
    diagRepo = new DiagnosisCardRepository(db);
    repairRepo = new RepairHistoryRepository(db);
    const app = express();
    app.use(express.json({ limit: "10mb" }));
    const noOpAuth = (_req: any, _res: any, next: (err?: unknown) => void) => next();
    app.use("/api/diagnosis-cards", createDiagnosisCardRouter(diagRepo, repairRepo, noOpAuth));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv; baseUrl = url;
  });
  afterAll(async () => { await close(server); });

  it("GET / lists cards filtered by workflow_id + outcome, total reflects true count", async () => {
    await diagRepo.insert({ flow_id: "f-1", workflow_id: "wf-A", node_id: "n",
      failure_signature: "sig-1", error_text: "e",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      analysis_reasoning: null, suggested_repair_type: null, suggested_repair_content: null,
      outcome: "recovered", attempt_count: 1, diagnosis_level: "L1" });
    await diagRepo.insert({ flow_id: "f-2", workflow_id: "wf-A", node_id: "n",
      failure_signature: "sig-1", error_text: "e",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      analysis_reasoning: null, suggested_repair_type: null, suggested_repair_content: null,
      outcome: "not_recovered", attempt_count: 1, diagnosis_level: "L1" });
    await diagRepo.insert({ flow_id: "f-3", workflow_id: "wf-B", node_id: "n",
      failure_signature: "sig-2", error_text: "e",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      analysis_reasoning: null, suggested_repair_type: null, suggested_repair_content: null,
      outcome: "recovered", attempt_count: 1, diagnosis_level: "L1" });

    const byWf = await (await fetch(`${baseUrl}/api/diagnosis-cards?workflowId=wf-A`)).json() as { items: any[]; total: number };
    expect(byWf.items.length).toBe(2);
    expect(byWf.total).toBe(2);

    const byOutcome = await (await fetch(`${baseUrl}/api/diagnosis-cards?outcome=recovered`)).json() as { items: any[]; total: number };
    expect(byOutcome.items.every((x) => x.outcome === "recovered")).toBe(true);
    expect(byOutcome.total).toBe(2);
  });

  it("GET /:id returns the full card + related repair_history joined by signature", async () => {
    const id = await diagRepo.insert({ flow_id: "f-d", workflow_id: "wf-X", node_id: "n",
      failure_signature: "detail-sig", error_text: "err",
      input_snapshot: JSON.stringify({ x: 1 }), output_snapshot: null, step_traces_snapshot: null,
      analysis_reasoning: "reasoning here", suggested_repair_type: "kb_hint", suggested_repair_content: "k",
      outcome: "recovered", attempt_count: 1, diagnosis_level: "L1" });
    // Seed a related repair_history row using the same signature
    await repairRepo.insert({ flow_id: "f-d", node_id: "n", failure_signature: "detail-sig",
      lesson_id: null, diagnosis_card_id: id, suggestion_outcome_id: null,
      repair_type: "kb_hint", repair_content: "k", applied_by: "guardian",
      retry_success: 1, level: "L1" });
    const res = await fetch(`${baseUrl}/api/diagnosis-cards/${id}`);
    expect(res.status).toBe(200);
    const body = await res.json() as { id: number; related_repair_history: any[] };
    expect(body.id).toBe(id);
    expect(body.related_repair_history.length).toBe(1);
    expect(body.related_repair_history[0].applied_by).toBe("guardian");
  });

  // ── Regression: CR#4 — diagnosis-cards /:id must filter repair_history by
  // the FK diagnosis_card_id, NOT by failure_signature LIKE. Two diagnosis_cards
  // with the SAME signature but DIFFERENT ids must not cross-include each other's
  // repair_history — otherwise the audit trail is misattributed.
  it("GET /:id does not include repair_history of other cards with the same signature (CR#4)", async () => {
    // Card A (id=A) and Card B (id=B) both surface the SAME signature.
    const idA = await diagRepo.insert({ flow_id: "f-A", workflow_id: "wf-X", node_id: "n",
      failure_signature: "shared-sig", error_text: "errA",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      analysis_reasoning: null, suggested_repair_type: null, suggested_repair_content: null,
      outcome: "not_recovered", attempt_count: 1, diagnosis_level: "L2" });
    const idB = await diagRepo.insert({ flow_id: "f-B", workflow_id: "wf-X", node_id: "n",
      failure_signature: "shared-sig", error_text: "errB",
      input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
      analysis_reasoning: null, suggested_repair_type: null, suggested_repair_content: null,
      outcome: "recovered", attempt_count: 1, diagnosis_level: "L2" });
    // Card B produced the actual repair attempt, properly referencing cardB's id.
    await repairRepo.insert({ flow_id: "f-B", node_id: "n", failure_signature: "shared-sig",
      lesson_id: null, diagnosis_card_id: idB, suggestion_outcome_id: null,
      repair_type: "prompt_patch", repair_content: "real fix owned by B",
      applied_by: "guardian", retry_success: 1, level: "L2" });
    // Also seed an unrelated row with the SAME signature but NULL card id
    // (legacy write from before the FK was wired) — must also be excluded.
    await repairRepo.insert({ flow_id: "f-C", node_id: "n", failure_signature: "shared-sig",
      lesson_id: null, diagnosis_card_id: null, suggestion_outcome_id: null,
      repair_type: "alert", repair_content: "orphan row",
      applied_by: "manual", retry_success: null, level: null });

    // Card A's detail: should have ZERO repair_history — its FK chain is empty.
    const resA = await fetch(`${baseUrl}/api/diagnosis-cards/${idA}`);
    const bodyA = await resA.json() as { related_repair_history: any[] };
    expect(bodyA.related_repair_history.length).toBe(0);

    // Card B's detail: only the row that actually belongs to B.
    const resB = await fetch(`${baseUrl}/api/diagnosis-cards/${idB}`);
    const bodyB = await resB.json() as { related_repair_history: any[] };
    expect(bodyB.related_repair_history.length).toBe(1);
    expect(bodyB.related_repair_history[0].repair_content).toBe("real fix owned by B");
  });

  it("GET /:id returns 404 when card not found", async () => {
    const res = await fetch(`${baseUrl}/api/diagnosis-cards/9999999`);
    expect(res.status).toBe(404);
  });

  it("GET /:id 400s on non-positive id", async () => {
    const res = await fetch(`${baseUrl}/api/diagnosis-cards/0`);
    expect(res.status).toBe(400);
  });
});