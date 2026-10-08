/**
 * Materializer — shared node materialization logic for dynamic workflow
 * node expansion (loop-group, dynamic-template, llm-orchestrator).
 *
 * Extracted from loop-group.ts so all materialization strategies reuse the
 * same ID rewriting, dependsOn rewriting, and executor spec rewriting logic.
 */
import type {
  ApprovalExecutor,
  CollaborationExecutor,
  HumanGateActions,
  HumanWaitExecutor,
  HumanWaitSpec,
  WorkflowNode,
} from "./types.js";

// ── Node ID Rewriting ──

/**
 * Build a mapping from original node IDs to runtime materialized IDs.
 * @param body  Template body nodes (or loop body nodes)
 * @param idFn  Function that produces a runtime ID given the original node ID
 */
export function buildNodeIdMap(
  body: ReadonlyArray<{ id: string }>,
  idFn: (originalId: string) => string,
): Record<string, string> {
  return Object.fromEntries(body.map((node) => [node.id, idFn(node.id)]));
}

// ── Executor Spec Rewriting ──

function isHumanExecutor(executor: unknown): executor is HumanWaitExecutor {
  return Boolean(
    executor
      && typeof executor === "object"
      && "type" in executor
      && executor.type === "human",
  );
}

function isCollaborationExecutor(executor: unknown): executor is CollaborationExecutor {
  return Boolean(
    executor
      && typeof executor === "object"
      && "type" in executor
      && executor.type === "collaboration",
  );
}

function isApprovalExecutor(executor: unknown): executor is ApprovalExecutor {
  return Boolean(
    executor
      && typeof executor === "object"
      && "type" in executor
      && executor.type === "approval",
  );
}

function rewriteHumanGateActions(
  actions: HumanGateActions | undefined,
  nodeIds: Record<string, string>,
): HumanGateActions | undefined {
  const revise = actions?.revise;
  const runtimeTarget = revise ? nodeIds[revise.target] : undefined;
  if (!actions || !revise || !runtimeTarget) return actions;
  return {
    ...actions,
    revise: {
      ...revise,
      target: runtimeTarget,
    },
  };
}

function rewriteHumanWaitExecutor(
  executor: HumanWaitExecutor,
  nodeIds: Record<string, string>,
): HumanWaitExecutor {
  const actions = rewriteHumanGateActions(executor.actions, nodeIds);
  return actions === executor.actions ? executor : { ...executor, actions };
}

function rewriteHumanWaitSpec(
  wait: HumanWaitSpec | undefined,
  nodeIds: Record<string, string>,
): HumanWaitSpec | undefined {
  if (!wait) return wait;
  const actions = rewriteHumanGateActions(wait.actions, nodeIds);
  return actions === wait.actions ? wait : { ...wait, actions };
}

function rewriteCollaborationExecutor(
  executor: CollaborationExecutor,
  nodeIds: Record<string, string>,
): CollaborationExecutor {
  const onFeedback = executor.onFeedback;
  const runtimeTarget = onFeedback ? nodeIds[onFeedback.target] : undefined;
  if (!onFeedback || !runtimeTarget) return executor;
  return {
    ...executor,
    onFeedback: {
      ...onFeedback,
      target: runtimeTarget,
    },
  };
}

function rewriteApprovalExecutor(
  executor: ApprovalExecutor,
  nodeIds: Record<string, string>,
): ApprovalExecutor {
  const onRevise = executor.onRevise;
  const runtimeTarget = onRevise ? nodeIds[onRevise.target] : undefined;
  if (!onRevise || !runtimeTarget) return executor;
  return {
    ...executor,
    onRevise: {
      ...onRevise,
      target: runtimeTarget,
    },
  };
}

// ── Core Node Rewriting ──

/**
 * Rewrite a single node: replace its `id` and `dependsOn` using the
 * provided nodeIds map, and rewrite executor specs and onResult wait
 * specs that contain internal node references.
 */
export function rewriteNode<T extends { id: string; dependsOn: string[] }>(
  node: T,
  nodeIds: Record<string, string>,
): T {
  let runtimeNode = {
    ...node,
    id: nodeIds[node.id],
    dependsOn: node.dependsOn.map((dep) => nodeIds[dep] ?? dep),
  };

  // Rewrite executor specs that reference other node IDs
  const executor = (node as { executor?: unknown }).executor;
  if (isHumanExecutor(executor)) {
    const rewrittenExecutor = rewriteHumanWaitExecutor(executor, nodeIds);
    if (rewrittenExecutor !== executor) {
      runtimeNode = { ...runtimeNode, executor: rewrittenExecutor };
    }
  } else if (isCollaborationExecutor(executor)) {
    const rewrittenExecutor = rewriteCollaborationExecutor(executor, nodeIds);
    if (rewrittenExecutor !== executor) {
      runtimeNode = { ...runtimeNode, executor: rewrittenExecutor };
    }
  } else if (isApprovalExecutor(executor)) {
    const rewrittenExecutor = rewriteApprovalExecutor(executor, nodeIds);
    if (rewrittenExecutor !== executor) {
      runtimeNode = { ...runtimeNode, executor: rewrittenExecutor };
    }
  }

  // Rewrite onResult wait specs
  const onResult = (node as { onResult?: WorkflowNode["onResult"] }).onResult;
  const thenWait = rewriteHumanWaitSpec(onResult?.then?.wait, nodeIds);
  const elseWait = rewriteHumanWaitSpec(onResult?.else?.wait, nodeIds);
  if (thenWait !== onResult?.then?.wait || elseWait !== onResult?.else?.wait) {
    runtimeNode = {
      ...runtimeNode,
      onResult: {
        ...onResult,
        ...(onResult?.then ? { then: { ...onResult.then, wait: thenWait } } : {}),
        ...(onResult?.else ? { else: { ...onResult.else, wait: elseWait } } : {}),
      },
    };
  }

  return runtimeNode as T;
}

// ── Materialization ──

/** Result of materializing a body of nodes. */
export type MaterializeResult<T extends { id: string; dependsOn: string[] } = WorkflowNode> = {
  /** The materialized runtime nodes with rewritten IDs, dependsOn, and executor specs. */
  runtimeNodes: T[];
  /** Map from original node ID → materialized runtime node ID. */
  nodeIds: Record<string, string>;
};

/**
 * Materialize a body of nodes using the given ID generation function.
 * This is the shared entry point for all materialization strategies
 * (loop-group, dynamic-template, llm-orchestrator).
 *
 * @param body    Template/loop body nodes
 * @param idFn    Function producing a runtime ID given the original node ID
 */
export function materializeBody<T extends { id: string; dependsOn: string[] }>(
  body: ReadonlyArray<T>,
  idFn: (originalId: string) => string,
): MaterializeResult<T> {
  const nodeIds = buildNodeIdMap(body, idFn);
  return {
    runtimeNodes: body.map((node) => rewriteNode(node, nodeIds)),
    nodeIds,
  };
}