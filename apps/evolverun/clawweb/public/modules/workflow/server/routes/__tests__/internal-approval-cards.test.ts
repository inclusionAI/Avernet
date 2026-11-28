// @vitest-environment node
import { afterEach, describe, expect, it, vi } from "vitest";
import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import express from "express";
import type { Server } from "node:http";
import { createInternalApprovalCardsRouter, invalidateResolvedCache } from "../internal/approval-cards";
import { ApprovalCardRepository } from "../../repositories/approval-card-repository";

const servers: Server[] = [];
const databases: SqliteDatabase[] = [];

afterEach(async () => {
  invalidateResolvedCache();
  vi.restoreAllMocks();
  await Promise.all(
    servers.splice(0).map(
      (s) => new Promise<void>((resolve, reject) => s.close((err) => (err ? reject(err) : resolve()))),
    ),
  );
  await Promise.all(databases.splice(0).map((db) => db.close()));
});

function resolvedCard(id: number, flowId: string) {
  return {
    id,
    flow_id: flowId,
    node_id: "review",
    workflow_id: "wf-1",
    workflow_title: null,
    approval_type: null,
    message: null,
    card_fields_json: null,
    approver_ids: "reviewer",
    approver_names: null,
    approval_policy: "any",
    approved_by: "reviewer",
    rejected_by: "",
    status: "approved",
    delivery_mode: "card-web",
    created_at: 100,
    resolved_at: 200,
    processed_at: null,
    comment: null,
  };
}

type InsertCall = {
  sql: string;
  params: unknown[];
};

function makeRepo(opts: {
  existingPending?: { id: number; flow_id: string; node_id: string };
  insertId?: number;
}) {
  const inserts: InsertCall[] = [];
  const queries: InsertCall[] = [];

  const db = {
    query: async (sql: string, params: unknown[]) => {
      queries.push({ sql, params });
      if (
        opts.existingPending &&
        sql.includes("flow_id = ?") &&
        sql.includes("node_id = ?") &&
        sql.includes("status = 'pending'")
      ) {
        return [{
          id: opts.existingPending.id,
          flow_id: opts.existingPending.flow_id,
          node_id: opts.existingPending.node_id,
          workflow_id: "wf-1",
          workflow_title: null,
          approval_type: null,
          message: null,
          card_fields_json: null,
          approver_ids: "reviewer",
          approver_names: null,
          approval_policy: "any",
          approved_by: "",
          rejected_by: "",
          status: "pending",
          delivery_mode: "card-web",
          created_at: 100,
          resolved_at: null,
          processed_at: null,
          comment: null,
        }];
      }
      return [];
    },
    exec: async (sql: string, params: unknown[]) => {
      inserts.push({ sql, params });
      return { insertId: opts.insertId ?? 42, affectedRows: 1 };
    },
  };

  return {
    repo: new ApprovalCardRepository(db as any),
    inserts,
    queries,
  };
}

async function createServer(repo: ApprovalCardRepository, strictJson = true) {
  const app = express();
  app.use(express.json({ strict: strictJson }));
  app.use("/approval-cards", createInternalApprovalCardsRouter(repo));
  const server = app.listen(0, "127.0.0.1");
  servers.push(server);
  await new Promise<void>((resolve) => server.once("listening", resolve));
  const base = `http://127.0.0.1:${(server.address() as any).port}/approval-cards`;
  return { app, server, base };
}

const validPayload = {
  flow_id: "flow-1",
  node_id: "review",
  workflow_id: "wf-1",
  approver_ids: "reviewer",
};

describe("POST /approval-cards", () => {
  it("creates a new card when no pending card exists", async () => {
    const { repo, inserts, queries } = makeRepo({ insertId: 123 });
    const { base } = await createServer(repo);

    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(validPayload),
    });

    expect(res.status).toBe(201);
    expect(await res.json()).toEqual({ success: true, data: { id: 123 } });
    expect(queries.length).toBe(1);
    expect(inserts.length).toBe(1);
  });

  it.each([
    ["001,002,003", "Alex,,Chris", "001,002,003", "Alex,002,Chris"],
    ["001,002,003", ", Chris ,", "001,002,003", "001,Chris,003"],
    ["001,,003", "Alex,Ignored,Chris", "001,003", "Alex,Chris"],
    ["001,002", undefined, "001,002", "001,002"],
  ])("keeps names aligned with IDs (%s, %s)", async (ids, names, savedIds, savedNames) => {
    const { repo, inserts } = makeRepo({});
    const { base } = await createServer(repo);
    const res = await fetch(base, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...validPayload, approver_ids: ids, approver_names: names }),
    });
    expect(res.status).toBe(201);
    expect(inserts[0].params[7]).toBe(savedIds);
    expect(inserts[0].params[8]).toBe(savedNames);
  });

  it("reuses an existing pending card instead of inserting a duplicate", async () => {
    const { repo, inserts, queries } = makeRepo({
      existingPending: { id: 777, flow_id: "flow-1", node_id: "review" },
    });
    const { base } = await createServer(repo);

    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(validPayload),
    });

    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ success: true, data: { id: 777 } });
    expect(queries.length).toBe(1);
    expect(inserts.length).toBe(0);
  });

  it("returns 400 when required fields are missing", async () => {
    const { repo } = makeRepo({});
    const { base } = await createServer(repo);

    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ flow_id: "flow-1", node_id: "review" }),
    });

    expect(res.status).toBe(400);
    const body = await res.json();
    expect(body.success).toBe(false);
    expect(body.error).toBe("Bad Request");
  });

  it("returns 503 when repository is not configured", async () => {
    const { base } = await createServer(null as any);

    const res = await fetch(base, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(validPayload),
    });

    expect(res.status).toBe(503);
    expect(await res.json()).toMatchObject({ success: false, error: "Service Unavailable" });
  });
});

describe("GET /approval-cards/resolved", () => {
  it("filters by the bot-id prefix while retaining historical unowned rows", async () => {
    const { repo, queries } = makeRepo({});
    const db = (repo as any).db;
    db.query = async (sql: string, params: unknown[]) => {
      queries.push({ sql, params });
      return [resolvedCard(1, "flow-bot-a"), resolvedCard(2, "flow-legacy")];
    };
    const { base } = await createServer(repo);

    const res = await fetch(`${base}/resolved?botId=bot-a&limit=7`);

    expect(res.status).toBe(200);
    expect(await res.json()).toMatchObject({
      success: true,
      data: [{ flow_id: "flow-bot-a" }, { flow_id: "flow-legacy" }],
    });
    expect(queries).toHaveLength(1);
    expect(queries[0].sql).toContain("LEFT JOIN flow_runs");
    expect(queries[0].sql).toContain("origin_bot_id = ?");
    expect(queries[0].sql).toContain("origin_bot_id LIKE ?");
    expect(queries[0].sql).toContain("origin_bot_id IS NULL");
    expect(queries[0].params).toEqual(["bot-a", "bot-a:%", 7]);
  });

  it("keeps legacy cross-bot polling while joining flow status when botId is omitted", async () => {
    const { repo, queries } = makeRepo({});
    const { base } = await createServer(repo);

    const res = await fetch(`${base}/resolved?limit=9`);

    expect(res.status).toBe(200);
    expect(queries).toHaveLength(1);
    expect(queries[0].sql).toContain("LEFT JOIN flow_runs");
    expect(queries[0].sql).not.toContain("origin_bot_id = ?");
    expect(queries[0].params).toEqual([9]);
  });

  it("does not reuse one bot's cached response for another bot", async () => {
    const calls: string[] = [];
    const repo = {
      findResolvedCardWeb: async (_limit: number, botId?: string) => {
        calls.push(botId ?? "legacy");
        return [resolvedCard(botId === "bot-a" ? 1 : 2, `flow-${botId}`)];
      },
    } as unknown as ApprovalCardRepository;
    const { base } = await createServer(repo);

    const first = await fetch(`${base}/resolved?botId=bot-a`);
    const second = await fetch(`${base}/resolved?botId=bot-b`);

    expect((await first.json()).data[0].flow_id).toBe("flow-bot-a");
    expect((await second.json()).data[0].flow_id).toBe("flow-bot-b");
    expect(calls).toEqual(["bot-a", "bot-b"]);
  });
});

function sqliteFixture() {
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
      created_at INTEGER DEFAULT 100, resolved_at INTEGER,
      processed_at INTEGER, comment TEXT DEFAULT 'preserve review', gmt_modified INTEGER DEFAULT 100
    );
    CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, origin_bot_id TEXT, status TEXT);
  `);
  const add = (id: number, opts: {
    status?: string; flowStatus?: string | null; owner?: string | null;
    deliveryMode?: string; processedAt?: number; missingRun?: boolean;
  } = {}) => {
    raw.prepare(`INSERT INTO approval_cards (id, flow_id, status, delivery_mode, resolved_at, processed_at)
      VALUES (?, ?, ?, ?, ?, ?)`).run(id, `flow-${id}`, opts.status ?? "approved",
      opts.deliveryMode ?? "card-web", id * 10, opts.processedAt ?? null);
    if (!opts.missingRun) {
      raw.prepare("INSERT INTO flow_runs (flow_id, origin_bot_id, status) VALUES (?, ?, ?)")
        .run(`flow-${id}`, opts.owner === undefined ? "bot-a:owner" : opts.owner,
          opts.flowStatus === undefined ? "waiting" : opts.flowStatus);
    }
  };
  return { raw, db, repo: new ApprovalCardRepository(db), add };
}

async function pollIds(base: string, query = "") {
  const response = await fetch(`${base}/resolved${query}`);
  expect(response.status).toBe(200);
  const body = await response.json();
  expect(body.success).toBe(true);
  return body.data.map((row: { id: number }) => row.id);
}

function acknowledge(base: string, id: string | number, body?: unknown) {
  return fetch(`${base}/${id}/processed`, {
    method: "POST",
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
  });
}

describe("approval poller consumption over HTTP with SQLite", () => {
  it.each(["", "?botId=bot-a"])("hides historical terminal/unacked cards without changing history (%s)", async (query) => {
    const { raw, repo, add } = sqliteFixture();
    let id = 0;
    for (const flowStatus of ["succeeded", "failed", "cancelled", "canceled", "completed"]) {
      for (const status of ["approved", "rejected"]) add(++id, { status, flowStatus });
    }
    add(++id, { status: "rejected", flowStatus: "completed", owner: null });
    const expected: number[] = [];
    for (const flowStatus of [null, "unknown", "waiting", "running", "blocked"]) {
      for (const status of ["approved", "rejected"]) {
        add(++id, { status, flowStatus });
        expected.push(id);
      }
    }
    add(++id, { missingRun: true }); expected.push(id);
    add(++id, { owner: null }); expected.push(id);
    add(++id, { status: "rejected", owner: "" }); expected.push(id);
    add(++id, { status: "pending" });
    add(++id, { status: "expired" });
    add(++id, { deliveryMode: "card-dingtalk" });
    add(++id, { processedAt: 100 });
    const history = raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
    const { base } = await createServer(repo);

    expect(await pollIds(base, query)).toEqual(expected.reverse());
    expect(raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
  });

  it.each(["approved", "rejected"])("acks %s durably, clears every bot/legacy key, and preserves old success JSON", async (status) => {
    const { raw, db, repo, add } = sqliteFixture();
    add(1, { status, owner: null });
    add(2);
    add(3, { owner: "bot-b:owner" });
    const history = raw.prepare("SELECT * FROM approval_cards WHERE id = 1").get();
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const { base } = await createServer(repo);
    const queries = ["", "?botId=bot-a", "?botId=bot-b", "?botId=bot-a&limit=1"];
    const before = [[3, 2, 1], [2, 1], [3, 1], [2]];
    for (const [index, query] of queries.entries()) expect(await pollIds(base, query)).toEqual(before[index]);
    expect(find).toHaveBeenCalledTimes(4);

    const response = await acknowledge(base, 1, { consumed: true });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true });
    const after = raw.prepare("SELECT * FROM approval_cards WHERE id = 1").get() as Record<string, unknown>;
    expect(after).toEqual({ ...history as object, processed_at: expect.any(Number) });
    expect(after.processed_at).toBe(Math.floor(after.processed_at as number));
    const expected = [[3, 2], [2], [3], [2]];
    for (const [index, query] of queries.entries()) expect(await pollIds(base, query)).toEqual(expected[index]);
    expect(find).toHaveBeenCalledTimes(8);

    const repeated = await acknowledge(base, 1, { consumed: true });
    expect(repeated.status).toBe(200);
    expect(await repeated.json()).toEqual({ success: true });
    expect(raw.prepare("SELECT * FROM approval_cards WHERE id = 1").get()).toEqual(after);
    invalidateResolvedCache();
    const freshRepo = new ApprovalCardRepository(db);
    const fresh = await createServer(freshRepo);
    expect(await pollIds(fresh.base)).toEqual([3, 2]);
    expect(await pollIds(fresh.base, "?botId=bot-a")).toEqual([2]);
    expect(await freshRepo.findByFlowId("flow-1")).toEqual([after]);
  });

  it("keeps bot-owned results isolated while preserving unknown-owner attribution", async () => {
    const { repo, add } = sqliteFixture();
    add(1, { owner: null });
    add(2, { owner: "bot-a" });
    add(3, { owner: "bot-b:owner" });
    add(4, { owner: "bot_100%!:owner" });
    add(5, { owner: "botX100anything!:owner" });
    add(6, { owner: "bot-ab:owner" });
    add(7, { owner: "bot-a-other" });
    const { base } = await createServer(repo);
    expect(await pollIds(base, "?botId=bot-a")).toEqual([2, 1]);
    expect(await pollIds(base, "?botId=bot-b")).toEqual([3, 1]);
    expect(await pollIds(base, "?botId=bot_100%25!")).toEqual([4, 1]);
    expect(await pollIds(base)).toEqual([7, 6, 5, 4, 3, 2, 1]);
  });

  it.each([false, true])("keeps literal-star Bot caches separate from legacy polling (legacy first: %s)", async (legacyFirst) => {
    const { repo, add } = sqliteFixture();
    add(1, { owner: null });
    add(2, { owner: "*:owner" });
    add(3, { owner: "bot-b:owner" });
    const { base } = await createServer(repo);
    const cases: [string, number[]][] = [["", [3, 2, 1]], ["?botId=*", [2, 1]]];
    if (!legacyFirst) cases.reverse();
    for (const [query, expected] of cases) expect(await pollIds(base, query)).toEqual(expected);
    for (const [query, expected] of cases) expect(await pollIds(base, query)).toEqual(expected);
  });

  it.each(["1junk", "1.2", "1.0", "1e2", "0x1", "+1", "-1", "0", "9007199254740992", "1%20", "%201", "1%0A", "1%0D", "wrong"])(
    "rejects the complete invalid ID %s rather than acknowledging a numeric prefix", async (id) => {
      const { raw, repo, add } = sqliteFixture();
      add(1);
      const history = raw.prepare("SELECT * FROM approval_cards").all();
      const mark = vi.spyOn(repo, "markProcessed");
      const { base } = await createServer(repo);
      for (const body of [undefined, {}, { consumed: true }]) {
        const response = await acknowledge(base, id, body);
        expect(response.status).toBe(400);
        expect(await response.json()).toMatchObject({ success: false, error: "Bad Request" });
      }
      expect(mark).not.toHaveBeenCalled();
      expect(raw.prepare("SELECT * FROM approval_cards").all()).toEqual(history);
    },
  );

  it.each(["0001", "9007199254740991"])("accepts complete safe decimal IDs (%s)", async (id) => {
    const { repo, add } = sqliteFixture();
    add(Number(id));
    const { base } = await createServer(repo);
    const response = await acknowledge(base, id, { consumed: true });
    expect(response.status).toBe(200);
    expect(await response.json()).toEqual({ success: true });
    expect((await repo.findById(Number(id)))?.processed_at).toEqual(expect.any(Number));
  });

  it("redelivers reopened unconsumed cards after the unchanged 30-second cache TTL", async () => {
    const { raw, repo, add } = sqliteFixture();
    add(1, { flowStatus: "completed" });
    add(2, { status: "rejected", flowStatus: "failed" });
    await repo.markProcessed(2, true);
    const history = raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
    const clock = vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_000);
    const { base } = await createServer(repo);
    for (const query of ["", "?botId=bot-a"]) expect(await pollIds(base, query)).toEqual([]);
    raw.exec("UPDATE flow_runs SET status = 'running'");
    for (const query of ["", "?botId=bot-a"]) expect(await pollIds(base, query)).toEqual([]);
    clock.mockReturnValue(1_800_000_030_001);
    for (const query of ["", "?botId=bot-a"]) expect(await pollIds(base, query)).toEqual([1]);
    expect(raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
  });

  it.each(["pending", "expired", "other-mode", "missing"])("does not falsely acknowledge %s or invalidate cached polls", async (kind) => {
    const { raw, repo, add } = sqliteFixture();
    add(1);
    if (kind !== "missing") add(2, {
      status: kind === "other-mode" ? "approved" : kind,
      deliveryMode: kind === "other-mode" ? "card-dingtalk" : "card-web",
    });
    const history = raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const { base } = await createServer(repo);
    expect(await pollIds(base)).toEqual([1]);
    const response = await acknowledge(base, 2, { consumed: true });
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ success: false, error: "Conflict", message: expect.stringMatching(/not found|resolved card-web/i) });
    expect(await pollIds(base)).toEqual([1]);
    expect(find).toHaveBeenCalledTimes(1);
    expect(raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
  });

  it("returns HTTP failure on an actual SQLite write failure, retains cache, and allows retry", async () => {
    const { raw, repo, add } = sqliteFixture();
    add(1);
    const history = await repo.findById(1);
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const { base } = await createServer(repo);
    expect(await pollIds(base)).toEqual([1]);
    raw.exec(`CREATE TRIGGER fail_ack BEFORE UPDATE ON approval_cards
      BEGIN SELECT RAISE(ABORT, 'ack persistence failed'); END;`);
    const response = await acknowledge(base, 1, { consumed: true });
    expect(response.status).toBe(500);
    expect(await response.json()).toMatchObject({ success: false, message: "ack persistence failed" });
    expect(await repo.findById(1)).toEqual(history);
    expect(await pollIds(base)).toEqual([1]);
    expect(find).toHaveBeenCalledTimes(1);
    raw.exec("DROP TRIGGER fail_ack");
    expect((await acknowledge(base, 1, { consumed: true })).status).toBe(200);
    expect(await pollIds(base)).toEqual([]);
  });

  it("does not return success or drop cache when a zero-row write has not persisted", async () => {
    const { db, repo, add } = sqliteFixture();
    add(1);
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const { base } = await createServer(repo);
    expect(await pollIds(base)).toEqual([1]);
    vi.spyOn(db, "exec").mockResolvedValue({ affectedRows: 0 });
    const response = await acknowledge(base, 1, { consumed: true });
    expect(response.status).toBe(409);
    expect((await response.json()).success).toBe(false);
    expect((await repo.findById(1))?.processed_at).toBeNull();
    expect(await pollIds(base)).toEqual([1]);
    expect(find).toHaveBeenCalledTimes(1);
  });

  it("propagates acknowledgment-verification read failures without clearing cache", async () => {
    const { db, repo, add } = sqliteFixture();
    add(1);
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const { base } = await createServer(repo);
    expect(await pollIds(base)).toEqual([1]);
    vi.spyOn(db, "exec").mockResolvedValue({ affectedRows: 0 });
    vi.spyOn(db, "query").mockRejectedValue(new Error("ack verification failed"));
    const response = await acknowledge(base, 1, { consumed: true });
    expect(response.status).toBe(500);
    expect(await response.json()).toMatchObject({ success: false, message: "ack verification failed" });
    expect(await pollIds(base)).toEqual([1]);
    expect(find).toHaveBeenCalledTimes(1);
  });

  it("does not cache failed resolved reads", async () => {
    const { db, repo, add } = sqliteFixture();
    add(1);
    const { base } = await createServer(repo);
    vi.spyOn(db, "query").mockRejectedValueOnce(new Error("poll read failed"));
    const response = await fetch(`${base}/resolved`);
    expect(response.status).toBe(500);
    expect(await response.json()).toMatchObject({ success: false, message: "poll read failed" });
    expect(await pollIds(base)).toEqual([1]);
  });

  it("does not let a pre-ack in-flight poll repopulate invalidated cache", async () => {
    const { repo, add } = sqliteFixture();
    add(1);
    const find = repo.findResolvedCardWeb.bind(repo);
    let release!: () => void;
    let captured!: () => void;
    const capturedRows = new Promise<void>((resolve) => { captured = resolve; });
    const gate = new Promise<void>((resolve) => { release = resolve; });
    const spy = vi.spyOn(repo, "findResolvedCardWeb").mockImplementationOnce(async (limit, botId) => {
      const rows = await find(limit, botId);
      captured();
      await gate;
      return rows;
    });
    const { base } = await createServer(repo);
    const inFlight = fetch(`${base}/resolved?botId=bot-a`);
    await capturedRows;
    const ack = await acknowledge(base, 1, { consumed: true });
    release();
    expect(ack.status).toBe(200);
    expect((await (await inFlight).json()).data.map((row: { id: number }) => row.id)).toEqual([1]);
    expect(await pollIds(base, "?botId=bot-a")).toEqual([]);
    expect(spy).toHaveBeenCalledTimes(2);
  });
});

describe("legacy and explicit consumption protocol over HTTP", () => {
  it.each([undefined, {}, { legacy: true }])("legacy body %j reads without writes or cache invalidation", async (body) => {
    const { raw, db, repo, add } = sqliteFixture();
    add(1, { status: "approved", owner: null });
    add(2, { status: "rejected", owner: null });
    const history = raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const mark = vi.spyOn(repo, "markProcessed");
    const exec = vi.spyOn(db, "exec");
    const { base } = await createServer(repo);
    const queries = ["", "?botId=bot-a", "?botId=bot-b", "?botId=bot-a&limit=1"];
    const expected = [[2, 1], [2, 1], [2, 1], [2]];
    for (const [index, query] of queries.entries()) expect(await pollIds(base, query)).toEqual(expected[index]);
    const query = vi.spyOn(db, "query");

    for (const id of [1, 2]) {
      const response = await acknowledge(base, id, body);
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({ success: true });
      expect(mark).toHaveBeenLastCalledWith(id, false);
    }
    expect(query).toHaveBeenCalledTimes(2);
    expect(exec).not.toHaveBeenCalled();
    expect(raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
    for (const [index, key] of queries.entries()) expect(await pollIds(base, key)).toEqual(expected[index]);
    expect(find).toHaveBeenCalledTimes(4);
    expect(query).toHaveBeenCalledTimes(2);
  });

  it.each(["approved", "rejected"])("legacy ACKs accept a terminal %s card and preserve a prior durable timestamp", async (status) => {
    const { raw, db, repo, add } = sqliteFixture();
    add(1, { status, flowStatus: "completed" });
    const { base } = await createServer(repo);
    const history = await repo.findById(1);
    const exec = vi.spyOn(db, "exec");
    expect((await acknowledge(base, 1, {})).status).toBe(200);
    expect(exec).not.toHaveBeenCalled();
    expect(await repo.findById(1)).toEqual(history);
    const durable = await acknowledge(base, 1, { consumed: true });
    expect(durable.status).toBe(200);
    expect(await durable.json()).toEqual({ success: true });
    const consumed = await repo.findById(1);
    expect(consumed?.processed_at).toEqual(expect.any(Number));
    exec.mockClear();
    for (const body of [undefined, {}, { legacy: true }]) {
      const response = await acknowledge(base, 1, body);
      expect(response.status).toBe(200);
      expect(await response.json()).toEqual({ success: true });
    }
    expect(exec).not.toHaveBeenCalled();
    expect(raw.prepare("SELECT * FROM approval_cards").get()).toEqual(consumed);
  });

  it.each(["pending", "expired", "other-mode", "missing"])("legacy ACK returns 409 for %s without writes or cache invalidation", async (kind) => {
    const { raw, db, repo, add } = sqliteFixture();
    add(1);
    if (kind !== "missing") add(2, {
      status: kind === "other-mode" ? "approved" : kind,
      deliveryMode: kind === "other-mode" ? "card-dingtalk" : "card-web",
    });
    const history = raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const exec = vi.spyOn(db, "exec");
    const { base } = await createServer(repo);
    expect(await pollIds(base)).toEqual([1]);
    const response = await acknowledge(base, 2, {});
    expect(response.status).toBe(409);
    expect(await response.json()).toMatchObject({ success: false, error: "Conflict" });
    expect(exec).not.toHaveBeenCalled();
    expect(await pollIds(base)).toEqual([1]);
    expect(find).toHaveBeenCalledTimes(1);
    expect(raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(history);
  });

  it.each([
    null, [], [true], "legacy", 1, true, false,
    { consumed: false }, { consumed: null }, { consumed: 0 }, { consumed: 1 },
    { consumed: "true" }, { consumed: "false" }, { consumed: "" }, { consumed: [] }, { consumed: {} },
  ])("rejects invalid body %j before repository/database calls or cache mutation", async (body) => {
    const { raw, db, repo, add } = sqliteFixture();
    add(1);
    const history = raw.prepare("SELECT * FROM approval_cards").all();
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const mark = vi.spyOn(repo, "markProcessed");
    // A permissive JSON parser ensures the route, not only Express's default
    // strict parser, enforces the plain-object transport contract.
    const { base } = await createServer(repo, false);
    expect(await pollIds(base)).toEqual([1]);
    const exec = vi.spyOn(db, "exec");
    const query = vi.spyOn(db, "query");
    const response = await acknowledge(base, 1, body);
    expect(response.status).toBe(400);
    expect(await response.json()).toMatchObject({ success: false, error: "Bad Request" });
    expect(mark).not.toHaveBeenCalled();
    expect(exec).not.toHaveBeenCalled();
    expect(query).not.toHaveBeenCalled();
    expect(await pollIds(base)).toEqual([1]);
    expect(find).toHaveBeenCalledTimes(1);
    expect(raw.prepare("SELECT * FROM approval_cards").all()).toEqual(history);
  });

  it("rejects malformed JSON without reaching the repository or invalidating cache", async () => {
    const { db, repo, add } = sqliteFixture();
    add(1);
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const mark = vi.spyOn(repo, "markProcessed");
    const { base } = await createServer(repo);
    expect(await pollIds(base)).toEqual([1]);
    const exec = vi.spyOn(db, "exec");
    const query = vi.spyOn(db, "query");
    const response = await fetch(`${base}/1/processed`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: '{"consumed":',
    });
    expect(response.status).toBe(400);
    expect(mark).not.toHaveBeenCalled();
    expect(exec).not.toHaveBeenCalled();
    expect(query).not.toHaveBeenCalled();
    expect(await pollIds(base)).toEqual([1]);
    expect(find).toHaveBeenCalledTimes(1);
  });

  it("returns legacy read failures without writing or clearing cached polls", async () => {
    const { db, repo, add } = sqliteFixture();
    add(1);
    const find = vi.spyOn(repo, "findResolvedCardWeb");
    const { base } = await createServer(repo);
    expect(await pollIds(base)).toEqual([1]);
    const exec = vi.spyOn(db, "exec");
    vi.spyOn(db, "query").mockRejectedValue(new Error("legacy eligibility failed"));
    const response = await acknowledge(base, 1, {});
    expect(response.status).toBe(500);
    expect(await response.json()).toMatchObject({ success: false, message: "legacy eligibility failed" });
    expect(exec).not.toHaveBeenCalled();
    expect(await pollIds(base)).toEqual([1]);
    expect(find).toHaveBeenCalledTimes(1);
  });
});
