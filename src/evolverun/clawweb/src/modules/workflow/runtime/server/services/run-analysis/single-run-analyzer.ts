/**
 * SingleRunAnalyzer — run-end async analyser that consolidates node_executions +
 * node_step_traces + run_logs into a single diagnosis_card, and writes a draft
 * lesson when no validated lesson matched.
 *
 * Replaces the three parallel in-memory analyses (Auto-Heal / run-archives /
 * analysis.ts) with one source of truth.
 */
import { DiagnosisCardRepository, type DiagnosisCardInsert } from "../../repositories/diagnosis-card-repository.js";
import { LessonRepository } from "../../repositories/lesson-repository.js";
import type { IDatabase } from "../../db.js";

export type SingleRunAnalysisInput = DiagnosisCardInsert;

export class SingleRunAnalyzer {
  constructor(
    db: IDatabase,
    private diagnosisCards: DiagnosisCardRepository,
    private lessons: LessonRepository,
  ) {
    void db;
  }

  async analyzeAndPersist(input: SingleRunAnalysisInput): Promise<void> {
    await this.diagnosisCards.insert(input);
    // If no validated lesson matched, promote a draft lesson when we have a suggested fix.
    const existing = await this.lessons.listBySignature(input.failure_signature);
    if (existing.length === 0 && input.suggested_repair_type && input.suggested_repair_content) {
      await this.lessons.upsert({
        failure_signature: input.failure_signature,
        error_class: (input as SingleRunAnalysisInput & { error_class_raw?: string | null }).error_class_raw ?? null,
        executor_type: null,
        tool_or_node: input.node_id,
        repair_type: input.suggested_repair_type as "kb_hint" | "prompt_patch" | "arg_template_fix" | "node_patch" | "alert",
        repair_content: input.suggested_repair_content,
        confidence_score: 0.5,
        status: "draft",
        source: "log_analysis",
        evidence_run_ids: JSON.stringify([input.flow_id]),
      });
    }
  }
}
