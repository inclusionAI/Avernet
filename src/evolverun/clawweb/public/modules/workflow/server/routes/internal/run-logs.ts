/**
 * Internal API routes for run_logs — receives console log writes from ClawMind.
 */
import { Router, type Request, type Response } from "express";
import type { RunLogRepository, RunLogInsert } from "@avernet/workflow/server/repositories/run-log-repository";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalRunLogsRouter(
  runLogRepo: RunLogRepository | null,
): Router {
  const router = Router();

  // POST /batch — batch insert run log entries
  router.post("/batch", asyncHandler(async (req: Request, res: Response) => {
    const entries = req.body as RunLogInsert[];
    if (!Array.isArray(entries) || entries.length === 0) {
      res.status(400).json({ error: "Bad Request", message: "Body must be a non-empty array of RunLogInsert" });
      return;
    }
    if (!runLogRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Run log repository not configured" });
      return;
    }
    // Diagnostic: log first entry to verify body parsing
    const first = entries[0];
    console.log(
      `[run-logs] POST /batch: count=${entries.length} ` +
      `firstFlowId=${first?.flow_id?.substring(0, 20) ?? "MISSING"} ` +
      `firstLevel=${first?.level ?? "MISSING"} ` +
      `repoDbType=${runLogRepo ? (runLogRepo as any).db?.dbType ?? "unknown" : "null"}`,
    );
    const inserted = await runLogRepo.insertBatch(entries);
    console.log(`[run-logs] POST /batch result: inserted=${inserted}/${entries.length}`);
    res.status(201).json({ success: true, inserted });
  }));

  // GET /:flowId — query run logs by flowId
  router.get("/:flowId", asyncHandler(async (req: Request, res: Response) => {
    if (!runLogRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Run log repository not configured" });
      return;
    }
    const flowId = req.params.flowId as string;
    const logs = await runLogRepo.findByFlowId(flowId);
    res.json({ data: logs });
  }));

  // DELETE /:flowId — delete run logs by flowId
  router.delete("/:flowId", asyncHandler(async (req: Request, res: Response) => {
    if (!runLogRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Run log repository not configured" });
      return;
    }
    const flowId = req.params.flowId as string;
    const deleted = await runLogRepo.deleteByFlowId(flowId);
    res.json({ success: true, deleted });
  }));

  return router;
}
