/**
 * Repository for http_callback_configs table — per-workflow HTTP callback notification settings.
 * Managed via clawweb UI; read by ClawMind engine at runtime.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type HttpCallbackConfigRow = {
  id: number;
  config_id: string;
  workflow_id: string;
  name: string;
  url: string;
  secret: string;
  enabled: number; // 0 | 1
  notify_on: string; // JSON array string
  timeout_ms: number;
  max_retries: number;
  retry_delay_ms: number;
  include_node_output: number; // 0 | 1
  gmt_create: number;
  gmt_modified: number;
};

export type HttpCallbackConfigCreate = {
  configId: string;
  workflowId: string;
  name: string;
  url: string;
  secret: string;
  enabled?: boolean;
  notifyOn: string[]; // Will be JSON-serialized
  timeoutMs?: number;
  maxRetries?: number;
  retryDelayMs?: number;
  includeNodeOutput?: boolean;
};

export type HttpCallbackConfigUpdate = Partial<Omit<HttpCallbackConfigCreate, "configId" | "workflowId">>;

const SELECT_COLUMNS = `id, config_id, workflow_id, name, url, secret, enabled, notify_on,
  timeout_ms, max_retries, retry_delay_ms, include_node_output, gmt_create, gmt_modified` as const;

export class HttpCallbackConfigRepository {
  constructor(private db: IDatabase) {}

  async findByWorkflowId(workflowId: string): Promise<HttpCallbackConfigRow[]> {
    return this.db.query<HttpCallbackConfigRow>(
      `SELECT ${SELECT_COLUMNS} FROM http_callback_configs WHERE workflow_id = ? ORDER BY gmt_create`,
      [workflowId],
    );
  }

  async findByConfigId(configId: string): Promise<HttpCallbackConfigRow | null> {
    const rows = await this.db.query<HttpCallbackConfigRow>(
      `SELECT ${SELECT_COLUMNS} FROM http_callback_configs WHERE config_id = ?`,
      [configId],
    );
    return rows[0] ?? null;
  }

  async findAll(limit: number = 100, offset: number = 0): Promise<HttpCallbackConfigRow[]> {
    return this.db.query<HttpCallbackConfigRow>(
      `SELECT ${SELECT_COLUMNS} FROM http_callback_configs ORDER BY gmt_create DESC LIMIT ? OFFSET ?`,
      [limit, offset],
    );
  }

  async insert(data: HttpCallbackConfigCreate): Promise<HttpCallbackConfigRow> {
    const now = this.db.dialect.now();
    const enabled = data.enabled !== false ? 1 : 0;
    const notifyOn = JSON.stringify(data.notifyOn);
    const timeoutMs = data.timeoutMs ?? 5000;
    const maxRetries = data.maxRetries ?? 2;
    const retryDelayMs = data.retryDelayMs ?? 1000;
    const includeNodeOutput = data.includeNodeOutput ? 1 : 0;

    await this.db.exec(
      `INSERT INTO http_callback_configs
         (config_id, workflow_id, name, url, secret, enabled, notify_on,
          timeout_ms, max_retries, retry_delay_ms, include_node_output, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [data.configId, data.workflowId, data.name, data.url, data.secret, enabled, notifyOn,
       timeoutMs, maxRetries, retryDelayMs, includeNodeOutput, now, now],
    );

    const result = await this.findByConfigId(data.configId);
    return result!;
  }

  async update(configId: string, data: HttpCallbackConfigUpdate): Promise<HttpCallbackConfigRow | null> {
    const sets: string[] = [];
    const values: unknown[] = [];

    if (data.name !== undefined) { sets.push("name = ?"); values.push(data.name); }
    if (data.url !== undefined) { sets.push("url = ?"); values.push(data.url); }
    if (data.secret !== undefined) { sets.push("secret = ?"); values.push(data.secret); }
    if (data.enabled !== undefined) { sets.push("enabled = ?"); values.push(data.enabled ? 1 : 0); }
    if (data.notifyOn !== undefined) { sets.push("notify_on = ?"); values.push(JSON.stringify(data.notifyOn)); }
    if (data.timeoutMs !== undefined) { sets.push("timeout_ms = ?"); values.push(data.timeoutMs); }
    if (data.maxRetries !== undefined) { sets.push("max_retries = ?"); values.push(data.maxRetries); }
    if (data.retryDelayMs !== undefined) { sets.push("retry_delay_ms = ?"); values.push(data.retryDelayMs); }
    if (data.includeNodeOutput !== undefined) { sets.push("include_node_output = ?"); values.push(data.includeNodeOutput ? 1 : 0); }

    if (sets.length === 0) return this.findByConfigId(configId);

    sets.push("gmt_modified = ?");
    values.push(this.db.dialect.now());
    values.push(configId);

    await this.db.exec(
      `UPDATE http_callback_configs SET ${sets.join(", ")} WHERE config_id = ?`,
      values,
    );

    return this.findByConfigId(configId);
  }

  async deleteByConfigId(configId: string): Promise<boolean> {
    const result = await this.db.exec(
      "DELETE FROM http_callback_configs WHERE config_id = ?",
      [configId],
    );
    return result.affectedRows > 0;
  }

  async deleteByWorkflowId(workflowId: string): Promise<number> {
    const result = await this.db.exec(
      "DELETE FROM http_callback_configs WHERE workflow_id = ?",
      [workflowId],
    );
    return result.affectedRows;
  }

  /** Cascade update workflow_id when a workflow is renamed */
  async updateWorkflowId(oldWorkflowId: string, newWorkflowId: string): Promise<void> {
    await this.db.exec(
      "UPDATE http_callback_configs SET workflow_id = ? WHERE workflow_id = ?",
      [newWorkflowId, oldWorkflowId],
    );
  }
}