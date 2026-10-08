import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type RunReadScope = { botId: string; ownerId: string };
export type RunListQuery = RunReadScope & { limit?: number; beforeId?: number; workflowId?: string; status?: string; identityKey?: string; includeHidden?: boolean };
export type RunLogQuery = RunReadScope & { flowId: string; limit?: number; afterId?: number; nodeId?: string; level?: string };
export type ReadPage = { items: Record<string, unknown>[]; nextCursor: number | null };

function scopeKey(scope: RunReadScope): string {
  if (!scope.botId?.trim() || !scope.ownerId?.trim()) throw new Error("Missing bot/owner scope");
  return `${scope.botId}:${scope.ownerId}`;
}
function pageLimit(value = 20): number {
  if (!Number.isSafeInteger(value) || value < 1 || value > 50) throw new Error("limit must be an integer between 1 and 50");
  return value;
}
function cursor(value?: number): void {
  if (value !== undefined && (!Number.isSafeInteger(value) || value < 0)) throw new Error("Invalid cursor");
}
function page(rows: Record<string, unknown>[], limit: number): ReadPage {
  return { items: rows.slice(0, limit), nextCursor: rows.length > limit ? Number(rows[limit - 1].id) : null };
}

/** Internal service callers supply bot scope from runtime configuration, never tool arguments.
 * Exact origin ownership deliberately excludes legacy rows without provable ownership. */
export class RunReadRepository {
  constructor(private db: IDatabase) {}

  async listRuns(q: RunListQuery): Promise<ReadPage> {
    const key = scopeKey(q); const limit = pageLimit(q.limit); cursor(q.beforeId);
    const where = ["origin_bot_id = ?"]; const args: unknown[] = [key];
    for (const [col, val] of [["workflow_id", q.workflowId], ["status", q.status], ["identity_key", q.identityKey]]) {
      if (val) { where.push(`${col} = ?`); args.push(val); }
    }
    if (q.beforeId !== undefined) { where.push("id < ?"); args.push(q.beforeId); }
    if (!q.includeHidden) where.push("COALESCE(CAST(JSON_EXTRACT(state_json, '$.workflowData.flowHidden') AS CHAR), 'false') NOT IN ('true', '1')");
    const rows = await this.db.query<Record<string, unknown>>(
      `SELECT id, flow_id, workflow_id, status, origin_bot_id, identity_key, started_at, gmt_modified
       FROM flow_runs WHERE ${where.join(" AND ")} ORDER BY id DESC LIMIT ?`, [...args, limit + 1]);
    return page(rows, limit);
  }

  async readLogs(q: RunLogQuery): Promise<ReadPage> {
    const key = scopeKey(q); const limit = pageLimit(q.limit); cursor(q.afterId);
    const owned = await this.db.query(`SELECT flow_id FROM flow_runs WHERE flow_id = ? AND origin_bot_id = ?`, [q.flowId, key]);
    if (!owned.length) throw new Error("Flow not found in bot/owner scope");
    const where = ["flow_id = ?", "id > ?"]; const args: unknown[] = [q.flowId, q.afterId ?? 0];
    for (const [col, val] of [["node_id", q.nodeId], ["level", q.level]]) {
      if (val) { where.push(`${col} = ?`); args.push(val); }
    }
    const rows = await this.db.query<Record<string, unknown>>(
      `SELECT id, node_id, level, source, SUBSTR(message, 1, 2000) AS message,
       ${this.db.dialect.driver === "mysql2" ? "CHAR_LENGTH" : "LENGTH"}(message) AS message_length, timestamp FROM run_logs
       WHERE ${where.join(" AND ")} ORDER BY id ASC LIMIT ?`, [...args, limit + 1]);
    return page(rows, limit);
  }
}
