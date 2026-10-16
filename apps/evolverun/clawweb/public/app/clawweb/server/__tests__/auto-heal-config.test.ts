import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import Database from "better-sqlite3";
import { sqliteDialect } from "@avernet/clawweb-shared/server/db/dialect";
import { AppConfigRepository } from "@avernet/workflow/server/repositories/app-config-repository";
import { resolveAutoHealGitConfigFromDb, resolveAutoHealGitSettings } from "../auto-heal-config.js";
import * as sharedGitConfig from "@avernet/workflow/server/config/auto-heal-git-config";

function createTestDb() {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE cm_app_config (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      config_key VARCHAR(64) NOT NULL,
      config_yaml TEXT NOT NULL,
      version INTEGER NOT NULL DEFAULT 1,
      enabled INTEGER NOT NULL DEFAULT 1,
      description TEXT,
      updated_by VARCHAR(64),
      gmt_create INTEGER NOT NULL DEFAULT (unixepoch()),
      gmt_modified INTEGER NOT NULL DEFAULT (unixepoch())
    );
    CREATE UNIQUE INDEX uk_cm_app_config_key ON cm_app_config (config_key);
  `);

  const db = {
    dbType: "sqlite" as const,
    dialect: sqliteDialect,
    query: <T,>(sql: string, params: unknown[] = []) => {
      const stmt = raw.prepare(sql);
      return (params.length ? stmt.all(...(params as never[])) : stmt.all()) as T[];
    },
    exec: async (sql: string, params: unknown[] = []) => {
      const stmt = raw.prepare(sql);
      const r = params.length ? stmt.run(...(params as never[])) : stmt.run();
      return { affectedRows: r.changes, insertId: r.lastInsertRowid as number };
    },
    transaction: async <T,>(fn: (tx: typeof db) => Promise<T>) => fn(db),
    close: async () => raw.close(),
  };
  return { db: db as never };
}

describe("auto-heal config compatibility exports", () => {
  it("re-exports the shared resolver implementations without wrapping them", () => {
    expect(resolveAutoHealGitConfigFromDb).toBe(sharedGitConfig.resolveAutoHealGitConfigFromDb);
    expect(resolveAutoHealGitSettings).toBe(sharedGitConfig.resolveAutoHealGitSettings);
  });
});

describe("resolveAutoHealGitConfigFromDb", () => {
  let ctx: ReturnType<typeof createTestDb>;

  beforeEach(() => {
    ctx = createTestDb();
  });

  afterEach(async () => {
    await ctx.db.close();
  });

  it("returns empty config when no git config row exists", async () => {
    const config = await resolveAutoHealGitConfigFromDb(ctx.db as never);
    expect(config).toEqual({
      remoteUrl: "",
      username: "",
      email: "",
      token: "",
      authorName: "",
      authorEmail: "",
    });
  });

  it("returns empty config when git config is disabled", async () => {
    const repo = new AppConfigRepository(ctx.db as never);
    await repo.create({
      config_key: "git",
      config_yaml: "remoteUrl: https://git.example.com/clawflows.git\nusername: user\n",
    });
    await repo.update("git", { enabled: 0 });
    const config = await resolveAutoHealGitConfigFromDb(ctx.db as never);
    expect(config.remoteUrl).toBe("");
  });

  it("parses git config from cm_app_config", async () => {
    const repo = new AppConfigRepository(ctx.db as never);
    await repo.create({
      config_key: "git",
      config_yaml: `
remoteUrl: "https://git.example.com/clawflows.git"
username: "operator"
token: "secret-token"
email: "operator@example.com"
authorName: "Operator"
authorEmail: "operator@example.com"
      `.trim(),
    });

    const config = await resolveAutoHealGitConfigFromDb(ctx.db as never);
    expect(config).toEqual({
      remoteUrl: "https://git.example.com/clawflows.git",
      username: "operator",
      email: "operator@example.com",
      token: "secret-token",
      authorName: "Operator",
      authorEmail: "operator@example.com",
    });
  });

  it("ignores non-string fields", async () => {
    const repo = new AppConfigRepository(ctx.db as never);
    await repo.create({
      config_key: "git",
      config_yaml: `
remoteUrl: "https://git.example.com/clawflows.git"
username: 123
token: true
      `.trim(),
    });

    const config = await resolveAutoHealGitConfigFromDb(ctx.db as never);
    expect(config.remoteUrl).toBe("https://git.example.com/clawflows.git");
    expect(config.username).toBe("");
    expect(config.token).toBe("");
  });

  it("warns and returns empty config on invalid yaml", async () => {
    const repo = new AppConfigRepository(ctx.db as never);
    await repo.create({
      config_key: "git",
      config_yaml: "remoteUrl: \"unclosed",
    });

    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const config = await resolveAutoHealGitConfigFromDb(ctx.db as never);
    expect(config.remoteUrl).toBe("");
    expect(warnSpy).toHaveBeenCalledWith(expect.stringContaining("Failed to parse cm_app_config git config"));
    warnSpy.mockRestore();
  });
});
