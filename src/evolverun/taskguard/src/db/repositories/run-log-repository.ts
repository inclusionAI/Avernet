/**
 * RunLogRepository — persists and queries run_logs records.
 *
 * Stores console log output captured during workflow execution,
 * keyed by flow_id for run archive generation.
 *
 * Best-effort writes: DB failure is logged but doesn't throw.
 */
import type { IDatabase, Row } from "../types.js";
import { nowForDb } from "../types.js";
import type {
  IRunLogRepository,
  RunLogRow,
  RunLogInsert,
} from "./types.js";

const INSERT_SQL = `INSERT INTO run_logs (
  flow_id, node_id, level, source, message, timestamp, seq,
  gmt_create, gmt_modified
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)`;

const BATCH_INSERT_SQL = `INSERT INTO run_logs (
  flow_id, node_id, level, source, message, timestamp, seq,
  gmt_create, gmt_modified
) VALUES `;

function rowToRunLog(row: Row): RunLogRow {
  return {
    id: Number(row.id),
    flow_id: String(row.flow_id),
    node_id: row.node_id != null ? String(row.node_id) : null,
    level: String(row.level),
    source: row.source != null ? String(row.source) : null,
    message: String(row.message),
    timestamp: Number(row.timestamp),
    seq: Number(row.seq),
    gmt_create: Number(row.gmt_create),
    gmt_modified: row.gmt_modified != null ? Number(row.gmt_modified) : null,
  };
}

export class RunLogRepository implements IRunLogRepository {
  constructor(private db: IDatabase) {}

  async insertBatch(entries: RunLogInsert[]): Promise<number> {
    if (entries.length === 0) return 0;
    const now = nowForDb(this.db.dbType);
    try {
      const placeholders = entries.map(() => "(?, ?, ?, ?, ?, ?, ?, ?, ?)").join(", ");
      const params: unknown[] = [];
      for (const e of entries) {
        params.push(
          e.flow_id,
          e.node_id,
          e.level,
          e.source,
          e.message,
          e.timestamp,
          e.seq,
          now,
          now,
        );
      }
      await this.db.exec(`${BATCH_INSERT_SQL} ${placeholders}`, params);
      return entries.length;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] RunLogRepository.insertBatch failed: ${msg}`);
      return 0;
    }
  }

  async findByFlowId(flowId: string): Promise<RunLogRow[]> {
    try {
      const rows = await this.db.query<Row>(
        `SELECT * FROM run_logs WHERE flow_id = ? ORDER BY seq ASC`,
        [flowId],
      );
      return rows.map(rowToRunLog);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] RunLogRepository.findByFlowId failed: ${msg}`);
      return [];
    }
  }

  async deleteByFlowId(flowId: string): Promise<number> {
    try {
      const result = await this.db.exec(
        `DELETE FROM run_logs WHERE flow_id = ?`,
        [flowId],
      );
      return result.affectedRows ?? 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] RunLogRepository.deleteByFlowId failed: ${msg}`);
      return 0;
    }
  }

  async deleteOlderThan(olderThan: number): Promise<number> {
    try {
      const result = await this.db.exec(
        `DELETE FROM run_logs WHERE gmt_create < ?`,
        [olderThan],
      );
      return result.affectedRows ?? 0;
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(`[db] RunLogRepository.deleteOlderThan failed: ${msg}`);
      return 0;
    }
  }
}
