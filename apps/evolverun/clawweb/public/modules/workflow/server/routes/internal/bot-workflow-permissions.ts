/**
 * Internal API routes for bot_workflow_permissions — permission check for ClawMind.
 */
import { Router, type Request, type Response } from "express";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { apiLog } from "@avernet/workflow/server/routes/internal-logger";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

const VALID_PERMISSIONS = new Set(["view", "execute", "edit"]);

export function createInternalBotWorkflowPermissionsRouter(botPermRepo: BotWorkflowPermissionRepository | null): Router {
  const router = Router();

  /** POST /check — check if a bot has a specific permission on a workflow */
  router.post("/check", asyncHandler(async (req: Request, res: Response) => {
    const { botId, botOwnerId, workflowId, permission } = req.body as {
      botId?: string;
      botOwnerId?: string;
      workflowId?: string;
      permission?: string;
    };
    apiLog("READ", "/bot-workflow-permissions/check", { botId, botOwnerId, workflowId, permission });

    if (!botPermRepo) {
      apiLog("READ", "/bot-workflow-permissions/check", { botId, botOwnerId, workflowId, permission, status: 503, error: "Database not configured" });
      res.status(503).json({ success: false, error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    try {
      if (!botId || !botOwnerId || !workflowId || !permission) {
        apiLog("READ", "/bot-workflow-permissions/check", { botId, botOwnerId, workflowId, permission, status: 400, error: "Missing required fields" });
        res.status(400).json({ success: false, error: "Bad Request", message: "Missing required fields: botId, botOwnerId, workflowId, permission" });
        return;
      }

      if (!VALID_PERMISSIONS.has(permission)) {
        apiLog("READ", "/bot-workflow-permissions/check", { botId, botOwnerId, workflowId, permission, status: 400, error: "Invalid permission value" });
        res.status(400).json({ success: false, error: "Bad Request", message: "permission must be one of: view, execute, edit" });
        return;
      }

      const allowed = await botPermRepo.checkPermission(botId, botOwnerId, workflowId, permission as "view" | "execute" | "edit");
      // Check if any permission records exist for this workflow (used by engine for fallback logic)
      const hasRecords = await botPermRepo.hasRecordsForWorkflow(workflowId);
      apiLog("READ", "/bot-workflow-permissions/check", { botId, botOwnerId, workflowId, permission, status: 200, allowed, hasRecords });
      res.json({ allowed, hasRecords });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      apiLog("READ", "/bot-workflow-permissions/check", { botId, botOwnerId, workflowId, permission, status: 500, error: msg });
      res.status(500).json({ success: false, error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}