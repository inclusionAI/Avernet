/**
 * CandidateVersionService — Phase 3 auto-evolution guard.
 *
 * For a validated lesson with confidence ≥ 0.9, generate a candidate patch, run it
 * against the bench, and auto-deploy only when BOTH the baseline beat AND the
 * overfit check pass. On failure the spec is rolled back to the pre-patch draft
 * and the lesson's confidence is decremented (applyOutcome false) — the loop will
 * re-evaluate on the next batch.
 *
 * Wiring: built on the existing WorkflowSpecRepository + WorkflowDeployHistoryRepository
 * + a BenchRunnerPort that the caller injects (NOT a new BenchRunService module — HAULT
 * gate 5 forbids inventing one). The LessonRepository / SuggestionOutcomeRepository
 * are the same ones used by SuggestionPanelService.
 */
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import { LessonRepository } from "../../repositories/lesson-repository.js";
import { SuggestionOutcomeRepository } from "../../repositories/suggestion-outcome-repository.js";
import { WorkflowSpecRepository } from "../../repositories/workflow-spec-repository.js";
import type { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import { applyRepairToSpec } from "../suggestion-panel-service.js";
import type { IDatabase } from "../../db.js";

/** Port-style seam for the bench run phase. Real installs inject a wrapper around
 *  the existing bench routes / repositories; tests inject a fake. */
export type BenchRunnerPort = {
  runFor(workflowId: string, opts: { version: string; domainId: number | null }): Promise<{
    scoreVsBaseline: number;
    /** Fraction (0..1) of per-round score variance above which we flag overfit. */
    scoreFluctuationAcrossRounds: number;
  }>;
};

/** Port-style seam for the deploy step. Real install delegates to
 *  WorkflowDeployHistoryRepository (+ git tag in Phase 3+); tests inject a fake. */
export type DeployerPort = {
  deploy(workflowId: string, opts: { source: string; userId?: string }): Promise<{ success: boolean; deploy_number: number | null; error?: string }>;
};

export type AutoEvolveResult = {
  deployed: boolean;
  reason: string;
  deploy_number?: number;
};

export class CandidateVersionService {
  constructor(
    private db: IDatabase,
    private lessons: LessonRepository,
    private outcomes: SuggestionOutcomeRepository,
    private specRepo: WorkflowSpecRepository,
    deployHistoryRepo: WorkflowDeployHistoryRepository | null,
    private bench: BenchRunnerPort,
    private deployer: DeployerPort,
  ) {
    void deployHistoryRepo;
  }

  async autoEvolve(lessonId: number, user: { id: string } = { id: "auto-evolve" }): Promise<AutoEvolveResult> {
    const lesson = await this.lessons.getById(lessonId);
    if (!lesson) return { deployed: false, reason: "lesson_not_found" };
    if (lesson.status !== "validated") return { deployed: false, reason: "lesson_not_validated" };
    if ((lesson.confidence_score ?? 0) < 0.9) return { deployed: false, reason: "confidence_below_threshold" };
    // Transient classes must NEVER be auto-deployed (T3 guard).
    if (lesson.error_class === "dependency-down" || lesson.error_class === "auth") {
      return { deployed: false, reason: "transient_class_not_auto_deployable" };
    }
    if (lesson.repair_type === "node_patch") {
      // Phase 3 does not auto-deploy node-structure edits; that path stays human-gated.
      return { deployed: false, reason: "node_patch_requires_human_gate" };
    }
    if (lesson.repair_type === "kb_hint") {
      // kb_hint does not modify the spec — no auto-deploy needed.
      return { deployed: false, reason: "kb_hint_no_spec_change" };
    }

    let workflowIds: string[] = [];
    try {
      workflowIds = JSON.parse(lesson.related_workflow_ids ?? "[]") as string[];
    } catch {
      workflowIds = [];
    }
    if (workflowIds.length === 0) {
      return { deployed: false, reason: "no_target_workflow_ids" };
    }

    let lastDeploy: number | undefined;
    for (const wfId of workflowIds) {
      const specRow = await this.specRepo.findByWorkflowId(wfId);
      if (!specRow) continue;
      const baseSpecYaml = specRow.spec_json;
      const baseSpec = parseYaml(baseSpecYaml) as Record<string, unknown>;
      const patchedSpec = applyRepairToSpec(baseSpec, lesson);
      const patchedSpecYaml = stringifyYaml(patchedSpec);
      // Save the patched draft (no tag yet).
      await this.specRepo.upsert(wfId, specRow.pack_id, patchedSpecYaml);

      const candidateVersion = `${wfId}-auto-${Date.now()}`;
      // Bench phase.
      const benchResult = await this.bench.runFor(wfId, { version: candidateVersion, domainId: lesson.bench_domain_id ?? null });
      const scoreVsBaseline = benchResult.scoreVsBaseline;
      const passedBaseline = scoreVsBaseline > 0 ? 1 : 0;
      const overfitDetected = benchResult.scoreFluctuationAcrossRounds > 0.05 ? 1 : 0;

      // Record the lineage row in cm_bench_candidate_versions.
      let linId: number | undefined;
      try {
        const r = await this.db.exec(
          `INSERT INTO cm_bench_candidate_versions (workflow_id, lesson_id, candidate_version, score_vs_baseline, passed_baseline, overfit_detected)
           VALUES (?, ?, ?, ?, ?, ?)`,
          [wfId, lessonId, candidateVersion, scoreVsBaseline, passedBaseline, overfitDetected],
        );
        linId = r.insertId;
      } catch (err) {
        // Lineage write is best-effort; the bench gate still decides deploy.
        console.warn(`[auto-evolve] lineage write failed: ${err instanceof Error ? err.message : err}`);
      }

      if (passedBaseline === 1 && overfitDetected === 0) {
        const dep = await this.deployer.deploy(wfId, { source: "auto_release", userId: user.id });
        if (dep.success && dep.deploy_number != null) {
          lastDeploy = dep.deploy_number;
          if (linId != null) {
            await this.db.exec(
              `UPDATE cm_bench_candidate_versions SET deployed = 1, deploy_number = ? WHERE id = ?`,
              [dep.deploy_number, linId],
            ).catch(() => { /* best-effort */ });
          }
          await this.lessons.updateStatus(lessonId, "live").catch(() => {});
          await this.outcomes.insert({
            lesson_id: lessonId, workflow_id: wfId, node_id: lesson.tool_or_node,
            failure_signature: lesson.failure_signature, adopted: 1,
            applied_version: String(dep.deploy_number), metrics_before: null,
            metrics_after: null, verdict: "improved", source: "auto_release",
          }).catch(() => {});
        } else {
          // Roll back to the pre-patch draft and demote confidence.
          await this.specRepo.upsert(wfId, specRow.pack_id, baseSpecYaml);
          await this.lessons.applyOutcome(lessonId, false).catch(() => {});
          return { deployed: false, reason: dep.error ?? "deploy_failed" };
        }
      } else {
        // Roll back to the pre-patch draft and demote confidence.
        await this.specRepo.upsert(wfId, specRow.pack_id, baseSpecYaml);
        await this.lessons.applyOutcome(lessonId, false).catch(() => {});
        return { deployed: false, reason: "bench_baseline_not_passed_or_overfit" };
      }
    }
    return { deployed: true, reason: "ok", deploy_number: lastDeploy };
  }
}
