/**
 * Loop-group node expansion — materializes loop body nodes into per-iteration
 * runtime nodes with rewritten IDs, dependsOn, and executor specs.
 *
 * The core rewriting logic (ID rewriting, dependsOn rewriting, executor spec
 * rewriting) lives in `materializer.ts` and is shared with dynamic-template
 * and llm-orchestrator materialization.
 */
import type { LoopRuntimeNodeMeta, WorkflowNode } from "./types.js";
import { materializeBody } from "./materializer.js";

export function loopRuntimeNodeId(loopId: string, iteration: number, bodyNodeId: string): string {
  return `${loopId}__iter${iteration}__${bodyNodeId}`;
}

export function materializeLoopIteration(args: {
  loopId: string;
  iteration: number;
  iterationVar: string;
  body: WorkflowNode[];
}): {
  runtimeNodes: WorkflowNode[];
  nodeIds: Record<string, string>;
  meta: Record<string, LoopRuntimeNodeMeta>;
} {
  // Use the shared materializer with a loop-specific ID function
  const { runtimeNodes, nodeIds } = materializeBody(
    args.body,
    (bodyNodeId) => loopRuntimeNodeId(args.loopId, args.iteration, bodyNodeId),
  );

  // Build the loop-specific runtime metadata
  const meta = Object.fromEntries(
    args.body.map((node) => [
      nodeIds[node.id],
      {
        loopId: args.loopId,
        iteration: args.iteration,
        bodyNodeId: node.id,
        iterationVar: args.iterationVar,
      },
    ]),
  );

  return { runtimeNodes, nodeIds, meta };
}

export function loopIterationNodeId(loopNodeId: string, bodyNodeId: string, iteration: number): string {
  return loopRuntimeNodeId(loopNodeId, iteration, bodyNodeId);
}

export function expandLoopBodyForIteration<T extends { id: string; dependsOn: string[] }>(
  loopNodeId: string,
  body: T[],
  iteration: number,
): T[] {
  const { runtimeNodes } = materializeBody(
    body,
    (bodyNodeId) => loopRuntimeNodeId(loopNodeId, iteration, bodyNodeId),
  );
  return runtimeNodes;
}