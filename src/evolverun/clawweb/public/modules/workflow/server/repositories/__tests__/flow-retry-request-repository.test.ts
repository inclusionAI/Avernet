/**
 * Tests for FlowRetryRequestRepository — the cross-engine retry mailbox.
 * Claim is a CAS: exactly one concurrent claimer wins; expired leases become
 * claimable again; release returns a row to the pool with attempts+1.
 */
import { describe, it, expect } from "vitest";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { FlowRetryRequestRepository } from "../flow-retry-request-repository.js";
import Database from "better-sqlite3";

function createTestDb(): IDatabase {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE flow_retry_requests (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      request_id VARCHAR(255) NOT NULL,
      flow_id VARCHAR(255) NOT NULL,
      node_id VARCHAR(255),
      reason TEXT,
      requester VARCHAR(255),
      options_json TEXT,
      status VARCHAR(255) NOT NULL DEFAULT 'pending',
      claim_engine VARCHAR(255),
      claimed_at INTEGER,
      attempts INTEGER NOT NULL DEFAULT 0,
      result_message TEXT,
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE UNIQUE INDEX uk_flow_retry_requests_request_id ON flow_retry_requests (request_id);
    CREATE INDEX idx_flow_retry_requests_status ON flow_retry_requests (status);
    CREATE INDEX idx_flow_retry_requests_flow_id ON flow_retry_requests (flow_id);
  `);

  return {
    dbType: "sqlite",
    dialect: sqliteDialect,
    query: async <T>(sql: string, params?: unknown[]) => {
      const stmt = raw.prepare(sql);
      const rows = params ? stmt.all(...params) : stmt.all();
      return rows as T[];
    },
    exec: async (sql: string, params?: unknown[]) => {
      const stmt = raw.prepare(sql);
      const result = params ? stmt.run(...params) : stmt.run();
      return { affectedRows: result.changes, insertId: result.lastInsertRowid as number };
    },
    transaction: async <T>(fn: (db: IDatabase) => Promise<T>) => {
      return raw.transaction(() => fn(createTestDb()))();
    },
    close: async () => { raw.close(); },
  };
}

describe("FlowRetryRequestRepository", () => {
  it("create inserts a pending request and dedupes on an open request for the same flow", async () => {
    const db = createTestDb();
    const repo = new FlowRetryRequestRepository(db);
    try {
      const first = await repo.create({ requestId: "r1", flowId: "flow-a", nodeId: "content-analysis", reason: "cortex-recovery:x", requester: "bot-1" });
      expect(first?.status).toBe("pending");
      expect(first?.request_id).toBe("r1");

      // duplicate retry for the same flow returns the existing open row
      const dup = await repo.create({ requestId: "r2", flowId: "flow-a", reason: "again" });
      expect(dup?.request_id).toBe("r1");

      // a different flow gets its own row
      const other = await repo.create({ requestId: "r3", flowId: "flow-b" });
      expect(other?.request_id).toBe("r3");
    } finally {
      await db.close();
    }
  });

  it("claimPending CAS: exactly one winner per row, loser gets nothing", async () => {
    const db = createTestDb();
    const repo = new FlowRetryRequestRepository(db);
    try {
      await repo.create({ requestId: "r1", flowId: "flow-a" });
      const now = Math.floor(Date.now() / 1000);

      const first = await repo.claimPending(10, 600, "engine-1", now);
      expect(first).toHaveLength(1);
      expect(first[0].claim_engine).toBe("engine-1");
      expect(first[0].status).toBe("claimed");

      // second engine polling right after: nothing left to claim
      const second = await repo.claimPending(10, 600, "engine-2", now);
      expect(second).toHaveLength(0);
    } finally {
      await db.close();
    }
  });

  it("expired lease becomes claimable again (dead claimer recovery)", async () => {
    const db = createTestDb();
    const repo = new FlowRetryRequestRepository(db);
    try {
      await repo.create({ requestId: "r1", flowId: "flow-a" });
      const t0 = 1_000_000;
      await repo.claimPending(10, 600, "engine-1", t0);

      // within lease: still owned by engine-1
      expect(await repo.claimPending(10, 600, "engine-2", t0 + 300)).toHaveLength(0);
      // after lease expiry: engine-2 wins
      const reclaimed = await repo.claimPending(10, 600, "engine-2", t0 + 601);
      expect(reclaimed).toHaveLength(1);
      expect(reclaimed[0].claim_engine).toBe("engine-2");
    } finally {
      await db.close();
    }
  });

  it("release returns the row to pending with attempts+1; complete marks terminal", async () => {
    const db = createTestDb();
    const repo = new FlowRetryRequestRepository(db);
    try {
      await repo.create({ requestId: "r1", flowId: "flow-a" });
      const now = Math.floor(Date.now() / 1000);
      await repo.claimPending(10, 600, "engine-1", now);

      // wrong host → release back to pool
      expect(await repo.release("r1")).toBe(true);
      const after = await repo.claimPending(10, 600, "engine-2", now);
      expect(after).toHaveLength(1);
      expect(after[0].attempts).toBe(1);

      // executor finishes
      expect(await repo.complete("r1", true, "retried on owner host")).toBe(true);

      // completed rows are never claimable again, and create no longer dedupes on them
      expect(await repo.claimPending(10, 600, "engine-3", now)).toHaveLength(0);
      const fresh = await repo.create({ requestId: "r4", flowId: "flow-a" });
      expect(fresh?.request_id).toBe("r4");

      expect(await repo.complete("missing", false, "x")).toBe(false);
      expect(await repo.release("missing")).toBe(false);
    } finally {
      await db.close();
    }
  });
});


it("preserves retry options through persistence and claim", async () => {
 const db = createTestDb(); const repo = new FlowRetryRequestRepository(db);
 try {
  const options = {useCurrentDef: true, debug: true, inputOverrides: {ticket: "42"}};
  const row = await repo.create({requestId:"options-r",flowId:"options-f",options});
  expect(JSON.parse(row!.options_json!)).toEqual(options);
  const claimed = await repo.claimPending(1,600,"engine-a",Math.floor(Date.now()/1000),["options-f"]);
  expect(JSON.parse(claimed[0].options_json!)).toEqual(options);
  expect(await repo.claimPending(1,600,"engine-b",Math.floor(Date.now()/1000),["options-f"])).toEqual([]);
 } finally { await db.close(); }
});


it("old consumers cannot claim requests carrying execution options", async () => {
 const db = createTestDb(); const repo = new FlowRetryRequestRepository(db);
 try {
  await repo.create({requestId:"old-consumer-r",flowId:"old-consumer-f",options:{debug:true}});
  expect(await repo.claimPending(1,600,"old-engine",Math.floor(Date.now()/1000),["old-consumer-f"],false)).toEqual([]);
  expect(await repo.claimPending(1,600,"new-engine",Math.floor(Date.now()/1000),["old-consumer-f"],true)).toHaveLength(1);
 } finally {await db.close();}
});
