/**
 * Repository for http_callback_configs table — per-workflow HTTP callback notification settings.
 * Read-only in ClawMind; writes are managed by clawweb frontend.
 */
import type { IDatabase } from "../types.js";
import type {
  IHttpCallbackConfigRepository,
  HttpCallbackConfigRow,
  HttpCallbackConfigInsert,
} from "../../alerts/http-callback-types.js";
import { nowForDb } from "../types.js";

export class HttpCallbackConfigRepository implements IHttpCallbackConfigRepository {
  constructor(private db: IDatabase) {}

  async findByWorkflowId(workflowId: string): Promise<HttpCallbackConfigRow[]> {
    return this.db.query<HttpCallbackConfigRow>(
      `SELECT id, config_id, workflow_id, name, url, secret, enabled, notify_on,
              timeout_ms, max_retries, retry_delay_ms, include_node_output,
              gmt_create, gmt_modified
       FROM http_callback_configs WHERE workflow_id = ?`,
      [workflowId],
    );
  }

  async findByConfigId(configId: string): Promise<HttpCallbackConfigRow | null> {
    const rows = await this.db.query<HttpCallbackConfigRow>(
      `SELECT id, config_id, workflow_id, name, url, secret, enabled, notify_on,
              timeout_ms, max_retries, retry_delay_ms, include_node_output,
              gmt_create, gmt_modified
       FROM http_callback_configs WHERE config_id = ?`,
      [configId],
    );
    return rows[0] ?? null;
  }

  async findAllWorkflowIds(): Promise<string[]> {
    const rows = await this.db.query<{ workflow_id: string }>(
      `SELECT DISTINCT workflow_id FROM http_callback_configs`,
      [],
    );
    return rows.map((r) => r.workflow_id);
  }

  async insert(config: HttpCallbackConfigInsert): Promise<number> {
    const result = await this.db.exec(
      `INSERT INTO http_callback_configs
         (config_id, workflow_id, name, url, secret, enabled, notify_on,
          timeout_ms, max_retries, retry_delay_ms, include_node_output)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        config.configId,
        config.workflowId,
        config.name,
        config.url,
        config.secret ?? null,
        config.enabled ?? 1,
        config.notifyOn,
        config.timeoutMs ?? 5000,
        config.maxRetries ?? 2,
        config.retryDelayMs ?? 1000,
        config.includeNodeOutput ?? 0,
      ],
    );
    return result.insertId ?? 0;
  }

  async update(configId: string, config: Partial<HttpCallbackConfigInsert>): Promise<boolean> {
    const sets: string[] = [];
    const values: unknown[] = [];

    if (config.name !== undefined) { sets.push("name = ?"); values.push(config.name); }
    if (config.url !== undefined) { sets.push("url = ?"); values.push(config.url); }
    if (config.secret !== undefined) { sets.push("secret = ?"); values.push(config.secret ?? null); }
    if (config.enabled !== undefined) { sets.push("enabled = ?"); values.push(config.enabled); }
    if (config.notifyOn !== undefined) { sets.push("notify_on = ?"); values.push(config.notifyOn); }
    if (config.timeoutMs !== undefined) { sets.push("timeout_ms = ?"); values.push(config.timeoutMs); }
    if (config.maxRetries !== undefined) { sets.push("max_retries = ?"); values.push(config.maxRetries); }
    if (config.retryDelayMs !== undefined) { sets.push("retry_delay_ms = ?"); values.push(config.retryDelayMs); }
    if (config.includeNodeOutput !== undefined) { sets.push("include_node_output = ?"); values.push(config.includeNodeOutput); }

    if (sets.length === 0) return false;

    sets.push("gmt_modified = ?");
    values.push(nowForDb(this.db.dbType));
    values.push(configId);

    const result = await this.db.exec(
      `UPDATE http_callback_configs SET ${sets.join(", ")} WHERE config_id = ?`,
      values,
    );
    return (result.affectedRows ?? 0) > 0;
  }

  async deleteByConfigId(configId: string): Promise<boolean> {
    const result = await this.db.exec(
      `DELETE FROM http_callback_configs WHERE config_id = ?`,
      [configId],
    );
    return (result.affectedRows ?? 0) > 0;
  }

  async deleteByWorkflowId(workflowId: string): Promise<number> {
    const result = await this.db.exec(
      `DELETE FROM http_callback_configs WHERE workflow_id = ?`,
      [workflowId],
    );
    return result.affectedRows ?? 0;
  }
}