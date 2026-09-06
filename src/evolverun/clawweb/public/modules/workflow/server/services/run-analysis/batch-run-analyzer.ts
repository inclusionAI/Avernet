/**
 * BatchRunAnalyzer — cron-driven (daily in Phase 2; configurable).
 * Groups diagnosis_cards by failure_signature over the configured window,
 * computes priority_score = log(occurrence_count) * affected_workflows_count,
 * and upserts into weakness_list.
 *
 * G11 (proposal §9.2): new active weakness rows whose priority_score ≥
 * `dispatchThreshold` (default 2.0 = ~2 affected workflows OR ~7 occurrences)
 * auto-dispatch one ce_task per signature via the injected EvolveRepository.
 * The ce_task config_json carries failure_signature + evidence card ids so
 * the existing evolve pipeline can pick up the proposed fix without manual
 * triage. Idempotency: the analyzer queries ce_tasks for an active
 * pending/in-progress task with the same signature BEFORE dispatching.
 */
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { DiagnosisCardRepository } from "../../repositories/diagnosis-card-repository.js";
import { WeaknessListRepository } from "../../repositories/weakness-list-repository.js";
import type { EvolveRepository } from "@avernet/clawevolve/server/repositories/evolve-repository";

type AggRow = {
  failure_signature: string;
  error_class: string | null;
  occurrence_count: number;
  affected_workflows_count: number;
  workflow_ids: string;
  evidence_diagnosis_ids: string;
  latest_occurrence: number;
  first_occurrence: number;
};

export class BatchRunAnalyzer {
  constructor(
    private db: IDatabase,
    diagRepo: DiagnosisCardRepository,
    private weaknessRepo: WeaknessListRepository,
    private windowDays: number = 7,
    /**
     * Optional — when provided, top weaknesses auto-dispatch ce_tasks (G11).
     * Null preserves the pre-G11 behavior (weakness_list only, no auto-dispatch).
     */
    private evolveRepo: EvolveRepository | null = null,
    private dispatchThreshold: number = 2.0,
  ) {
    void diagRepo;
  }

  async runOnce(): Promise<void> {
    const since = Math.floor(Date.now() / 1000) - this.windowDays * 86400;
    const rows = await this.db.query<AggRow>(
      `SELECT
         failure_signature,
         MAX(NULLIF(error_text, '')) AS error_class,
         COUNT(*) AS occurrence_count,
         COUNT(DISTINCT workflow_id) AS affected_workflows_count,
         GROUP_CONCAT(DISTINCT workflow_id) AS workflow_ids,
         GROUP_CONCAT(id) AS evidence_diagnosis_ids,
         MAX(gmt_create) AS latest_occurrence,
         MIN(gmt_create) AS first_occurrence
       FROM diagnosis_cards
       WHERE gmt_create >= ? AND outcome = 'not_recovered'
       GROUP BY failure_signature`,
      [since],
    );
    for (const r of rows) {
      const occurrence = Math.max(1, r.occurrence_count);
      const affected = Math.max(1, r.affected_workflows_count);
      const priority = Math.round((Math.log(occurrence + 1) * affected) * 100) / 100;
      await this.weaknessRepo.upsert({
        failure_signature: r.failure_signature,
        // error_text isn't a reliable class column; the analyzer leaves it null here and
        // relies on the LLM-driven classifier run upstream (SingleRunAnalyzer).
        error_class: null,
        workflow_ids: r.workflow_ids,
        occurrence_count: occurrence,
        affected_workflows_count: affected,
        repairability: "auto",
        priority_score: priority,
        evidence_diagnosis_ids: r.evidence_diagnosis_ids,
        latest_occurrence: r.latest_occurrence,
        first_occurrence: r.first_occurrence,
        matched_lesson_ids: null,
        status: "active",
      });

      // G11: auto-dispatch a ce_task for top weaknesses. Best-effort — failure
      // here does NOT undo the weakness_list upsert above. Idempotent via the
      // existence-check query.
      if (this.evolveRepo && priority >= this.dispatchThreshold) {
        try {
          await this.dispatchTaskFor(r, priority);
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e);
          console.warn(`[batch-analyzer] G11 dispatch failed for sig=${r.failure_signature}: ${msg}`);
        }
      }
    }
  }

  /**
   * Dispatch a `weakness_evolve` ce_task for the given aggregated signature.
   * Idempotent: if a pending or in-progress task with the same sig already
   * exists in ce_tasks.config_json's `failure_signature` field, skip (read
   * ce_tasks rows and JSON-parse rather than JSON_EXTRACT for SQLite↔MySQL
   * portability).
   */
  private async dispatchTaskFor(r: AggRow, priority: number): Promise<void> {
    if (!this.evolveRepo) return;
    // Look for any active ce_task hosting this exact signature.
    const existing = await this.db.query<{ task_id: string; config_json: string; status: string }>(
      `SELECT task_id, config_json, status FROM ce_tasks
       WHERE task_type = 'weakness_evolve'
         AND status IN ('pending', 'in_progress', 'waiting', 'review')
         AND gmt_create >= ?
       ORDER BY gmt_create DESC LIMIT 30`,
      [Math.floor(Date.now() / 1000) - 30 * 86400],
    );
    for (const row of existing) {
      try {
        const cfg = JSON.parse(row.config_json ?? "{}");
        if (cfg.failure_signature === r.failure_signature) {
          // Already dispatched for this weakness — skip silently.
          return;
        }
      } catch { /* malformed config_json — ignore, continue scanning */ }
    }

    // Compose the new ce_task. The config_json carries all context the evolve
    // service needs to plan the patch (signature + evidence + workflows + priority).
    const configJson = JSON.stringify({
      failure_signature: r.failure_signature,
      workflow_ids: r.workflow_ids?.split(",").filter(Boolean) ?? [],
      evidence_diagnosis_ids: r.evidence_diagnosis_ids?.split(",").map(Number).filter((n) => n > 0) ?? [],
      occurrence_count: r.occurrence_count,
      affected_workflows_count: r.affected_workflows_count,
      priority_score: priority,
      window_days: this.windowDays,
    });
    const taskId = `WE-${Date.now().toString(36)}-${Math.abs(hashString(r.failure_signature) % 1000).toString(36).padStart(3, "0")}`.toUpperCase();
    const taskName = `弱点进化: ${r.failure_signature.slice(0, 48)}`;
    await this.evolveRepo.createTask({
      taskId,
      taskType: "weakness_evolve",
      userId: "system",
      botId: "auto-evolve",
      taskName,
      remark: `G11 自动派单 (priority=${priority.toFixed(2)}, occurrences=${r.occurrence_count}, workflows=${r.affected_workflows_count})`,
      configJson,
      createdBy: "batch-run-analyzer-g11",
    });
  }
}

/** Small stable string hash (djb2) used to suffix the task id with a short salt. */
function hashString(s: string): number {
  let h = 5381;
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) + h) ^ s.charCodeAt(i);
  }
  return h;
}
