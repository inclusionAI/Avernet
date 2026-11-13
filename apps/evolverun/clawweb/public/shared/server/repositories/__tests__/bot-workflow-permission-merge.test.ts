// @vitest-environment node
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import Database from "better-sqlite3";
import { SqliteDatabase, type IDatabase } from "../../db.js";
import { dialectFor } from "../../db/dialect.js";
import {
  BotWorkflowPermissionRepository,
  type BotWorkflowPermissionRow,
  type BotWorkflowPermissionUpsert,
} from "../bot-workflow-permission-repository.js";

const databases: Database.Database[] = [];
const directories: string[] = [];
const permissionFields = ["can_view", "can_execute", "can_edit"] as const;
const input: BotWorkflowPermissionUpsert & { bot_id: string } = {
  bot_id: "default", bot_owner_id: "owner", workflow_id: "workflow",
  can_view: 1, can_execute: 0, can_edit: 0,
};
const columns = "id, bot_id, bot_owner_id, workflow_id, env, can_view, can_execute, can_edit, gmt_create, gmt_modified";
const readSql = `SELECT ${columns} FROM bot_workflow_permissions WHERE bot_id = ? AND bot_owner_id = ? AND workflow_id = ? AND env = ?`;

beforeEach(() => { vi.stubEnv("SERVER_ENV", "dev"); });
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllEnvs();
  databases.splice(0).forEach(db => db.close());
  directories.splice(0).forEach(directory => rmSync(directory, { recursive: true, force: true }));
});

function connection(path = ":memory:") {
  const raw = new Database(path);
  databases.push(raw);
  const db = new SqliteDatabase(raw);
  return { raw, db, repo: new BotWorkflowPermissionRepository(db) };
}

function fixture(path = ":memory:") {
  const fixture = connection(path);
  fixture.raw.exec(`CREATE TABLE bot_workflow_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT, bot_id TEXT, bot_owner_id TEXT NOT NULL,
    workflow_id TEXT NOT NULL, env TEXT NOT NULL,
    can_view INTEGER NOT NULL, can_execute INTEGER NOT NULL, can_edit INTEGER NOT NULL,
    gmt_create INTEGER NOT NULL, gmt_modified INTEGER NOT NULL
  );
  CREATE UNIQUE INDEX uk_bot_wf_perm
    ON bot_workflow_permissions (bot_id, bot_owner_id, workflow_id, env);`);
  return fixture;
}

function fileFixtures() {
  const directory = mkdtempSync(join(tmpdir(), "permission-merge-"));
  directories.push(directory);
  const path = join(directory, "permissions.sqlite");
  const first = fixture(path);
  first.raw.pragma("journal_mode = WAL");
  return [first, connection(path), connection(path)];
}

function mockDatabase(dbType: IDatabase["dbType"]) {
  const persisted: BotWorkflowPermissionRow = {
    ...input, id: 42, env: "dev", can_execute: 1, can_edit: 1, gmt_create: 100, gmt_modified: 200,
  };
  const database: IDatabase = {
    dbType, dialect: dialectFor(dbType),
    async query<T>() { return [persisted] as T[]; },
    async exec() { return { affectedRows: 1, insertId: 999 }; },
    async transaction<T>(fn: (db: IDatabase) => Promise<T>) { return fn(database); },
    async close() {},
  };
  const db = {
    ...database,
    query: vi.spyOn(database, "query"), exec: vi.spyOn(database, "exec"),
    transaction: vi.spyOn(database, "transaction"),
  };
  return { db, persisted, repo: new BotWorkflowPermissionRepository(database) };
}

function compact(sql: string): string { return sql.replace(/\s+/g, " ").trim(); }

function expectReadback(db: ReturnType<typeof mockDatabase>["db"]) {
  expect(db.query).toHaveBeenCalledTimes(1);
  expect(compact(db.query.mock.calls[0][0])).toBe(readSql);
  expect(db.query.mock.calls[0][1]).toEqual(["default", "owner", "workflow", "dev"]);
  expect(db.exec.mock.invocationCallOrder[0]).toBeLessThan(db.query.mock.invocationCallOrder[0]);
  expect(db.transaction).not.toHaveBeenCalled();
}

describe("BotWorkflowPermissionRepository.mergeGrants SQLite", () => {
  it("keeps one row and the same ID on repeated grants while preserving creation time", async () => {
    const { repo, raw, db } = fixture();
    const now = vi.spyOn(db.dialect, "now").mockReturnValue(100);
    const first = await repo.mergeGrants(input);
    expect(first).toEqual({ ...input, id: 1, env: "dev", gmt_create: 100, gmt_modified: 100 });
    now.mockReturnValue(200);
    for (let attempt = 0; attempt < 5; attempt++) {
      expect(await repo.mergeGrants(input)).toEqual({ ...first, gmt_modified: 200 });
    }
    expect(raw.prepare("SELECT COUNT(*) AS count FROM bot_workflow_permissions").get()).toEqual({ count: 1 });
  });

  it("normalizes each incoming permission independently, including nonzero legacy numbers", async () => {
    const { repo } = fixture();
    expect(await repo.mergeGrants({ ...input, can_view: -2, can_execute: 0, can_edit: 7 }))
      .toMatchObject({ can_view: 1, can_execute: 0, can_edit: 1 });
  });

  it.each(permissionFields)("preserves a stronger stored %s grant when incoming bits are zero", async field => {
    const { repo } = fixture();
    const original = await repo.upsert({ ...input, can_view: 0, can_execute: 0, can_edit: 0, [field]: 1 });
    expect(await repo.mergeGrants({ ...input, can_view: 0, can_execute: 0, can_edit: 0 }))
      .toMatchObject({ id: original.id, can_view: 0, can_execute: 0, can_edit: 0, [field]: 1 });
  });

  it("ORs and normalizes the stored current values instead of replacing them", async () => {
    const { repo } = fixture();
    const original = await repo.upsert({ ...input, can_view: -3, can_execute: 4, can_edit: 0 });
    expect(await repo.mergeGrants({ ...input, can_view: 0, can_execute: 0, can_edit: -9 }))
      .toMatchObject({ id: original.id, can_view: 1, can_execute: 1, can_edit: 1 });
  });

  it.each([false, true])("unions concurrent complementary grants across separate connections (existing=%s)", async existing => {
    const connections = fileFixtures();
    expect(new Set(connections.map(({ raw }) => raw)).size).toBe(3);
    expect(new Set(connections.map(({ repo }) => repo)).size).toBe(3);
    const original = existing
      ? await connections[0].repo.upsert({ ...input, can_view: 0, can_execute: 0, can_edit: 0 })
      : null;
    const results = await Promise.all(connections.map(({ repo }, index) => repo.mergeGrants({
      ...input, can_view: 0, can_execute: 0, can_edit: 0, [permissionFields[index]]: 1,
    })));
    expect(new Set(results.map(row => row.id)).size).toBe(1);
    if (original) expect(results[0].id).toBe(original.id);
    const rows = connections[0].raw.prepare(`SELECT ${columns} FROM bot_workflow_permissions`).all();
    expect(rows).toEqual([expect.objectContaining({ ...input, id: results[0].id, can_view: 1, can_execute: 1, can_edit: 1 })]);
  });

  it("does not merge distinct owner, workflow, env, or bot keys", async () => {
    const { repo, raw } = fixture();
    const first = await repo.mergeGrants(input);
    const identities = [
      { bot_owner_id: "other-owner" }, { workflow_id: "other-workflow" }, { bot_id: "other-bot" }, {},
    ];
    const ids = new Set([first.id]);
    for (const [index, identity] of identities.entries()) {
      vi.stubEnv("SERVER_ENV", index === identities.length - 1 ? "prod" : "dev");
      const next = await repo.mergeGrants({ ...input, ...identity, can_view: 0, can_execute: 1 });
      expect(next).toMatchObject({ ...input, ...identity, can_view: 0, can_execute: 1,
        env: index === identities.length - 1 ? "prod" : "dev" });
      ids.add(next.id);
    }
    expect(ids.size).toBe(5);
    expect(raw.prepare("SELECT COUNT(*) AS count FROM bot_workflow_permissions").get()).toEqual({ count: 5 });
    expect(raw.prepare(`SELECT ${columns} FROM bot_workflow_permissions WHERE id = ?`).get(first.id)).toEqual(first);
  });

  it("leaves admin upsert free to replace and downgrade merged permissions", async () => {
    const { repo, raw } = fixture();
    const first = await repo.mergeGrants({ ...input, can_execute: 1, can_edit: 1 });
    const downgraded = await repo.upsert({ ...input, can_view: 0, can_execute: 0, can_edit: 0 });
    expect(downgraded).toMatchObject({ id: first.id, can_view: 0, can_execute: 0, can_edit: 0 });
    expect(raw.prepare("SELECT can_view, can_execute, can_edit FROM bot_workflow_permissions WHERE id = ?").get(first.id))
      .toEqual({ can_view: 0, can_execute: 0, can_edit: 0 });
    expect(raw.prepare("SELECT COUNT(*) AS count FROM bot_workflow_permissions").get()).toEqual({ count: 1 });
  });

  it("does not change admin owner-level normalization or nonboolean replacement values", async () => {
    const { repo, raw } = fixture();
    const first = await repo.upsert({ ...input, bot_id: "", can_view: 3 });
    const second = await repo.upsert({ ...input, bot_id: null, can_view: 0, can_edit: 2 });
    expect(second).toMatchObject({ id: first.id, bot_id: null, can_view: 0, can_execute: 0, can_edit: 2 });
    expect(raw.prepare("SELECT COUNT(*) AS count FROM bot_workflow_permissions").get()).toEqual({ count: 1 });
  });

  it("requires the provisioned complete unique index and does not silently fall back", async () => {
    const { repo, raw } = fixture();
    raw.exec("DROP INDEX uk_bot_wf_perm");
    await expect(repo.mergeGrants(input)).rejects.toThrow(/ON CONFLICT.*UNIQUE/i);
    expect(raw.prepare("SELECT COUNT(*) AS count FROM bot_workflow_permissions").get()).toEqual({ count: 0 });
  });

  it("propagates SQLite write failures without attempting readback", async () => {
    const { repo, raw, db } = fixture();
    raw.exec(`CREATE TRIGGER reject_permission_merge BEFORE INSERT ON bot_workflow_permissions
      BEGIN SELECT RAISE(ABORT, 'permission write rejected'); END;`);
    const query = vi.spyOn(db, "query");
    await expect(repo.mergeGrants(input)).rejects.toThrow("permission write rejected");
    expect(query).not.toHaveBeenCalled();
  });
});

describe("BotWorkflowPermissionRepository.mergeGrants SQL and errors", () => {
  it("uses a single SQLite conflict write before an explicit four-key readback", async () => {
    const { db, repo, persisted } = mockDatabase("sqlite");
    vi.spyOn(db.dialect, "now").mockReturnValue(200);
    expect(await repo.mergeGrants(input)).toBe(persisted);
    expect(db.exec).toHaveBeenCalledTimes(1);
    const [sql, params] = db.exec.mock.calls[0];
    expect(compact(sql)).toContain("ON CONFLICT (bot_id, bot_owner_id, workflow_id, env) DO UPDATE SET");
    for (const field of permissionFields) {
      expect(compact(sql)).toContain(`${field} = CASE WHEN bot_workflow_permissions.${field} <> 0 OR excluded.${field} <> 0 THEN 1 ELSE 0 END`);
    }
    expect(compact(sql)).toContain("gmt_modified = excluded.gmt_modified");
    expect(params).toEqual(["default", "owner", "workflow", "dev", 1, 0, 0, 200, 200]);
    expectReadback(db);
  });

  describe.each(["mysql", "zdas"] as const)("%s", dbType => {
    it("uses current database bits OR VALUES(), preserves row identity, and reads back all four keys", async () => {
      const { db, repo, persisted } = mockDatabase(dbType);
      const timestamp = "2026-11-11 12:34:56";
      vi.spyOn(db.dialect, "now").mockReturnValue(timestamp);
      expect(await repo.mergeGrants({ ...input, can_view: -1, can_execute: 0, can_edit: 3 })).toBe(persisted);
      expect(db.exec).toHaveBeenCalledTimes(1);
      const [sql, params] = db.exec.mock.calls[0];
      const normalized = compact(sql);
      expect(normalized).toMatch(/^INSERT INTO bot_workflow_permissions /);
      expect(normalized).toContain("ON DUPLICATE KEY UPDATE");
      for (const field of permissionFields) {
        expect(normalized).toContain(`${field} = CASE WHEN bot_workflow_permissions.${field} <> 0 OR VALUES(${field}) <> 0 THEN 1 ELSE 0 END`);
      }
      expect(normalized).toContain("gmt_modified = VALUES(gmt_modified)");
      const conflictUpdate = normalized.split("ON DUPLICATE KEY UPDATE")[1];
      expect(conflictUpdate).not.toMatch(/\b(?:id|bot_id|bot_owner_id|workflow_id|env|gmt_create)\s*=/);
      expect(normalized).not.toMatch(/\b(?:IGNORE|REPLACE|RETURNING|LAST_INSERT_ID|ON CONFLICT)\b/);
      expect(params).toEqual(["default", "owner", "workflow", "dev", 1, 0, 1, timestamp, timestamp]);
      expectReadback(db);
    });

    it("accepts a legitimate unchanged duplicate write with affectedRows=0", async () => {
      const { db, repo, persisted } = mockDatabase(dbType);
      db.exec.mockResolvedValue({ affectedRows: 0 });
      expect(await repo.mergeGrants(input)).toBe(persisted);
      expectReadback(db);
    });
  });

  describe.each(["sqlite", "mysql", "zdas"] as const)("%s failures", dbType => {
    it("propagates write errors without a success read or fallback", async () => {
      const { db, repo } = mockDatabase(dbType);
      const error = new Error("permission write failed");
      db.exec.mockRejectedValue(error);
      await expect(repo.mergeGrants(input)).rejects.toBe(error);
      expect(db.exec).toHaveBeenCalledTimes(1);
      expect(db.query).not.toHaveBeenCalled();
      expect(db.transaction).not.toHaveBeenCalled();
    });

    it("propagates readback failures", async () => {
      const { db, repo } = mockDatabase(dbType);
      const error = new Error("permission readback failed");
      db.query.mockRejectedValue(error);
      await expect(repo.mergeGrants(input)).rejects.toBe(error);
      expectReadback(db);
    });

    it("rejects missing readback rows instead of inventing a successful persisted grant", async () => {
      const { db, repo } = mockDatabase(dbType);
      db.query.mockResolvedValue([]);
      await expect(repo.mergeGrants(input)).rejects.toThrow(/permission.*(?:not found|missing)|(?:not found|missing).*permission/i);
      expectReadback(db);
    });
  });

  it("rejects the noop adapter even though its neutral dialect is SQLite", async () => {
    const { db, repo } = mockDatabase("noop");
    db.exec.mockResolvedValue({ affectedRows: 0 });
    db.query.mockResolvedValue([]);
    expect(db.dialect.name).toBe("sqlite");
    await expect(repo.mergeGrants(input)).rejects.toThrow(/unsupported.*noop|noop.*unsupported/i);
    expect(db.exec).not.toHaveBeenCalled();
    expect(db.query).not.toHaveBeenCalled();
    expect(db.transaction).not.toHaveBeenCalled();
  });
});
