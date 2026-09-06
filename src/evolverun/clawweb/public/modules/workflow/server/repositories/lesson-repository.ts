/**
 * LessonRepository — read/write the lessons (system memory) table.
 *
 * No business logic beyond the confidence-update rule:
 *   success → +0.05, fail → −0.15, clamped to [0.10, 1.00].
 * Recallable by status IN (validated, live) AND confidence ≥ threshold.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";

export type LessonRow = {
  id: number;
  failure_signature: string;
  error_class: string | null;
  executor_type: string | null;
  tool_or_node: string | null;
  repair_type: "kb_hint" | "prompt_patch" | "arg_template_fix" | "node_patch" | "alert";
  repair_content: string;
  confidence_score: number;
  hit_count: number;
  success_count: number;
  fail_count: number;
  status: "draft" | "validated" | "live" | "expired";
  evidence_run_ids: string | null;
  source: string | null;
  related_workflow_ids: string | null;
  metrics_before: string | null;
  metrics_after: string | null;
  bench_domain_id: number | null;
  last_hit_at: number | null;
  last_hit_success: number | null;
  gmt_create: number;
  gmt_modified: number | null;
};

export type LessonInsert = {
  failure_signature: string;
  error_class?: string | null;
  executor_type?: string | null;
  tool_or_node?: string | null;
  repair_type: LessonRow["repair_type"];
  repair_content: string;
  confidence_score?: number;
  status?: LessonRow["status"];
  evidence_run_ids?: string | null;
  source?: string | null;
  related_workflow_ids?: string | null;
  metrics_before?: string | null;
  metrics_after?: string | null;
  bench_domain_id?: number | null;
};

export class LessonRepository {
  constructor(private db: IDatabase) {}

  /** Insert a brand-new lesson. Caller must handle UNIQUE conflicts OR use upsert. */
  async insert(input: LessonInsert): Promise<number> {
    const r = await this.db.exec(
      `INSERT INTO lessons (failure_signature, error_class, executor_type, tool_or_node,
         repair_type, repair_content, confidence_score, status, evidence_run_ids, source,
         related_workflow_ids, metrics_before, metrics_after, bench_domain_id)
       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
      [input.failure_signature, input.error_class ?? null, input.executor_type ?? null,
       input.tool_or_node ?? null, input.repair_type, input.repair_content,
       input.confidence_score ?? 0.5, input.status ?? "draft", input.evidence_run_ids ?? null,
       input.source ?? null, input.related_workflow_ids ?? null,
       input.metrics_before ?? null, input.metrics_after ?? null,
       input.bench_domain_id ?? null],
    );
    return r.insertId ?? 0;
  }

  /** Idempotent upsert keyed on (failure_signature, repair_type). Updates repair_content on conflict. */
  async upsert(input: LessonInsert): Promise<number> {
    const existing = await this.db.query<LessonRow>(
      `SELECT * FROM lessons WHERE failure_signature = ? AND repair_type = ?`,
      [input.failure_signature, input.repair_type],
    );
    if (existing.length > 0) {
      await this.db.exec(
        `UPDATE lessons SET repair_content = ?, error_class = COALESCE(?, error_class),
           tool_or_node = COALESCE(?, tool_or_node), gmt_modified = ? WHERE id = ?`,
        [input.repair_content, input.error_class ?? null, input.tool_or_node ?? null,
         Math.floor(Date.now() / 1000), existing[0].id],
      );
      return existing[0].id;
    }
    return this.insert(input);
  }

  async listBySignature(failure_signature: string): Promise<LessonRow[]> {
    return this.db.query<LessonRow>(
      `SELECT * FROM lessons WHERE failure_signature = ? ORDER BY confidence_score DESC`, [failure_signature],
    );
  }

  /** Status IN (validated, live) AND confidence ≥ threshold — the L1 recall pool. */
  async listRecallable(minConfidence: number = 0.6, limit = 10): Promise<LessonRow[]> {
    return this.db.query<LessonRow>(
      `SELECT * FROM lessons WHERE status IN ('validated', 'live') AND confidence_score >= ?
       ORDER BY confidence_score DESC, last_hit_at DESC LIMIT ?`,
      [minConfidence, limit],
    );
  }

  /** Find a single lesson to recall for a given signature, with confidence gate. */
  async recallBySignature(failure_signature: string, minConfidence: number = 0.6): Promise<LessonRow | null> {
    const rows = await this.db.query<LessonRow>(
      `SELECT * FROM lessons WHERE failure_signature = ? AND status IN ('validated', 'live')
         AND confidence_score >= ? ORDER BY confidence_score DESC LIMIT 1`,
      [failure_signature, minConfidence],
    );
    return rows[0] ?? null;
  }

  /** Apply an outcome (success/fail) — updates confidence, counters and last_hit_*.
   *  Confidence rule: success +0.05, fail −0.15, clamped to [0.10, 1.00]. */
  async applyOutcome(lessonId: number, success: boolean): Promise<void> {
    const delta = success ? 0.05 : -0.15;
    const now = Math.floor(Date.now() / 1000);
    await this.db.exec(
      `UPDATE lessons SET
         confidence_score = MIN(1.0, MAX(0.10, confidence_score + ?)),
         hit_count = hit_count + 1,
         success_count = success_count + ?,
         fail_count = fail_count + ?,
         last_hit_at = ?,
         last_hit_success = ?,
         gmt_modified = ?
       WHERE id = ?`,
      [delta, success ? 1 : 0, success ? 0 : 1, now, success ? 1 : 0, now, lessonId],
    );
    // Auto-promote draft → validated after 3 consecutive successes, but only on success path here.
    if (success) {
      await this.db.exec(
        `UPDATE lessons SET status = 'validated' WHERE id = ? AND status = 'draft'
           AND success_count >= 3 AND fail_count = 0`,
        [lessonId],
      );
    } else {
      // Demote after two consecutive failures
      await this.db.exec(
        `UPDATE lessons SET status = 'expired' WHERE id = ? AND status = 'validated'
           AND fail_count >= 2`,
        [lessonId],
      );
    }
  }

  async updateStatus(lessonId: number, status: LessonRow["status"]): Promise<void> {
    await this.db.exec(
      `UPDATE lessons SET status = ?, gmt_modified = ? WHERE id = ?`,
      [status, Math.floor(Date.now() / 1000), lessonId],
    );
  }

  /**
   * Update mutable fields on the lesson row AT id = lessonId ONLY.
   *
   * Critical: this method targets the row by primary key (`WHERE id = ?`) so
   * that a caller-supplied new `failure_signature` cannot accidentally merge
   * two lessons (a row resolved by (sig, repair_type) upsert would write into
   * whichever lesson already owns that key, leaving the original id-anchored
   * row stale). Use this — never `upsert()` — from PUT /:id.
   *
   * Only fields the caller explicitly provides are modified; unspecified
   * fields keep their existing values. If the caller changes `failure_signature`
   * to one already owned by a different lesson's (sig, repair_type) key, the
   * underlying UNIQUE INDEX raises — the route handler is responsible for
   * catching that and returning 409 Conflict.
   *
   * Returns the number of rows actually updated (0 → row missing / already
   * at this state, caller should detect by getById afterwards).
   */
  async updateFields(lessonId: number, fields: Partial<Omit<LessonInsert, "fail_count" | "success_count" | "hit_count">>): Promise<number> {
    const sets: string[] = [];
    const params: unknown[] = [];
    for (const [k, v] of Object.entries(fields)) {
      if (v === undefined) continue;
      sets.push(`${k} = ?`);
      params.push(v ?? null);
    }
    if (sets.length === 0) return 0;
    sets.push("gmt_modified = ?");
    params.push(Math.floor(Date.now() / 1000));
    params.push(lessonId);
    const r = await this.db.exec(
      `UPDATE lessons SET ${sets.join(", ")} WHERE id = ?`,
      params,
    );
    return r.affectedRows;
  }

  async retire(lessonId: number): Promise<void> {
    await this.updateStatus(lessonId, "expired");
  }

  /**
   * Bulk-retire lessons that have been inactive for at least `inactiveDays`
   * days. "Inactive" means no hit since the cut-off timestamp — the row's
   * last_hit_at is either NULL (never hit) and was created past the cut-off
   * (kept — a brand new lesson shouldn't immediately expire), OR < cutoff.
   *
   * Only lessons whose status would change (draft / validated / live →
   * expired) are touched; already-expired rows are skipped via WHERE clause.
   *
   * Returns the number of rows retired.
   *
   * Proposal §7.3 / T10 stage 3 contract: "90 天不命中 → 失效". Driven by a
   * scheduler (services/lesson-expire-scheduler.ts) that invokes this method
   * daily, NOT by per-request inspection.
   */
  async retireStale(inactiveDays: number): Promise<number> {
    const cutoffSec = Math.floor(Date.now() / 1000) - inactiveDays * 86400;
    // Update only rows still in the recallable/active statuses whose
    // last_hit_at (or gmt_create if never hit) predates the cutoff.
    const r = await this.db.exec(
      `UPDATE lessons
         SET status = 'expired', gmt_modified = ?
       WHERE status IN ('draft', 'validated', 'live')
         AND COALESCE(last_hit_at, gmt_create) < ?`,
      [Math.floor(Date.now() / 1000), cutoffSec],
    );
    return r.affectedRows;
  }

  async getById(id: number): Promise<LessonRow | null> {
    const rows = await this.db.query<LessonRow>(`SELECT * FROM lessons WHERE id = ?`, [id]);
    return rows[0] ?? null;
  }

  /** List by status filter and/or matching related workflow id. Used by the
   *  external /api/lessons management UI for the new "经验" evolution tab. */
  async list(opts: {
    status?: LessonRow["status"] | null;
    workflowId?: string | null;
    failureSignature?: string | null;
    errorClass?: string | null;
    limit?: number;
    offset?: number;
  }): Promise<{ rows: LessonRow[]; total: number }> {
    const where: string[] = [];
    const params: unknown[] = [];
    if (opts.status) { where.push("status = ?"); params.push(opts.status); }
    if (opts.workflowId) {
      // related_workflow_ids is a JSON string column; LIKE is good enough for
      // the dashboard panel since workflow IDs are opaque strings without SQL
      // metacharacters in practice.
      where.push("related_workflow_ids LIKE ?"); params.push(`%"${opts.workflowId}"%`);
    }
    if (opts.failureSignature) { where.push("failure_signature LIKE ?"); params.push(`%${opts.failureSignature}%`); }
    if (opts.errorClass) { where.push("error_class = ?"); params.push(opts.errorClass); }
    const whereSql = where.length ? `WHERE ${where.join(" AND ")}` : "";
    const limit = Math.min(Math.max(opts.limit ?? 20, 1), 200);
    const offset = Math.max(0, opts.offset ?? 0);
    const totalRows = await this.db.query<{ c: number }>(`SELECT COUNT(*) AS c FROM lessons ${whereSql}`, params);
    const rows = await this.db.query<LessonRow>(
      `SELECT * FROM lessons ${whereSql} ORDER BY gmt_modified DESC, gmt_create DESC LIMIT ? OFFSET ?`,
      [...params, limit, offset],
    );
    return { rows, total: (totalRows[0]?.c ?? 0) as number };
  }
}