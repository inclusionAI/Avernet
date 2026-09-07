import { Router, type Request, type Response } from "express";
import type { SuggestionPanelService } from "../services/suggestion-panel-service.js";
import type { LessonRepository } from "../repositories/lesson-repository.js";

/**
 * createSuggestionRouter — mounts the human-approved self-evolution panel:
 *   GET  /                        — list validated/live lessons + active weaknesses (paginated)
 *   POST /:id/apply               — one-click apply a lesson through save → deploy
 *
 * Auth: `authMiddleware` is the same admin-auth middleware used by other /api routes.
 */
export function createSuggestionRouter(
  service: SuggestionPanelService,
  lessonRepo: LessonRepository,
  authMiddleware: (req: Request, res: Response, next: (err?: unknown) => void) => void,
): Router {
  const r = Router();
  r.use(authMiddleware);

  // GET / — list validated/live lessons with pagination. weakness_list join is left
  // for the dashboard route (T7 wiring); this endpoint focuses on the lessons table.
  r.get("/", async (req, res) => {
    try {
      const limit = Math.min(Number(req.query.limit ?? 50), 200);
      const offset = Math.max(0, Number(req.query.offset ?? 0));
      // SQLite + MySQL compatible: status IN ('validated','live') ORDER BY confidence DESC.
      const rows = await lessonRepo.listRecallable(0.0, limit + offset);
      const page = rows.slice(offset, offset + limit);
      res.json({ items: page, total: rows.length, limit, offset });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  });

  // POST /:id/apply — one-click apply; returns 202 on success (with deploy_number),
  // 409 on conflict (lesson not deployable / spec not found), 400 on bad input.
  r.post("/:id/apply", async (req, res) => {
    try {
      const id = Number(req.params.id);
      if (!Number.isInteger(id) || id <= 0) {
        return res.status(400).json({ error: "bad_request", message: "id must be a positive integer" });
      }
      const workflowId = String(req.body?.workflow_id ?? "");
      if (!workflowId) {
        return res.status(400).json({ error: "bad_request", message: "workflow_id required" });
      }
      const user = {
        id: String(req.headers["x-user-id"] ?? "unknown"),
        name: String(req.headers["x-user-name"] ?? ""),
      };
      const result = await service.applyOne(id, workflowId, user);
      if (result.deployed) {
        return res.status(202).json({ applied: true, deploy_number: result.deploy_number });
      }
      // Map known error strings to HTTP codes.
      const codeMap: Record<string, number> = {
        lesson_not_found: 404,
        lesson_not_in_validated_state: 409,
        workflow_spec_not_found: 404,
        node_patch_requires_bench_gate: 422,
      };
      const code = codeMap[result.error ?? ""] ?? 409;
      return res.status(code).json({ applied: false, error: result.error });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  });

  return r;
}