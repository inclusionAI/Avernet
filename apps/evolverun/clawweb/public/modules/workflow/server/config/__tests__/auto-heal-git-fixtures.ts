import Database from "better-sqlite3";
import { SqliteDatabase } from "@avernet/clawweb-shared/server/db/sqlite-database";
import type { ClawFlowsGitConfig, GitAuthor } from "../../services/clawflows-git-client.js";

export const fallbackGitConfig: ClawFlowsGitConfig = Object.freeze({
  remoteUrl: "https://git.example.com/fallback.git",
  username: "fallback-user",
  email: "fallback-git@example.com",
  token: "fallback-token",
});
export const fallbackAuthor: GitAuthor = Object.freeze({
  name: "Fallback Author",
  email: "fallback-author@example.com",
});

export function createAutoHealConfigTestDb(): SqliteDatabase {
  const raw = new Database(":memory:");
  raw.exec(`
    CREATE TABLE cm_app_config (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      config_key TEXT NOT NULL UNIQUE,
      config_yaml TEXT NOT NULL,
      version INTEGER NOT NULL DEFAULT 1,
      enabled INTEGER NOT NULL DEFAULT 1,
      description TEXT,
      updated_by TEXT,
      gmt_create INTEGER NOT NULL,
      gmt_modified INTEGER NOT NULL
    );
  `);
  return new SqliteDatabase(raw);
}
