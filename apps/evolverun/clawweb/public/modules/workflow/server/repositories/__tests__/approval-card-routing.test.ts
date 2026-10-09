// @vitest-environment node
import Database from "better-sqlite3";
import { describe, expect, it } from "vitest";
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
        resolved_at INTEGER
      );
      CREATE TABLE flow_runs (flow_id TEXT PRIMARY KEY, origin_bot_id TEXT);
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
      expect((await repo.findResolvedCardWeb(50, "bot_100%")).map((row) => row.id)).toEqual([8, 5, 4, 3]);
      expect((await repo.findResolvedCardWeb(50)).map((row) => row.id)).toEqual([10, 9, 8, 5, 4, 3, 2, 1]);
    } finally {
      await db.close();
    }
  });
});
