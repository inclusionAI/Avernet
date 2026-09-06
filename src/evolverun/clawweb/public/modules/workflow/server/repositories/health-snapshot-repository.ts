/**
 * HealthSnapshotRepository — stores and queries daily health score snapshots.
 */
import type { IDatabase, Row } from "@avernet/clawweb-shared/server/db";

export type HealthSnapshot = {
  id: number;
  workflow_id: string;
  snapshot_date: string;
  overall_score: number;
  success_rate: number;
  node_failure_rate: number;
  p95_duration_ms: number | null;
  retry_rate: number | null;
  total_tokens: number | null;
};

export class HealthSnapshotRepository {
  constructor(private db: IDatabase) {}

  async upsertSnapshot(snap: Omit<HealthSnapshot, "id">): Promise<void> {
    const fields = "(workflow_id, snapshot_date, overall_score, success_rate, node_failure_rate, p95_duration_ms, retry_rate, total_tokens)";
    const values = "(?, ?, ?, ?, ?, ?, ?, ?)";
    const params = [snap.workflow_id, snap.snapshot_date, snap.overall_score, snap.success_rate,
                    snap.node_failure_rate, snap.p95_duration_ms, snap.retry_rate, snap.total_tokens];
    if (this.db.dbType === "sqlite") {
      await this.db.exec(
        `INSERT OR REPLACE INTO workflow_health_snapshots ${fields} VALUES ${values}`,
        params,
      );
    } else {
      // MySQL / ZDAS / OceanBase compatible upsert
      await this.db.exec(
        `INSERT INTO workflow_health_snapshots ${fields} VALUES ${values}
         ON DUPLICATE KEY UPDATE
          overall_score = VALUES(overall_score),
          success_rate = VALUES(success_rate),
          node_failure_rate = VALUES(node_failure_rate),
          p95_duration_ms = VALUES(p95_duration_ms),
          retry_rate = VALUES(retry_rate),
          total_tokens = VALUES(total_tokens)`,
        params,
      );
    }
  }

  async findByWorkflowAndDays(workflowId: string, days: number): Promise<HealthSnapshot[]> {
    const cutoffDate = new Date(Date.now() - days * 86400000).toISOString().slice(0, 10);
    const rows = await this.db.query<Row>(
      `SELECT * FROM workflow_health_snapshots
       WHERE workflow_id = ? AND snapshot_date >= ?
       ORDER BY snapshot_date ASC`,
      [workflowId, cutoffDate],
    );
    return rows.map((r) => ({
      id: Number(r.id),
      workflow_id: String(r.workflow_id),
      snapshot_date: String(r.snapshot_date),
      overall_score: Number(r.overall_score),
      success_rate: Number(r.success_rate),
      node_failure_rate: Number(r.node_failure_rate),
      p95_duration_ms: r.p95_duration_ms != null ? Number(r.p95_duration_ms) : null,
      retry_rate: r.retry_rate != null ? Number(r.retry_rate) : null,
      total_tokens: r.total_tokens != null ? Number(r.total_tokens) : null,
    }));
  }

  async getDistinctWorkflowIds(): Promise<string[]> {
    const rows = await this.db.query<Row>(
      `SELECT DISTINCT workflow_id FROM flow_runs WHERE started_at >= ${Math.floor(Date.now() / 1000) - 7 * 86400}`,
      [],
    );
    return rows.map((r) => String(r.workflow_id));
  }

  async hasSnapshotForDate(workflowId: string, date: string): Promise<boolean> {
    const rows = await this.db.query<Row>(
      `SELECT 1 FROM workflow_health_snapshots WHERE workflow_id = ? AND snapshot_date = ? LIMIT 1`,
      [workflowId, date],
    );
    return rows.length > 0;
  }
}