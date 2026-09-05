import type { IDatabase } from "../db.js";

export type SignatureByVersionRow = {
  failure_signature: string;
  workflow_version: number | null;
  occurrence_count: number;
};

export type PhysicalRepairCoverageRow = {
  applied_by: string;
  count: number;
  total: number;
};

export type LessonConfidenceDistributionRow = {
  bucket: string;
  count: number;
};

export type LessonCrossWorkflowReuseRow = {
  lesson_id: number;
  failure_signature: string;
  workflow_count: number;
  hit_count: number;
};

export type WeaknessListTopRow = {
  id: number;
  failure_signature: string;
  error_class: string | null;
  priority_score: number;
  occurrence_count: number;
  affected_workflows_count: number | null;
  repairability: string | null;
};

export class EvolutionMetricsRepository {
  constructor(private db: IDatabase) {}

  /**
   * Anchor 1: count failures per (failure_signature × workflow_version).
   *
   * Joins diagnosis_cards → flow_runs on flow_id to attach the workflow version that
   * produced each failure. Returns one row per (signature, version) bucket, sorted so
   * the dashboard can render the monotonic-failure-rate-trend chart directly.
   */
  async failureRateByVersion(signature: string | null = null): Promise<SignatureByVersionRow[]> {
    const params = signature ? [signature] : [];
    // LEFT JOIN so diagnosis_cards that aren't yet linked to a flow_runs row still
    // appear with workflow_version = null (robust against workflow_version column
    // being absent on legacy installs — the column was added in v104).
    return this.db.query<SignatureByVersionRow>(
      `SELECT
         d.failure_signature AS failure_signature,
         f.workflow_version AS workflow_version,
         COUNT(*) AS occurrence_count
       FROM diagnosis_cards d
       LEFT JOIN flow_runs f ON f.flow_id = d.flow_id
       ${signature ? "WHERE d.failure_signature = ?" : ""}
       GROUP BY d.failure_signature, f.workflow_version
       ORDER BY d.failure_signature, f.workflow_version`,
      params,
    );
  }

  /**
   * §10.3 — 自维护覆盖率 (auto-resolved / total failures).
   * Counts repair_history rows grouped by applied_by, plus the grand total,
   * within the time window [sinceTs, now). The dashboard draws a donut:
   * 'auto' slice = SUM(applied_by IN (guardian|auto_heal|evolution)),
   * 'manual' slice = applied_by='manual'.
   */
  async physicalRepairCoverage(sinceTs: number): Promise<PhysicalRepairCoverageRow[]> {
    const rows = await this.db.query<{ applied_by: string; count: number }>(
      `SELECT applied_by, COUNT(*) AS count FROM repair_history
       WHERE gmt_create >= ? GROUP BY applied_by`,
      [sinceTs],
    );
    const total = rows.reduce((acc, r) => acc + r.count, 0);
    return rows.map((r) => ({ ...r, total }));
  }

  /**
   * §10.3 — 经验可信度分布 (bucket-counts of confidence_score).
   * Buckets: [0.1–0.3), [0.3–0.5), [0.5–0.7), [0.7–0.9), [0.9–1.0].
   * Returns rows sorted ascending by bucket label.
   */
  async lessonConfidenceDistribution(): Promise<LessonConfidenceDistributionRow[]> {
    return this.db.query<LessonConfidenceDistributionRow>(
      `SELECT
         CASE
           WHEN confidence_score < 0.3 THEN '[0.1, 0.3)'
           WHEN confidence_score < 0.5 THEN '[0.3, 0.5)'
           WHEN confidence_score < 0.7 THEN '[0.5, 0.7)'
           WHEN confidence_score < 0.9 THEN '[0.7, 0.9)'
           ELSE '[0.9, 1.0]'
         END AS bucket,
         COUNT(*) AS count
       FROM lessons
       WHERE status != 'expired'
       GROUP BY bucket
       ORDER BY bucket`,
      [],
    );
  }

  /**
   * §10.3 — 经验复用次数 (cross-workflow hit count per lesson).
   * For each lesson, counts the distinct workflows that have referenced it via
   * suggestion_outcomes (failure_signature JOIN). Returns top N lessons by
   * cross-workflow count.
   */
  async lessonCrossWorkflowReuse(limit = 10): Promise<LessonCrossWorkflowReuseRow[]> {
    return this.db.query<LessonCrossWorkflowReuseRow>(
      `SELECT
         l.id AS lesson_id,
         l.failure_signature AS failure_signature,
         COUNT(DISTINCT so.workflow_id) AS workflow_count,
         l.hit_count AS hit_count
       FROM lessons l
       LEFT JOIN suggestion_outcomes so ON so.lesson_id = l.id
       WHERE l.hit_count > 0 AND l.status != 'expired'
       GROUP BY l.id, l.failure_signature, l.hit_count
       ORDER BY workflow_count DESC, l.hit_count DESC
       LIMIT ?`,
      [limit],
    );
  }

  /**
   * §10.3 — 弱点清单 Top N.
   * Returns the top N weakness_list rows by priority_score, status='active'.
   * Powers the '失败归因 Top 截面' dashboard widget.
   */
  async weaknessListTop(limit = 10): Promise<WeaknessListTopRow[]> {
    return this.db.query<WeaknessListTopRow>(
      `SELECT id, failure_signature, error_class, priority_score,
              occurrence_count, affected_workflows_count, repairability
       FROM weakness_list
       WHERE status = 'active'
       ORDER BY priority_score DESC LIMIT ?`,
      [limit],
    );
  }
}