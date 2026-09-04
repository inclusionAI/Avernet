import type { IDatabase } from "../db.js";

export type WeaknessListRow = {
  id: number;
  failure_signature: string;
  error_class: string | null;
  workflow_ids: string | null;
  occurrence_count: number;
  affected_workflows_count: number | null;
  repairability: "auto" | "semi" | "manual" | null;
  priority_score: number;
  evidence_diagnosis_ids: string | null;
  latest_occurrence: number | null;
  first_occurrence: number | null;
  matched_lesson_ids: string | null;
  status: "active" | "processed" | "closed";
  gmt_create: number; gmt_modified: number | null;
};

export class WeaknessListRepository {
  constructor(private db: IDatabase) {}

  /** Upsert by failure_signature. Refreshes counts/timestamps. */
  async upsert(input: Omit<WeaknessListRow, "id" | "gmt_create" | "gmt_modified">): Promise<void> {
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `INSERT INTO weakness_list (failure_signature, error_class, workflow_ids, occurrence_count,
         affected_workflows_count, repairability, priority_score, evidence_diagnosis_ids,
         latest_occurrence, first_occurrence, matched_lesson_ids, status, gmt_create, gmt_modified)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
       ON CONFLICT(failure_signature) DO UPDATE SET
         workflow_ids = excluded.workflow_ids,
         occurrence_count = excluded.occurrence_count,
         affected_workflows_count = excluded.affected_workflows_count,
         priority_score = excluded.priority_score,
         evidence_diagnosis_ids = excluded.evidence_diagnosis_ids,
         latest_occurrence = excluded.latest_occurrence,
         matched_lesson_ids = excluded.matched_lesson_ids,
         gmt_modified = excluded.gmt_modified`,
      [input.failure_signature, input.error_class, input.workflow_ids, input.occurrence_count,
       input.affected_workflows_count, input.repairability, input.priority_score,
       input.evidence_diagnosis_ids, now, input.first_occurrence ?? now,
       input.matched_lesson_ids, input.status, now, now],
    );
  }

  async listTop(limit = 20): Promise<WeaknessListRow[]> {
    return this.db.query<WeaknessListRow>(
      `SELECT * FROM weakness_list WHERE status = 'active'
       ORDER BY priority_score DESC LIMIT ?`, [limit],
    );
  }

  /** Count of active weaknesses — used by the listing endpoint to report a
   *  true total for pagination, independent of the page's limit/offset. */
  async countActive(): Promise<number> {
    const rows = await this.db.query<{ c: number }>(
      `SELECT COUNT(*) AS c FROM weakness_list WHERE status = 'active'`, [],
    );
    return rows[0]?.c ?? 0;
  }

  /** Find one weakness row by id (no status filter, so admin can drill into
   *  processed/closed ones too). Returns null on missing id. */
  async getById(id: number): Promise<WeaknessListRow | null> {
    const rows = await this.db.query<WeaknessListRow>(`SELECT * FROM weakness_list WHERE id = ?`, [id]);
    return rows[0] ?? null;
  }
}