/**
 * RunLogRepository — persists and queries run_logs records.
 *
 * Stores console log output captured during workflow execution,
 * keyed by flow_id for run archive generation.
 */
import type { IDatabase, Row } from "../db.js";

export type RunLogInsert = {
  flow_id: string;
  node_id: string | null;
  level: string;
  source: string | null;
  message: string;
  timestamp: number;
  seq: number;
};

export type RunLogRow = RunLogInsert & {
  id: number;
  gmt_create: number;
  gmt_modified: number | null;
};

export class RunLogRepository {
  constructor(private db: IDatabase) {}

  async insertBatch(entries: RunLogInsert[]): Promise<number> {
    if (entries.length === 0) return 0;
    try {
      // 7 columns — gmt_create / gmt_modify are omitted: both have
      // DEFAULT CURRENT_TIMESTAMP in production MySQL and DEFAULT (unixepoch())
      // in SQLite, so the DB fills them automatically.
      const placeholders = entries.map(() => "(?, ?, ?, ?, ?, ?, ?)").join(", ");
      const params: unknown[] = [];
      for (const e of entries) {
        params.push(e.flow_id, e.node_id, e.level, e.source, e.message, e.timestamp, e.seq);
      }
      const sql = `INSERT INTO run_logs (flow_id, node_id, level, source, message, timestamp, seq) VALUES ${placeholders}`;
      const result = await this.db.exec(sql, params);
      // Check affectedRows — if 0, the INSERT succeeded syntactically but wrote
      // no rows (e.g. table missing, constraint violation silently ignored, or
      // NoOpDatabase fallback). Log a warning so operators can diagnose.
      if (result.affectedRows === 0) {
        console.warn(
          `[db] RunLogRepository.insertBatch: affectedRows=0 for ${entries.length} entries. ` +
          `dbType=${this.db.dbType}. Check: table exists? constraints? NoOp fallback?`,
        );
        return 0;
      }
      if (result.affectedRows < entries.length) {
        console.warn(
          `[db] RunLogRepository.insertBatch: partial insert — ` +
          `sent=${entries.length} affectedRows=${result.affectedRows}`,
        );
      }
      return result.affectedRows;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      const stack = error instanceof Error ? (error.stack ?? "").split("\n").slice(0, 3).join(" | ") : "";
      console.warn(
        `[db] RunLogRepository.insertBatch failed for ${entries.length} entries: ${msg}` +
        (stack ? ` | stack: ${stack}` : ""),
      );
      return 0;
    }
  }

  async findByFlowId(flowId: string): Promise<RunLogRow[]> {
    try {
      const rows = await this.db.query<Row>(
        `SELECT * FROM run_logs WHERE flow_id = ? ORDER BY seq ASC`,
        [flowId],
      );
      return rows.map((r) => ({
        id: Number(r.id),
        flow_id: String(r.flow_id),
        node_id: r.node_id != null ? String(r.node_id) : null,
        level: String(r.level),
        source: r.source != null ? String(r.source) : null,
        message: String(r.message),
        timestamp: Number(r.timestamp),
        seq: Number(r.seq),
        gmt_create: Number(r.gmt_create),
        gmt_modified: r.gmt_modified != null ? Number(r.gmt_modified) : null,
      }));
    } catch (error) {
      console.warn(`[db] RunLogRepository.findByFlowId failed: ${error instanceof Error ? error.message : error}`);
      return [];
    }
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(`DELETE FROM run_logs WHERE flow_id = ?`, [flowId]);
      return result.affectedRows ?? 0;
    } catch {
      return 0;
    }
  }
}
