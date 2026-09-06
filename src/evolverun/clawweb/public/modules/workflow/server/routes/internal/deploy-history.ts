/**
 * Internal API for deploy history management.
 *
 * POST /                            — Write deploy history record (called by ClawMind after deploy/rollback/pull)
 * GET  /:workflowId/active           — Get the currently active/default deploy record
 * POST /:workflowId/set-active       — Mark a version as active for a workflow
 * GET  /:workflowId/latest-version   — Get MAX(version) for a workflow
 * GET  /pack/:packId/max-deploy-number — Get MAX(deploy_number) for a pack+workflow
 * GET  /:workflowId/history          — List deploy history for a workflow (no spec_json)
 * GET  /:workflowId/versions/:version/snapshot — Get spec snapshot for rollback (deploy/edit only)
 * GET  /pack/:packId/deploy-number/:deployNumber — Get record by deploy_number
 */
import { Router, type Request, type Response } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { WorkflowDeployHistoryRepository } from "@avernet/workflow/server/repositories/workflow-deploy-history-repository";
import type { WorkflowSpecRepository } from "@avernet/workflow/server/repositories/workflow-spec-repository";

export function createInternalDeployHistoryRouter(
  wfdhRepo: WorkflowDeployHistoryRepository | null,
  workflowSpecRepo: WorkflowSpecRepository | null,
): Router {
  const router = Router();

  /** POST / — Write deploy history record (with 409 auto-retry) */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const b = req.body as {
      packId: string; workflowId: string; deployNumber: number;
      version: number; tagName?: string | null; action: string; fromDeployNumber?: number;
      specJson: string; note?: string; botId?: string; ownerId?: string;
      isActive?: boolean;
    };
    if (!b.packId || !b.workflowId || typeof b.deployNumber !== "number"
        || typeof b.version !== "number" || !b.action || !b.specJson) {
      res.status(400).json({ error: "Bad Request", message: "Missing required fields" });
      return;
    }

    let version = b.version;
    const MAX_INSERT_ATTEMPTS = 3;

    for (let attempt = 1; attempt <= MAX_INSERT_ATTEMPTS; attempt++) {
      try {
        await wfdhRepo.insert({
          packId: b.packId, workflowId: b.workflowId, deployNumber: b.deployNumber,
          version, tagName: b.tagName ?? null, action: b.action as "deploy" | "rollback" | "pull" | "migration" | "edit",
          fromDeployNumber: b.fromDeployNumber, specJson: b.specJson,
          note: b.note, botId: b.botId, ownerId: b.ownerId,
          isActive: b.isActive,
        });
        // Sync version to workflow_specs table
        if (workflowSpecRepo) {
          try { await workflowSpecRepo.updateVersion(b.workflowId, version); }
          catch (err) { console.warn(`[deploy-history] updateVersion failed: ${err instanceof Error ? err.message : err}`); }
        }
        res.json({ success: true, version });
        return; // success
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        const isDuplicate = msg.includes("UNIQUE") || msg.includes("Duplicate");

        if (isDuplicate && attempt < MAX_INSERT_ATTEMPTS) {
          // Version conflict — re-compute from MAX(version) + 1 and retry
          console.warn(`[deploy-history] Insert version=${version} conflicted for ${b.workflowId}, retrying with MAX(version)+1 (attempt ${attempt}/${MAX_INSERT_ATTEMPTS})`);
          try {
            const maxV = await wfdhRepo.getLatestVersion(b.workflowId);
            version = maxV + 1;
          } catch {
            version = version + 1;
          }
          continue; // retry
        }

        if (isDuplicate) {
          // All retries exhausted — still conflict. Return 409 with details.
          console.error(`[deploy-history] Insert failed after ${MAX_INSERT_ATTEMPTS} attempts for ${b.workflowId}: ${msg}`);
          res.status(409).json({ error: "Conflict", message: msg });
        } else {
          res.status(500).json({ error: "Internal Server Error", message: msg });
        }
        return;
      }
    }
  }));

  /** GET /:workflowId/latest-version — Get MAX(version) for a workflow */
  router.get("/:workflowId/latest-version", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    const maxVersion = await wfdhRepo.getLatestVersion(workflowId);
    res.json({ workflowId, maxVersion });
  }));

  /** GET /pack/:packId/max-deploy-number — Get MAX(deploy_number) for a pack+workflow */
  router.get("/pack/:packId/max-deploy-number", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const packId = String(req.params.packId);
    const workflowId = String(req.query.workflowId ?? "");
    if (!workflowId) { res.status(400).json({ error: "Bad Request", message: "workflowId query param required" }); return; }
    const maxDeployNumber = await wfdhRepo.getMaxDeployNumber(packId, workflowId);
    res.json({ packId, workflowId, maxDeployNumber });
  }));

  /** GET /:workflowId/active — Get the currently active/default deploy record */
  router.get("/:workflowId/active", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    const row = await wfdhRepo.findActiveByWorkflowId(workflowId);
    if (!row) { res.json({ found: false }); return; }
    const gmtCreate = typeof row.gmt_create === "string"
      ? Math.floor(new Date(row.gmt_create).getTime() / 1000)
      : row.gmt_create;
    const gmtModified = typeof row.gmt_modified === "string"
      ? Math.floor(new Date(row.gmt_modified).getTime() / 1000)
      : row.gmt_modified;
    res.json({
      found: true,
      packId: row.pack_id,
      deployNumber: row.deploy_number,
      version: row.version,
      tagName: row.tag_name,
      action: row.action,
      specJson: row.spec_json,
      botId: row.bot_id,
      ownerId: row.owner_id,
      gmtCreate,
      gmtModified,
    });
  }));

  /** POST /:workflowId/set-active — Mark a version as active for a workflow.
   *  Accepts optional actorId for audit purposes. Internal callers should supply
   *  the caller identity (e.g. ClawMind user id) so that default-version changes
   *  can be traced. */
  router.post("/:workflowId/set-active", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    const version = parseInt(String(req.body?.version), 10);
    if (isNaN(version)) { res.status(400).json({ error: "Bad Request", message: "version body field required" }); return; }
    const actorId = req.body?.actorId != null ? String(req.body.actorId) : undefined;
    if (actorId != null && actorId.length === 0) { res.status(400).json({ error: "Bad Request", message: "actorId must be a non-empty string" }); return; }
    const ok = await wfdhRepo.setActive(workflowId, version);
    if (!ok) { res.status(404).json({ error: "Not Found", message: "Version not found for workflow" }); return; }
    if (actorId) {
      console.info(`[deploy-history] actor=${actorId} set workflow=${workflowId} version=${version} as active`);
    }
    res.json({ success: true, workflowId, version, actorId });
  }));

  /** GET /:workflowId/history — List deploy history for a workflow (no spec_json in response) */
  router.get("/:workflowId/history", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    const limit = Math.min(100, Math.max(1, parseInt(req.query.limit as string ?? "20", 10)));
    if (isNaN(limit)) { res.status(400).json({ error: "Bad Request" }); return; }
    const rows = await wfdhRepo.listHistory(workflowId, limit);
    const history = rows.map((r) => ({
      deployNumber: r.deploy_number,
      version: r.version,
      tagName: r.tag_name,
      action: r.action,
      fromDeployNumber: r.from_deploy_number,
      note: r.note,
      botId: r.bot_id,
      ownerId: r.owner_id,
      isActive: r.is_active,
      gmtCreate: typeof r.gmt_create === "string"
        ? Math.floor(new Date(r.gmt_create).getTime() / 1000)
        : r.gmt_create,
    }));
    res.json({ workflowId, history });
  }));

  /** GET /:workflowId/versions/:version/snapshot — Get spec snapshot (deploy/edit only, for rollback) */
  router.get("/:workflowId/versions/:version/snapshot", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    const version = parseInt(String(req.params.version), 10);
    if (isNaN(version)) { res.status(400).json({ error: "Bad Request", message: "Invalid version" }); return; }
    const row = await wfdhRepo.findByVersionDeployOrEdit(workflowId, version);
    if (!row) { res.json({ found: false }); return; }
    const gmtCreate = typeof row.gmt_create === "string"
      ? Math.floor(new Date(row.gmt_create).getTime() / 1000)
      : row.gmt_create;
    res.json({
      found: true,
      deployNumber: row.deploy_number,
      version: row.version,
      tagName: row.tag_name,
      action: row.action,
      specJson: row.spec_json,
      gmtCreate,
    });
  }));

  /** GET /pack/:packId/deploy-number/:deployNumber — Get record by deploy_number (unique per UK) */
  router.get("/pack/:packId/deploy-number/:deployNumber", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const packId = String(req.params.packId);
    const workflowId = String(req.query.workflowId ?? "");
    const deployNumber = parseInt(String(req.params.deployNumber), 10);
    if (!workflowId) { res.status(400).json({ error: "Bad Request", message: "workflowId query param required" }); return; }
    if (isNaN(deployNumber)) { res.status(400).json({ error: "Bad Request", message: "Invalid deployNumber" }); return; }
    const row = await wfdhRepo.findByDeployNumber(packId, workflowId, deployNumber);
    if (!row) { res.json({ found: false }); return; }
    const gmtCreate = typeof row.gmt_create === "string"
      ? Math.floor(new Date(row.gmt_create).getTime() / 1000)
      : row.gmt_create;
    res.json({
      found: true,
      packId,
      workflowId,
      deployNumber: row.deploy_number,
      version: row.version,
      tagName: row.tag_name,
      action: row.action,
      fromDeployNumber: row.from_deploy_number,
      specJson: row.spec_json,
      note: row.note,
      gmtCreate,
    });
  }));

  return router;
}
