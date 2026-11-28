// @vitest-environment node
import { execFileSync } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createRequire } from "node:module";
import { tmpdir } from "node:os";
import { join } from "node:path";
import Database from "better-sqlite3";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db";
import { ApprovalCardRepository } from "../approval-card-repository.js";

const databases = new Set<SqliteDatabase>();
const directories: string[] = [];
const require = createRequire(import.meta.url);

afterEach(async () => {
  vi.restoreAllMocks();
  await Promise.all([...databases].map((db) => db.close()));
  databases.clear();
  for (const directory of directories.splice(0)) rmSync(directory, { recursive: true, force: true });
});

function openDatabase(file: string) {
  const raw = new Database(file);
  const db = new SqliteDatabase(raw);
  databases.add(db);
  return { raw, db, repo: new ApprovalCardRepository(db) };
}

async function closeDatabase(db: SqliteDatabase) {
  await db.close();
  databases.delete(db);
}

function persistedRowsInNewProcess(file: string) {
  // No TS loader or built package is needed: an independent process reads the
  // durable bytes; reopened repositories below exercise the production query.
  const output = execFileSync(process.execPath, [
    "--input-type=commonjs", "-e", `
      const Database = require(process.argv[1]);
      const db = new Database(process.argv[2], { readonly: true });
      try {
        process.stdout.write(JSON.stringify(db.prepare('SELECT * FROM approval_cards ORDER BY id').all()));
      } finally {
        db.close();
      }
    `,
    require.resolve("better-sqlite3"), file,
  ], { encoding: "utf8", timeout: 10_000 });
  return JSON.parse(output);
}

describe("file-backed approval consumption survives restarts", () => {
  it.each(["approved", "rejected"])("persists the first %s ACK across independent connections and process lifetimes", async (status) => {
    const directory = mkdtempSync(join(tmpdir(), "approval-card-persistence-"));
    directories.push(directory);
    const file = join(directory, "approvals.sqlite");
    const writer = openDatabase(file);
    writer.raw.exec(`
      CREATE TABLE approval_cards (
        id INTEGER PRIMARY KEY, flow_id TEXT NOT NULL, node_id TEXT DEFAULT 'review',
        status TEXT NOT NULL, delivery_mode TEXT DEFAULT 'card-web',
        approved_by TEXT DEFAULT '', rejected_by TEXT DEFAULT '', comment TEXT DEFAULT 'audit note',
        created_at INTEGER DEFAULT 100, resolved_at INTEGER DEFAULT 200,
        processed_at INTEGER, gmt_modified INTEGER DEFAULT 100
      );
      CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, origin_bot_id TEXT, status TEXT);
    `);
    const insert = writer.raw.prepare(`INSERT INTO approval_cards
      (id, flow_id, status, approved_by, rejected_by) VALUES (?, ?, ?, ?, ?)`);
    insert.run(1, "flow-acked", status, status === "approved" ? "reviewer" : "", status === "rejected" ? "reviewer" : "");
    insert.run(2, "flow-retry", "approved", "reviewer", "");
    insert.run(3, "flow-terminal", "rejected", "", "reviewer");
    writer.raw.exec(`UPDATE approval_cards SET resolved_at = 400 - id * 100;
      INSERT INTO flow_runs VALUES
      ('flow-acked', 'bot-a:owner', 'waiting'),
      ('flow-retry', NULL, 'waiting'),
      ('flow-terminal', 'bot-a:owner', 'succeeded');`);
    const before = writer.raw.prepare("SELECT * FROM approval_cards ORDER BY id").all();
    const flows = writer.raw.prepare("SELECT * FROM flow_runs ORDER BY flow_id").all();
    const reader = openDatabase(file);
    for (const botId of [undefined, "bot-a"]) {
      expect((await reader.repo.findResolvedCardWeb(50, botId)).map((row) => row.id)).toEqual([1, 2]);
    }

    const clock = vi.spyOn(Date, "now").mockReturnValue(1_800_000_000_999);
    expect(await writer.repo.markProcessed(1)).toBe(true);
    const expected = before.map((row: Record<string, unknown>) => row.id === 1
      ? { ...row, processed_at: 1_800_000_000 }
      : row);
    // This is another SQLite connection, not a fresh repository on one handle.
    expect(await reader.repo.findById(1)).toEqual(expected[0]);
    for (const botId of [undefined, "bot-a"]) {
      expect((await reader.repo.findResolvedCardWeb(50, botId)).map((row) => row.id)).toEqual([2]);
    }
    await closeDatabase(writer.db);
    await closeDatabase(reader.db);

    // All original connections are closed before a child process opens the file.
    expect(persistedRowsInNewProcess(file)).toEqual(expected);
    const restarted = openDatabase(file);
    clock.mockReturnValue(1_800_000_100_000);
    expect(await restarted.repo.markProcessed(1)).toBe(true);
    expect(restarted.raw.prepare("SELECT * FROM approval_cards ORDER BY id").all()).toEqual(expected);
    expect(restarted.raw.prepare("SELECT * FROM flow_runs ORDER BY flow_id").all()).toEqual(flows);
    for (const botId of [undefined, "bot-a"]) {
      expect((await restarted.repo.findResolvedCardWeb(50, botId)).map((row) => row.id)).toEqual([2]);
    }
    await closeDatabase(restarted.db);
    expect(persistedRowsInNewProcess(file)).toEqual(expected);
  });
});
