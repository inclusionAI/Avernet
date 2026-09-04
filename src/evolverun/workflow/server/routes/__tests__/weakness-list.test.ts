/**
 * Tests for createWeaknessListRouter — the external /api/weakness-list route.
 * Returns active weaknesses sorted by priority_score DESC, with JSON-array
 * (not raw string) outputs for evidence_diagnosis_ids / workflow_ids.
 *
 * Test strategy: mount the router on a real Express app listening on a
 * random port (Node 23 built-in http + fetch), then hit it via HTTP.
 * No supertest dependency; uses Node's native fetch().
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
import express from "express";
import type { Server } from "node:http";
import type { IDatabase } from "../../db.js";
import { sqliteDialect } from "../../db/dialect.js";
import { WeaknessListRepository } from "../../repositories/weakness-list-repository.js";
import { DiagnosisCardRepository } from "../../repositories/diagnosis-card-repository.js";
import { createWeaknessListRouter } from "../weakness-list.js";
import Database from "better-sqlite3";

function createTestDb(): IDatabase {
  const raw = new Database(":memory:");
  raw.exec(`
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
    -- Diagnosis cards table is required for the /:id detail endpoint that
    -- drills out evidence cards when a diagRepo is wired in.
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

describe("GET /api/weakness-list", () => {
  let db: IDatabase;
  let repo: WeaknessListRepository;
  let server: Server;
  let baseUrl: string;

  beforeEach(async () => {
    db = createTestDb();
    repo = new WeaknessListRepository(db);
    await repo.upsert({
      failure_signature: "sig-A", error_class: "timeout", workflow_ids: JSON.stringify(["wf1"]),
      occurrence_count: 5, affected_workflows_count: 1, repairability: "auto",
      priority_score: 1.6, evidence_diagnosis_ids: JSON.stringify([10, 11, 12, 13, 14]),
      latest_occurrence: 1000, first_occurrence: 200, matched_lesson_ids: null, status: "active",
    });
    await repo.upsert({
      failure_signature: "sig-B", error_class: "param-type-mismatch", workflow_ids: JSON.stringify(["wf1", "wf2"]),
      occurrence_count: 3, affected_workflows_count: 2, repairability: "semi",
      priority_score: 2.4, evidence_diagnosis_ids: JSON.stringify([20, 21, 22]),
      latest_occurrence: 2000, first_occurrence: 100, matched_lesson_ids: null, status: "active",
    });
    await repo.upsert({
      failure_signature: "sig-closed", error_class: "auth", workflow_ids: "[]",
      occurrence_count: 1, affected_workflows_count: 1, repairability: "manual",
      priority_score: 5.0, evidence_diagnosis_ids: "[30]",
      latest_occurrence: 3000, first_occurrence: 2900, matched_lesson_ids: null, status: "closed",
    });

    const app = express();
    app.use(express.json());
    app.use("/api/weakness-list", createWeaknessListRouter(repo, (_req, _res, next) => next()));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv;
    baseUrl = url;
  });

  afterEach(async () => { await close(server); });

  it("returns active weaknesses sorted by priority_score DESC, excludes closed", async () => {
    const res = await fetch(`${baseUrl}/api/weakness-list`);
    expect(res.status).toBe(200);
    const body = await res.json() as { items: any[]; total: number; limit: number; offset: number };
    expect(body.items).toHaveLength(2);
    expect(body.items[0].failure_signature).toBe("sig-B");
    expect(body.items[1].failure_signature).toBe("sig-A");
    expect(body.total).toBe(2);
    expect(body.items.find(x => x.failure_signature === "sig-closed")).toBeUndefined();
  });

  it("respects ?limit (only top-N by priority) but still reports the true active total", async () => {
    const res = await fetch(`${baseUrl}/api/weakness-list?limit=1`);
    const body = await res.json() as { items: any[]; total: number };
    expect(res.status).toBe(200);
    expect(body.items).toHaveLength(1);
    expect(body.items[0].failure_signature).toBe("sig-B"); // 2.4 > 1.6
    // total must be the true count of ALL active weaknesses (2), NOT the
    // page-truncated length (1) — otherwise frontend pagination UIs would
    // shrink the page count as users advance past offset.
    expect(body.total).toBe(2);
  });

  it("exposes evidence_diagnosis_ids and workflow_ids as JSON arrays, not raw strings", async () => {
    const res = await fetch(`${baseUrl}/api/weakness-list`);
    const body = await res.json() as { items: any[] };
    const first = body.items[0];
    expect(Array.isArray(first.evidence_diagnosis_ids)).toBe(true);
    expect(first.evidence_diagnosis_ids).toEqual([20, 21, 22]);
    expect(Array.isArray(first.workflow_ids)).toBe(true);
    expect(first.workflow_ids).toEqual(["wf1", "wf2"]);
  });

  it("returns 500 with a clear error on repository failure", async () => {
    const failRepo = {
      listTop: async () => { throw new Error("db down"); },
      countActive: async () => { throw new Error("db down"); },
    } as unknown as WeaknessListRepository;
    const app = express();
    app.use(express.json());
    app.use("/api/weakness-list", createWeaknessListRouter(failRepo, (_req, _res, next) => next()));
    const { server: srv, baseUrl: url } = await listenOn(app);
    try {
      const res = await fetch(`${url}/api/weakness-list`);
      expect(res.status).toBe(500);
      const body = await res.json() as { error: string; message: string };
      expect(body.error).toBe("internal");
      // Promise.all rejects with whichever side fails first; either way the
      // message echoes the mock's "db down" sentinel.
      expect(body.message).toContain("db down");
    } finally {
      await close(srv);
    }
  });
});

describe("GET /api/weakness-list/:id (detail with diagnosis cards drill-down)", () => {
  let db: IDatabase;
  let repo: WeaknessListRepository;
  let diagRepo: DiagnosisCardRepository;
  let server: Server;
  let baseUrl: string;
  let weakId: number;
  let cardIds: number[] = [];

  beforeEach(async () => {
    db = createTestDb();
    repo = new WeaknessListRepository(db);
    diagRepo = new DiagnosisCardRepository(db);
  });

  afterEach(async () => { await close(server); });

  it("GET /:id returns the weakness row + drilled-out diagnosis cards (one round trip)", async () => {
    // Seed 2 diagnosis cards
    cardIds = [];
    for (let i = 0; i < 2; i++) {
      const id = await diagRepo.insert({ flow_id: "f-1", workflow_id: "wf-A", node_id: "n",
        failure_signature: "shared-sig", error_text: "err",
        input_snapshot: null, output_snapshot: null, step_traces_snapshot: null,
        analysis_reasoning: null, suggested_repair_type: "kb_hint", suggested_repair_content: "k",
        outcome: "not_recovered", attempt_count: 1, diagnosis_level: "L1" });
      cardIds.push(id);
    }
    // Seed a weakness row that references both card ids
    await repo.upsert({
      failure_signature: "shared-sig", error_class: "timeout",
      workflow_ids: JSON.stringify(["wf-A"]),
      occurrence_count: 2, affected_workflows_count: 1, repairability: "auto",
      priority_score: 4.0,
      evidence_diagnosis_ids: JSON.stringify(cardIds),
      latest_occurrence: 1000, first_occurrence: 100, matched_lesson_ids: null, status: "active",
    });
    // weakness_list is keyed on UNIQUE(failure_signature), so the upsert above returns void;
    // we lookup the row by getById via a small query side-channel.
    const weakRows = await repo.listTop(50);
    weakId = weakRows[0].id;

    const app = express();
    app.use(express.json());
    app.use("/api/weakness-list", createWeaknessListRouter(repo, (_req, _res, next) => next(), diagRepo));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv; baseUrl = url;

    const res = await fetch(`${baseUrl}/api/weakness-list/${weakId}`);
    expect(res.status).toBe(200);
    const body = await res.json() as { id: number; evidence_diagnosis_ids: number[]; evidence_cards: any[] };
    expect(body.id).toBe(weakId);
    expect(body.evidence_diagnosis_ids.length).toBe(2);
    expect(body.evidence_diagnosis_ids).toEqual(expect.arrayContaining(cardIds));
    expect(body.evidence_cards.length).toBe(2);
  });

  it("GET /:id returns 404 when id does not exist", async () => {
    const app = express();
    app.use(express.json());
    app.use("/api/weakness-list", createWeaknessListRouter(repo, (_req, _res, next) => next(), diagRepo));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv; baseUrl = url;

    const res = await fetch(`${baseUrl}/api/weakness-list/9999999`);
    expect(res.status).toBe(404);
  });

  it("GET /:id returns 400 for non-positive id", async () => {
    const app = express();
    app.use(express.json());
    app.use("/api/weakness-list", createWeaknessListRouter(repo, (_req, _res, next) => next(), diagRepo));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv; baseUrl = url;

    const res = await fetch(`${baseUrl}/api/weakness-list/0`);
    expect(res.status).toBe(400);
  });
});