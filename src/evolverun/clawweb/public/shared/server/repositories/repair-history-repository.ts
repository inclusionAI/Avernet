import type { IDatabase } from "../db.js";

export type RepairHistoryRow = {
  id: number;
  flow_id: string; node_id: string; failure_signature: string;
  lesson_id: number | null; diagnosis_card_id: number | null; suggestion_outcome_id: number | null;
  repair_type: string; repair_content: string | null;
  applied_by: "guardian" | "auto_heal" | "evolution" | "manual";
  retry_success: number | null;
  level: "L1" | "L2" | "L3" | null;
  gmt_create: number; gmt_modified: number | null;
};

export type RepairHistoryInsert = Omit<RepairHistoryRow, "id" | "gmt_create" | "gmt_modified">;

export class RepairHistoryRepository {
  constructor(private db: IDatabase) {}

  async insert(input: RepairHistoryInsert): Promise<number> {
    const r = await this.db.exec(
      `INSERT INTO repair_history (flow_id, node_id, failure_signature, lesson_id, diagnosis_card_id,
         suggestion_outcome_id, repair_type, repair_content, applied_by, retry_success, level)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [input.flow_id, input.node_id, input.failure_signature, input.lesson_id,
       input.diagnosis_card_id, input.suggestion_outcome_id, input.repair_type,
       input.repair_content, input.applied_by, input.retry_success, input.level],
    );
    return r.insertId ?? 0;
  }

  async countBySource(since: number): Promise<{ applied_by: string; count: number }[]> {
    return this.db.query<{ applied_by: string; count: number }>(
      `SELECT applied_by, COUNT(*) as count FROM repair_history WHERE gmt_create >= ? GROUP BY applied_by`,
      [since],
    );
  }

  /** List repair records filtered by signature / applied_by / retry_success /
   *  time window, latest first. Powers /api/repair-history (external dashboard) + the
   *  §10.3 metrics (自维护覆盖率, L1 命中率). */
  async list(opts: {
    failureSignature?: string | null;
    appliedBy?: RepairHistoryRow["applied_by"] | null;
    retrySuccess?: boolean | null;
    sinceTs?: number | null;
    untilTs?: number | null;
    limit?: number;
    offset?: number;
  }): Promise<{ rows: RepairHistoryRow[]; total: number }> {
    const where: string[] = [];
    const params: unknown[] = [];
    if (opts.failureSignature) { where.push("failure_signature = ?"); params.push(opts.failureSignature); }
    if (opts.appliedBy) { where.push("applied_by = ?"); params.push(opts.appliedBy); }
    if (opts.retrySuccess === true) { where.push("retry_success = 1"); }
    else if (opts.retrySuccess === false) { where.push("retry_success = 0"); }
    if (opts.sinceTs != null) { where.push("gmt_create >= ?"); params.push(opts.sinceTs); }
    if (opts.untilTs != null) { where.push("gmt_create < ?"); params.push(opts.untilTs); }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
    const limit = Math.min(Math.max(opts.limit ?? 50, 1), 200);
    const offset = Math.max(0, opts.offset ?? 0);
    const totalRows = await this.db.query<{ c: number }>(`SELECT COUNT(*) AS c FROM repair_history ${whereSql}`, params);
    const rows = await this.db.query<RepairHistoryRow>(
      `SELECT * FROM repair_history ${whereSql} ORDER BY gmt_create DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );
    return { rows, total: (totalRows[0]?.c ?? 0) as number };
  }

  /** Counts grouped by repair_level — backs "L1 命中率 / L2 成功率" (§10.3). */
  async countByLevel(sinceTs: number): Promise<{ level: string | null; total: number; succeeded: number }[]> {
    return this.db.query<{ level: string | null; total: number; succeeded: number }>(
      `SELECT level, COUNT(*) AS total, SUM(CASE WHEN retry_success = 1 THEN 1 ELSE 0 END) AS succeeded
       FROM repair_history WHERE gmt_create >= ? GROUP BY level`,
      [sinceTs],
    );
  }

  /**
   * List repair_history rows directly tied to a diagnosis card via its FK
   * `diagnosis_card_id`, ordered newest-first. Used by /api/diagnosis-cards/:id
   * detail to drill the actual repair attempts for THIS card — NOT a
   * failure_signature LIKE match, which would include rows shipped by
   * unrelated diagnosis cards that happened to surface the same signature.
   */
  async listByDiagnosisCardId(cardId: number, limit = 50): Promise<RepairHistoryRow[]> {
    const safeLimit = Math.min(Math.max(limit, 1), 200);
    return this.db.query<RepairHistoryRow>(
      `SELECT * FROM repair_history WHERE diagnosis_card_id = ? ORDER BY gmt_create DESC LIMIT ?`,
      [cardId, safeLimit],
    );
  }
}