/**
 * Repository for http_callback_logs table — audit log of every HTTP callback
 * dispatch attempt (including retries).
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type HttpCallbackLogRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  config_id: string;
  config_name: string | null;
  callback_url: string;
  notify_event: string;
  node_id: string | null;
  attempt: number;
  max_attempts: number;
  request_body: string | null;
  request_headers: string | null;
  response_status_code: number | null;
  response_body: string | null;
  duration_ms: number | null;
  status: string;
  error_message: string | null;
  callback_source: string;
  gmt_create: number;
  gmt_modified: number;
};

export type HttpCallbackLogInsert = {
  flow_id: string;
  workflow_id: string;
  config_id: string;
  config_name: string | null;
  callback_url: string;
  notify_event: string;
  node_id: string | null;
  attempt: number;
  max_attempts: number;
  request_body: string | null;
  request_headers: string | null;
  response_status_code: number | null;
  response_body: string | null;
  duration_ms: number | null;
  status: string;
  error_message: string | null;
  /** 'workflow-level' | 'platform-level' — defaults to 'workflow-level' for backward compatibility */
  callbackSource?: string;
};

const SELECT_COLUMNS = `id, flow_id, workflow_id, config_id, config_name, callback_url,
  notify_event, node_id, attempt, max_attempts, request_body, request_headers,
  response_status_code, response_body, duration_ms, status, error_message,
  callback_source, gmt_create, gmt_modified` as const;

export class HttpCallbackLogRepository {
  constructor(private db: IDatabase) {}

  async insert(log: HttpCallbackLogInsert): Promise<number> {
    const now = this.db.dialect.now();
    const result = await this.db.exec(
      `INSERT INTO http_callback_logs
         (flow_id, workflow_id, config_id, config_name, callback_url,
          notify_event, node_id, attempt, max_attempts,
          request_body, request_headers,
          response_status_code, response_body,
          duration_ms, status, error_message,
          callback_source,
          gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        log.flow_id,
        log.workflow_id,
        log.config_id,
        log.config_name,
        log.callback_url,
        log.notify_event,
        log.node_id,
        log.attempt,
        log.max_attempts,
        log.request_body,
        log.request_headers,
        log.response_status_code,
        log.response_body,
        log.duration_ms,
        log.status,
        log.error_message,
        log.callbackSource ?? 'workflow-level',
        now,
        now,
      ],
    );
    return result.insertId ?? 0;
  }

  async findByFlowId(flowId: string, limit = 100): Promise<HttpCallbackLogRow[]> {
    return this.db.query<HttpCallbackLogRow>(
      `SELECT ${SELECT_COLUMNS} FROM http_callback_logs WHERE flow_id = ? ORDER BY gmt_create DESC LIMIT ?`,
      [flowId, limit],
    );
  }

  async findByWorkflowId(workflowId: string, limit = 100): Promise<HttpCallbackLogRow[]> {
    return this.db.query<HttpCallbackLogRow>(
      `SELECT ${SELECT_COLUMNS} FROM http_callback_logs WHERE workflow_id = ? ORDER BY gmt_create DESC LIMIT ?`,
      [workflowId, limit],
    );
  }

  async findByStatus(status: string, limit = 100): Promise<HttpCallbackLogRow[]> {
    return this.db.query<HttpCallbackLogRow>(
      `SELECT ${SELECT_COLUMNS} FROM http_callback_logs WHERE status = ? ORDER BY gmt_create DESC LIMIT ?`,
      [status, limit],
    );
  }

  async deleteOlderThan(timestamp: number): Promise<number> {
    const result = await this.db.exec(
      `DELETE FROM http_callback_logs WHERE gmt_create < ?`,
      [timestamp],
    );
    return result.affectedRows ?? 0;
  }
}