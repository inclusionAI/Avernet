import type { Request, Response } from "express";
import type { BotWorkflowPermissionRepository } from "../repositories/bot-workflow-permission-repository.js";

export type WorkflowAccessMode = "view" | "edit";

export function resolveWorkflowActorId(req: Request): string | null {
  const cookies = req.cookies as Record<string, string> | undefined;
  const value = [
    req.header("X-Staff-Id"),
    req.header("staff_id"),
    req.header("X-User-Id"),
    cookies?.staff_id,
  ].map((item) => item?.trim()).find(Boolean);
  return value || null;
}

export async function hasWorkflowAccess(
  req: Request,
  repo: BotWorkflowPermissionRepository | null,
  workflowId: string,
  mode: WorkflowAccessMode,
): Promise<boolean> {
  if (req.isAdmin) return true;
  // Some isolated/read-only deployments do not configure the permission table.
  // Keep their existing behavior; the normal ClawWeb runtime always injects it.
  if (!repo) return true;
  const actor = resolveWorkflowActorId(req);
  if (!actor) return false;
  if (mode === "edit") return repo.hasEditPermission(workflowId, actor);
  const view = await repo.getViewByIdsForOwner(actor);
  return Boolean(view?.viewableIds.has(workflowId));
}

export async function requireWorkflowAccess(
  req: Request,
  res: Response,
  repo: BotWorkflowPermissionRepository | null,
  workflowId: string,
  mode: WorkflowAccessMode,
): Promise<boolean> {
  if (!repo) return true;
  if (!resolveWorkflowActorId(req) && !req.isAdmin) {
    res.status(401).json({ error: "Unauthorized", message: "User identity required" });
    return false;
  }
  if (!await hasWorkflowAccess(req, repo, workflowId, mode)) {
    res.status(403).json({ error: "Forbidden", message: `No ${mode} permission for this workflow` });
    return false;
  }
  return true;
}
