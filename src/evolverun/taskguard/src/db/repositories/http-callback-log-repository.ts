/**
 * Repository for http_callback_logs table — audit log of every HTTP callback
 * dispatch attempt (including retries).
 *
 * Writes are fire-and-forget from HttpCallbackDispatcher; reads are for
 * audit/troubleshooting queries via clawweb.
 */
import type { IDatabase } from "../types.js";
import type {
  IHttpCallbackLogRepository,
  HttpCallbackLogRow,
  HttpCallbackLogInsert,
} from "../../alerts/http-callback-types.js";
import { nowForDb } from "../types.js";

export class HttpCallbackLogRepository implements IHttpCallbackLogRepository {
  constructor(private db: IDatabase) {}

  async insert(log: HttpCallbackLogInsert): Promise<number> {
    const now = nowForDb(this.db.dbType);
    const result = await this.db.exec(
      `INSERT INTO http_callback_logs
         (flow_id, workflow_id, config_id, config_name, callback_url,
          notify_event, node_id, attempt, max_attempts,
          request_body, request_headers,
          response_status_code, response_body,
          duration_ms, status, error_message,
          gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [
        log.flowId,
        log.workflowId,
        log.configId,
        log.configName,
        log.callbackUrl,
        log.notifyEvent,
        log.nodeId,
        log.attempt,
        log.maxAttempts,
        log.requestBody,
        log.requestHeaders,
        log.responseStatusCode,
        log.responseBody,
        log.durationMs,
        log.status,
        log.errorMessage,
        now,
        now,
      ],
    );
    return result.insertId ?? 0;
  }

  async findByFlowId(flowId: string, limit = 100): Promise<HttpCallbackLogRow[]> {
    return this.db.query<HttpCallbackLogRow>(
      `SELECT * FROM http_callback_logs WHERE flow_id = ? ORDER BY gmt_create DESC LIMIT ?`,
      [flowId, limit],
    );
  }

  async findByWorkflowId(workflowId: string, limit = 100): Promise<HttpCallbackLogRow[]> {
    return this.db.query<HttpCallbackLogRow>(
      `SELECT * FROM http_callback_logs WHERE workflow_id = ? ORDER BY gmt_create DESC LIMIT ?`,
      [workflowId, limit],
    );
  }

  async findByStatus(status: string, limit = 100): Promise<HttpCallbackLogRow[]> {
    return this.db.query<HttpCallbackLogRow>(
      `SELECT * FROM http_callback_logs WHERE status = ? ORDER BY gmt_create DESC LIMIT ?`,
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