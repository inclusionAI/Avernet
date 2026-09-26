/**
 * Facades API routes — CRUD for facade command bindings.
 * GET    /              — list all DB facade bindings
 * POST   /              — create a new facade binding
 * PUT    /:command      — update an existing facade binding
 * DELETE /:command      — delete a facade binding
 */
import { Router, type Request, type Response } from "express";
import type { IFacadeBindingRepository } from "../../db/repositories/types.js";

const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/;

export function createFacadesRouter(
  facadeBindingRepo: IFacadeBindingRepository | null,
): Router {
  const router = Router();

  /** GET / — list all DB facade bindings */
  router.get("/", async (_req: Request, res: Response) => {
    if (!facadeBindingRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const rows = await facadeBindingRepo.listAll();
      res.json(rows.map((r) => ({
        command: r.command,
        workflowId: r.workflow_id,
        packId: r.pack_id,
        remark: r.remark,
      })));
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** POST / — create a new facade binding */
  router.post("/", async (req: Request, res: Response) => {
    if (!facadeBindingRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { command, workflowId, packId, remark } = req.body ?? {};
      if (!command || !workflowId) {
        res.status(400).json({ error: "Bad Request", message: "command and workflowId are required" });
        return;
      }
      if (!COMMAND_PATTERN.test(command)) {
        res.status(400).json({ error: "Bad Request", message: "command must be kebab-case or snake-case (lowercase letters, digits, hyphens, underscores)" });
        return;
      }
      const existing = await facadeBindingRepo.findByCommand(command);
      if (existing) {
        res.status(409).json({ error: "Conflict", message: `command "${command}" already bound to workflow "${existing.workflow_id}"` });
        return;
      }
      const row = await facadeBindingRepo.upsert({ command, workflow_id: workflowId, pack_id: packId, remark });
      res.status(201).json({
        command: row.command,
        workflowId: row.workflow_id,
        packId: row.pack_id,
        remark: row.remark,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (msg.includes("already bound") || msg.includes("conflict")) {
        res.status(409).json({ error: "Conflict", message: msg });
        return;
      }
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** PUT /:command — update an existing facade binding */
  router.put("/:command", async (req: Request, res: Response) => {
    if (!facadeBindingRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const { command } = req.params as Record<string, string>;
      const existing = await facadeBindingRepo.findByCommand(command);
      if (!existing) {
        res.status(404).json({ error: "Not Found", message: `facade binding "${command}" not found` });
        return;
      }
      const { workflowId, packId, remark } = req.body ?? {};
      if (workflowId && workflowId !== existing.workflow_id) {
        const conflictCheck = await facadeBindingRepo.findByCommand(command);
        if (!conflictCheck) {
          res.status(404).json({ error: "Not Found" });
          return;
        }
      }
      await facadeBindingRepo.upsert({
        command,
        workflow_id: workflowId ?? existing.workflow_id,
        pack_id: packId ?? existing.pack_id ?? undefined,
        remark: remark !== undefined ? remark : (existing.remark ?? undefined),
      });
      const updated = await facadeBindingRepo.findByCommand(command);
      res.json({
        command: updated!.command,
        workflowId: updated!.workflow_id,
        packId: updated!.pack_id,
        remark: updated!.remark,
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** DELETE /:command — delete a facade binding */
  router.delete("/:command", async (req: Request, res: Response) => {
    if (!facadeBindingRepo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const deleted = await facadeBindingRepo.deleteByCommand(req.params.command as string);
      if (!deleted) {
        res.status(404).json({ error: "Not Found", message: `facade binding "${req.params.command}" not found` });
        return;
      }
      res.status(204).end();
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}