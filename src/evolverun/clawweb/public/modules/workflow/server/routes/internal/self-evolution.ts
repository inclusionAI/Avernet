/**
 * Internal API routes for self-evolution decoupled writes — ClawMind calls
 * these (signature-protected, mounted under /api/internal/self-evolution)
 * to persist the fast-loop experience stream: lessons, diagnosis cards,
 * repair-history ledger, and suggestion outcomes.
 *
 * These routes exist so the engine can stream runtime observations to clawweb
 * via HTTP (DATABASE_MODE=api) without each side re-implementing SQL. All four
 * repos are SQL repositories shared with the dashboard / suggestion panel.
 *
 * Mounted under /api/internal/self-evolution.
 * All endpoints are protected by the Ed25519 signature middleware applied to
 * the parent /api/internal router.
 */
import { Router, type Request, type Response } from "express";
import type { LessonRepository, LessonInsert } from "../../repositories/lesson-repository.js";
import type { DiagnosisCardRepository, DiagnosisCardInsert } from "../../repositories/diagnosis-card-repository.js";
import type { RepairHistoryRepository, RepairHistoryInsert } from "@avernet/clawweb-shared/server/repositories/repair-history-repository";
import type { SuggestionOutcomeRepository, SuggestionOutcomeInsert } from "../../repositories/suggestion-outcome-repository.js";
import { apiLog } from "../internal-logger.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export type SelfEvolutionRepos = {
  lessonRepo: LessonRepository;
  diagnosisCardRepo: DiagnosisCardRepository;
  repairHistoryRepo: RepairHistoryRepository;
  suggestionOutcomeRepo: SuggestionOutcomeRepository;
};

export function createInternalSelfEvolutionRouter(repos: SelfEvolutionRepos): Router {
  const router = Router();
  const { lessonRepo, diagnosisCardRepo, repairHistoryRepo, suggestionOutcomeRepo } = repos;

  // ─────────────────────────────────────────────────────────────────────
  // POST /lessons — upsert a draft|validated lesson.
  // Body shape matches LessonInsert (minus id/timestamps).
  // Idempotent on (failure_signature, repair_type).
  // ─────────────────────────────────────────────────────────────────────
  router.post("/lessons", asyncHandler(async (req: Request, res: Response) => {
    const body = req.body as Partial<LessonInsert>;
    if (!body?.failure_signature || !body?.repair_type) {
      apiLog("WRITE", "/self-evolution/lessons", { status: 400, missing: !body?.failure_signature ? "failure_signature" : "repair_type" });
      return res.status(400).json({
        error: "bad_request",
        message: `Missing required fields: failure_signature and repair_type are required (got signature=${body?.failure_signature ?? "undefined"}, repair_type=${body?.repair_type ?? "undefined"})`,
      });
    }
    const id = await lessonRepo.upsert({
      failure_signature: body.failure_signature,
      error_class: body.error_class ?? null,
      executor_type: body.executor_type ?? null,
      tool_or_node: body.tool_or_node ?? null,
      repair_type: body.repair_type,
      repair_content: body.repair_content ?? "",
      confidence_score: body.confidence_score ?? 0.5,
      status: (body.status as LessonInsert["status"]) ?? "draft",
      source: body.source ?? "unknown",
      evidence_run_ids: body.evidence_run_ids ?? null,
      related_workflow_ids: body.related_workflow_ids ?? null,
      metrics_before: body.metrics_before ?? null,
      metrics_after: body.metrics_after ?? null,
      bench_domain_id: body.bench_domain_id ?? null,
    });
    apiLog("WRITE", "/self-evolution/lessons", { id, status: 201 });
    return res.status(201).json({ id, failure_signature: body.failure_signature, status: body.status ?? "draft" });
  }));

  // ─────────────────────────────────────────────────────────────────────
  // GET /lessons/:sig/recall — recall the highest-confidence validated or
  // live lesson above `min_confidence` (default 0.6) for the given signature.
  // Returns 200 with body { lesson: LessonRow | null } when found, 404 when
  // not. The ClawMind `local-lesson-adapter` uses this endpoint to short-
  // circuit LLM diagnosis at L1.
  // ─────────────────────────────────────────────────────────────────────
  router.get("/lessons/:sig/recall", asyncHandler(async (req: Request, res: Response) => {
    const sig = decodeURIComponent(String(req.params.sig));
    const minConfidenceRaw = Number(req.query.min_confidence ?? 0.6);
    const minConfidence = Number.isFinite(minConfidenceRaw) ? minConfidenceRaw : 0.6;
    const lesson = await lessonRepo.recallBySignature(sig, minConfidence);
    if (!lesson) {
      apiLog("READ", "/self-evolution/lessons/recall", { sig, minConfidence, status: 404 });
      return res.status(404).json({ error: "not_found", message: `No validated/live lesson at confidence >= ${minConfidence} for signature "${sig}"` });
    }
    apiLog("READ", "/self-evolution/lessons/recall", { sig, minConfidence, lessonId: lesson.id, status: 200 });
    return res.status(200).json({ lesson });
  }));

  // ─────────────────────────────────────────────────────────────────────
  // POST /lessons/:id/outcome — record an outcome for the lesson and mutate
  // confidence accordingly via `applyOutcome`. Body: SuggestionOutcomeInsert
  // (minus lesson_id which comes from the path) plus optional `adopted` flag.
  // ─────────────────────────────────────────────────────────────────────
  router.post("/lessons/:id/outcome", asyncHandler(async (req: Request, res: Response) => {
    const lessonId = Number(req.params.id);
    if (!Number.isInteger(lessonId) || lessonId <= 0) {
      return res.status(400).json({ error: "bad_request", message: "lesson id must be a positive integer" });
    }
    const body = req.body as Partial<SuggestionOutcomeInsert>;
    // `failure_signature` is OPTIONAL in the body — we look the lesson up by its
    // id from the URL path and read the signature from the row, so api-mode
    // callers (which don't have the signature on hand) can record outcomes
    // without fabricating a placeholder signature that the older code path
    // rejected via listBySignature().
    if (!body?.workflow_id || !body?.verdict || !body?.source) {
      return res.status(400).json({
        error: "bad_request",
        message: "workflow_id, verdict, source are required (failure_signature is optional — looked up by id)",
      });
    }
    // Look up the lesson by id. Use the lesson row's own failure_signature for
    // the suggestion_outcomes row; fall back to the body's value (if provided)
    // when no row exists (e.g., test fixtures that set outcome before insert).
    const found = await lessonRepo.getById(lessonId);
    if (!found) {
      return res.status(404).json({ error: "not_found", message: `lesson ${lessonId} not found` });
    }
    const resolvedSignature = found.failure_signature;

    // Apply the confidence change (LessonRepository handles +/- math).
    const success = body.verdict === "improved";
    await lessonRepo.applyOutcome(lessonId, success);

    // Persist the outcome row for the shared score-card. Use the looked-up
    // signature so downstream aggregation (weakness_list match_lesson_ids,
    // dashboard cross-version counts) joins correctly.
    const outcomeId = await suggestionOutcomeRepo.insert({
      lesson_id: lessonId,
      workflow_id: body.workflow_id,
      node_id: body.node_id ?? null,
      failure_signature: resolvedSignature,
      adopted: Number(body.adopted ?? 0),
      applied_version: body.applied_version ?? null,
      metrics_before: body.metrics_before ?? null,
      metrics_after: body.metrics_after ?? null,
      verdict: body.verdict,
      source: body.source,
    });
    apiLog("WRITE", "/self-evolution/lessons/outcome", { lessonId, outcomeId, verdict: body.verdict, status: 201 });
    return res.status(201).json({ id: outcomeId });
  }));

  // ─────────────────────────────────────────────────────────────────────
  // POST /diagnosis-cards — persist a diagnosis card for an observed failure
  // (L1/L2/L3 levels). Body shape matches DiagnosisCardInsert.
  // ─────────────────────────────────────────────────────────────────────
  router.post("/diagnosis-cards", asyncHandler(async (req: Request, res: Response) => {
    const body = req.body as Partial<DiagnosisCardInsert>;
    if (!body?.flow_id || !body?.workflow_id || !body?.node_id || !body?.failure_signature || !body?.error_text) {
      return res.status(400).json({
        error: "bad_request",
        message: "flow_id, workflow_id, node_id, failure_signature, error_text are required",
      });
    }
    const id = await diagnosisCardRepo.insert({
      flow_id: body.flow_id,
      workflow_id: body.workflow_id,
      node_id: body.node_id,
      failure_signature: body.failure_signature,
      error_text: body.error_text,
      input_snapshot: body.input_snapshot ?? null,
      output_snapshot: body.output_snapshot ?? null,
      step_traces_snapshot: body.step_traces_snapshot ?? null,
      analysis_reasoning: body.analysis_reasoning ?? null,
      suggested_repair_type: body.suggested_repair_type ?? null,
      suggested_repair_content: body.suggested_repair_content ?? null,
      matched_lesson_id: body.matched_lesson_id ?? null,
      outcome: body.outcome ?? "not_recovered",
      attempt_count: body.attempt_count ?? 1,
      diagnosis_level: body.diagnosis_level ?? null,
    });
    apiLog("WRITE", "/self-evolution/diagnosis-cards", { id, sig: body.failure_signature, status: 201 });
    return res.status(201).json({ id });
  }));

  // ─────────────────────────────────────────────────────────────────────
  // PATCH /diagnosis-cards/:id — update outcome (recovered|not_recovered|
  // escalated) and optionally matched_lesson_id. Used by the L1/L2 loop when
  // a diagnosis eventually resolves (recovered) or escalates (escalated).
  // ─────────────────────────────────────────────────────────────────────
  router.patch("/diagnosis-cards/:id", asyncHandler(async (req: Request, res: Response) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id) || id <= 0) {
      return res.status(400).json({ error: "bad_request", message: "id must be a positive integer" });
    }
    const body = req.body as { outcome?: DiagnosisCardInsert["outcome"]; matched_lesson_id?: number | null };
    if (!body?.outcome) {
      return res.status(400).json({ error: "bad_request", message: "outcome is required (recovered|not_recovered|escalated)" });
    }
    const allowed = ["recovered", "not_recovered", "escalated"];
    if (!allowed.includes(body.outcome)) {
      return res.status(400).json({ error: "bad_request", message: `outcome must be one of: ${allowed.join("|")}` });
    }
    // DiagnosisCardRepository.updateOutcome returns the affected row count
    // (UPDATE doesn't throw when no row matches); use it to distinguish a real
    // patch from a no-op-on-missing-id so we return 404 rather than a phantom 200.
    const affected = await diagnosisCardRepo.updateOutcome(id, body.outcome, body.matched_lesson_id ?? null);
    if (affected === 0) {
      apiLog("WRITE", "/self-evolution/diagnosis-cards/patch", { id, status: 404, reason: "no row matched" });
      return res.status(404).json({ error: "not_found", message: `diagnosis card ${id} not found` });
    }
    apiLog("WRITE", "/self-evolution/diagnosis-cards/patch", { id, outcome: body.outcome, status: 200 });
    return res.status(200).json({ id, outcome: body.outcome });
  }));

  // ─────────────────────────────────────────────────────────────────────
  // POST /repair-history — append a ledger row recording that a repair was
  // attempted. Body: RepairHistoryInsert (flow_id, node_id, failure_signature,
  // lesson_id, diagnosis_card_id, suggestion_outcome_id, repair_type,
  // repair_content, applied_by, retry_success, level).
  // ─────────────────────────────────────────────────────────────────────
  router.post("/repair-history", asyncHandler(async (req: Request, res: Response) => {
    const body = req.body as Partial<RepairHistoryInsert>;
    if (!body?.flow_id || !body?.node_id || !body?.failure_signature || !body?.repair_type || !body?.applied_by) {
      return res.status(400).json({
        error: "bad_request",
        message: "flow_id, node_id, failure_signature, repair_type, applied_by are required",
      });
    }
    const id = await repairHistoryRepo.insert({
      flow_id: body.flow_id,
      node_id: body.node_id,
      failure_signature: body.failure_signature,
      lesson_id: body.lesson_id ?? null,
      diagnosis_card_id: body.diagnosis_card_id ?? null,
      suggestion_outcome_id: body.suggestion_outcome_id ?? null,
      repair_type: body.repair_type,
      repair_content: body.repair_content ?? null,
      applied_by: body.applied_by,
      retry_success: body.retry_success ?? null,
      level: body.level ?? null,
    });
    apiLog("WRITE", "/self-evolution/repair-history", { id, appliedBy: body.applied_by, status: 201 });
    return res.status(201).json({ id });
  }));

  // ─────────────────────────────────────────────────────────────────────
  // POST /suggestion-outcomes — standalone outcome write (alternative to
  // /lessons/:id/outcome when the caller already knows the lesson_id and
  // doesn't need the confidence mutation). Body: SuggestionOutcomeInsert.
  // ─────────────────────────────────────────────────────────────────────
  router.post("/suggestion-outcomes", asyncHandler(async (req: Request, res: Response) => {
    const body = req.body as Partial<SuggestionOutcomeInsert>;
    if (!body?.lesson_id || !body?.workflow_id || !body?.failure_signature || !body?.verdict || !body?.source) {
      return res.status(400).json({
        error: "bad_request",
        message: "lesson_id, workflow_id, failure_signature, verdict, source are required",
      });
    }
    const id = await suggestionOutcomeRepo.insert({
      lesson_id: body.lesson_id,
      workflow_id: body.workflow_id,
      node_id: body.node_id ?? null,
      failure_signature: body.failure_signature,
      adopted: Number(body.adopted ?? 0),
      applied_version: body.applied_version ?? null,
      metrics_before: body.metrics_before ?? null,
      metrics_after: body.metrics_after ?? null,
      verdict: body.verdict,
      source: body.source,
    });
    apiLog("WRITE", "/self-evolution/suggestion-outcomes", { id, lessonId: body.lesson_id, status: 201 });
    return res.status(201).json({ id });
  }));

  return router;
}