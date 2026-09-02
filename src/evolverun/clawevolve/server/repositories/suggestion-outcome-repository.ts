/**
 * SuggestionOutcomeRepository — the cross-loop shared score card.
 * Both fast loop (source='runtime_retry') and slow loop
 * (source='batch_patch' | 'auto_release') write here.
 */
import type { IDatabase } from "../db.js";

export type SuggestionOutcomeRow = {
  id: number;
  lesson_id: number;
  workflow_id: string;
  node_id: string | null;
  failure_signature: string;
  adopted: number;
  applied_version: string | null;
  metrics_before: string | null;
  metrics_after: string | null;
  verdict: "improved" | "neutral" | "regressed";
  source: "runtime_retry" | "batch_patch" | "auto_release";
  gmt_create: number;
  gmt_modified: number | null;
};

export type SuggestionOutcomeInsert = Omit<SuggestionOutcomeRow, "id" | "gmt_create" | "gmt_modified">;

export class SuggestionOutcomeRepository {
  constructor(private db: IDatabase) {}

  async insert(input: SuggestionOutcomeInsert): Promise<number> {
    const r = await this.db.exec(
      `INSERT INTO suggestion_outcomes (lesson_id, workflow_id, node_id, failure_signature, adopted,
         applied_version, metrics_before, metrics_after, verdict, source)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [input.lesson_id, input.workflow_id, input.node_id, input.failure_signature,
       input.adopted ? 1 : 0, input.applied_version, input.metrics_before,
       input.metrics_after, input.verdict, input.source],
    );
    return r.insertId ?? 0;
  }

  async byLesson(lessonId: number, limit = 50): Promise<SuggestionOutcomeRow[]> {
    return this.db.query<SuggestionOutcomeRow>(
      `SELECT * FROM suggestion_outcomes WHERE lesson_id = ? ORDER BY gmt_create DESC LIMIT ?`,
      [lessonId, limit],
    );
  }

  /** Aggregate hit/improve/regress counts per lesson — drives the dashboard confidence distribution. */
  async aggregateByLesson(lessonId: number): Promise<{ hits: number; improved: number; regressed: number }> {
    const rows = await this.db.query<{ hits: number; improved: number; regressed: number }>(
      `SELECT
         COUNT(*) AS hits,
         SUM(CASE WHEN verdict = 'improved' THEN 1 ELSE 0 END) AS improved,
         SUM(CASE WHEN verdict = 'regressed' THEN 1 ELSE 0 END) AS regressed
       FROM suggestion_outcomes WHERE lesson_id = ?`,
      [lessonId],
    );
    return rows[0] ?? { hits: 0, improved: 0, regressed: 0 };
  }
}