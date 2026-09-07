/**
 * createWeaknessListRouter — external admin route for the weakness_list table:
 *   GET /                  — list active weaknesses sorted by priority DESC (paginated)
 *   GET /:id               — weakness detail + drill-down to the underlying
 *                            diagnosis cards referenced by its evidence_diagnosis_ids
 *
 * Mounted at /api/weakness-list. Auth via the same admin-auth middleware used
 * by other /api routes. This complements /api/suggestions (T8) by surfacing
 * the batch-analysis ranking needed by the dashboard and evolution panel.
 *
 * Plan ref: self-evolution plan §10.1 — listed under /api/weakness-list; the
 * /:id detail endpoint was a G4 missing gap, surfaced during unified proposal
 * CR.
 */
import { Router, type Request, type Response } from "express";
import type { WeaknessListRepository, WeaknessListRow } from "../repositories/weakness-list-repository.js";
import type { DiagnosisCardRepository } from "../repositories/diagnosis-card-repository.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

function safeParseJsonArray(raw: string | null): unknown[] {
  if (!raw) return [];
  try {
    const parsed = JSON.parse(raw);
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

export function createWeaknessListRouter(
  repo: WeaknessListRepository,
  authMiddleware: (req: Request, res: Response, next: (err?: unknown) => void) => void,
  diagRepo?: DiagnosisCardRepository,
): Router {
  const r = Router();
  r.use(authMiddleware);

  // GET / — list active weaknesses sorted by priority_score DESC, paginated.
  //   ?limit=  (default 20, max 200)
  //   ?offset= (default 0, min 0)
  //
  // Returns: { items: [...], total, limit, offset }
  //   - `total` is the TRUE count of active weaknesses (SELECT COUNT(*)), not
  //     the page-truncated size, so the frontend can render pagination
  //     correctly across pages with > limit rows.
  //   - items expose evidence_diagnosis_ids / workflow_ids / matched_lesson_ids
  //     as real JSON arrays (the repo persists them as TEXT); the route
  //     re-parses so the frontend can drill down without string parsing.
  r.get("/", asyncHandler(async (req, res) => {
    const limit = Math.min(Math.max(Number(req.query.limit ?? 20), 1), 200);
    const offset = Math.max(0, Number(req.query.offset ?? 0));

    try {
      const [page, total] = await Promise.all([
        repo.listTop(limit + offset).then((rows) => rows.slice(offset, offset + limit)),
        repo.countActive(),
      ]);
      const items = page.map((row: WeaknessListRow) => ({
        ...row,
        workflow_ids: safeParseJsonArray(row.workflow_ids),
        evidence_diagnosis_ids: safeParseJsonArray(row.evidence_diagnosis_ids),
        matched_lesson_ids: safeParseJsonArray(row.matched_lesson_ids),
      }));
      res.json({ items, total, limit, offset });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // GET /:id — weakness detail with the underlying diagnosis cards drilled out.
  // The drill-down chain: 大盘 Top → weakness_list ✓ → diagnosis_cards ✓ →
  //   run_logs/node_step_traces (via /api/runs/...).
  // If diagRepo is not provided at mount time the response still includes
  // evidence_diagnosis_ids as parsed JSON, so the caller can fetch cards
  // themselves via /api/diagnosis-cards.
  r.get("/:id", asyncHandler(async (req, res) => {
    const id = Number(req.params.id);
    if (!Number.isInteger(id) || id <= 0) {
      return res.status(400).json({ error: "bad_request", message: "id must be a positive integer" });
    }
    const row = await repo.getById(id);
    if (!row) {
      return res.status(404).json({ error: "not_found", message: `weakness ${id} not found` });
    }
    const evidenceIds = safeParseJsonArray(row.evidence_diagnosis_ids)
      .filter((x): x is number => typeof x === "number" && Number.isInteger(x) && x > 0);
    const evidence_cards = diagRepo && evidenceIds.length
      ? await diagRepo.listByIds(evidenceIds)
      : [];
    res.json({
      ...row,
      workflow_ids: safeParseJsonArray(row.workflow_ids),
      evidence_diagnosis_ids: evidenceIds,
      matched_lesson_ids: safeParseJsonArray(row.matched_lesson_ids),
      evidence_cards,
    });
  }));

  return r;
}