import { Router, type Request, type Response } from "express";
import { EvolutionMetricsRepository } from "../repositories/evolution-metrics.js";
import { asyncHandler } from "../middleware/async-handler.js";

/**
 * createEvolutionMetricsRouter — exposes the family of §10.3 dashboard widgets
 * plus the cross-version failure-rate anchor:
 *   GET /failure-rate-by-version[?signature=...]    — Anchor 1 (already shipped)
 *   GET /physical-repair-coverage?since=<ts>        — §10.3 自维护覆盖率
 *   GET /lesson-confidence-distribution             — §10.3 经验可信度分布
 *   GET /lesson-cross-workflow-reuse?limit=10       — §10.3 经验复用次数
 *   GET /weakness-list-top?limit=10                 — §10.3 弱点清单 Top N
 *   GET /repair-by-source?since=<ts>                — appears twice for back-compat;
 *                                                     proxies /api/repair-history/by-source
 *   GET /repair-by-level?since=<ts>                 — §10.3 L1 命中率 / L2 成功率
 *
 * Auth: `adminAuthMiddleware` is already applied globally before any /api route
 * is mounted (see server/index.ts:547), so no per-router auth is needed.
 */
export function createEvolutionMetricsRouter(repo: EvolutionMetricsRepository): Router {
  const r = Router();

  r.get("/failure-rate-by-version", asyncHandler(async (req: Request, res: Response) => {
    try {
      const signature = req.query.signature ? String(req.query.signature) : null;
      const rows = await repo.failureRateByVersion(signature);
      res.json({ items: rows });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // §10.3 自维护覆盖率 — repair_history GROUP BY applied_by + grand total
  r.get("/physical-repair-coverage", asyncHandler(async (req: Request, res: Response) => {
    try {
      const sinceTs = req.query.since ? Number(req.query.since) : 0;
      const items = await repo.physicalRepairCoverage(sinceTs);
      res.json({ items, since: sinceTs });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // §10.3 经验可信度分布 — bucket counts of lessons.confidence_score
  r.get("/lesson-confidence-distribution", asyncHandler(async (_req: Request, res: Response) => {
    try {
      const items = await repo.lessonConfidenceDistribution();
      res.json({ items });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // §10.3 经验复用次数 — top-N lessons by cross-workflow hit count
  r.get("/lesson-cross-workflow-reuse", asyncHandler(async (req: Request, res: Response) => {
    try {
      const limit = Math.min(Math.max(Number(req.query.limit ?? 10), 1), 50);
      const items = await repo.lessonCrossWorkflowReuse(limit);
      res.json({ items });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  // §10.3 弱点清单 Top N — active weakness_list ranked by priority_score DESC
  r.get("/weakness-list-top", asyncHandler(async (req: Request, res: Response) => {
    try {
      const limit = Math.min(Math.max(Number(req.query.limit ?? 10), 1), 50);
      const items = await repo.weaknessListTop(limit);
      res.json({ items });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      res.status(500).json({ error: "internal", message: msg });
    }
  }));

  return r;
}