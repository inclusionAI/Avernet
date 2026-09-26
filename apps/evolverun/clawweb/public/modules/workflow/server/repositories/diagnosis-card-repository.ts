import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type DiagnosisCardRow = {
  id: number;
  flow_id: string; workflow_id: string; node_id: string;
  failure_signature: string; error_text: string;
  input_snapshot: string | null; output_snapshot: string | null; step_traces_snapshot: string | null;
  analysis_reasoning: string | null;
  suggested_repair_type: "kb_hint" | "prompt_patch" | "arg_template_fix" | "node_patch" | "alert" | null;
  suggested_repair_content: string | null;
  matched_lesson_id: number | null;
  outcome: "recovered" | "not_recovered" | "escalated";
  attempt_count: number;
  diagnosis_level: "L1" | "L2" | "L3" | null;
  gmt_create: number; gmt_modified: number | null;
  // Optional raw error class carried alongside the analysis (not persisted as a dedicated column;
  // surfaced by SingleRunAnalyzer to also upsert a draft lesson with error_class set).
  error_class_raw?: string | null;
};

export type DiagnosisCardInsert = Omit<DiagnosisCardRow, "id" | "gmt_create" | "gmt_modified" | "matched_lesson_id"> & {
  matched_lesson_id?: number | null;
};

export class DiagnosisCardRepository {
  constructor(private db: IDatabase) {}

  async insert(input: DiagnosisCardInsert): Promise<number> {
    const r = await this.db.exec(
      `INSERT INTO diagnosis_cards (flow_id, workflow_id, node_id, failure_signature, error_text,
         input_snapshot, output_snapshot, step_traces_snapshot, analysis_reasoning,
         suggested_repair_type, suggested_repair_content, matched_lesson_id, outcome,
         attempt_count, diagnosis_level)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [input.flow_id, input.workflow_id, input.node_id, input.failure_signature, input.error_text,
       input.input_snapshot, input.output_snapshot, input.step_traces_snapshot,
       input.analysis_reasoning, input.suggested_repair_type, input.suggested_repair_content,
       input.matched_lesson_id ?? null, input.outcome, input.attempt_count, input.diagnosis_level],
    );
    return r.insertId ?? 0;
  }

  async listBySignature(failure_signature: string, limit = 50): Promise<DiagnosisCardRow[]> {
    return this.db.query<DiagnosisCardRow>(
      `SELECT * FROM diagnosis_cards WHERE failure_signature = ? ORDER BY gmt_create DESC LIMIT ?`,
      [failure_signature, limit],
    );
  }

  /**
   * Update a diagnosis card's outcome (and optionally matched_lesson_id).
   * Returns the number of rows actually updated — callers (notably the
   * `/api/internal/self-evolution/diagnosis-cards/:id` PATCH handler) use this
   * to distinguish "row patched" (1) from "no row matched id" (0), since the
   * underlying UPDATE does not throw on a missing id.
   */
  async updateOutcome(id: number, outcome: DiagnosisCardRow["outcome"], matchedLessonId: number | null = null): Promise<number> {
    const r = await this.db.exec(
      `UPDATE diagnosis_cards SET outcome = ?, matched_lesson_id = ?, gmt_modified = ? WHERE id = ?`,
      [outcome, matchedLessonId, Math.floor(Date.now() / 1000), id],
    );
    return r.affectedRows;
  }

  async getById(id: number): Promise<DiagnosisCardRow | null> {
    const rows = await this.db.query<DiagnosisCardRow>(`SELECT * FROM diagnosis_cards WHERE id = ?`, [id]);
    return rows[0] ?? null;
  }

  /** Batched lookup by ids (e.g. weakness-list detail drill-down). Empty-safe. */
  async listByIds(ids: readonly number[]): Promise<DiagnosisCardRow[]> {
    if (!ids.length) return [];
    const placeholders = ids.map(() => "?").join(",");
    return this.db.query<DiagnosisCardRow>(
      `SELECT * FROM diagnosis_cards WHERE id IN (${placeholders}) ORDER BY gmt_create DESC`,
      [...ids],
    );
  }

  /** List cards with optional filters, sorted by latest first. Used by the
   *  external /api/diagnosis-cards management UI for the new "诊断" evolution
   *  tab and Bigfish dashboard drill-downs. */
  async list(opts: {
    workflowId?: string | null;
    failureSignature?: string | null;
    outcome?: DiagnosisCardRow["outcome"] | null;
    limit?: number;
    offset?: number;
  }): Promise<{ rows: DiagnosisCardRow[]; total: number }> {
    const where: string[] = [];
    const params: unknown[] = [];
    if (opts.workflowId) { where.push("workflow_id = ?"); params.push(opts.workflowId); }
    if (opts.failureSignature) { where.push("failure_signature = ?"); params.push(opts.failureSignature); }
    if (opts.outcome) { where.push("outcome = ?"); params.push(opts.outcome); }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
    const limit = Math.min(Math.max(opts.limit ?? 20, 1), 200);
    const offset = Math.max(0, opts.offset ?? 0);
    const totalRows = await this.db.query<{ c: number }>(`SELECT COUNT(*) AS c FROM diagnosis_cards ${whereSql}`, params);
    const rows = await this.db.query<DiagnosisCardRow>(
      `SELECT * FROM diagnosis_cards ${whereSql} ORDER BY gmt_create DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );
    return { rows, total: (totalRows[0]?.c ?? 0) as number };
  }
}