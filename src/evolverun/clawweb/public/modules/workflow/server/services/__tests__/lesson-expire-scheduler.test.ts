/**
 * Lesson retire-stale + expire scheduler — G15 (proposal §7.3 / T10 stage-3).
 *
 * Validates three behaviors:
 *   1. LessonRepository.retireStale(90) only retires rows whose
 *      `COALESCE(last_hit_at, gmt_create)` predates the cutoff AND whose
 *      status is in (draft | validated | live). Already-expired rows are
 *      skipped (idempotent — running twice returns 0 the second time).
 *   2. Never-hit lessons are kept if they're brand new (gmt_create recent
 *      past the cutoff), so we don't churn rows we just learned.
 *   3. LessonExpireScheduler boot-protects against inactiveDays < 7 and
 *      exposes runOnce() that delegates to retireStale.
 */
import { describe, it, expect, beforeAll, afterAll } from "vitest";
import { DatabaseSync } from "node:sqlite";
import { LessonRepository } from "../../repositories/lesson-repository.js";
import { LessonExpireScheduler } from "../lesson-expire-scheduler.js";

function createTestDb() {
  const raw = new DatabaseSync(":memory:") as DatabaseSync;
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

describe("LessonRepository.retireStale (G15)", () => {
  let repo: LessonRepository;
  let db: any;

  beforeAll(() => {
    const ctx = createTestDb();
    db = ctx.db;
    repo = new LessonRepository(db);
  });

  it("retires lessons whose last_hit_at is older than the cutoff", async () => {
    // lesson-A: validated, last hit 100 days ago (cutoff at 90d → MUST retire).
    const oldTs = Math.floor(Date.now() / 1000) - 100 * 86400;
    const idA = await repo.insert({ failure_signature: "old-sig-A", repair_type: "kb_hint",
      repair_content: "old", status: "validated", source: "manual" });
    await db.exec(`UPDATE lessons SET last_hit_at = ? WHERE id = ?`, [oldTs, idA]);

    // lesson-B: validated, hit 30 days ago (cutoff at 90d → MUST KEEP).
    const recentTs = Math.floor(Date.now() / 1000) - 30 * 86400;
    const idB = await repo.insert({ failure_signature: "recent-sig-B", repair_type: "kb_hint",
      repair_content: "recent", status: "validated", source: "manual" });
    await db.exec(`UPDATE lessons SET last_hit_at = ? WHERE id = ?`, [recentTs, idB]);

    const affected = await repo.retireStale(90);
    expect(affected).toBe(1);

    const afterA = await repo.getById(idA);
    const afterB = await repo.getById(idB);
    expect(afterA?.status).toBe("expired");
    expect(afterB?.status).toBe("validated");
  });

  it("skips already-expired rows (idempotent)", async () => {
    const oldTs = Math.floor(Date.now() / 1000) - 200 * 86400;
    const id = await repo.insert({ failure_signature: "already-expired-sig", repair_type: "kb_hint",
      repair_content: "x", status: "validated", source: "manual" });
    await db.exec(`UPDATE lessons SET last_hit_at = ?, status = ? WHERE id = ?`, [oldTs, "expired", id]);

    const affected = await repo.retireStale(90);
    expect(affected).toBe(0); // already expired, no-op
  });

  it("preserves brand-new never-hit lessons (gmt_create > cutoff)", async () => {
    // new lesson, never hit. last_hit_at NULL → falls back to gmt_create.
    // gmt_create is `now` which is < 90d cutoff, so it MUST be kept.
    const id = await repo.insert({ failure_signature: "brand-new-sig", repair_type: "kb_hint",
      repair_content: "new", status: "draft", source: "manual" });
    const affected = await repo.retireStale(90);
    expect(affected).toBe(0);
    const after = await repo.getById(id);
    expect(after?.status).toBe("draft");
  });

  it("retires lessons whose status is draft|live|validated when stale, regardless of status variety", async () => {
    const oldTs = Math.floor(Date.now() / 1000) - 365 * 86400;
    const idL = await repo.insert({ failure_signature: "live-stale-sig", repair_type: "kb_hint",
      repair_content: "x", status: "live", source: "manual" });
    await db.exec(`UPDATE lessons SET last_hit_at = ? WHERE id = ?`, [oldTs, idL]);

    const idD = await repo.insert({ failure_signature: "draft-stale-sig", repair_type: "kb_hint",
      repair_content: "x", status: "draft", source: "manual" });
    await db.exec(`UPDATE lessons SET last_hit_at = ? WHERE id = ?`, [oldTs, idD]);

    const affected = await repo.retireStale(90);
    expect(affected).toBe(2);
  });
});

describe("LessonExpireScheduler (G15 scheduler)", () => {
  let repo: LessonRepository;
  let db: any;

  beforeAll(() => {
    const ctx = createTestDb();
    db = ctx.db;
    repo = new LessonRepository(db);
  });

  it("rejects inactiveDays < 7 in constructor", () => {
    expect(() => new LessonExpireScheduler(repo, 6)).toThrow(/inactiveDays=6/);
    expect(() => new LessonExpireScheduler(repo, 1)).toThrow(/must be >= 7/);
  });

  it("runOnce delegates to retireStale and returns affectedRows", async () => {
    const oldTs = Math.floor(Date.now() / 1000) - 200 * 86400;
    const id = await repo.insert({ failure_signature: "scheduler-sig", repair_type: "kb_hint",
      repair_content: "x", status: "validated", source: "manual" });
    await db.exec(`UPDATE lessons SET last_hit_at = ? WHERE id = ?`, [oldTs, id]);

    const scheduler = new LessonExpireScheduler(repo, 90, 86_400_000);
    const affected = await scheduler.runOnce();
    expect(affected).toBe(1);
    scheduler.stop(); // clearing the timer if it was started
  });

  it("runOnce reentrancy guard: concurrent calls return 0 from the waiting caller", async () => {
    // Stub retireStale to block until we release it.
    let release!: () => void;
    const block = new Promise<void>((r) => { release = r; });
    const called = { count: 0 };
    const stubRepo = {
      retireStale: async () => {
        called.count += 1;
        await block;
        return 1;
      },
    } as unknown as LessonRepository;
    const scheduler = new LessonExpireScheduler(stubRepo, 90, 86_400_000);

    // Kick off the first run — it will block on `block`.
    const first = scheduler.runOnce();
    // Second run concurrently: must be no-op'd.
    const second = await scheduler.runOnce();
    expect(second).toBe(0);
    // Release the first.
    release();
    const firstResult = await first;
    expect(firstResult).toBe(1);
    expect(called.count).toBe(1);
    scheduler.stop();
  });
});