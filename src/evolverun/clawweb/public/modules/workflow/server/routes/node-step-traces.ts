/**
 * Node Step Traces API routes — reads from node_step_traces table.
 *
 * Endpoints:
 *   GET /api/runs/:flowId/nodes/:nodeId/steps       — step list for a node
 *   GET /api/runs/:flowId/nodes/:nodeId/steps/:seq   — single step detail
 *   GET /api/runs/:flowId/steps-summary              — summary for all nodes in a run
 */
import { Router, type Request, type Response } from "express";
import { NodeStepTraceRepository } from "@avernet/workflow/server/repositories/node-step-traces-repository";
import type { IDatabase } from "@avernet/clawweb-shared/server/db";
import { asyncHandler } from "@avernet/clawweb-shared/server/middleware/async-handler";

export function createNodeStepTracesRouter(db: IDatabase): Router {
  const router = Router();
  const repo = new NodeStepTraceRepository(db);

  // GET /api/runs/:flowId/nodes/:nodeId/steps
  router.get("/:flowId/nodes/:nodeId/steps", asyncHandler(async (req: Request, res: Response) => {
    try {
      const flowId = String(req.params.flowId);
      const nodeId = String(req.params.nodeId);
      const attempt = Number(req.query.attempt) || 1;
      const stepType = req.query.stepType as string | undefined;
      const limit = Number(req.query.limit) || 100;
      const offset = Number(req.query.offset) || 0;

      const steps = await repo.findByFlowNode(flowId, nodeId, attempt, {
        stepType,
        limit,
        offset,
      });

      if (steps.length === 0) {
        res.json({
          success: true,
          data: {
            flowId,
            nodeId,
            attempt,
            totalSteps: 0,
            toolCallCount: 0,
            toolErrorCount: 0,
            steps: [],
          },
        });
        return;
      }

      const toolCallCount = steps.filter((s) => s.step_type === "tool_call").length;
      const toolErrorCount = steps.filter((s) => s.is_error === 1).length;

      res.json({
        success: true,
        data: {
          flowId,
          nodeId,
          attempt,
          skillName: steps[0]?.skill_name ?? null,
          totalSteps: steps.length,
          toolCallCount,
          toolErrorCount,
          steps: steps.map((s) => ({
            stepSeq: s.step_seq,
            stepType: s.step_type,
            toolName: s.tool_name,
            toolUseId: s.tool_use_id,
            toolInputJson: s.tool_input_json,
            toolOutputText: s.tool_output_text,
            isError: s.is_error === 1,
            textContent: s.text_content,
            sessionKey: s.session_key ?? null,
          })),
        },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: msg });
    }
  }));

  // GET /api/runs/:flowId/nodes/:nodeId/steps/:seq
  router.get("/:flowId/nodes/:nodeId/steps/:seq", asyncHandler(async (req: Request, res: Response) => {
    try {
      const flowId = String(req.params.flowId);
      const nodeId = String(req.params.nodeId);
      const seq = String(req.params.seq);
      const attempt = Number(req.query.attempt) || 1;

      const step = await repo.findBySeq(flowId, nodeId, attempt, Number(seq));

      if (!step) {
        res.status(404).json({
          success: false,
          error: `Step ${seq} not found for node ${nodeId} (attempt ${attempt})`,
        });
        return;
      }

      res.json({
        success: true,
        data: {
          stepSeq: step.step_seq,
          stepType: step.step_type,
          toolName: step.tool_name,
          toolUseId: step.tool_use_id,
          toolInputJson: step.tool_input_json,
          toolOutputText: step.tool_output_text,
          isError: step.is_error === 1,
          textContent: step.text_content,
          sessionKey: step.session_key ?? null,
        },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: msg });
    }
  }));

  // GET /api/runs/:flowId/steps-summary
  router.get("/:flowId/steps-summary", asyncHandler(async (req: Request, res: Response) => {
    try {
      const flowId = String(req.params.flowId);

      const summaries = await repo.findSummaryByFlowId(flowId);

      res.json({
        success: true,
        data: {
          flowId,
          nodes: summaries.map((s) => ({
            nodeId: s.node_id,
            attempt: s.attempt,
            skillName: s.skill_name,
            toolCallCount: s.tool_call_count,
            toolErrorCount: s.tool_error_count,
            totalSteps: s.total_steps,
          })),
        },
      });
    } catch (error) {
      const msg = error instanceof Error ? error.message : String(error);
      res.status(500).json({ success: false, error: msg });
    }
  }));

  return router;
}
