import { Buffer } from "node:buffer";
import Database from "better-sqlite3";
import { describe, expect, it, vi } from "vitest";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db/sqlite-database";
import {
  RunLogRepository,
  type RunLogInsert,
} from "../run-log-repository.js";

const limit = 65_535;
const marker = "\n...[truncated]";
const budget = limit - Buffer.byteLength(marker, "utf8");

function entry(message: string, seq: number): RunLogInsert {
  return {
    flow_id: "flow-'中",
    node_id: seq % 2 === 0 ? null : "node-🙂",
    level: "info",
    source: seq % 2 === 0 ? null : "console",
    message,
    timestamp: 1_700_000_000 + seq,
    seq,
  };
}

function createMockDatabase(affectedRows: number) {
  const exec = vi.fn<IDatabase["exec"]>().mockResolvedValue({ affectedRows });
  const db: IDatabase = {
    dbType: "sqlite",
    dialect: sqliteDialect,
    query: async <T>() => [] as T[],
    exec,
    transaction: async (fn) => fn(db),
    close: async () => {},
  };
  return { db, exec };
}

describe("RunLogRepository message guard", () => {
  it("does not write an empty frozen batch", async () => {
    const { db, exec } = createMockDatabase(0);
    const entries: RunLogInsert[] = [];
    Object.freeze(entries);
    expect(await new RunLogRepository(db).insertBatch(entries)).toBe(0);
    expect(exec).not.toHaveBeenCalled();
  });

  it("bounds every SQL message parameter without changing frozen inputs or other fields", async () => {
    const short = "short 'quoted' 中文🙂\n\u0000";
    const entries = [
      entry(short, 1),
      entry("a".repeat(limit + 1), 2),
      entry("中".repeat(limit), 3),
      entry("🙂".repeat(limit), 4),
      entry("a".repeat(limit), 5),
      entry("", 6),
      entry("a".repeat(budget - 1) + "🙂" + "z".repeat(32), 7),
    ];
    const originals = entries.map((value) => ({ ...value }));
    for (const value of entries) Object.freeze(value);
    Object.freeze(entries);
    const expectedMessages = [
      short,
      "a".repeat(budget) + marker,
      "中".repeat(Math.floor(budget / 3)) + marker,
      "🙂".repeat(Math.floor(budget / 4)) + marker,
      "a".repeat(limit),
      "",
      "a".repeat(budget - 1) + marker,
    ];
    const { db, exec } = createMockDatabase(entries.length);
    expect(await new RunLogRepository(db).insertBatch(entries)).toBe(entries.length);
    expect(exec).toHaveBeenCalledTimes(1);
    const [sql, params] = exec.mock.calls[0];
    expect(sql).toBe(
      "INSERT INTO run_logs (flow_id, node_id, level, source, message, timestamp, seq) VALUES " +
      entries.map(() => "(?, ?, ?, ?, ?, ?, ?)").join(", "),
    );
    expect(params).toEqual(originals.flatMap((value, index) => [
      value.flow_id,
      value.node_id,
      value.level,
      value.source,
      expectedMessages[index],
      value.timestamp,
      value.seq,
    ]));
    for (let index = 0; index < entries.length; index += 1) {
      expect(Buffer.byteLength(params![index * 7 + 4] as string, "utf8")).toBeLessThanOrEqual(limit);
    }
    expect(entries).toEqual(originals);
  });

  it("roundtrips bounded messages through real SQLite with a byte constraint", async () => {
    const raw = new Database(":memory:");
    const db: IDatabase = new SqliteDatabase(raw);
    try {
      raw.exec(`
        CREATE TABLE run_logs (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          flow_id TEXT NOT NULL,
          node_id TEXT,
          level TEXT NOT NULL,
          source TEXT,
          message TEXT NOT NULL CHECK (length(CAST(message AS BLOB)) <= 65535),
          timestamp INTEGER NOT NULL,
          seq INTEGER NOT NULL,
          gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
          gmt_modified INTEGER
        )
      `);
      const entries = [
        entry("a".repeat(limit + 1), 3),
        entry("中".repeat(limit), 1),
        entry("🙂".repeat(limit), 2),
        entry("short 'quoted' 中文🙂\n\u0000", 4),
        entry("a".repeat(limit), 5),
        entry("", 6),
        entry("a".repeat(budget - 1) + "🙂" + "z".repeat(32), 7),
      ];
      const originals = entries.map((value) => ({ ...value }));
      for (const value of entries) Object.freeze(value);
      Object.freeze(entries);
      const repository = new RunLogRepository(db);
      expect(await repository.insertBatch(entries)).toBe(entries.length);
      const rows = await repository.findByFlowId(entries[0].flow_id);
      expect(rows.map((row) => row.seq)).toEqual([1, 2, 3, 4, 5, 6, 7]);
      const expectedMessages = [
        "中".repeat(Math.floor(budget / 3)) + marker,
        "🙂".repeat(Math.floor(budget / 4)) + marker,
        "a".repeat(budget) + marker,
        entries[3].message,
        entries[4].message,
        "",
        "a".repeat(budget - 1) + marker,
      ];
      for (const [index, row] of rows.entries()) {
        const original = originals.find((value) => value.seq === row.seq)!;
        expect(row).toMatchObject({ ...original, message: expectedMessages[index] });
        expect(Buffer.byteLength(row.message, "utf8")).toBeLessThanOrEqual(limit);
        expect(Buffer.from(row.message, "utf8").toString("utf8")).toBe(row.message);
        expect(row.message).not.toContain("\uFFFD");
      }
      expect(await db.query<{ seq: number; bytes: number }>(
        "SELECT seq, length(CAST(message AS BLOB)) AS bytes FROM run_logs ORDER BY seq",
      )).toEqual(expectedMessages.map((message, index) => ({
        seq: index + 1,
        bytes: Buffer.byteLength(message, "utf8"),
      })));
      expect(entries).toEqual(originals);
    } finally {
      await db.close();
    }
  });
});
