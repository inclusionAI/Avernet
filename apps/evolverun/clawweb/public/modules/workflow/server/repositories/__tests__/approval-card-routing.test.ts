// @vitest-environment node
import Database from "better-sqlite3";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { ApprovalCardRepository } from "../approval-card-repository.js";

describe("resolved approval-card routing", () => {
  it("filters by bot-id prefix and retains rows without origin metadata", async () => {
    const raw = new Database(":memory:");
    const db = new SqliteDatabase(raw);
    const repo = new ApprovalCardRepository(db);
    raw.exec(`
      CREATE TABLE approval_cards (
        id INTEGER PRIMARY KEY,
        flow_id TEXT NOT NULL,
        status TEXT NOT NULL,
        delivery_mode TEXT NOT NULL,
        resolved_at INTEGER,
        processed_at INTEGER
      );
      CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, origin_bot_id TEXT, status TEXT);
      INSERT INTO approval_cards (id, flow_id, status, delivery_mode, resolved_at) VALUES
        (1, 'flow-a', 'approved', 'card-web', 10),
        (2, 'flow-b', 'rejected', 'card-web', 20),
        (3, 'flow-null', 'approved', 'card-web', 30),
        (4, 'flow-empty', 'approved', 'card-web', 40),
        (5, 'flow-missing-run', 'approved', 'card-web', 50),
        (6, 'flow-pending', 'pending', 'card-web', NULL),
        (7, 'flow-other-mode', 'approved', 'card-dingtalk', 60),
        (8, 'flow-wildcard', 'approved', 'card-web', 70),
        (9, 'flow-wildcard-lookalike', 'approved', 'card-web', 80),
        (10, 'flow-exact-bot', 'approved', 'card-web', 90);
      INSERT INTO flow_runs (flow_id, origin_bot_id) VALUES
        ('flow-a', 'bot-a:owner-1'),
        ('flow-b', 'bot-b:owner-2'),
        ('flow-null', NULL),
        ('flow-empty', ''),
        ('flow-pending', 'bot-a:owner-1'),
        ('flow-other-mode', 'bot-a:owner-1'),
        ('flow-wildcard', 'bot_100%:owner-3'),
        ('flow-wildcard-lookalike', 'botX100anything:owner-4'),
        ('flow-exact-bot', 'bot-a');
    `);

    try {
      expect((await repo.findResolvedCardWeb(50, "bot-a")).map((row) => row.id)).toEqual([10, 5, 4, 3, 1]);
      expect((await repo.findResolvedCardWeb(50, " bot_100% ")).map((row) => row.id)).toEqual([8, 5, 4, 3]);
      expect((await repo.findResolvedCardWeb(50)).map((row) => row.id)).toEqual([10, 9, 8, 5, 4, 3, 2, 1]);
    } finally {
      await db.close();
    }
  });
});

const databases: SqliteDatabase[] = [];
afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all(databases.splice(0).map((db) => db.close()));
});

function consumptionFixture() {
  const raw = new Database(":memory:");
  const db = new SqliteDatabase(raw);
  databases.push(db);
  raw.exec(`
    CREATE TABLE approval_cards (
      id INTEGER PRIMARY KEY, flow_id TEXT NOT NULL, node_id TEXT DEFAULT 'review',
      workflow_id TEXT DEFAULT 'wf-1', workflow_title TEXT, approval_type TEXT,
      message TEXT, card_fields_json TEXT, approver_ids TEXT DEFAULT 'reviewer',
      approver_names TEXT, approval_policy TEXT DEFAULT 'any',
      approved_by TEXT DEFAULT '', rejected_by TEXT DEFAULT '',
      status TEXT NOT NULL, delivery_mode TEXT DEFAULT 'card-web',
      created_at INTEGER DEFAULT 100, resolved_at INTEGER DEFAULT 200,
      processed_at INTEGER, comment TEXT, gmt_modified INTEGER DEFAULT 100
    );
    CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, origin_bot_id TEXT, status TEXT);
  `);
  const add = (id: number, status: string, flowStatus?: string | null, owner: string | null = "bot-a:owner") => {
    raw.prepare(`INSERT INTO approval_cards
      (id, flow_id, status, approved_by, rejected_by, comment, card_fields_json)
      VALUES (?, ?, ?, ?, ?, 'review note', '[{"label":"task","value":"T-1"}]')`)
      .run(id, `flow-${id}`, status, status === "approved" ? "reviewer" : "", status === "rejected" ? "reviewer" : "");
    if (flowStatus !== undefined) {
      raw.prepare("INSERT INTO flow_runs (flow_id, origin_bot_id, status) VALUES (?, ?, ?)")
        .run(`flow-${id}`, owner, flowStatus);
    }
  };
  return { raw, db, repo: new ApprovalCardRepository(db), add };
}

const terminalStatuses = ["succeeded", "failed", "cancelled", "canceled", "completed"];

describe("resolved approval-card consumption", () => {
  it.each([undefined, "bot-a"])("excludes only confirmed terminal and acknowledged cards (%s)", async (botId) => {
    const { raw, repo, add } = consumptionFixture();
    let id = 0;
    for (const status of terminalStatuses) {
      for (const decision of ["approved", "rejected"]) add(++id, decision, status);
    }
    // Terminal rows without attribution must not leak through the historical fallback.
    add(++id, "approved", "completed", null);
    const expected: number[] = [];
    for (const status of [null, "unknown", "waiting", "running", "blocked", undefined]) {
      for (const decision of ["approved", "rejected"]) {
        add(++id, decision, status);
        expected.push(id);
      }
    }
    // A missing owner is not proof of consumption.
    add(++id, "approved", "waiting", null);
    expected.push(id);
    add(++id, "rejected", "blocked", "");
    expected.push(id);
    add(++id, "approved", "running");
    raw.prepare("UPDATE approval_cards SET processed_at = 300 WHERE id = ?").run(id);
    add(++id, "pending", "waiting");
    add(++id, "expired", "waiting");
    add(++id, "approved", "running");
    raw.prepare("UPDATE approval_cards SET delivery_mode = 'card-dingtalk' WHERE id = ?").run(id);
    const history = raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
    const flows = raw.prepare("SELECT * FROM flow_runs ORDER BY flow_id").all();

    const rows = await repo.findResolvedCardWeb(100, botId);
    expect(rows.map((row) => row.id).sort((a, b) => a - b)).toEqual(expected);
    expect(rows.every((row) => row.processed_at === null)).toBe(true);
    expect(raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
    expect(raw.prepare("SELECT * FROM flow_runs ORDER BY flow_id").all()).toEqual(flows);
  });

  it.each([undefined, "bot-a"])("filters before applying the result limit (%s)", async (botId) => {
    const { raw, repo, add } = consumptionFixture();
    add(1, "approved", "waiting");
    add(2, "rejected", "running");
    add(3, "approved", "completed");
    add(4, "rejected", "blocked");
    raw.exec("UPDATE approval_cards SET resolved_at = id * 100; UPDATE approval_cards SET processed_at = 500 WHERE id = 4");
    expect((await repo.findResolvedCardWeb(1, botId)).map((row) => row.id)).toEqual([2]);
  });

  it.each(["approved", "rejected"])("durably and idempotently acknowledges %s without rewriting history", async (status) => {
    const { raw, db, repo, add } = consumptionFixture();
    add(1, status, "waiting");
    const before = await repo.findById(1);
    const clock = vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_999);
    expect(await repo.markProcessed(1, true)).toBe(true);
    const freshRepo = new ApprovalCardRepository(db);
    expect(await freshRepo.findById(1)).toEqual({ ...before, processed_at: 1_800_000_000 });
    for (const botId of [undefined, "bot-a"]) expect(await freshRepo.findResolvedCardWeb(50, botId)).toEqual([]);

    clock.mockReturnValue(1_800_000_100_000);
    expect(await freshRepo.markProcessed(1, true)).toBe(true);
    expect(await freshRepo.findByFlowId("flow-1")).toEqual([{ ...before, processed_at: 1_800_000_000 }]);
    expect(raw.prepare("SELECT COUNT(*) AS count FROM approval_cards").get()).toEqual({ count: 1 });
  });

  it.each([undefined, "bot-a"])("redelivers reopened unconsumed runs but not acknowledged runs (%s)", async (botId) => {
    const { raw, repo, add } = consumptionFixture();
    add(1, "approved", "completed");
    add(2, "rejected", "failed");
    await repo.markProcessed(2, true);
    const history = raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
    expect(await repo.findResolvedCardWeb(50, botId)).toEqual([]);
    raw.exec("UPDATE flow_runs SET status = 'running'");
    expect((await repo.findResolvedCardWeb(50, botId)).map((row) => row.id)).toEqual([1]);
    expect(raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
  });

  it("still permits acknowledgment of a resolved historical terminal card", async () => {
    const { repo, add } = consumptionFixture();
    add(1, "rejected", "failed");
    expect(await repo.findResolvedCardWeb()).toEqual([]);
    expect(await repo.markProcessed(1, true)).toBe(true);
    expect((await repo.findById(1))?.processed_at).toEqual(expect.any(Number));
  });

  it.each(["pending", "expired", "other-mode", "missing"])("does not acknowledge %s cards", async (kind) => {
    const { raw, repo, add } = consumptionFixture();
    if (kind !== "missing") add(1, kind === "other-mode" ? "approved" : kind, "waiting");
    if (kind === "other-mode") raw.exec("UPDATE approval_cards SET delivery_mode = 'card-dingtalk', processed_at = 50");
    const before = raw.prepare("SELECT * FROM approval_cards").all();
    expect(await repo.markProcessed(1, true)).toBe(false);
    expect(raw.prepare("SELECT * FROM approval_cards").all()).toEqual(before);
  });

  it.each([0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
    "rejects invalid repository acknowledgment IDs without a database write (%s)", async (id) => {
      const { db, repo, add } = consumptionFixture();
      add(1, "approved", "waiting");
      const exec = vi.spyOn(db, "exec");
      const query = vi.spyOn(db, "query");
      expect(await repo.markProcessed(id, true)).toBe(false);
      expect(exec).not.toHaveBeenCalled();
      expect(query).not.toHaveBeenCalled();
    },
  );

  it("verifies persisted consumption when MySQL reports zero affected rows on a repeated ack", async () => {
    const { db, repo, add } = consumptionFixture();
    add(1, "approved", "waiting");
    await repo.markProcessed(1, true);
    const before = await repo.findById(1);
    // MySQL may report changed rows, not matched rows, on an unchanged COALESCE update.
    const exec = db.exec.bind(db);
    vi.spyOn(db, "exec").mockImplementation(async (sql, params) => {
      await exec(sql, params);
      return { affectedRows: 0 };
    });
    const query = vi.spyOn(db, "query");
    expect(await repo.markProcessed(1, true)).toBe(true);
    expect(query).toHaveBeenCalled();
    expect(await repo.findById(1)).toEqual(before);
  });

  it("does not report a successful no-op write without a persisted acknowledgment", async () => {
    const { db, repo, add } = consumptionFixture();
    add(1, "approved", "waiting");
    vi.spyOn(db, "exec").mockResolvedValue({ affectedRows: 0 });
    expect(await repo.markProcessed(1, true)).toBe(false);
    expect((await repo.findById(1))?.processed_at).toBeNull();
  });

  it("propagates write and acknowledgment-verification failures", async () => {
    const { db, repo, add } = consumptionFixture();
    add(1, "approved", "waiting");
    const exec = vi.spyOn(db, "exec").mockRejectedValue(new Error("write unavailable"));
    await expect(repo.markProcessed(1, true)).rejects.toThrow("write unavailable");
    exec.mockResolvedValue({ affectedRows: 0 });
    vi.spyOn(db, "query").mockRejectedValue(new Error("verification unavailable"));
    await expect(repo.markProcessed(1, true)).rejects.toThrow("verification unavailable");
  });
});

describe("legacy approval-card acknowledgment", () => {
  it.each([
    ["approved", "waiting"], ["rejected", "waiting"],
    ["approved", "completed"], ["rejected", "failed"],
  ])("validates %s on a %s flow by reading only", async (status, flowStatus) => {
    const { raw, db, repo, add } = consumptionFixture();
    add(1, status, flowStatus);
    const history = raw.prepare("SELECT * FROM approval_cards").all();
    const flows = raw.prepare("SELECT * FROM flow_runs").all();
    const exec = vi.spyOn(db, "exec");
    const query = vi.spyOn(db, "query");

    expect(await repo.markProcessed(1)).toBe(true);
    expect(await repo.markProcessed(1, false)).toBe(true);
    expect(query).toHaveBeenCalledTimes(2);
    expect(exec).not.toHaveBeenCalled();
    expect(raw.prepare("SELECT * FROM approval_cards").all()).toEqual(history);
    expect(raw.prepare("SELECT * FROM flow_runs").all()).toEqual(flows);
    if (flowStatus === "waiting") {
      expect((await repo.findResolvedCardWeb()).map((row) => row.id)).toEqual([1]);
    }
  });

  it.each(["approved", "rejected"])("leaves a durable %s timestamp intact on later legacy ACKs", async (status) => {
    const { raw, db, repo, add } = consumptionFixture();
    add(1, status, "completed");
    vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_999);
    expect(await repo.markProcessed(1, true)).toBe(true);
    const history = raw.prepare("SELECT * FROM approval_cards").all();
    const exec = vi.spyOn(db, "exec");
    expect(await repo.markProcessed(1)).toBe(true);
    expect(await repo.markProcessed(1, false)).toBe(true);
    expect(exec).not.toHaveBeenCalled();
    expect(raw.prepare("SELECT * FROM approval_cards").all()).toEqual(history);
    expect((await repo.findById(1))?.processed_at).toBe(1_800_000_000);
  });

  it.each(["pending", "expired", "other-mode", "missing"])("rejects %s without writing", async (kind) => {
    const { raw, db, repo, add } = consumptionFixture();
    if (kind !== "missing") add(1, kind === "other-mode" ? "approved" : kind, "waiting");
    if (kind === "other-mode") raw.exec("UPDATE approval_cards SET delivery_mode = 'card-dingtalk', processed_at = 50");
    const history = raw.prepare("SELECT * FROM approval_cards").all();
    const exec = vi.spyOn(db, "exec");
    const query = vi.spyOn(db, "query");
    expect(await repo.markProcessed(1)).toBe(false);
    expect(query).toHaveBeenCalledTimes(1);
    expect(exec).not.toHaveBeenCalled();
    expect(raw.prepare("SELECT * FROM approval_cards").all()).toEqual(history);
  });

  it.each([0, -1, 1.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1])(
    "rejects invalid legacy IDs without database calls (%s)", async (id) => {
      const { db, repo } = consumptionFixture();
      const exec = vi.spyOn(db, "exec");
      const query = vi.spyOn(db, "query");
      expect(await repo.markProcessed(id)).toBe(false);
      expect(exec).not.toHaveBeenCalled();
      expect(query).not.toHaveBeenCalled();
    },
  );

  it("propagates legacy eligibility read errors without attempting a write", async () => {
    const { db, repo, add } = consumptionFixture();
    add(1, "approved", "waiting");
    const exec = vi.spyOn(db, "exec");
    vi.spyOn(db, "query").mockRejectedValue(new Error("legacy read unavailable"));
    await expect(repo.markProcessed(1)).rejects.toThrow("legacy read unavailable");
    expect(exec).not.toHaveBeenCalled();
  });
});
