import { Router } from "express";
import { RunReadRepository } from "../../repositories/run-read-repository.js";

/** Mounted behind the existing internal signature middleware. POST binds scope/filters to the signed body. */
export function createInternalRunReadsRouter(repo: RunReadRepository): Router {
  const router = Router();
  for (const kind of ["runs", "logs"] as const) {
    router.post(`/${kind}`, async (req, res) => {
      try {
        const q = req.body;
        if (!q || typeof q.botId !== "string" || typeof q.ownerId !== "string"
          || !q.botId.trim() || !q.ownerId.trim()
          || (kind === "logs" && (typeof q.flowId !== "string" || !q.flowId))) {
          res.status(400).json({ success: false, message: "Missing bot/owner scope or flowId" }); return;
        }
        for (const key of ["workflowId", "status", "identityKey", "nodeId", "level"]) {
          if (q[key] !== undefined && typeof q[key] !== "string") {
            res.status(400).json({success:false,message:`Invalid ${key}`}); return;
          }
        }
        if (q.includeHidden !== undefined && typeof q.includeHidden !== "boolean") {
          res.status(400).json({success:false,message:"Invalid includeHidden"}); return;
        }
        const data = kind === "runs" ? await repo.listRuns(q) : await repo.readLogs(q);
        res.json({ success: true, data });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        const status = message.includes("not found") ? 404 : /scope|cursor|limit must/.test(message) ? 400 : 500;
        res.status(status).json({ success: false, message: status === 500 ? "Shared run query failed" : message });
      }
    });
  }
  return router;
}
