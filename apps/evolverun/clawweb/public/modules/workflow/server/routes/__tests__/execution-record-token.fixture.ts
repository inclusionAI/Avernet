import { createHash } from "node:crypto";
import type Database from "better-sqlite3";

// Synthetic fixtures only. Production credentials are never read by these tests.
export const executionRecordTestToken = "ab".repeat(32);
export const executionRecordTokenHeaders = { authorization: `Bearer ${executionRecordTestToken}` };
export const executionRecordUnauthorized = { error: "Unauthorized", message: "Invalid execution record token" };
export const executionRecordLegacyWorkflow = { workflow_yaml: null, workflow_snapshot: { status: "unresolved", reason: "LEGACY_MISSING" } } as const;
export const executionRecordAuthUnavailable = { error: "Service Unavailable", message: "Execution record authentication unavailable" };

export function tokenConfigYaml(token?: string): string {
  return `execution_record_sha256: "${createHash("sha256").update(token ?? executionRecordTestToken, "utf8").digest("hex")}"\n`;
}

export function seedExecutionRecordToken(raw: Database.Database, token?: string): void {
  raw.exec(`CREATE TABLE IF NOT EXISTS cm_app_config (
    id INTEGER PRIMARY KEY, config_key TEXT NOT NULL UNIQUE, config_yaml TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1, enabled INTEGER NOT NULL DEFAULT 1,
    description TEXT, updated_by TEXT, gmt_create INTEGER, gmt_modified INTEGER
  )`);
  raw.prepare("DELETE FROM cm_app_config WHERE config_key = ?").run("api_token");
  raw.prepare("INSERT INTO cm_app_config (config_key, config_yaml, enabled) VALUES (?, ?, 1)")
    .run("api_token", tokenConfigYaml(token));
}
