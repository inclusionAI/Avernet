import type { Router, Request, Response } from 'express';
import type { BotWorkflowPermissionRepository } from '@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository';
import { requireWorkflowAccess } from '@avernet/clawweb-shared/server/services/workflow-access';
import { asyncHandler } from '@avernet/clawweb-shared/server/middleware/async-handler';
import type { WorkflowDeployHistoryRepository } from '../repositories/workflow-deploy-history-repository.js';
import type { WorkflowVersionSourceService } from '../contracts/workflow-version-source.js';

/** Release history and provenance share the same authenticated workflow boundary. */
export function registerWorkflowHistoryListRoute(
  router: Router,
  wfdhRepo: WorkflowDeployHistoryRepository | null,
  botPermRepo: BotWorkflowPermissionRepository | null,
  versionSource: WorkflowVersionSourceService | null,
  toEpochSec: (value: Date | string | number | null | undefined) => number,
): void {
  /** GET /:workflowId/history — deploy history list (no spec_json) */
  router.get("/:workflowId/history", asyncHandler(async (req: Request, res: Response) => {
    if (!wfdhRepo) { res.status(503).json({ error: "Service Unavailable" }); return; }
    const workflowId = String(req.params.workflowId);
    if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "view")) return;
    const limit = Math.min(100, Math.max(1, parseInt(req.query.limit as string ?? "20", 10)));
    if (isNaN(limit)) { res.status(400).json({ error: "Bad Request" }); return; }
    const releaseOnly = String(req.query.releaseOnly ?? "") === "true";
    const rows = await wfdhRepo.listHistory(workflowId, limit, { releaseOnly });
    const toHistoryItem = (r: {
      deploy_number: number;
      version: number;
      tag_name: string | null;
      action: string;
      from_deploy_number?: number | null;
      note?: string | null;
      bot_id?: string | null;
      owner_id?: string | null;
      gmt_create: Date | string | number | null;
    }, isActive = false) => ({
      deployNumber: r.deploy_number,
      version: r.version,
      tagName: r.tag_name,
      action: r.action,
      fromDeployNumber: r.from_deploy_number,
      note: r.note,
      botId: r.bot_id,
      ownerId: r.owner_id,
      isActive,
      gmtCreate: toEpochSec(r.gmt_create),
    });
    const sources = versionSource ? await versionSource.forWorkflow(workflowId) : new Map();
    const history = rows.map((r) => ({ ...toHistoryItem(r, !!r.is_active), source: sources.get(r.deploy_number) ?? null }));
    // `history` is a bounded page, while an explicitly selected online version
    // may be older than that page. Return it separately so clients do not
    // accidentally compare against the newest release instead.
    const active = await wfdhRepo.findActiveByWorkflowId(workflowId);
    res.json({ workflowId, history, active: active ? { ...toHistoryItem(active, true), source: sources.get(active.deploy_number) ?? null } : null });
  }));

}
