/**
 * Internal API routes for facade_bindings — upsert and delete for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import type { FacadeBindingRepository } from "@avernet/workflow/server/repositories/facade-binding-repository";
import { apiLog } from "@avernet/workflow/server/routes/internal-logger";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createInternalFacadesRouter(facadeRepo: FacadeBindingRepository | null): Router {
  const router = Router();

  /** PUT / — upsert a facade binding */
  router.put("/", asyncHandler(async (req: Request, res: Response) => {
    const { command, workflowId } = req.body as { command?: string; workflowId?: string };
    apiLog("WRITE", "/facades", { command, workflowId });
    if (!facadeRepo) {
      apiLog("WRITE", "/facades", { command, workflowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { packId, remark } = req.body as {
        command?: string;
        workflowId?: string;
        packId?: string;
        remark?: string;
      };

      if (!command || !workflowId) {
        apiLog("WRITE", "/facades", { command, workflowId, status: 400, error: "Missing required fields" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: command, workflowId" });
        return;
      }

      const row = await facadeRepo.upsert({
        command,
        workflowId,
        packId: packId ?? null,
        remark: remark ?? null,
      });
      apiLog("WRITE", "/facades", { command, workflowId, status: 200 });
      res.json({ success: true, data: row });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("WRITE", "/facades", { command, workflowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:command — delete by command */
  router.delete("/:command", asyncHandler(async (req: Request, res: Response) => {
    const command = req.params.command;
    apiLog("DELETE", `/facades/${command}`, { command });
    if (!facadeRepo) {
      apiLog("DELETE", `/facades/${command}`, { command, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const deleted = await facadeRepo.deleteByCommand(String(command));
      if (!deleted) {
        apiLog("DELETE", `/facades/${command}`, { command, status: 404 });
        res.status(404).json({ success: false, error: "Not Found", message: `Facade binding "/${command}" not found` });
        return;
      }
      apiLog("DELETE", `/facades/${command}`, { command, status: 200 });
      res.json({ success: true, data: { affected: true } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("DELETE", `/facades/${command}`, { command, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /by-workflow/:workflowId — delete by workflow */
  router.delete("/by-workflow/:workflowId", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = req.params.workflowId;
    apiLog("DELETE", `/facades/by-workflow/${workflowId}`, { workflowId });
    if (!facadeRepo) {
      apiLog("DELETE", `/facades/by-workflow/${workflowId}`, { workflowId, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const affectedRows = await facadeRepo.deleteByWorkflowId(String(workflowId));
      apiLog("DELETE", `/facades/by-workflow/${workflowId}`, { workflowId, status: 200, affectedRows });
      res.json({ success: true, data: { deleted: affectedRows } });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("DELETE", `/facades/by-workflow/${workflowId}`, { workflowId, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}