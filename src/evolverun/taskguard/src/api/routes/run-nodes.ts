/**
 * Run nodes API route — node execution detail with lazy-load for large payloads.
 * GET /:flowId/nodes — list node executions, with ?full=true to skip truncation
 */
import { Router, type Request, type Response } from "express";
import type { INodeExecutionRepository } from "../../db/repositories/types.js";

export function createRunNodesRouter(repo: INodeExecutionRepository | null): Router {
  const router = Router();

  router.get("/:flowId/nodes", async (req: Request, res: Response) => {
    if (!repo) {
      res.status(503).json({ error: "Service Unavailable", message: "Database not configured" });
      return;
    }
    try {
      const flowId = String(req.params.flowId);
      const full = req.query.full === "true";
      const nodes = await repo.findByFlowId(flowId, { limit: 500 });

      if (!full) {
        // Truncate input_json/output_json to ~10KB for listing
        const TRUNCATE_LIMIT = 10 * 1024;
        const truncated = nodes.map((node) => ({
          ...node,
          input_json: truncateField(node.input_json, TRUNCATE_LIMIT),
          output_json: truncateField(node.output_json, TRUNCATE_LIMIT),
        }));
        res.json(truncated);
      } else {
        res.json(nodes);
      }
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}

function truncateField(value: string | null, maxBytes: number): string | null {
  if (!value) return null;
  const byteLength = Buffer.byteLength(value, "utf-8");
  if (byteLength <= maxBytes) return value;
  return value.substring(0, maxBytes) + `... [truncated, ${byteLength} bytes total]`;
}