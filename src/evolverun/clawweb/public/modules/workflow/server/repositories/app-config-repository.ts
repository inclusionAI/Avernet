/**
 * Repository for cm_app_config table — DB-stored ClawMind application configuration.
 *
 * Each row stores one top-level YAML config section (e.g. "execution", "teclaw", "git").
 * The local application.yaml retains only bootstrap sections (api, database).
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type AppConfigRow = {
  id: number;
  config_key: string;
  config_yaml: string;
  version: number;
  enabled: number;
  description: string | null;
  updated_by: string | null;
  gmt_create: number | string;
  gmt_modified: number | string;
};

export type CreateAppConfigInput = {
  config_key: string;
  config_yaml: string;
  description?: string;
  updated_by?: string;
};

export type UpdateAppConfigInput = {
  config_yaml?: string;
  enabled?: number;
  description?: string;
  updated_by?: string;
};

const SELECT_COLUMNS = `id, config_key, config_yaml, version, enabled, description, updated_by, gmt_create, gmt_modified`;

export class AppConfigRepository {
  constructor(private db: IDatabase) {}

  async listAll(enabledOnly = false): Promise<AppConfigRow[]> {
    const sql = enabledOnly
      ? `SELECT ${SELECT_COLUMNS} FROM cm_app_config WHERE enabled = 1 ORDER BY config_key`
      : `SELECT ${SELECT_COLUMNS} FROM cm_app_config ORDER BY config_key`;
    return this.db.query<AppConfigRow>(sql);
  }

  async findByKey(configKey: string): Promise<AppConfigRow | null> {
    const rows = await this.db.query<AppConfigRow>(
      `SELECT ${SELECT_COLUMNS} FROM cm_app_config WHERE config_key = ?`,
      [configKey],
    );
    return rows[0] ?? null;
  }

  async create(input: CreateAppConfigInput): Promise<AppConfigRow> {
    const now = this.db.dialect.now();
    await this.db.exec(
      `INSERT INTO cm_app_config (config_key, config_yaml, version, enabled, description, updated_by, gmt_create, gmt_modified)
       VALUES (?, ?, 1, 1, ?, ?, ?, ?)`,
      [
        input.config_key,
        input.config_yaml,
        input.description ?? null,
        input.updated_by ?? null,
        now,
        now,
      ],
    );
    const result = await this.findByKey(input.config_key);
    return result!;
  }

  async update(configKey: string, input: UpdateAppConfigInput): Promise<AppConfigRow | null> {
    const existing = await this.findByKey(configKey);
    if (!existing) return null;

    const now = this.db.dialect.now();
    const sets: string[] = [];
    const values: unknown[] = [];

    if (input.config_yaml !== undefined) {
      sets.push("config_yaml = ?");
      values.push(input.config_yaml);
    }
    if (input.enabled !== undefined) {
      sets.push("enabled = ?");
      values.push(input.enabled);
    }
    if (input.description !== undefined) {
      sets.push("description = ?");
      values.push(input.description);
    }
    if (input.updated_by !== undefined) {
      sets.push("updated_by = ?");
      values.push(input.updated_by);
    }

    if (sets.length === 0) return existing;

    // Optimistic lock: increment version; application-layer gmt_modified (no trigger)
    sets.push("version = ?");
    values.push(existing.version + 1);
    sets.push("gmt_modified = ?");
    values.push(now);
    values.push(configKey);

    await this.db.exec(
      `UPDATE cm_app_config SET ${sets.join(", ")} WHERE config_key = ?`,
      values,
    );
    return this.findByKey(configKey);
  }

  async delete(configKey: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM cm_app_config WHERE config_key = ?",
      [configKey],
    );
    return result.affectedRows > 0;
  }
}
