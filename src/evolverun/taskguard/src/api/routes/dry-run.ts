/**
 * Dry-run API route.
 * POST / — execute a dry run of a workflow spec with mock config
 */
import { Router, type Request, type Response } from "express";
import type { WorkflowSpec, MockConfig } from "../../types.js";
import { executeDryRun } from "../../runner/dry-run.js";
import { MockRegistry } from "../../runner/mock-registry.js";

export type DryRunRequestBody = {
  spec: WorkflowSpec;
  params?: Record<string, string>;
  mocks?: Record<string, MockConfig>;
};

export type DryRunNodeResult = {
  nodeId: string;
  status: string;
  output?: unknown;
  error?: string;
  durationMs?: number;
};

export type DryRunResponse = {
  nodeStates: Record<string, DryRunNodeResult>;
  nodeReports?: Array<{
    nodeId: string;
    nodeStatus: string;
    mockSource: string;
  }>;
};

export function createDryRunRouter(): Router {
  const router = Router();

  router.post("/", async (req: Request, res: Response) => {
    try {
      const body = req.body as DryRunRequestBody;
      if (!body.spec || !body.spec.id || !Array.isArray(body.spec.nodes)) {
        res.status(400).json({ error: "Bad Request", message: "Invalid request: spec with id and nodes is required" });
        return;
      }

      const registry = new MockRegistry();
      if (body.mocks) {
        for (const [nodeId, config] of Object.entries(body.mocks)) {
          registry.register(nodeId, config, "override");
        }
      }

      const result = await executeDryRun(body.spec, body.params ?? {}, registry);

      const nodeStates: Record<string, DryRunNodeResult> = {};
      for (const [nodeId, state] of Object.entries(result.flowState.nodeStates)) {
        nodeStates[nodeId] = {
          nodeId,
          status: state.status,
          output: state.result,
          error: state.error ?? undefined,
          durationMs: state.completedAt && state.startedAt
            ? state.completedAt - state.startedAt
            : undefined,
        };
      }

      const response: DryRunResponse = {
        nodeStates,
        nodeReports: result.nodeReports.map((r) => ({
          nodeId: r.nodeId,
          nodeStatus: r.nodeStatus,
          mockSource: r.mockSource,
        })),
      };

      res.json(response);
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ error: "Internal Server Error", message: msg });
    }
  });

  return router;
}