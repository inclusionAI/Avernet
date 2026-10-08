/**
 * Internal API: POST /api/internal/deploy-history
 *
 * Allows taskguard to insert deploy history records from the deploy/rollback commands.
 * The deploy command computes version = MAX(version)+1 and deploy_number = MAX(deploy_number)+1
 * from deploy_history, then writes the record atomically with the spec save.
 * The /api/workflows/save endpoint with skipDeployHistory:true skips this write,
 * so deploy/rollback must write their own deploy_history records.
 */
import { Router, type Request, type Response } from "express";
import type { WorkflowDeployHistoryRepository } from "../../repositories/workflow-deploy-history-repository.js";
import { asyncHandler } from "../../middleware/async-handler.js";

export function createInternalDeployHistoryRouter(
  wfdhRepo: WorkflowDeployHistoryRepository | null,
): Router {
  const router = Router();

  /** POST /api/internal/deploy-history — insert a deploy history record */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) {
      res.status(503).json({ error: "Service Unavailable" });
      return;
    }

    const {
      packId,
      workflowId,
      deployNumber,
      version,
      tagName,
      action,
      fromDeployNumber,
      specJson,
      note,
      botId,
      ownerId,
    } = req.body as {
      packId: string;
      workflowId: string;
      deployNumber: number;
      version: number;
      tagName: string;
      action: string;
      fromDeployNumber?: number;
      specJson: string;
      note?: string;
      botId?: string | null;
      ownerId?: string | null;
    };

    // Validate required fields
    if (!packId || !workflowId || deployNumber == null || version == null || !action || !specJson) {
      res.status(400).json({ error: "Bad Request", message: "Missing required fields: packId, workflowId, deployNumber, version, action, specJson" });
      return;
    }

    try {
      const row = await wfdhRepo.insert({
        packId,
        workflowId,
        deployNumber,
        version,
        tagName,
        action,
        fromDeployNumber,
        specJson,
        note,
        botId,
        ownerId,
      });

      res.status(201).json({
        workflowId: row.workflow_id,
        deployNumber: row.deploy_number,
        version: row.version,
        action: row.action,
      });
    } catch (error: any) {
      // Check for unique key violation (deploy_number + workflow_id already exists)
      const msg = error?.message ?? String(error);
      if (msg.includes("UNIQUE") || msg.includes("duplicate") || msg.includes("1062") || msg.includes("UNIQUE constraint")) {
        res.status(409).json({ error: "Conflict", message: `Deploy history record already exists for ${workflowId} v${version} or deploy #${deployNumber}` });
        return;
      }
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}