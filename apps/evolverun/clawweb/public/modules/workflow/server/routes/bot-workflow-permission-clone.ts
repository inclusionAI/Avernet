/**
 * POST / — bot workflow permission clone endpoint (mounted by the host under
 * /api/internal/bot-workflow-permissions/clone, behind the service-token
 * middleware — never exposed to browsers).
 *
 * Request: { source_bot_id, source_owner_id?, target_bot_id, target_owner_id?,
 *            scope?: "exact" | "exact+owner-rows", dry_run? }
 *
 * Clones the source bot's workflow grants (can_view/can_execute/can_edit) onto
 * the target bot, using a UNION merge so an existing stronger grant is never
 * downgraded. Owner-level source rows (bot_id NULL/''/'*') are materialized
 * into explicit target rows only under scope="exact+owner-rows"; global
 * wildcard rows (bot_owner_id='*') are never cloned.
 *
 * Identity resolution order for each side (explicit owner param beats all):
 *   1. `*_owner_id` request param
 *   2. compound `bot_id:owner_staff_no` form (the BaaS Open API shape)
 *   3. host-injected `resolveBotOwner` lookup (e.g. ac_bots)
 *
 * Reads do not filter env (shared across environments, like the rest of the
 * permission repository); writes go through upsert and stamp the current env.
 */
import { Router, type Request, type Response } from "express";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import type {
  BotWorkflowPermissionRepository,
  BotWorkflowPermissionRow,
} from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";

export type BotWorkflowPermissionCloneScope = "exact" | "exact+owner-rows";

export type CloneRouterDeps = {
  botPermRepo: BotWorkflowPermissionRepository | null;
  /** Host-supplied bot_id → owner_id lookup (e.g. ac_bots). Optional: when
   * absent, a bare bot_id without an explicit owner param is a 400. */
  resolveBotOwner?: ((botId: string) => Promise<string | null>) | null;
};

type BotRef = { botId: string; ownerId: string; source: "param" | "compound" | "directory" };

type CloneRequest = {
  source_bot_id?: unknown;
  source_owner_id?: unknown;
  target_bot_id?: unknown;
  target_owner_id?: unknown;
  scope?: unknown;
  dry_run?: unknown;
};

type EffectiveGrant = { can_view: number; can_execute: number; can_edit: number };

const VALID_SCOPES: ReadonlySet<string> = new Set(["exact", "exact+owner-rows"]);

/** Parse a bare/compound bot reference into (botId, ownerId). */
async function resolveBotRef(
  rawBotId: unknown,
  rawOwnerId: unknown,
  resolveBotOwner: CloneRouterDeps["resolveBotOwner"],
): Promise<BotRef | { error: "INVALID_PARAMS" } | { error: "BOT_NOT_FOUND" }> {
  let botId = typeof rawBotId === "string" ? rawBotId.trim() : "";
  const explicitOwner = typeof rawOwnerId === "string" ? rawOwnerId.trim() : "";
  if (!botId || botId.includes("*")) return { error: "INVALID_PARAMS" };

  // Compound form: "bot_id:owner_staff_no" (BaaS Open API shape). Bot ids
  // themselves never contain ':' (yyyymmdd_xxxx / "default"), so a ':' always
  // marks an owner suffix — strip it regardless of where the owner comes from.
  let compoundOwner: string | null = null;
  const colonIndex = botId.indexOf(":");
  if (colonIndex > 0 && colonIndex < botId.length - 1) {
    const suffix = botId.slice(colonIndex + 1).trim();
    const prefix = botId.slice(0, colonIndex).trim();
    if (prefix && suffix) {
      botId = prefix;
      compoundOwner = suffix;
    }
  }

  if (explicitOwner) {
    return { botId, ownerId: explicitOwner, source: "param" };
  }

  if (compoundOwner) {
    return { botId, ownerId: compoundOwner, source: "compound" };
  }

  if (resolveBotOwner) {
    const ownerId = await resolveBotOwner(botId);
    if (!ownerId) return { error: "BOT_NOT_FOUND" };
    return { botId, ownerId, source: "directory" };
  }

  return { error: "INVALID_PARAMS" };
}

/** Merge rows per workflow with widest-rule-wins (reads span all envs). */
function mergeRowsByWorkflow(rows: BotWorkflowPermissionRow[]): Map<string, EffectiveGrant> {
  const merged = new Map<string, EffectiveGrant>();
  for (const row of rows) {
    const cur = merged.get(row.workflow_id);
    if (!cur) {
      merged.set(row.workflow_id, {
        can_view: row.can_view ? 1 : 0,
        can_execute: row.can_execute ? 1 : 0,
        can_edit: row.can_edit ? 1 : 0,
      });
    } else {
      cur.can_view = cur.can_view || row.can_view ? 1 : 0;
      cur.can_execute = cur.can_execute || row.can_execute ? 1 : 0;
      cur.can_edit = cur.can_edit || row.can_edit ? 1 : 0;
    }
  }
  return merged;
}

export function createBotWorkflowPermissionCloneRouter(deps: CloneRouterDeps): Router {
  const router = Router();

  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!deps.botPermRepo) {
      res.status(503).json({
        error: "Service Unavailable",
        code: "SERVICE_UNAVAILABLE",
        message: "Bot workflow permission repository not configured",
      });
      return;
    }

    const body = (req.body ?? {}) as CloneRequest;
    const scope = typeof body.scope === "string" && body.scope ? body.scope : "exact";
    if (!VALID_SCOPES.has(scope)) {
      res.status(400).json({
        error: "Bad Request",
        code: "INVALID_PARAMS",
        message: `scope must be one of: ${[...VALID_SCOPES].join(", ")}`,
      });
      return;
    }

    const source = await resolveBotRef(body.source_bot_id, body.source_owner_id, deps.resolveBotOwner);
    if ("error" in source) {
      res.status(source.error === "BOT_NOT_FOUND" ? 404 : 400).json({
        error: source.error === "BOT_NOT_FOUND" ? "Not Found" : "Bad Request",
        code: source.error === "BOT_NOT_FOUND" ? "BOT_NOT_FOUND" : "INVALID_PARAMS",
        message: source.error === "BOT_NOT_FOUND"
          ? `Cannot resolve owner for source bot: ${typeof body.source_bot_id === "string" ? body.source_bot_id : ""}`
          : "source_bot_id is required and its owner must be resolvable (explicit param, compound form, or directory)",
      });
      return;
    }

    const target = await resolveBotRef(body.target_bot_id, body.target_owner_id, deps.resolveBotOwner);
    if ("error" in target) {
      res.status(target.error === "BOT_NOT_FOUND" ? 404 : 400).json({
        error: target.error === "BOT_NOT_FOUND" ? "Not Found" : "Bad Request",
        code: target.error === "BOT_NOT_FOUND" ? "BOT_NOT_FOUND" : "INVALID_PARAMS",
        message: target.error === "BOT_NOT_FOUND"
          ? `Cannot resolve owner for target bot: ${typeof body.target_bot_id === "string" ? body.target_bot_id : ""}`
          : "target_bot_id is required and its owner must be resolvable (explicit param, compound form, or directory)",
      });
      return;
    }

    if (source.botId === target.botId && source.ownerId === target.ownerId) {
      res.status(409).json({
        error: "Conflict",
        code: "IDENTITY_CONFLICT",
        message: "source and target resolve to the same (bot_id, owner_id)",
      });
      return;
    }

    // Read source grants (reads span envs; merged widest-rule-wins).
    const sourceRows = await deps.botPermRepo.listRowsForBot(source.ownerId, source.botId);
    if (scope === "exact+owner-rows") {
      sourceRows.push(...(await deps.botPermRepo.listOwnerLevelRows(source.ownerId)));
    }
    const sourceGrants = mergeRowsByWorkflow(sourceRows);
    if (sourceGrants.size === 0) {
      res.status(404).json({
        error: "Not Found",
        code: "NO_SOURCE_GRANTS",
        message: "Source bot has no workflow permission rows to clone",
      });
      return;
    }

    // Read existing target grants for the union merge (never downgrade).
    const targetRows = await deps.botPermRepo.listRowsForBot(target.ownerId, target.botId);
    const targetGrants = mergeRowsByWorkflow(targetRows);

    const dryRun = body.dry_run === true;
    const granted: Array<{ workflow_id: string; can_view: number; can_execute: number; can_edit: number }> = [];
    const skipped: Array<{ workflow_id: string; reason: "already-covered" }> = [];

    for (const [workflowId, grant] of sourceGrants) {
      const existing = targetGrants.get(workflowId);
      const merged: EffectiveGrant = {
        can_view: (existing?.can_view ?? 0) | grant.can_view,
        can_execute: (existing?.can_execute ?? 0) | grant.can_execute,
        can_edit: (existing?.can_edit ?? 0) | grant.can_edit,
      };

      if (existing
        && merged.can_view === existing.can_view
        && merged.can_execute === existing.can_execute
        && merged.can_edit === existing.can_edit) {
        skipped.push({ workflow_id: workflowId, reason: "already-covered" });
        continue;
      }

      if (dryRun) {
        granted.push({ workflow_id: workflowId, ...merged });
        continue;
      }

      const row = await deps.botPermRepo.upsert({
        bot_id: target.botId,
        bot_owner_id: target.ownerId,
        workflow_id: workflowId,
        can_view: merged.can_view,
        can_execute: merged.can_execute,
        can_edit: merged.can_edit,
      });
      granted.push({
        workflow_id: workflowId,
        can_view: row.can_view ? 1 : 0,
        can_execute: row.can_execute ? 1 : 0,
        can_edit: row.can_edit ? 1 : 0,
      });
    }

    console.info(
      "[bot-workflow-permission-clone] scope=%s dry_run=%s source=%s:%s target=%s:%s granted=%d skipped=%d",
      scope, dryRun, source.botId, source.ownerId, target.botId, target.ownerId,
      granted.length, skipped.length,
    );

    res.json({
      success: true,
      dry_run: dryRun,
      granted: granted.length,
      rows: granted,
      skipped,
      source_effective_total: sourceGrants.size,
      source_resolved: { bot_id: source.botId, owner_id: source.ownerId, source: source.source },
      target_resolved: { bot_id: target.botId, owner_id: target.ownerId, source: target.source },
    });
  }));

  return router;
}