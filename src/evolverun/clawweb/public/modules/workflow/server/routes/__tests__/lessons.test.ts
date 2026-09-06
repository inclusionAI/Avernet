/**
 * External /api/lessons route tests — G2 (unified proposal CR).
 *
 * Covers GET /, GET /:id, POST /, PUT /:id, DELETE /:id, POST /search against
 * a real in-memory SQLite db + real LessonRepository, using the native fetch
 * on a random port pattern adopted by weakness-list/self-evolution tests.
 */
import { describe, it, beforeAll, afterAll, expect } from "vitest";
import express from "express";
import { DatabaseSync } from "node:sqlite";
import { LessonRepository } from "../../repositories/lesson-repository.js";
import { createLessonRouter } from "../lessons.js";

type DbSync = DatabaseSync;
const SQLITE = await import("node:sqlite");

function createTestDb(): { db: DbSync } {
  const raw = new SQLITE.DatabaseSync(":memory:") as DbSync;
  raw.exec(`
    CREATE TABLE lessons (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      failure_signature VARCHAR(256) NOT NULL,
      error_class VARCHAR(64), executor_type VARCHAR(64), tool_or_node VARCHAR(128),
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
    CREATE UNIQUE INDEX uk_lessons_sig_type ON lessons (failure_signature, repair_type);
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

describe("createLessonRouter (external /api/lessons)", () => {
  let server: any;
  let baseUrl: string;
  let repo: LessonRepository;

  beforeAll(async () => {
    const { db } = createTestDb();
    repo = new LessonRepository(db);
    const app = express();
    app.use(express.json({ limit: "10mb" }));
    const noOpAuth = (_req: any, _res: any, next: (err?: unknown) => void) => next();
    app.use("/api/lessons", createLessonRouter(repo, noOpAuth));
    const { server: srv, baseUrl: url } = await listenOn(app);
    server = srv; baseUrl = url;
  });
  afterAll(async () => { await close(server); });

  it("POST / creates a draft lesson and returns 201 with id + sig + status", async () => {
    const res = await fetch(`${baseUrl}/api/lessons`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        failure_signature: "timeout · cli-script · curl",
        repair_type: "kb_hint",
        repair_content: "use --max-time 30",
        confidence_score: 0.6,
        status: "draft",
        source: "manual",
      }),
    });
    expect(res.status).toBe(201);
    const body = await res.json() as { id: number; status: string };
    expect(body.id).toBeGreaterThan(0);
    expect(body.status).toBe("draft");
  });

  it("POST / is idempotent on (failure_signature, repair_type)", async () => {
    const payload = { failure_signature: "idemp-sig", repair_type: "kb_hint", repair_content: "v1" };
    const r1 = await (await fetch(`${baseUrl}/api/lessons`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) })).json() as { id: number };
    const r2 = await (await fetch(`${baseUrl}/api/lessons`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...payload, repair_content: "v2" }) })).json() as { id: number };
    expect(r2.id).toBe(r1.id);
    // Verify repair_content was overwritten
    const row = await repo.getById(r1.id);
    expect(row?.repair_content).toBe("v2");
  });

  it("POST / 400s on missing required fields", async () => {
    const res = await fetch(`${baseUrl}/api/lessons`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ failure_signature: "x" }),
    });
    expect(res.status).toBe(400);
  });

  it("GET /:id returns the lesson when it exists, 404 when not", async () => {
    const id = await repo.insert({ failure_signature: "by-id-sig", repair_type: "kb_hint",
      repair_content: "x", status: "validated", source: "manual" });
    const ok = await fetch(`${baseUrl}/api/lessons/${id}`);
    expect(ok.status).toBe(200);
    const body = await ok.json() as { id: number; failure_signature: string };
    expect(body.id).toBe(id);
    expect(body.failure_signature).toBe("by-id-sig");

    const miss = await fetch(`${baseUrl}/api/lessons/9999999`);
    expect(miss.status).toBe(404);
  });

  it("GET / supports status + workflow filters and true total", async () => {
    await repo.insert({ failure_signature: "filter-draft", repair_type: "kb_hint",
      repair_content: "x", status: "draft", related_workflow_ids: JSON.stringify(["wf-A"]),
      source: "manual" });
    await repo.insert({ failure_signature: "filter-live", repair_type: "kb_hint",
      repair_content: "x", status: "live", related_workflow_ids: JSON.stringify(["wf-B"]),
      source: "manual" });
    // Filter by status
    const byStatus = await fetch(`${baseUrl}/api/lessons?status=live`).then(r => r.json()) as { items: any[]; total: number };
    expect(byStatus.items.every((x) => x.status === "live")).toBe(true);
    expect(byStatus.total).toBeGreaterThanOrEqual(1);
    // Filter by workflowId (LIKE match against related_workflow_ids JSON)
    const byWf = await fetch(`${baseUrl}/api/lessons?workflowId=wf-A`).then(r => r.json()) as { items: any[] };
    expect(byWf.items.every((x) => (x.related_workflow_ids ?? "").includes("wf-A"))).toBe(true);
  });

  it("PUT /:id edits fields (status transition + repair_content rewrite)", async () => {
    const id = await repo.insert({ failure_signature: "put-sig", repair_type: "kb_hint",
      repair_content: "old", status: "draft", source: "manual" });
    const res = await fetch(`${baseUrl}/api/lessons/${id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ repair_content: "new", status: "validated" }),
    });
    expect(res.status).toBe(200);
    const after = await repo.getById(id);
    expect(after?.repair_content).toBe("new");
    expect(after?.status).toBe("validated");
  });

  // ── Regression: CR#2/#3 lessons PUT cross-row-write bug ────────────────
  // Previously, PUT /:id called repo.upsert(keyed on failure_signature,
  // repair_type). If the caller changed failure_signature to a value already
  // owned by ANOTHER lesson, the upsert would silently overwrite that other
  // lesson's row — the URL id-anchored row was left untouched, and the
  // 200 response returned stale getById(id) data. This test reproduces the
  // scenario and asserts:
  //   1. PUT /:id at id=A with conflicting failure_signature yields 409.
  //   2. Row B is NOT modified (its repair_content untouched).
  //   3. Row A is NOT modified either (its fields untouched).
  it("PUT /:id does not silently merge two lessons on conflicting sig change (CR#2/#3)", async () => {
    const idA = await repo.insert({ failure_signature: "sig-A", repair_type: "kb_hint",
      repair_content: "content-A", status: "validated", source: "manual" });
    const idB = await repo.insert({ failure_signature: "sig-B", repair_type: "kb_hint",
      repair_content: "content-B-precious", status: "validated", source: "manual" });
    expect(idA).not.toBe(idB);

    // Caller tries to retarget A's failure_signature to "sig-B" — should be rejected.
    const res = await fetch(`${baseUrl}/api/lessons/${idA}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ failure_signature: "sig-B" }),
    });
    expect(res.status).toBe(409);
    const body = await res.json() as { error: string };
    expect(body.error).toBe("conflict");

    // CRITICAL: B has its precious content, A has its original sig.
    const stillA = await repo.getById(idA);
    const stillB = await repo.getById(idB);
    expect(stillA?.failure_signature).toBe("sig-A");
    expect(stillA?.repair_content).toBe("content-A");
    expect(stillB?.failure_signature).toBe("sig-B");
    expect(stillB?.repair_content).toBe("content-B-precious");
  });

  it("PUT /:id updates only fields the caller supplied (partial patch)", async () => {
    const id = await repo.insert({ failure_signature: "partial-sig", repair_type: "kb_hint",
      repair_content: "orig", status: "draft", source: "manual", confidence_score: 0.5 });
    // Patch only confidence — repair_content and status must be untouched.
    const res = await fetch(`${baseUrl}/api/lessons/${id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confidence_score: 0.85 }),
    });
    expect(res.status).toBe(200);
    const after = await repo.getById(id);
    expect(after?.confidence_score).toBe(0.85);
    expect(after?.repair_content).toBe("orig");
    expect(after?.status).toBe("draft");
  });

  it("PUT /:id with empty body returns 400", async () => {
    const id = await repo.insert({ failure_signature: "empty-body-sig", repair_type: "kb_hint",
      repair_content: "x", status: "draft", source: "manual" });
    const res = await fetch(`${baseUrl}/api/lessons/${id}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(400);
  });

  it("DELETE /:id soft-retires the lesson (status=expired, row still present)", async () => {
    const id = await repo.insert({ failure_signature: "del-sig", repair_type: "kb_hint",
      repair_content: "x", status: "validated", source: "manual" });
    const res = await fetch(`${baseUrl}/api/lessons/${id}`, { method: "DELETE" });
    expect(res.status).toBe(200);
    const body = await res.json() as { status: string };
    expect(body.status).toBe("expired");
    // Row still present — soft delete only
    const stillThere = await repo.getById(id);
    expect(stillThere).not.toBeNull();
    expect(stillThere?.status).toBe("expired");
  });

  it("POST /search returns top-N by confidence DESC for a signature substring", async () => {
    const res = await fetch(`${baseUrl}/api/lessons/search`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ failureSignature: "filter", limit: 5 }),
    });
    expect(res.status).toBe(200);
    const body = await res.json() as { items: any[] };
    expect(body.items.length).toBeGreaterThan(0);
    // All returned items should match the LIKE substring 'filter'.
    expect(body.items.every((x) => x.failure_signature.includes("filter"))).toBe(true);
  });

  it("POST /search 400s when failureSignature missing", async () => {
    const res = await fetch(`${baseUrl}/api/lessons/search`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    });
    expect(res.status).toBe(400);
  });
});