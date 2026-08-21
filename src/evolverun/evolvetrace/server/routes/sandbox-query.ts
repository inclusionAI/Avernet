/**
 * Sandbox query routes — queries ARCA and BaaS Meta tables for sandbox_id
 * by bot_id + entity_id.
 *
 * GET /api/sandbox-query?bot_id=xxx&entity_id=123
 */
import { Router, type Request, type Response } from "express";
import { asyncHandler } from "../middleware/async-handler.js";
import type { SandboxQueryService, SandboxQueryResult } from "../services/sandbox-query-service.js";

export interface SandboxQueryRouterDeps {
  sandboxQueryService: SandboxQueryService | null;
}

export function createSandboxQueryRouter(deps: SandboxQueryRouterDeps): Router {
  const router = Router();

  /**
   * GET /api/sandbox-query
   * Query params:
   *   bot_id    — required, the bot identifier (e.g., "20260526_0ugmubp1")
   *   entity_id — required, the entity ID (e.g., 168944)
   *
   * Returns: { success, data: { arca: string[], baas: string[] } }
   */
  router.get(
    "/",
    asyncHandler(async (req: Request, res: Response) => {
      if (!deps.sandboxQueryService) {
        res.status(503).json({ success: false, error: "Sandbox query service unavailable" });
        return;
      }

      const botId = req.query.bot_id as string | undefined;
      const entityIdRaw = req.query.entity_id as string | undefined;

      if (!botId || !entityIdRaw) {
        res.status(400).json({ success: false, error: "Missing required query params: bot_id, entity_id" });
        return;
      }

      const entityId = Number(entityIdRaw);
      if (!Number.isInteger(entityId) || entityId <= 0) {
        res.status(400).json({ success: false, error: "entity_id must be a positive integer" });
        return;
      }

      // Validate bot_id format: alphanumeric, underscores, hyphens (prevent injection)
      if (!/^[a-zA-Z0-9_-]{1,256}$/.test(botId)) {
        res.status(400).json({ success: false, error: "Invalid bot_id format" });
        return;
      }

      const result: SandboxQueryResult = await deps.sandboxQueryService.query(botId, entityId);

      res.json({
        success: true,
        data: {
          arca: result.arca,
          baas: result.baas,
        },
      });
    }),
  );

  return router;
}