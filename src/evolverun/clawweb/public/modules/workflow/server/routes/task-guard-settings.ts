import { Router, type Request, type Response } from "express";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import type { AutoAnalysisSettings } from "../services/task-guard/auto-analysis-settings.js";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { requireWorkflowAccess, resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";

export function createTaskGuardSettingsRouter(input: {
  settings: AutoAnalysisSettings;
  permissionRepo: BotWorkflowPermissionRepository | null;
}): Router {
  const router = Router();

  router.get("/workflows/:workflowId/auto-analysis", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId ?? "").trim();
    if (!workflowId) { res.status(400).json({ error: "workflowId 为必填项" }); return; }
    if (!await requireWorkflowAccess(req, res, input.permissionRepo, workflowId, "view")) return;
    res.json(await input.settings.get(workflowId));
  }));

  router.put("/workflows/:workflowId/auto-analysis", asyncHandler(async (req: Request, res: Response) => {
    const workflowId = String(req.params.workflowId ?? "").trim();
    if (!workflowId) { res.status(400).json({ error: "workflowId 为必填项" }); return; }
    if (typeof req.body?.enabled !== "boolean") {
      res.status(400).json({ error: "enabled 必须是 boolean" }); return;
    }
    if (!await requireWorkflowAccess(req, res, input.permissionRepo, workflowId, "edit")) return;
    const actor = resolveWorkflowActorId(req) ?? "admin";
    res.json(await input.settings.set(workflowId, req.body.enabled, actor));
  }));

  return router;
}
