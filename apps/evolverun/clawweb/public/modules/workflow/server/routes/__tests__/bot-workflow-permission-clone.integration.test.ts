// @vitest-environment node
import Database from "better-sqlite3";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Request, Response, Router } from "express";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db/sqlite-database";
import { migrations } from "@avernet/clawweb-shared/server/schema";
import {
  BotWorkflowPermissionRepository,
  type BotWorkflowPermissionRow,
  type BotWorkflowPermissionUpsert,
} from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { createBotWorkflowPermissionCloneRouter } from "../bot-workflow-permission-clone.js";

const payload = {
  source_bot_id: "src-bot", source_owner_id: "src-owner",
  target_bot_id: "tgt-bot", target_owner_id: "tgt-owner",
};

function request(router: Router, body: unknown): Promise<{ status: number; body: Record<string, unknown> }> {
  return new Promise((resolve, reject) => {
    // The existing JSON request needs no new headers or idempotency token.
    const req = { method: "POST", url: "/", headers: { "content-type": "application/json" }, body } as Request;
    let status = 200;
    const res = {
      status(code: number) { status = code; return this; },
      json(data: Record<string, unknown>) { resolve({ status, body: data }); return this; },
    } as Response;
    router(req, res, (error?: unknown) => error ? reject(error) : resolve({ status: 404, body: {} }));
  });
}

let db: SqliteDatabase;
let repo: BotWorkflowPermissionRepository;
let router: Router;

async function seed(
  botId: string | null,
  ownerId: string,
  workflowId: string,
  grant: [number, number, number],
  env = "dev",
): Promise<void> {
  await db.exec(
    `INSERT INTO bot_workflow_permissions
     (bot_id, bot_owner_id, workflow_id, env, can_view, can_execute, can_edit, gmt_create, gmt_modified)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [botId, ownerId, workflowId, env, ...grant, 1, 1],
  );
}

async function allRows(): Promise<BotWorkflowPermissionRow[]> {
  return db.query<BotWorkflowPermissionRow>("SELECT * FROM bot_workflow_permissions ORDER BY id");
}

function expectedGrant(data: Partial<BotWorkflowPermissionUpsert> = {}) {
  return {
    bot_id: "tgt-bot", bot_owner_id: "tgt-owner", workflow_id: "w1", env: "dev",
    can_view: 1, can_execute: 1, can_edit: 1,
    ...data,
  };
}

beforeEach(async () => {
  vi.stubEnv("SERVER_ENV", "dev");
  db = new SqliteDatabase(new Database(":memory:"));
  // Use the real permission-table migration, including its database unique key.
  const migration = migrations.find(entry => entry.version === 18)!;
  for (const sql of migration.sql) await db.exec(sql);
  repo = new BotWorkflowPermissionRepository(db);
  vi.spyOn(repo, "upsert");
  router = createBotWorkflowPermissionCloneRouter({ botPermRepo: repo });
});

afterEach(async () => {
  try {
    expect(repo.upsert).not.toHaveBeenCalled();
  } finally {
    await db.close();
    vi.restoreAllMocks();
    vi.unstubAllEnvs();
  }
});

describe("permission clone with the SQLite repository", () => {
  it("replaying the same payload keeps one target row and its original ID", async () => {
    await seed("src-bot", "src-owner", "w1", [1, 1, 1]);

    const first = await request(router, payload);
    expect(first).toEqual({
      status: 200,
      body: {
        success: true, outcome: "granted", message: "Copied 1 workflow: w1",
        dry_run: false, granted: 1,
        rows: [{ workflow_id: "w1", can_view: 1, can_execute: 1, can_edit: 1 }],
        skipped: [], source_effective_total: 1,
        source_resolved: { bot_id: "src-bot", owner_id: "src-owner", source: "param" },
        target_resolved: { bot_id: "tgt-bot", owner_id: "tgt-owner", source: "param" },
      },
    });
    const beforeReplay = await allRows();
    expect(beforeReplay).toHaveLength(2);
    const [target] = await repo.listRowsForBot("tgt-owner", "tgt-bot");
    expect(target).toMatchObject(expectedGrant());

    const replay = await request(router, payload);
    expect(replay).toEqual({
      status: 200,
      body: {
        ...first.body,
        outcome: "already_covered",
        message: "Clone complete: target bot already covers 1 workflow: w1",
        granted: 0, rows: [], skipped: [{ workflow_id: "w1", reason: "already-covered" }],
      },
    });
    expect(await allRows()).toEqual(beforeReplay);
    expect(await repo.listRowsForBot("tgt-owner", "tgt-bot")).toEqual([target]);
  });

  it.each([false, true])("unions complementary concurrent clones without duplicates (existing target: %s)", async existing => {
    await seed("src-bot", "src-owner", "w1", [1, 1, 0]);
    await seed("other-src", "other-owner", "w1", [0, 0, 1]);
    if (existing) await seed("tgt-bot", "tgt-owner", "w1", [1, 0, 0]);
    const initialTarget = await repo.listRowsForBot("tgt-owner", "tgt-bot");
    const read = repo.listRowsForBot.bind(repo);
    let targetReads = 0;
    let release!: () => void;
    const bothRead = new Promise<void>(resolve => { release = resolve; });
    vi.spyOn(repo, "listRowsForBot").mockImplementation(async (ownerId, botId) => {
      const rows = await read(ownerId, botId);
      if (ownerId === "tgt-owner" && botId === "tgt-bot") {
        // Both routes must see the pre-write snapshot; no timing/sleep assumptions.
        expect(rows).toEqual(initialTarget);
        if (++targetReads === 2) release();
        await bothRead;
      }
      return rows;
    });

    const results = await Promise.all([
      request(router, payload),
      request(router, { ...payload, source_bot_id: "other-src", source_owner_id: "other-owner" }),
    ]);
    expect(targetReads).toBe(2);
    for (const result of results) {
      expect(result).toMatchObject({
        status: 200,
        body: { success: true, outcome: "granted", granted: 1, skipped: [], source_effective_total: 1 },
      });
    }
    const targets = await read("tgt-owner", "tgt-bot");
    expect(targets).toHaveLength(1);
    expect(targets[0]).toMatchObject(expectedGrant());
    if (existing) expect(targets[0].id).toBe(initialTarget[0].id);
    expect(await allRows()).toHaveLength(3);
  });

  it.each([false, true])("dry_run does not insert or update permissions (existing target: %s)", async existing => {
    await seed("src-bot", "src-owner", "w1", [1, 1, 0]);
    if (existing) await seed("tgt-bot", "tgt-owner", "w1", [0, 0, 1]);
    const before = await allRows();

    const preview = await request(router, { ...payload, dry_run: true });
    expect(preview).toMatchObject({
      status: 200,
      body: {
        success: true, outcome: "granted", message: "Would copy 1 workflow: w1",
        dry_run: true, granted: 1,
        rows: [{ workflow_id: "w1", can_view: 1, can_execute: 1, can_edit: existing ? 1 : 0 }],
        skipped: [], source_effective_total: 1,
      },
    });
    expect(await allRows()).toEqual(before);
  });

  it.each(["exact", "exact+owner-rows"])("preserves cross-env reads and %s scope without cloning global grants", async scope => {
    await seed("src-bot", "src-owner", "w1", [1, 0, 0], "pre");
    await seed("src-bot", "src-owner", "w1", [0, 1, 0], "prod");
    await seed("tgt-bot", "tgt-owner", "w1", [0, 0, 1], "pre");
    await seed("src-bot", "src-owner", "covered", [1, 0, 0], "pre");
    await seed("tgt-bot", "tgt-owner", "covered", [1, 1, 1], "prod");
    await seed(null, "src-owner", "owner-null", [1, 0, 0]);
    await seed("", "src-owner", "owner-empty", [0, 1, 0]);
    await seed("*", "src-owner", "owner-star", [0, 0, 1]);
    await seed(null, "*", "global-owner", [1, 1, 1]);
    await seed("src-bot", "*", "global-bot", [1, 1, 1]);
    const before = await allRows();

    const result = await request(router, { ...payload, scope });
    const expanded = scope === "exact+owner-rows";
    expect(result).toMatchObject({
      status: 200,
      body: {
        success: true, outcome: "granted", granted: expanded ? 4 : 1,
        source_effective_total: expanded ? 5 : 2,
        skipped: [{ workflow_id: "covered", reason: "already-covered" }],
      },
    });
    const targets = await repo.listRowsForBot("tgt-owner", "tgt-bot");
    const current = targets.filter(row => row.env === "dev");
    expect(current.map(row => row.workflow_id).sort()).toEqual(
      expanded ? ["owner-empty", "owner-null", "owner-star", "w1"] : ["w1"],
    );
    expect(current.find(row => row.workflow_id === "w1")).toMatchObject(expectedGrant());
    if (expanded) {
      expect(current).toEqual(expect.arrayContaining([
        expect.objectContaining(expectedGrant({ workflow_id: "owner-null", can_execute: 0, can_edit: 0 })),
        expect.objectContaining(expectedGrant({ workflow_id: "owner-empty", can_view: 0, can_edit: 0 })),
        expect.objectContaining(expectedGrant({ workflow_id: "owner-star", can_view: 0, can_execute: 0 })),
      ]));
    }
    expect(await allRows()).toHaveLength(before.length + current.length);
    expect((await allRows()).filter(row => before.some(original => original.id === row.id))).toEqual(before);
  });
});
