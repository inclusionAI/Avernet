/**
 * Run Archive API routes.
 *
 * GET /api/run-archives/:flowId         — full archive
 * GET /api/run-archives/:flowId/summary — lightweight summary only
 */
import { Router, type Request, type Response } from "express";
import type { RunArchiveBuilder } from "../../run-archive/builder.js";
import { formatArchiveSummary } from "../../run-archive/builder.js";

export function createRunArchiveRouter(
  runArchiveBuilder: RunArchiveBuilder | null,
): Router {
  const router = Router();

  // GET /:flowId — generate and return full archive
  router.get("/:flowId", async (req: Request, res: Response) => {
    if (!runArchiveBuilder) {
      res.status(503).json({ error: "Service Unavailable", message: "Run archive builder not configured" });
      return;
    }
    const flowId = String(req.params.flowId);
    try {
      const archive = await runArchiveBuilder.buildArchive(flowId);
      res.json({ data: archive });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      const status = msg.includes("not found") ? 404 : 500;
      res.status(status).json({ error: status === 404 ? "Not Found" : "Internal Server Error", message: msg });
    }
  });

  // GET /:flowId/summary — lightweight summary (flowRun + failureSummary only)
  router.get("/:flowId/summary", async (req: Request, res: Response) => {
    if (!runArchiveBuilder) {
      res.status(503).json({ error: "Service Unavailable", message: "Run archive builder not configured" });
      return;
    }
    const flowId = String(req.params.flowId);
    try {
      const archive = await runArchiveBuilder.buildArchive(flowId);
      res.json({
        data: {
          archive: archive.archive,
          flowRun: archive.flowRun,
          failureSummary: archive.failureSummary,
        },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      const status = msg.includes("not found") ? 404 : 500;
      res.status(status).json({ error: status === 404 ? "Not Found" : "Internal Server Error", message: msg });
    }
  });

  return router;
}
