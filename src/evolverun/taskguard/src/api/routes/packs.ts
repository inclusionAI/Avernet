/**
 * Packs API routes — workflow pack listing and spec management.
 * GET  /                          — list available packs
 * GET  /:packId/workflows         — list workflows in a pack
 * GET  /:packId/workflows/:workflowId — get workflow spec
 * PUT  /:packId/workflows/:workflowId — save workflow spec (with optional facade binding)
 */
import { Router, type Request, type Response } from "express";
import { writeFileSync } from "node:fs";
import { stringify as stringifyYaml } from "yaml";
import { loadWorkflowPackCatalog } from "../../packs/resolver.js";
import { normalizeWorkflowSpec, validateWorkflowSemantics } from "../../validation/workflow.js";
import type { WorkflowSpec } from "../../types.js";
import type { IFacadeBindingRepository } from "../../db/repositories/types.js";

const COMMAND_PATTERN = /^[a-z0-9][a-z0-9_-]*[a-z0-9]$|^[a-z0-9]$/;

export function createPacksRouter(
  facadeBindingRepo?: IFacadeBindingRepository | null,
): Router {
  const router = Router();

  /** GET / — list available packs */
  router.get("/", async (_req: Request, res: Response) => {
    try {
      const catalog = loadWorkflowPackCatalog();
      const packs = catalog.packs.map((pack) => ({
        packId: pack.manifest.id,
        name: pack.manifest.title ?? pack.manifest.id,
        version: pack.manifest.version,
        workflowCount: pack.workflows.length,
      }));
      res.json(packs);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** GET /:packId/workflows — list workflows in a pack */
  router.get("/:packId/workflows", async (req: Request, res: Response) => {
    try {
      const catalog = loadWorkflowPackCatalog();
      const pack = catalog.packs.find((p) => p.manifest.id === req.params.packId);
      if (!pack) {
        res.status(404).json({ error: "Not Found", message: `Pack "${req.params.packId}" not found` });
        return;
      }
      const workflows = catalog.workflows
        .filter((w) => w.pack.id === req.params.packId)
        .map((w) => ({
          workflowId: w.id,
          title: w.spec.title ?? w.id,
          filename: w.absolutePath.split("/").pop() ?? "",
        }));
      res.json(workflows);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** GET /:packId/workflows/:workflowId — get workflow spec */
  router.get("/:packId/workflows/:workflowId", async (req: Request, res: Response) => {
    try {
      const catalog = loadWorkflowPackCatalog();
      const workflow = catalog.workflows.find(
        (w) => w.pack.id === req.params.packId && w.id === req.params.workflowId,
      );
      if (!workflow) {
        res.status(404).json({ error: "Not Found", message: `Workflow "${req.params.workflowId}" in pack "${req.params.packId}" not found` });
        return;
      }
      res.json(workflow.spec);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  /** PUT /:packId/workflows/:workflowId — save workflow spec */
  router.put("/:packId/workflows/:workflowId", async (req: Request, res: Response) => {
    try {
      const spec = req.body as WorkflowSpec;
      const facade = (req.body as Record<string, unknown>).facade as
        | { command?: string; remark?: string }
        | undefined;

      // Validate the spec
      if (!spec.id || !spec.title || !Array.isArray(spec.nodes)) {
        res.status(400).json({
          error: "Bad Request",
          message: "Invalid WorkflowSpec: missing required fields (id, title, nodes)",
        });
        return;
      }

      const normalized = normalizeWorkflowSpec(spec);
      validateWorkflowSemantics(normalized);

      // Find the workflow file path
      const catalog = loadWorkflowPackCatalog();
      const workflow = catalog.workflows.find(
        (w) => w.pack.id === req.params.packId && w.id === req.params.workflowId,
      );
      if (!workflow) {
        res.status(404).json({ error: "Not Found", message: `Workflow "${req.params.workflowId}" in pack "${req.params.packId}" not found` });
        return;
      }

      // Write YAML to disk
      const yamlContent = stringifyYaml(normalized, { lineWidth: 0 });
      writeFileSync(workflow.absolutePath, yamlContent, "utf-8");

      // Handle facade binding
      if (facadeBindingRepo && facade) {
        const command = facade.command?.trim();
        const remark = facade.remark?.trim();

        if (command) {
          if (!COMMAND_PATTERN.test(command)) {
            res.status(400).json({ error: "Bad Request", message: "facade.command must be kebab-case or snake-case (lowercase letters, digits, hyphens, underscores)" });
            return;
          }
          try {
            await facadeBindingRepo.upsert({
              command,
              workflow_id: spec.id,
              pack_id: req.params.packId as string,
              remark: remark || undefined,
            });
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            if (msg.includes("already bound")) {
              res.status(409).json({ error: "Conflict", message: msg });
              return;
            }
            throw err;
          }
        } else {
          // Command cleared — remove any existing binding for this workflow
          await facadeBindingRepo.deleteByWorkflowId(spec.id);
        }
      }

      res.json(normalized);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      if (msg.includes("Validation") || msg.includes("validation")) {
        res.status(400).json({ error: "Bad Request", message: msg });
        return;
      }
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}