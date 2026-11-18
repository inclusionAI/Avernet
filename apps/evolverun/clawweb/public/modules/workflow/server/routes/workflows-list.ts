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

type WorkflowListPermission = "view" | "execute";
type WorkflowListScope = { restrictedIds: Set<string>; allowedIds: Set<string> } | null;

function parseWorkflowListPermission(req: Request): WorkflowListPermission | undefined {
  const value = req.query.permission;
  if (value === undefined || value === "") return "view";
  if (value === "view" || value === "execute") return value;
  return undefined;
}

function unauthorized(res: Response): void {
  res.status(401).json({ error: "Unauthorized", message: "User identity required" });
}

function badPermission(res: Response): void {
  res.status(400).json({ error: "Bad Request", message: "permission must be view or execute" });
}

function queryText(req: Request, key: string): string | undefined {
  const value = req.query[key];
  if (typeof value !== "string") return undefined;
  const trimmed = value.trim();
  return trimmed || undefined;
}

function emptyExecuteScope(): WorkflowListScope {
  return { restrictedIds: new Set(), allowedIds: new Set() };
}

/** Bot owner query parameters select the asset scope, not the logged-in principal. */
async function intersectActorAccess(
  req: Request,
  repo: BotWorkflowPermissionRepository | null,
  scope: WorkflowListScope,
  permission: WorkflowListPermission,
): Promise<WorkflowListScope> {
  const actor = resolveWorkflowActorId(req);
  if (req.isAdmin) return scope;
  // Execute visibility is fail-closed even when no permission repository is configured.
  // View lists retain their isolated/read-only compatibility behavior.
  if (!repo) return permission === "execute" ? emptyExecuteScope() : scope;
  if (!actor) return emptyExecuteScope();

  if (permission === "view") {
    const effective = await repo.getViewByIdsForOwner(actor);
    if (effective === null) return scope;
    return {
      restrictedIds: effective.restrictedIds,
      allowedIds: await intersectWithBotScope(req, repo, actor, effective.viewableIds, scope),
    };
  }

  const effective = await repo.getExecuteIdsForOwner(actor);
  if (effective === null) return scope;
  return {
    restrictedIds: effective.restrictedIds,
    allowedIds: await intersectWithBotScope(req, repo, actor, effective.executableIds, scope),
  };
}

async function intersectWithBotScope(
  req: Request,
  repo: BotWorkflowPermissionRepository,
  actor: string,
  allowedIds: Set<string>,
  scope: WorkflowListScope,
): Promise<Set<string>> {
  const botId = queryText(req, "botId") ?? "";
  const botOwnerId = queryText(req, "botOwnerId") ?? "";
  const scopeIds = botId
    ? await repo.getWorkflowIdsInBotScope(botOwnerId || actor, botId)
    : scope?.allowedIds;
  return new Set([...allowedIds].filter(id => !scopeIds || scopeIds.has(id)));
}

function toAllowedScope(
  scope: { restrictedIds: Set<string>; viewableIds: Set<string> } | null,
): WorkflowListScope {
  if (!scope) return null;
  return { restrictedIds: scope.restrictedIds, allowedIds: scope.viewableIds };
}

export function registerWorkflowListRoutes(
  router: Router,
  workflowSpecRepo: WorkflowSpecRepository | null,
  botPermRepo: BotWorkflowPermissionRepository | null,
  toEpochMs: (value: Date | string | number | null | undefined) => number,
): void {
  /** GET /list — paginated list of workflow specs, filtered by view or execute permission */
  router.get("/list", asyncHandler(async (req: Request, res: Response) => {
    try {
      if (!workflowSpecRepo) {
        res.json({ data: [], pagination: { page: 1, pageSize: 10, total: 0, totalPages: 0 } });
        return;
      }

      const permission = parseWorkflowListPermission(req);
      if (!permission) { badPermission(res); return; }

      const actor = resolveWorkflowActorId(req);
      if (!actor && !req.isAdmin) { unauthorized(res); return; }

      const page = Math.max(1, Number(req.query.page) || 1);
      const pageSize = Math.min(100, Math.max(10, Number(req.query.pageSize) || 10));
      const search = typeof req.query.search === "string" ? req.query.search : undefined;

      const { rows, total } = await workflowSpecRepo.findPage({ page, pageSize, search });

      const queryBotOwnerId = queryText(req, "botOwnerId");
      const queryBotId = queryText(req, "botId");
      const assetOwnerId = queryBotOwnerId?.trim() || actor || "";
      const botId = queryBotId?.trim() || undefined;

      let scope: WorkflowListScope = null;
      if (!req.isAdmin && botPermRepo && permission === "view") {
        scope = toAllowedScope(await botPermRepo.getViewByIdsForOwner(assetOwnerId, botId));
      }

      scope = await intersectActorAccess(req, botPermRepo, scope, permission);
      res.set("Cache-Control", "no-store");
      const filteredRows = scope === null
        ? rows
        : rows.filter((r) => {
            if (!scope!.restrictedIds.has(r.workflow_id)) return false;
            return scope!.allowedIds.has(r.workflow_id);
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

  /** GET / — list all saved workflow specs, filtered by view or execute permission */
  router.get("/", asyncHandler(async (req: Request, res: Response) => {
    try {
      if (!workflowSpecRepo) {
        res.json([]);
        return;
      }

      const permission = parseWorkflowListPermission(req);
      if (!permission) { badPermission(res); return; }

      const actor = resolveWorkflowActorId(req);
      if (!actor && !req.isAdmin) { unauthorized(res); return; }

      const queryBotOwnerId = queryText(req, "botOwnerId");
      const queryBotId = queryText(req, "botId");
      const assetOwnerId = queryBotOwnerId?.trim() || actor || "";
      const botId = queryBotId?.trim() || undefined;

      res.set("Cache-Control", "no-store");
      const cacheKey = "list:summaries";

      const rows = workflowsCache.get(cacheKey) ?? await workflowSpecRepo.listSummaries();
      workflowsCache.set(cacheKey, rows);

      let scope: WorkflowListScope = null;
      if (!req.isAdmin && botPermRepo && permission === "view") {
        scope = toAllowedScope(await botPermRepo.getViewByIdsForOwner(assetOwnerId, botId));
      }
      scope = await intersectActorAccess(req, botPermRepo, scope, permission);

      const result = rows
        .filter((r) => {
          if (scope === null) return true;
          if (!scope.restrictedIds.has(r.workflow_id)) return false;
          return scope.allowedIds.has(r.workflow_id);
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
