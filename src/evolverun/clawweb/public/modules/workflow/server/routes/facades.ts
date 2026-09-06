/**
 * Facade Bindings API routes — CRUD for slash-command-to-workflow bindings.
 */
import { Router, type Request, type Response } from "express";
import type { FacadeBindingRepository } from "../repositories/facade-binding-repository.js";
import type { BotWorkflowPermissionRepository } from "@avernet/clawweb-shared/server/repositories/bot-workflow-permission-repository";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";
import { ApiCache } from "../cache.js";
import { requireWorkflowAccess } from "@avernet/clawweb-shared/server/services/workflow-access";

const facadesCache = new ApiCache<unknown[]>({
  ttlMs: 5 * 60 * 1000,
  maxSize: 50,
  keyPrefix: "facades",
});

export function createFacadesRouter(
  facadeRepo: FacadeBindingRepository | null,
  botPermRepo: BotWorkflowPermissionRepository | null = null,
): Router {
  const router = Router();

  /** GET /list — paginated list of facade bindings */
  router.get("/list", asyncHandler(async (req: Request, res: Response) => {
    if (!facadeRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    const page = Math.max(1, Number(req.query.page) || 1);
    const pageSize = Math.min(100, Math.max(10, Number(req.query.pageSize) || 10));
    const search = typeof req.query.search === "string" && req.query.search.trim() ? req.query.search.trim() : undefined;

    try {
      const { rows, total } = await facadeRepo.findPage({ page, pageSize, search });
      const data = rows.map(rowToApi);
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

  /** GET / — list all facade bindings */
  router.get("/", asyncHandler(async (_req: Request, res: Response) => {
    if (!facadeRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }

    res.set("Cache-Control", "private, max-age=60, must-revalidate");

    const cached = facadesCache.get("all");
    if (cached) {
      res.json(cached);
      return;
    }

    try {
      const rows = await facadeRepo.listAll();
      const data = rows.map(rowToApi);
      facadesCache.set("all", data);
      res.json(data);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** POST / — create or update a facade binding (upsert) */
  router.post("/", asyncHandler(async (req: Request, res: Response) => {
    if (!facadeRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { command, workflowId, packId, remark } = req.body as {
        command?: string;
        workflowId?: string;
        packId?: string;
        remark?: string;
      };

      if (!command || !workflowId) {
        res.status(400).json({ error: "Bad Request", message: "Missing required fields: command, workflowId" });
        return;
      }
      if (!await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;

      const row = await facadeRepo.upsert({
        command,
        workflowId,
        packId: packId ?? null,
        remark: remark ?? null,
      });
      facadesCache.invalidate();
      res.status(201).json(rowToApi(row));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** PUT /:command — update a facade binding */
  router.put("/:command", asyncHandler(async (req: Request, res: Response) => {
    if (!facadeRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const command = String(req.params.command);
      const existing = await facadeRepo.findByCommand(command);
      if (!existing) {
        res.status(404).json({ error: "Not Found", message: `Facade binding "/${command}" not found` });
        return;
      }

      const { workflowId, packId, remark } = req.body as {
        workflowId?: string;
        packId?: string;
        remark?: string;
      };
      if (!await requireWorkflowAccess(req, res, botPermRepo, existing.workflow_id, "edit")) return;
      if (workflowId && workflowId !== existing.workflow_id
        && !await requireWorkflowAccess(req, res, botPermRepo, workflowId, "edit")) return;

      const row = await facadeRepo.upsert({
        command,
        workflowId: workflowId ?? existing.workflow_id,
        packId: packId ?? existing.pack_id,
        remark: remark ?? existing.remark,
      });
      facadesCache.invalidate();
      res.json(rowToApi(row));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  /** DELETE /:command — delete a facade binding */
  router.delete("/:command", asyncHandler(async (req: Request, res: Response) => {
    if (!facadeRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const command = String(req.params.command);
      const existing = await facadeRepo.findByCommand(command);
      if (!existing) {
        res.status(404).json({ error: "Not Found", message: `Facade binding "/${command}" not found` });
        return;
      }
      if (!await requireWorkflowAccess(req, res, botPermRepo, existing.workflow_id, "edit")) return;
      const deleted = await facadeRepo.deleteByCommand(command);
      if (!deleted) {
        res.status(404).json({ error: "Not Found", message: `Facade binding "/${req.params.command}" not found` });
        return;
      }
      facadesCache.invalidate();
      res.json({ affected: true });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  }));

  return router;
}

/** Convert DB row (snake_case) to API response (camelCase) */
function rowToApi(row: {
  id: number;
  command: string;
  workflow_id: string;
  pack_id: string | null;
  remark: string | null;
  gmt_create: number;
  gmt_modified: number;
}) {
  return {
    id: row.id,
    command: row.command,
    workflowId: row.workflow_id,
    packId: row.pack_id,
    remark: row.remark,
    gmtCreate: row.gmt_create,
    gmtModified: row.gmt_modified,
  };
}
