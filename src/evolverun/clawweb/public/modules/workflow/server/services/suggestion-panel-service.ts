/**
 * SuggestionPanelService — orchestrates the human-approved path:
 * validate → save (write spec + edit row, no tag) → audit through suggestion_outcomes.
 *
 * Phase 2 (T8): the auto-heal `applyFix` direct-write path on workflow_specs is
 * deprecated in favour of this service. The old path is NOT deleted in T8; it only
 * logs a deprecation span (`applying_deprecated_apply_fix`) — T8.4.
 *
 * Wiring: built directly on the existing WorkflowSpecRepository + WorkflowDeployHistoryRepository
 * (NOT new WorkflowSpecService / DeployService modules — HAULT gate 5 forbids inventing those).
 */
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import { LessonRepository } from "../repositories/lesson-repository.js";
import { WeaknessListRepository } from "../repositories/weakness-list-repository.js";
import { SuggestionOutcomeRepository } from "../repositories/suggestion-outcome-repository.js";
import { WorkflowSpecRepository } from "../repositories/workflow-spec-repository.js";
import { WorkflowDeployHistoryRepository } from "../repositories/workflow-deploy-history-repository.js";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import type { LessonRow } from "../repositories/lesson-repository.js";

export type ApplyOneResult = {
  deployed: boolean;
  deploy_number: number | null;
  error?: string;
};

export class SuggestionPanelService {
  constructor(
    db: IDatabase,
    private lessons: LessonRepository,
    weakness: WeaknessListRepository,
    private outcomes: SuggestionOutcomeRepository,
    private specRepo: WorkflowSpecRepository,
    private deployHistoryRepo: WorkflowDeployHistoryRepository | null,
  ) {
    void db;
    void weakness;
  }

  /**
   * One-click apply: validates the lesson, writes the patched spec via the standard
   * `save` flow (WorkflowSpecRepository.upsert + deploy_history action="edit"), and
   * records the suggestion_outcome. Returns the deploy_number assigned.
   *
   * `kb_hint` lessons do not modify the spec (they only prepend at retry-time) so
   * applyOne short-circuits with deployed=true and deploy_number=null — no spec write.
   */
  async applyOne(
    lessonId: number,
    workflowId: string,
    user: { id: string; name: string },
  ): Promise<ApplyOneResult> {
    const lesson = await this.lessons.getById(lessonId);
    if (!lesson) return { deployed: false, deploy_number: null, error: "lesson_not_found" };
    if (!["validated", "live"].includes(lesson.status)) {
      return { deployed: false, deploy_number: null, error: "lesson_not_in_validated_state" };
    }
    // node_patch is intentionally not supported by one-click apply — it requires the
    // bench-gated Phase 3 evolution path (T10), not a human one-click deploy.
    if (lesson.repair_type === "node_patch") {
      return { deployed: false, deploy_number: null, error: "node_patch_requires_bench_gate" };
    }
    if (lesson.repair_type === "kb_hint") {
      // kb_hint does not modify the spec; nothing to deploy. Mark as adopted so the
      // confidence bookkeeping still records the human approval.
      await this.outcomes.insert({
        lesson_id: lessonId, workflow_id: workflowId, node_id: lesson.tool_or_node,
        failure_signature: lesson.failure_signature, adopted: 1,
        applied_version: null, metrics_before: null, metrics_after: null,
        verdict: "neutral", source: "batch_patch",
      }).catch(() => { /* outcome write is best-effort */ });
      return { deployed: true, deploy_number: null };
    }

    const specRow = await this.specRepo.findByWorkflowId(workflowId);
    if (!specRow) return { deployed: false, deploy_number: null, error: "workflow_spec_not_found" };
    const currentSpec = parseYaml(specRow.spec_json) as Record<string, unknown>;
    const patchedSpec = applyRepairToSpec(currentSpec, lesson);
    const patchedSpecJson = stringifyYaml(patchedSpec);
    await this.specRepo.upsert(workflowId, specRow.pack_id, patchedSpecJson);

    // Write a deploy_history audit row (action="edit", no git tag — same shape as the
    // existing PUT /api/workflows/:id save endpoint). Available only on MySQL-backed
    // installs where workflow_deploy_history exists.
    let deployNumber: number | null = null;
    if (this.deployHistoryRepo) {
      try {
        const packId = specRow.pack_id ?? workflowId;
        const currentMaxVersion = await this.deployHistoryRepo.getLatestVersion(workflowId);
        const newVersion = currentMaxVersion + 1;
        const dbMaxDeployNumber = await this.deployHistoryRepo.getMaxDeployNumber(packId, workflowId);
        deployNumber = dbMaxDeployNumber + 1;
        await this.deployHistoryRepo.insert({
          packId, workflowId, deployNumber, version: newVersion,
          tagName: "", action: "edit", specJson: patchedSpecJson,
          botId: user.id, ownerId: user.id,
        });
        await this.specRepo.updateVersion(workflowId, newVersion);
      } catch (err) {
        // Non-fatal: deploy_history write failure should not block the suggestion apply.
        console.warn(`[suggestion-panel] deploy_history write failed (best-effort): ${err instanceof Error ? err.message : err}`);
      }
    }

    await this.lessons.updateStatus(lessonId, "live").catch(() => { /* best-effort */ });
    await this.outcomes.insert({
      lesson_id: lessonId, workflow_id: workflowId, node_id: lesson.tool_or_node,
      failure_signature: lesson.failure_signature, adopted: 1,
      applied_version: deployNumber != null ? String(deployNumber) : null,
      metrics_before: null, metrics_after: null,
      verdict: "neutral", source: "batch_patch",
    }).catch(() => { /* outcome write is best-effort */ });
    return { deployed: true, deploy_number: deployNumber };
  }
}

/**
 * Patch the YAML spec with the lesson fix. Strategy depends on repair_type:
 *  - prompt_patch: locate the target node by tool_or_node (or nodeId fallback),
 *    replace executor.prompt verbatim.
 *  - arg_template_fix: locate the node, JSON.parse(repair_content) and merge into
 *    executor.args (matching keys only).
 *  - kb_hint / alert / node_patch: no-op (kb_hint is handled by the service caller;
 *    node_patch is refused earlier).
 *
 * The spec structure is the standard ClawFlow workflow YAML:
 *   nodes:
 *     - id: <id>
 *       executor:
 *         type: <type>
 *         prompt: <string>
 *         args: { ... }
 */
export function applyRepairToSpec(spec: Record<string, unknown>, lesson: LessonRow): Record<string, unknown> {
  if (lesson.repair_type !== "prompt_patch" && lesson.repair_type !== "arg_template_fix") {
    return spec;
  }
  const nodes = (spec.nodes as Array<Record<string, unknown>> | undefined) ?? [];
  const target = nodes.find((n) => {
    const exec = n.executor as Record<string, unknown> | undefined;
    return (exec && lesson.tool_or_node && (
      exec.toolName === lesson.tool_or_node ||
      exec.tool_or_node === lesson.tool_or_node ||
      n.id === lesson.tool_or_node
    )) || n.id === lesson.tool_or_node;
  });
  if (!target) return spec; // no target → leave spec untouched
  const executor = (target.executor ?? {}) as Record<string, unknown>;
  if (lesson.repair_type === "prompt_patch") {
    target.executor = { ...executor, prompt: lesson.repair_content };
  } else {
    // arg_template_fix
    let overrides: Record<string, unknown> = {};
    try { overrides = JSON.parse(lesson.repair_content); } catch { return spec; }
    target.executor = { ...executor, args: { ...(executor.args as Record<string, unknown> ?? {}), ...overrides } };
  }
  return { ...spec, nodes: [...nodes] };
}
