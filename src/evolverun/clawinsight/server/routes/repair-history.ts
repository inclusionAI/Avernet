/**
 * createRepairHistoryRouter — external admin route for the repair_history
 * (修法 × 效果 × 来源) ledger. Exposes:
 *   GET /            — list repair records (filter by signature / applied_by / retrySuccess / time window)
 *   GET /by-source   — aggregated counts grouped by applied_by (for 自维护覆盖率 dashboard widget)
 *   GET /by-level    — aggregated counts grouped by L1/L2/L3 (for L1 命中率 / L2 成功率 widgets)
 *
 * Mounted at /api/repair-history. Auth via the admin-auth middleware used by
 * other /api routes. Used by the Bigfish "进化 Tab" + dashboard §10.3.
 *
 * Plan ref: self-evolution unified proposal §10.1 + §10.3 (G4 missing endpoint
 * surface; §10.3 metrics backed by /by-source and /by-level aggregations).
 */
import { Router, type Request, type Response } from "express";
import type { RepairHistoryRepository } from "../repositories/repair-history-repository.js";
import { asyncHandler } from "../middleware/async-handler.js";

export function createRepairHistoryRouter(
  repo: RepairHistoryRepository,
  authMiddleware: (req: Request, res: Response, next: (err?: unknown) => void) => void,
): Router {
  const r = Router();
  r.use(authMiddleware);

  // GET / — list with filters
  r.get("/", asyncHandler(async (req, res) => {
    const failureSignature = typeof req.query.failureSignature === "string" ? req.query.failureSignature : null;
    const appliedBy = typeof req.query.appliedBy === "string"
      ? req.query.appliedBy as "guardian" | "auto_heal" | "evolution" | "manual" : null;
    const retrySuccessParam = req.query.retrySuccess;
    let retrySuccess: boolean | null = null;
    if (retrySuccessParam === "true" || retrySuccessParam === "1") retrySuccess = true;
    else if (retrySuccessParam === "false" || retrySuccessParam === "0") retrySuccess = false;
    const sinceTsParam = req.query.since;
    const sinceTs = typeof sinceTsParam === "string" && Number.isFinite(Number(sinceTsParam)) ? Number(sinceTsParam) : null;
    const limit = Number(req.query.limit ?? 50);
    const offset = Number(req.query.offset ?? 0);
    try {
      const result = await repo.list({ failureSignature, appliedBy, retrySuccess, sinceTs, limit, offset });
      res.json({ items: result.rows, total: result.total, limit, offset });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // GET /by-source?since=<ts> → { items: [{ applied_by, count }] }
  // Powers §10.3 "自维护覆盖率" widget: SUM where applied_by IN (guardian/auto_heal/evolution) / 总失败数.
  r.get("/by-source", asyncHandler(async (req, res) => {
    const sinceTs = typeof req.query.since === "string" ? Number(req.query.since) : 0;
    try {
      const items = await repo.countBySource(sinceTs);
      res.json({ items, since: sinceTs });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // GET /by-level?since=<ts> → { items: [{ level, total, succeeded }] }
  // Powers §10.3 "L1 命中率 / L2 成功率" widgets: succeeded / total per level.
  r.get("/by-level", asyncHandler(async (req, res) => {
    const sinceTs = typeof req.query.since === "string" ? Number(req.query.since) : 0;
    try {
      const items = await repo.countByLevel(sinceTs);
      res.json({ items, since: sinceTs });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  return r;
}