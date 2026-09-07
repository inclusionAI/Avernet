/**
 * createLessonRouter — external admin route for the lessons (system memory)
 * table. Exposes:
 *   GET    /             — list lessons (filter by status / workflow / signature / error_class)
 *   GET    /:id          — lesson detail
 *   POST   /             — manual entry (admin录入)
 *   PUT    /:id          — edit (status transition, repair_content rewrite)
 *   DELETE /:id          — retire (soft-delete → status='expired', never hard delete)
 *   POST   /search       — search by failure_signature / error_class (returns top-N)
 *
 * Mounted at /api/lessons. Auth via the same admin-auth middleware used by
 * other /api routes. Used by the Bigfish "进化 Tab · 经验" sub-tab and the
 * legacy knowledge-base management page.
 *
 * Plan ref: self-evolution unified proposal §10.1 (G2 missing endpoint surface).
 */
import { Router, type Request, type Response } from "express";
import type { LessonRepository, LessonRow, LessonInsert } from "../repositories/lesson-repository.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

type StatusFilter = LessonRow["status"];

export function createLessonRouter(
  repo: LessonRepository,
  authMiddleware: (req: Request, res: Response, next: (err?: unknown) => void) => void,
): Router {
  const r = Router();
  r.use(authMiddleware);

  // GET / — list lessons with optional filters.
  r.get("/", asyncHandler(async (req, res) => {
    const status = typeof req.query.status === "string" ? req.query.status as StatusFilter : null;
    const workflowId = typeof req.query.workflowId === "string" ? req.query.workflowId : null;
    const failureSignature = typeof req.query.failureSignature === "string" ? req.query.failureSignature : null;
    const errorClass = typeof req.query.errorClass === "string" ? req.query.errorClass : null;
    const limit = Number(req.query.limit ?? 20);
    const offset = Number(req.query.offset ?? 0);
    try {
      const result = await repo.list({ status, workflowId, failureSignature, errorClass, limit, offset });
      res.json({ items: result.rows, total: result.total, limit, offset });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // GET /:id — single lesson detail
  r.get("/:id", asyncHandler(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id) || id <= 0) return res.status(400).json({ error: "bad_request", message: "id must be a positive integer" });
    const row = await repo.getById(id);
    if (!row) return res.status(404).json({ error: "not_found", message: `lesson ${id} not found` });
    res.json(row);
  }));

  // POST / — manual entry (creates a draft lesson or upserts on (sig, repair_type))
  r.post("/", asyncHandler(async (req, res) => {
    const body = req.body as Partial<LessonInsert>;
    if (!body?.failure_signature || !body?.repair_type || !body?.repair_content) {
      return res.status(400).json({
        error: "bad_request",
        message: "failure_signature, repair_type, repair_content are required",
      });
    }
    try {
      const id = await repo.upsert({
        failure_signature: body.failure_signature,
        error_class: body.error_class ?? null,
        executor_type: body.executor_type ?? null,
        tool_or_node: body.tool_or_node ?? null,
        repair_type: body.repair_type,
        repair_content: body.repair_content,
        confidence_score: body.confidence_score ?? 0.5,
        status: body.status ?? "draft",
        source: body.source ?? "manual",
        evidence_run_ids: body.evidence_run_ids ?? null,
        related_workflow_ids: body.related_workflow_ids ?? null,
        metrics_before: body.metrics_before ?? null,
        metrics_after: body.metrics_after ?? null,
        bench_domain_id: body.bench_domain_id ?? null,
      });
      res.status(201).json({ id, failure_signature: body.failure_signature, status: body.status ?? "draft" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // PUT /:id — edit fields. Allow status transition + repair_content rewrite.
  //   Important: target the row at id=id ONLY — never use the (sig, repair_type)
  //   upsert path here, otherwise a caller-supplied new failure_signature would
  //   silently merge two lessons (lower row gets overwritten, original id-anchored
  //   row untouched).
  r.put("/:id", asyncHandler(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id) || id <= 0) return res.status(400).json({ error: "bad_request", message: "id must be a positive integer" });
    const existing = await repo.getById(id);
    if (!existing) return res.status(404).json({ error: "not_found", message: `lesson ${id} not found` });
    const body = req.body as Partial<LessonInsert>;
    try {
      // Build the patch dict — only fields the caller explicitly supplied.
      // If the caller changes failure_signature to one owned by another lesson
      // (UK violation), updateFields throws a UNIQUE-constraint error which
      // the catch block below maps to HTTP 409 Conflict.
      const patch: Record<string, unknown> = {};
      for (const k of [
        "failure_signature", "error_class", "executor_type", "tool_or_node",
        "repair_type", "repair_content", "confidence_score", "status",
        "evidence_run_ids", "source", "related_workflow_ids",
        "metrics_before", "metrics_after", "bench_domain_id",
      ] as const) {
        if (body[k] !== undefined) patch[k] = body[k];
      }
      if (Object.keys(patch).length === 0) {
        return res.status(400).json({ error: "bad_request", message: "no fields supplied in body to update" });
      }
      await repo.updateFields(id, patch);
      const updated = await repo.getById(id);
      res.json(updated);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // UNIQUE constraint violation surfaces as "UNIQUE constraint failed:
      // lessons.failure_signature, lessons.repair_type" on sqlite and as
      // error code 1062 (ER_DUP_ENTRY) on mysql; both contain "UNIQUE"/"Duplicate".
      if (/UNIQUE constraint failed|Duplicate|ER_DUP_ENTRY|already exists/i.test(msg)) {
        return res.status(409).json({
          error: "conflict",
          message: `更新失败: (failure_signature, repair_type) 与已有经验冲突 — 不能用作此 lesson 的新签名`,
        });
      }
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // DELETE /:id — soft-delete (retire). Hard delete is intentionally NOT supported —
  //   lessons are audit history; "expired" status removes them from the recallable pool.
  r.delete("/:id", asyncHandler(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id) || id <= 0) return res.status(400).json({ error: "bad_request", message: "id must be a positive integer" });
    const existing = await repo.getById(id);
    if (!existing) return res.status(404).json({ error: "not_found", message: `lesson ${id} not found` });
    await repo.retire(id);
    const after = await repo.getById(id);
    res.json({ id, status: after?.status ?? "expired" });
  }));

  // POST /search — admin-side search (returns ranked top-N by confidence DESC)
  r.post("/search", asyncHandler(async (req, res) => {
    const body = req.body as { failureSignature?: string; status?: StatusFilter; limit?: number };
    if (!body?.failureSignature) {
      return res.status(400).json({ error: "bad_request", message: "failureSignature is required" });
    }
    try {
      const { rows } = await repo.list({
        failureSignature: body.failureSignature,
        status: body.status ?? null,
        limit: body.limit ?? 10,
        offset: 0,
      });
      res.json({ items: rows });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  return r;
}