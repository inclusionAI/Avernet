/** Workflow list endpoints. Cache data, but re-evaluate access on every request. */
import { Router, type Request, type Response } from "express";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { resolveWorkflowActorId } from "@avernet/clawweb-shared/server/services/workflow-access";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type { WorkflowSpecRepository, WorkflowSpecSummary } from "../repositories/workflow-spec-repository.js";
import { ApiCache } from "../cache.js";

export const workflowsCache = new ApiCache<WorkflowSpecSummary[]>({
  ttlMs: 2 * 60 * 1000,
  maxSize: 100,
  keyPrefix: "workflows",
});

type ViewPerm = { restrictedIds: Set<string>; viewableIds: Set<string> } | null;

/** Bot owner query parameters select the asset scope, not the logged-in principal. */
async function intersectActorAccess(req: Request, repo: BotWorkflowPermissionRepository | null, scope: ViewPerm): Promise<ViewPerm> {
  const actor = resolveWorkflowActorId(req);
  if (req.isAdmin || !repo || !actor) return scope;
  const effective = await repo.getViewByIdsForOwner(actor);
  // An empty permission table means unrestricted access, not an empty grant set.
  if (effective === null) return scope;
  const botId = typeof req.query.botId === "string" ? req.query.botId.trim() : "";
  const botOwnerId = typeof req.query.botOwnerId === "string" ? req.query.botOwnerId.trim() : "";
  const scopeIds = botId
    ? await repo.getWorkflowIdsInBotScope(botOwnerId || actor, botId)
    : scope?.viewableIds;
  return {
    restrictedIds: effective.restrictedIds,
    viewableIds: new Set([...effective.viewableIds].filter(id => !scopeIds || scopeIds.has(id))),
  };
}

export function registerWorkflowListRoutes(
  router: Router,
  workflowSpecRepo: WorkflowSpecRepository | null,
  botPermRepo: BotWorkflowPermissionRepository | null,
  toEpochMs: (value: Date | string | number | null | undefined) => number,
): void {
  /** GET /list — paginated list of workflow specs, filtered by view permission */
  router.get("/list", asyncHandler(async (req: Request, res: Response) => {
    try {
      if (!workflowSpecRepo) {
        res.json({ data: [], pagination: { page: 1, pageSize: 10, total: 0, totalPages: 0 } });
        return;
      }

      const page = Math.max(1, Number(req.query.page) || 1);
      const pageSize = Math.min(100, Math.max(10, Number(req.query.pageSize) || 10));
      const search = typeof req.query.search === "string" ? req.query.search : undefined;

      const { rows, total } = await workflowSpecRepo.findPage({ page, pageSize, search });

      // Apply permission filtering
      const queryBotOwnerId = req.query.botOwnerId as string | undefined;
      const headerUserId = req.headers["x-user-id"] as string | undefined;
      const queryBotId = req.query.botId as string | undefined;
      const botOwnerId = queryBotOwnerId?.trim() || headerUserId?.trim() || req.cookies?.staff_id?.trim() || resolveWorkflowActorId(req) || "";
      const botId = queryBotId?.trim() || undefined;

      // Require an authenticated identity; reject anonymous callers.
      if (!botOwnerId && !req.isAdmin) {
        res.status(401).json({ error: "Unauthorized", message: "User identity required" });
        return;
      }

      let viewPerm: ViewPerm = null;
      if (!req.isAdmin && botPermRepo && botOwnerId) {
        viewPerm = await botPermRepo.getViewByIdsForOwner(botOwnerId, botId);
      } else if (!req.isAdmin && botPermRepo && !botOwnerId) {
        viewPerm = { restrictedIds: new Set(), viewableIds: new Set() };
      }

      viewPerm = await intersectActorAccess(req, botPermRepo, viewPerm);
      res.set("Cache-Control", "no-store");
      const filteredRows = viewPerm === null
        ? rows
        : rows.filter((r) => {
            if (!viewPerm!.restrictedIds.has(r.workflow_id)) return false;
            return viewPerm!.viewableIds.has(r.workflow_id);
          });

      const data = filteredRows.map((r) => ({
        workflowId: r.workflow_id,
        title: r.title ?? r.workflow_id,
        packId: r.pack_id,
        updatedAt: toEpochMs(r.gmt_modified),
        ownerId: r.resolved_owner_id ?? r.owner_id ?? null,
      }));
      res.json({
        data,
        pagination: {
          page,
          pageSize,
          total,
          totalPages: Math.ceil(total / pageSize),
        },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** GET / — list all saved workflow specs from database, filtered by view permission */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    try {
      if (!workflowSpecRepo) {
        res.json([]);
        return;
      }

      const queryBotOwnerId = req.query.botOwnerId as string | undefined;
      const headerUserId = req.headers["x-user-id"] as string | undefined;
      const queryBotId = req.query.botId as string | undefined;
      const botOwnerId = queryBotOwnerId?.trim() || headerUserId?.trim() || req.cookies?.staff_id?.trim() || resolveWorkflowActorId(req) || "";
      const botId = queryBotId?.trim() || undefined;

      // Require an authenticated identity; reject anonymous callers.
      if (!botOwnerId && !req.isAdmin) {
        res.status(401).json({ error: "Unauthorized", message: "User identity required" });
        return;
      }

      res.set("Cache-Control", "no-store");

      const cacheKey = `list:${botOwnerId}:${botId ?? ""}:${req.isAdmin ? "admin" : "user"}`;

      const rows = workflowsCache.get(cacheKey) ?? await workflowSpecRepo.listSummaries();
      workflowsCache.set(cacheKey, rows);

      let viewPerm: ViewPerm = null;
      if (req.isAdmin) {
        viewPerm = null;
      } else if (botPermRepo && botOwnerId) {
        viewPerm = await botPermRepo.getViewByIdsForOwner(botOwnerId, botId);
      } else if (!botPermRepo) {
        // No permission table configured: allow all (isolated/read-only deployments).
        viewPerm = null;
      } else {
        // Has permission table but no owner id: show nothing.
        viewPerm = { restrictedIds: new Set(), viewableIds: new Set() };
      }

      viewPerm = await intersectActorAccess(req, botPermRepo, viewPerm);
      const result = rows
        .filter((r) => {
          if (viewPerm === null) return true;
          if (!viewPerm.restrictedIds.has(r.workflow_id)) return false;
          return viewPerm.viewableIds.has(r.workflow_id);
        })
        .map((r) => ({
          workflowId: r.workflow_id,
          title: r.title ?? r.workflow_id,
          packId: r.pack_id,
          updatedAt: toEpochMs(r.gmt_modified),
          ownerId: r.resolved_owner_id ?? r.owner_id ?? null,
        }));
      res.json(result);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

}
