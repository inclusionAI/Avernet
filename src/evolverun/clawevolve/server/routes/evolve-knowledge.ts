import { randomUUID } from "node:crypto";
import { Router, type Request, type Response } from "express";
import type { IDatabase } from "../db.js"
import type { EvolveRepository } from "../repositories/evolve-repository.js"
import type { BotWorkflowPermissionRepository } from "../repositories/bot-workflow-permission-repository.js"
import { requireWorkflowAccess } from "../services/workflow-access.js"
import { WorkflowEvolutionRepository } from "../repositories/workflow-evolution-repository.js"

function lessonSuccessRate(hitCount: number, rescuedCount: number): number {
  if (!hitCount) return 0;
  return Number((rescuedCount / hitCount).toFixed(4));
}

function withLessonStats(row: Awaited<ReturnType<EvolveRepository["findLesson"]>>) {
  if (!row) return null;
  return {
    ...row,
    confidence: Number(row.confidence ?? 0),
    successRate: lessonSuccessRate(Number(row.hit_count ?? 0), Number(row.rescued_count ?? 0)),
  };
}

function getActor(req: Request): string {
  return String(req.header("X-User-Id") ?? "").trim();
}


function positiveInt(value: unknown, fallback: number, max: number): number {
  const n = Number(value);
  if (!Number.isFinite(n) || n <= 0) return fallback;
  return Math.min(Math.floor(n), max);
}

function textOrNull(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed ? trimmed : null;
}

function lessonId(): string {
  return `LS-${randomUUID().slice(0, 12).toUpperCase()}`;
}

function diagnosisId(): string {
  return `DG-${randomUUID().slice(0, 12).toUpperCase()}`;
}



function parseJsonStringArray(value: string | null | undefined): string[] {
  if (!value) return [];
  try {
    const parsed = JSON.parse(value) as unknown;
    return Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === "string") : [];
  } catch {
    return [];
  }
}

export function createEvolveKnowledgeRouter(
  _db: IDatabase,
  repo: EvolveRepository | null,
  botPermRepo: BotWorkflowPermissionRepository | null = null,
): Router {
  const router = Router();
  const workflowEvolutionRepo = new WorkflowEvolutionRepository(_db);

  router.get("/lessons", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const workflowId = textOrNull(req.query.workflowId);
      if (workflowId && !await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
      const status = textOrNull(req.query.status);
      const query = textOrNull(req.query.query);
      const limit = positiveInt(req.query.limit, 50, 200);
      const offset = Math.max(Number(req.query.offset ?? 0) || 0, 0);
      const result = await repo.listLessons({ workflowId, status, query, limit, offset });
      res.json({ lessons: result.rows.map((row) => withLessonStats(row)), total: result.total, limit, offset });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.get("/lessons/:lessonId", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const row = await repo.findLesson(String(req.params.lessonId));
      if (!row) {
        res.status(404).json({ error: "Not Found", message: "Lesson not found" });
        return;
      }
      if (row.workflow_id && !await requireWorkflowAccess(req, res, botPermRepo, row.workflow_id, "view")) return;
      res.json({ lesson: withLessonStats(row) });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/lessons", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const workflowId = textOrNull(body.workflowId ?? body.workflow_id);
      if (workflowId && !await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
      const failureSignature = textOrNull(body.failureSignature ?? body.failure_signature);
      const failureMode = textOrNull(body.failureMode ?? body.failure_mode);
      const fixKind = textOrNull(body.fixKind ?? body.fix_kind);
      const fixSpec = typeof body.fixSpec === "string" ? body.fixSpec : typeof body.fix_spec === "string" ? body.fix_spec : "";
      if (!failureSignature || !failureMode || !fixKind || !fixSpec.trim()) {
        res.status(400).json({ error: "Bad Request", message: "failureSignature、failureMode、fixKind、fixSpec are required" });
        return;
      }
      const created = await repo.createLesson({
        lessonId: textOrNull(body.lessonId ?? body.lesson_id) ?? lessonId(),
        workflowId,
        nodeId: textOrNull(body.nodeId ?? body.node_id),
        failureSignature,
        failureMode,
        executorType: textOrNull(body.executorType ?? body.executor_type),
        fixKind,
        fixSpec,
        status: textOrNull(body.status) ?? "draft",
        confidence: Number.isFinite(Number(body.confidence)) ? Number(body.confidence) : 0,
        hitCount: Number.isFinite(Number(body.hitCount ?? body.hit_count)) ? Number(body.hitCount ?? body.hit_count) : 0,
        rescuedCount: Number.isFinite(Number(body.rescuedCount ?? body.rescued_count)) ? Number(body.rescuedCount ?? body.rescued_count) : 0,
        note: typeof body.note === "string" ? body.note : null,
        createdBy: getActor(req) || textOrNull(body.createdBy ?? body.created_by),
        updatedBy: getActor(req) || textOrNull(body.updatedBy ?? body.updated_by),
      });
      res.status(201).json({ lesson: withLessonStats(created) });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.patch("/lessons/:lessonId", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const existing = await repo.findLesson(String(req.params.lessonId));
      if (!existing) {
        res.status(404).json({ error: "Not Found", message: "Lesson not found" });
        return;
      }
      if (existing.workflow_id && !await requireWorkflowAccess(req, res, botPermRepo, existing.workflow_id, "edit")) return;
      const updated = await repo.updateLesson(String(req.params.lessonId), {
        workflowId: body.workflowId !== undefined || body.workflow_id !== undefined ? textOrNull(body.workflowId ?? body.workflow_id) : undefined,
        nodeId: body.nodeId !== undefined || body.node_id !== undefined ? textOrNull(body.nodeId ?? body.node_id) : undefined,
        failureSignature: typeof (body.failureSignature ?? body.failure_signature) === "string" ? String(body.failureSignature ?? body.failure_signature) : undefined,
        failureMode: typeof (body.failureMode ?? body.failure_mode) === "string" ? String(body.failureMode ?? body.failure_mode) : undefined,
        executorType: body.executorType !== undefined || body.executor_type !== undefined ? textOrNull(body.executorType ?? body.executor_type) : undefined,
        fixKind: typeof (body.fixKind ?? body.fix_kind) === "string" ? String(body.fixKind ?? body.fix_kind) : undefined,
        fixSpec: typeof (body.fixSpec ?? body.fix_spec) === "string" ? String(body.fixSpec ?? body.fix_spec) : undefined,
        status: typeof body.status === "string" ? body.status : undefined,
        confidence: body.confidence !== undefined ? Number(body.confidence) : undefined,
        note: body.note !== undefined ? (typeof body.note === "string" ? body.note : null) : undefined,
        updatedBy: getActor(req) || textOrNull(body.updatedBy ?? body.updated_by) || undefined,
      });
      if (!updated) {
        res.status(404).json({ error: "Not Found", message: "Lesson not found" });
        return;
      }
      res.json({ lesson: withLessonStats(updated) });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/lessons/:lessonId/outcomes", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const lesson = await repo.findLesson(String(req.params.lessonId));
      if (!lesson) {
        res.status(404).json({ error: "Not Found", message: "Lesson not found" });
        return;
      }
      if (lesson.workflow_id && !await requireWorkflowAccess(req, res, botPermRepo, lesson.workflow_id, "edit")) return;
      const outcome = await repo.recordLessonOutcome({
        outcomeId: textOrNull(body.outcomeId ?? body.outcome_id) ?? `OUT-${randomUUID().slice(0, 12).toUpperCase()}`,
        lessonId: lesson.lesson_id,
        workflowId: textOrNull(body.workflowId ?? body.workflow_id) ?? lesson.workflow_id,
        nodeId: textOrNull(body.nodeId ?? body.node_id) ?? lesson.node_id,
        action: textOrNull(body.action) ?? lesson.status,
        applied: body.applied === undefined ? true : Boolean(body.applied),
        succeeded: body.succeeded === undefined ? false : Boolean(body.succeeded),
        verdict: textOrNull(body.verdict) ?? "neutral",
        note: typeof body.note === "string" ? body.note : null,
        createdBy: getActor(req) || textOrNull(body.createdBy ?? body.created_by),
      });
      const refreshed = await repo.findLesson(lesson.lesson_id);
      res.json({ outcome, lesson: refreshed ? withLessonStats(refreshed) : null });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.get("/diagnoses", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const workflowId = textOrNull(req.query.workflowId);
      if (!workflowId && botPermRepo && !req.isAdmin) {
        res.status(400).json({ error: "Bad Request", message: "workflowId is required" });
        return;
      }
      if (workflowId && !await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
      const flowId = textOrNull(req.query.flowId);
      const analysisId = textOrNull(req.query.analysisId);
      const query = textOrNull(req.query.query);
      const limit = positiveInt(req.query.limit, 50, 200);
      const offset = Math.max(Number(req.query.offset ?? 0) || 0, 0);
      const sourceLimit = Math.min(offset + limit, 200);
      const [legacy, projected] = await Promise.all([
        analysisId
          ? Promise.resolve({ rows: [], total: 0 })
          : repo.listDiagnoses({ workflowId, query, limit: sourceLimit, offset: 0 }),
        workflowEvolutionRepo.listProjectedDiagnoses({
          workflowId: workflowId ?? undefined,
          flowId: flowId ?? undefined,
          analysisId: analysisId ?? undefined,
          query: query ?? undefined,
          limit: sourceLimit,
          offset: 0,
        }),
      ]);
      const seen = new Set<string>();
      const diagnoses = [...legacy.rows, ...projected.rows]
        .filter((row) => !flowId || String(row.flow_id ?? row.run_id ?? "") === flowId)
        .filter((row) => {
          const flowId = String(row.flow_id ?? row.run_id ?? "");
          const signature = String(row.failure_signature ?? "");
          const nodeId = String(row.weak_node_id ?? row.node_id ?? "");
          const key = flowId && signature
            ? `${flowId}\u0000${signature}\u0000${nodeId}`
            : String(row.id ?? row.diagnosis_id);
          if (seen.has(key)) return false;
          seen.add(key);
          return true;
        });
      res.json({ diagnoses: diagnoses.slice(offset, offset + limit), total: diagnoses.length, limit, offset });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/diagnoses/analyze-flow", (_req: Request, res: Response) => {
    res.status(501).json({ error: "Not Implemented", message: "Offline analysis moved to ClawMind plugin in Phase 1" });
  });

  router.get("/diagnoses/:diagnosisId", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const row = await repo.findDiagnosis(String(req.params.diagnosisId));
      if (!row) {
        res.status(404).json({ error: "Not Found", message: "Diagnosis not found" });
        return;
      }
      if (!await requireWorkflowAccess(req, res, botPermRepo, row.workflow_id, "view")) return;
      res.json({ diagnosis: row });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/run-diagnoses", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const failureSignature = textOrNull(body.failureSignature ?? body.failure_signature);
      const failureMode = textOrNull(body.failureMode ?? body.failure_mode);
      const flowId = textOrNull(body.flowId ?? body.flow_id);
      const workflowId = textOrNull(body.workflowId ?? body.workflow_id);
      if (!failureSignature || !failureMode || !flowId || !workflowId) {
        res.status(400).json({ error: "Bad Request", message: "failureSignature、failureMode、flowId、workflowId are required" });
        return;
      }
      if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
      const created = await repo.createDiagnosis({
        diagnosisId: textOrNull(body.diagnosisId ?? body.diagnosis_id) ?? diagnosisId(),
        flowId,
        workflowId,
        runId: textOrNull(body.runId ?? body.run_id),
        nodeId: textOrNull(body.nodeId ?? body.node_id),
        failureSignature,
        failureMode,
        executorType: textOrNull(body.executorType ?? body.executor_type),
        weakNodeId: textOrNull(body.weakNodeId ?? body.weak_node_id),
        suggestedFixKind: textOrNull(body.suggestedFixKind ?? body.suggested_fix_kind),
        lessonIdHit: textOrNull(body.lessonIdHit ?? body.lesson_id_hit),
        errorText: typeof body.errorText === "string" ? body.errorText : typeof body.error_text === "string" ? body.error_text : null,
        createdBy: getActor(req) || textOrNull(body.createdBy ?? body.created_by),
      });
      res.status(201).json({ diagnosis: created });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/diagnoses/:diagnosisId/promote", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const diagnosis = await repo.findDiagnosis(String(req.params.diagnosisId));
      if (!diagnosis) {
        res.status(404).json({ error: "Not Found", message: "Diagnosis not found" });
        return;
      }
      if (!await requireWorkflowAccess(req, res, botPermRepo, diagnosis.workflow_id, "edit")) return;
      const body = (req.body ?? {}) as Record<string, unknown>;
      const created = await repo.createLesson({
        lessonId: textOrNull(body.lessonId ?? body.lesson_id) ?? lessonId(),
        workflowId: textOrNull(body.workflowId ?? body.workflow_id) ?? diagnosis.workflow_id,
        nodeId: textOrNull(body.nodeId ?? body.node_id) ?? diagnosis.node_id,
        failureSignature: textOrNull(body.failureSignature ?? body.failure_signature) ?? diagnosis.failure_signature,
        failureMode: textOrNull(body.failureMode ?? body.failure_mode) ?? diagnosis.failure_mode,
        executorType: textOrNull(body.executorType ?? body.executor_type) ?? diagnosis.executor_type,
        fixKind: textOrNull(body.fixKind ?? body.fix_kind) ?? diagnosis.suggested_fix_kind ?? "prompt_patch",
        fixSpec: typeof body.fixSpec === "string" ? body.fixSpec : typeof body.fix_spec === "string" ? body.fix_spec : (diagnosis.error_text ?? diagnosis.failure_signature),
        status: textOrNull(body.status) ?? "draft",
        confidence: Number.isFinite(Number(body.confidence)) ? Number(body.confidence) : 0,
        note: typeof body.note === "string" ? body.note : diagnosis.error_text,
        createdBy: getActor(req) || diagnosis.created_by,
        updatedBy: getActor(req) || diagnosis.created_by,
      });
      await repo.backfillDiagnosisLessonHit(
        created.workflow_id ?? diagnosis.workflow_id,
        created.failure_signature,
        created.lesson_id,
      );
      res.status(201).json({ lesson: withLessonStats(created) });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });


  router.get("/suggestions", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const workflowId = textOrNull(req.query.workflowId);
      if (!workflowId) {
        res.status(400).json({ error: "Bad Request", message: "workflowId is required" });
        return;
      }
      if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
      const status = textOrNull(req.query.status);
      const limit = positiveInt(req.query.limit, 50, 200);
      const offset = Math.max(Number(req.query.offset ?? 0) || 0, 0);
      const result = await repo.listSuggestions({ workflowId, status, limit, offset });
      const suggestions = result.rows.map((row) => {
        const sourceIds = parseJsonStringArray(row.source_diagnosis_ids);
        const evidenceRuns = parseJsonStringArray(row.impact_run_ids);
        let proposal: Record<string, unknown> | null = null;
        try {
          const parsed = JSON.parse(row.proposal_json ?? "null") as unknown;
          if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) proposal = parsed as Record<string, unknown>;
        } catch { /* malformed legacy proposal stays unavailable */ }
        return {
          id: String(row.id),
          diagnosisId: sourceIds[0] ?? "",
          weakNode: row.weak_node_id ?? row.node_id ?? "未知节点",
          signature: row.failure_signature,
          failureMode: row.failure_mode,
          kind: row.fix_kind ?? "",
          fixSpec: row.fix_spec,
          impactRuns: evidenceRuns.length,
          evidenceRuns,
          description: row.fix_spec ?? row.failure_signature,
          proposalDigest: row.proposal_digest ?? null,
          proposal,
          applyTaskId: row.apply_task_id ?? null,
          // Databases created before the verification lifecycle used
          // `applied` for a successful write. It is not proof that the
          // workflow outcome improved, so expose it as waiting for evidence.
          status: row.status === "applied" ? "applied_unverified" : row.status,
          appliedAt: row.applied_at,
          verificationStatus: row.verification_status,
          verificationCheckedAt: row.verification_checked_at,
          recurrenceCount: Number(row.recurrence_count ?? 0),
          lastRecurrenceAt: row.last_recurrence_at,
        };
      });
      res.json({ suggestions, total: result.total, limit, offset });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/suggestions", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const body = (req.body ?? {}) as Record<string, unknown>;
      const workflowId = textOrNull(body.workflowId ?? body.workflow_id);
      const failureSignature = textOrNull(body.failureSignature ?? body.failure_signature);
      if (!workflowId || !failureSignature) {
        res.status(400).json({ error: "Bad Request", message: "workflowId and failureSignature are required" });
        return;
      }
      if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;
      const sourceDiagnosisIds = Array.isArray(body.source_diagnosis_ids)
        ? (body.source_diagnosis_ids as string[])
        : Array.isArray(body.sourceDiagnosisIds)
          ? (body.sourceDiagnosisIds as string[])
          : [];
      const impactRunIds = Array.isArray(body.impact_run_ids)
        ? (body.impact_run_ids as string[])
        : Array.isArray(body.impactRunIds)
          ? (body.impactRunIds as string[])
          : [];
      const created = await repo.createSuggestion({
        workflowId,
        nodeId: textOrNull(body.nodeId ?? body.node_id),
        weakNodeId: textOrNull(body.weakNodeId ?? body.weak_node_id),
        failureSignature,
        failureMode: textOrNull(body.failureMode ?? body.failure_mode),
        fixKind: textOrNull(body.fixKind ?? body.fix_kind),
        fixSpec: typeof body.fixSpec === "string" ? body.fixSpec : typeof body.fix_spec === "string" ? body.fix_spec : "",
        sourceDiagnosisIds,
        impactRunIds,
        status: textOrNull(body.status) ?? "pending",
        createdBy: getActor(req) || textOrNull(body.createdBy ?? body.created_by),
        updatedBy: getActor(req) || textOrNull(body.updatedBy ?? body.updated_by),
      });
      res.status(201).json({ suggestion: created });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  router.post("/analyze", (_req: Request, res: Response) => {
    res.status(501).json({ error: "Not Implemented", message: "Offline analysis moved to ClawMind plugin in Phase 1" });
  });

  router.get("/weak-links", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Evolve repository not configured" });
      return;
    }
    try {
      const workflowId = textOrNull(req.query.workflowId);
      if (!workflowId) {
        res.status(400).json({ error: "Bad Request", message: "workflowId is required" });
        return;
      }
      if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
      const limit = positiveInt(req.query.limit, 50, 200);
      const offset = Math.max(Number(req.query.offset ?? 0) || 0, 0);
      const result = await repo.listWeakLinks({ workflowId, limit, offset });
      res.json({ weakLinks: result.rows, total: result.total, limit, offset });
    } catch (error) {
      res.status(500).json({ error: "Internal Server Error", message: error instanceof Error ? error.message : String(error) });
    }
  });

  return router;
}
