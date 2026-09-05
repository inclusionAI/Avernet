/**
 * External /api/repair-history route tests — G4 (unified proposal CR).
 *
 * Covers GET / (filters), GET /by-source (applied_by aggregation),
 * GET /by-level (L1/L2/L3 aggregation), against a real in-memory SQLite db
 * + real RepairHistoryRepository.
 */
import { describe, it, beforeAll, afterAll, expect } from "vitest";
import express from "express";
import { DatabaseSync } from "node:sqlite";
import { RepairHistoryRepository } from "../../repositories/repair-history-repository.js";
import { createRepairHistoryRouter } from "../repair-history.js";

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

describe("createRepairHistoryRouter (external /api/repair-history)", () => {
  let server: any;
  let baseUrl: string;
  let repo: RepairHistoryRepository;

  beforeAll(async () => {
    const { db } = createTestDb();
    repo = new RepairHistoryRepository(db);
    const app = express();
    app.use(express.json({ limit: "10mb" }));
    const noOpAuth = (_req: any, _res: any, next: (err?: unknown) => void) => next();
    app.use("/api/repair-history", createRepairHistoryRouter(repo, noOpAuth));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv; baseUrl = url;
    // Seed test data
    await repo.insert({ flow_id: "f-1", node_id: "n", failure_signature: "sig-A",
      lesson_id: null, diagnosis_card_id: null, suggestion_outcome_id: null,
      repair_type: "kb_hint", repair_content: "k", applied_by: "guardian",
      retry_success: 1, level: "L1" });
    await repo.insert({ flow_id: "f-2", node_id: "n", failure_signature: "sig-A",
      lesson_id: null, diagnosis_card_id: null, suggestion_outcome_id: null,
      repair_type: "kb_hint", repair_content: "k", applied_by: "guardian",
      retry_success: 0, level: "L2" });
    await repo.insert({ flow_id: "f-3", node_id: "n", failure_signature: "sig-B",
      lesson_id: null, diagnosis_card_id: null, suggestion_outcome_id: null,
      repair_type: "prompt_patch", repair_content: "p", applied_by: "auto_heal",
      retry_success: 1, level: "L2" });
  });
  afterAll(async () => { await close(server); });

  it("GET / lists records filtered by failure_signature", async () => {
    const bySig = await (await fetch(`${baseUrl}/api/repair-history?failureSignature=sig-A`)).json() as { items: any[]; total: number };
    expect(bySig.items.length).toBe(2);
    expect(bySig.total).toBe(2);
    expect(bySig.items.every((x) => x.failure_signature === "sig-A")).toBe(true);
  });

  it("GET / filters by appliedBy and retrySuccess", async () => {
    const byApplied = await (await fetch(`${baseUrl}/api/repair-history?appliedBy=auto_heal`)).json() as { items: any[]; total: number };
    expect(byApplied.items.length).toBe(1);
    expect(byApplied.items[0].applied_by).toBe("auto_heal");

    const bySuccess = await (await fetch(`${baseUrl}/api/repair-history?retrySuccess=true`)).json() as { items: any[]; total: number };
    expect(bySuccess.items.every((x) => x.retry_success === 1)).toBe(true);
    expect(bySuccess.total).toBe(2);
  });

  it("GET /by-source returns aggregation grouped by applied_by", async () => {
    const body = await (await fetch(`${baseUrl}/api/repair-history/by-source`)).json() as { items: { applied_by: string; count: number }[] };
    const guardian = body.items.find((x) => x.applied_by === "guardian");
    expect(guardian?.count).toBe(2);
    const autoheal = body.items.find((x) => x.applied_by === "auto_heal");
    expect(autoheal?.count).toBe(1);
  });

  it("GET /by-level returns L1/L2/L3 aggregation with succeeded count for §10.3 metrics", async () => {
    const body = await (await fetch(`${baseUrl}/api/repair-history/by-level`)).json() as { items: { level: string | null; total: number; succeeded: number }[] };
    const l1 = body.items.find((x) => x.level === "L1");
    expect(l1?.total).toBe(1);
    expect(l1?.succeeded).toBe(1);
    const l2 = body.items.find((x) => x.level === "L2");
    expect(l2?.total).toBe(2);
    expect(l2?.succeeded).toBe(1);
  });
});