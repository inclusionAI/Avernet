import type {
  WorkflowNode,
  ExecutorResult,
  SubworkflowExecutor,
  FlowState,
  SubworkflowFlowMeta,
  WorkflowSpec,
} from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate } from "../runner.js";
import { resolveWorkflowOutputs } from "../workflow-outputs.js";
import { resolveWorkflow, resolveWorkflowByIdAndPackId, resolveWorkflowByIdFromPacks } from "../packs/resolver.js";
import type { ResolvedWorkflow } from "../packs/types.js";
import type { IWorkflowSpecRepository } from "../db/repositories/types.js";

export type SubworkflowDeps = {
  resolvedWorkflows?: ResolvedWorkflow[];
  /**
   * Optional DB/API repository for resolving child workflows registered in ClawWeb.
   * Subworkflow execution uses DB/API-first resolution when packId is not explicitly
   * pinned, then falls back to the local pack catalog.
   */
  workflowSpecRepo?: IWorkflowSpecRepository;
  /** When true, skip DB/API lookup and resolve only from local pack YAML. */
  debug?: boolean;
  executeChildWorkflow: (params: {
    childWorkflow: WorkflowSpec;
    childParams: Record<string, string>;
    parentFlowId: string;
    parentNodeId: string;
    depth: number;
  }) => Promise<SubworkflowCompletionResult>;
};

export type SubworkflowCompletionResult = {
  status: "succeeded" | "failed";
  workflowData: Record<string, unknown>;
  outputs?: Record<string, unknown>;
  error?: string;
};

function isSubworkflowExecutor(executor: WorkflowNode["executor"]): executor is SubworkflowExecutor {
  return executor.type === "subworkflow";
}

function resolveChildParams(
  configParams: Record<string, string> | undefined,
  templateCtx: TemplateContext,
  parentParams: Record<string, string>,
): Record<string, string> {
  if (!configParams || Object.keys(configParams).length === 0) {
    return { ...parentParams };
  }
  const resolved: Record<string, string> = {};
  for (const [key, value] of Object.entries(configParams)) {
    resolved[key] = resolveTemplate(value, templateCtx);
  }
  return resolved;
}

function resolveChildOutputs(
  childWorkflow: WorkflowSpec,
  workflowData: Record<string, unknown>,
): Record<string, unknown> {
  if (!childWorkflow.outputs || Object.keys(childWorkflow.outputs).length === 0) {
    return { ...workflowData };
  }
  const { values } = resolveWorkflowOutputs(childWorkflow.outputs, {
    params: (workflowData.params as Record<string, string>) ?? {},
    input: {},
    businessStatus: (workflowData.businessStatus as string) ?? "COMPLETED",
    currentPhase: (workflowData.currentPhase as string) ?? "",
    workflowData,
    actionOutputs: (workflowData.actionOutputs as Record<string, Record<string, unknown>>) ?? {},
    flowHooks: {},
    nodeOutput: (workflowData.nodeOutput as Record<string, Record<string, unknown>>) ?? {},
  });
  return values;
}

export async function executeSubworkflow(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  flowState: FlowState,
  flowId: string,
  deps: SubworkflowDeps,
): Promise<ExecutorResult> {
  if (!isSubworkflowExecutor(node.executor)) {
    return { status: "failed", error: "not a subworkflow node" };
  }

  const config = node.executor as SubworkflowExecutor;
  const onFailure = config.onFailure ?? "fail";
  const currentPackId = flowState.workflowPin?.packId;

  const packWorkflows = deps.resolvedWorkflows ?? [];

  let childWorkflow: WorkflowSpec | undefined;
  try {
    if (config.packId) {
      // Preserve explicit pack pinning semantics: a subworkflow that asks for a
      // specific pack should resolve from that pack, not from an unrelated DB row
      // with the same workflow id.
      childWorkflow = resolveWorkflowByIdAndPackId(config.workflowId, packWorkflows, config.packId)?.spec;
    } else {
      // DB/API-first. Use an empty pack fallback here so a DB miss does not
      // accidentally bypass same-pack semantics or throw on duplicate local pack
      // workflow ids; local fallback is handled explicitly below.
      if (deps.workflowSpecRepo && !deps.debug) {
        childWorkflow = (await resolveWorkflow(config.workflowId, deps.workflowSpecRepo, [], deps.debug))?.spec;
      }
      if (!childWorkflow) {
        childWorkflow = currentPackId
          ? resolveWorkflowByIdAndPackId(config.workflowId, packWorkflows, currentPackId)?.spec
          : resolveWorkflowByIdFromPacks(config.workflowId, packWorkflows)?.spec;
      }
    }
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    return {
      status: "failed",
      error: `subworkflow node ${node.id}: failed to resolve workflow "${config.workflowId}": ${message}`,
    };
  }

  if (!childWorkflow) {
    const hasDbResolver = !!deps.workflowSpecRepo && !deps.debug && !config.packId;
    const hasPackCatalog = packWorkflows.length > 0;
    if (!hasDbResolver && !hasPackCatalog) {
      return {
        status: "failed",
        error: `subworkflow node ${node.id}: no workflow catalog or DB/API resolver available for resolution`,
      };
    }
    return {
      status: "failed",
      error: `subworkflow node ${node.id}: workflow "${config.packId ? `${config.workflowId} (pack: ${config.packId})` : config.workflowId}" not found`,
    };
  }

  const childWorkflowSpec = childWorkflow;
  const childParams = resolveChildParams(config.params, templateCtx, flowState.params);
  const parentMeta = flowState.subworkflowMeta;
  const childDepth = parentMeta ? parentMeta.depth + 1 : 1;

  if (childDepth > 3) {
    return {
      status: "failed",
      error: `subworkflow node ${node.id}: maximum nesting depth exceeded (depth=${childDepth}, max=3). Chain: this prevents infinite subworkflow recursion.`,
    };
  }

  const runOnce = async (): Promise<ExecutorResult> => {
    const completion = await deps.executeChildWorkflow({
      childWorkflow: childWorkflowSpec,
      childParams,
      parentFlowId: flowId,
      parentNodeId: node.id,
      depth: childDepth,
    });

    if (completion.status === "succeeded") {
      const result = completion.outputs ?? resolveChildOutputs(childWorkflowSpec, completion.workflowData);
      return { status: "succeeded", result };
    }

    return {
      status: "failed",
      error: completion.error
        ? `subworkflow failed: ${completion.error}`
        : `subworkflow "${childWorkflowSpec.id}" failed with unknown error`,
    };
  };

  const handleResult = async (result: ExecutorResult): Promise<ExecutorResult> => {
    if (result.status !== "failed") {
      return result;
    }

    if (onFailure === "skip") {
      return { status: "succeeded", result: {} };
    }

    if (onFailure === "retry") {
      const maxAttempts = node.retry?.maxAttempts ?? 1;
      const backoffMs = node.retry?.backoffMs ?? 0;

      for (let attempt = 2; attempt <= maxAttempts; attempt++) {
        if (backoffMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, backoffMs));
        }

        const retryResult = await runOnce();
        if (retryResult.status !== "failed") {
          return retryResult;
        }
      }

      // All retries exhausted — propagate failure (or skip if combined with skip)
      return result;
    }

    // onFailure === "fail" (default)
    return result;
  };

  return runOnce().then(handleResult);
}

export function buildChildFlowState(
  childWorkflow: WorkflowSpec,
  childParams: Record<string, string>,
  parentFlowState: FlowState,
  parentNodeId: string,
  parentFlowId: string,
  depth: number,
): FlowState {
  const childMeta: SubworkflowFlowMeta = {
    parentFlowId,
    parentNodeId,
    depth,
  };

  const initialState: FlowState = {
    workflowId: childWorkflow.id,
    workflowVersion: childWorkflow.version,
    params: childParams,
    executionMode: parentFlowState.executionMode,
    bcsGroupId: parentFlowState.bcsGroupId,
    businessStatus: "INIT",
    currentPhase: childWorkflow.nodes[0]?.phase ?? "P1",
    activeNodes: [],
    nodeStates: {},
    workflowData: {},
    actionOutputs: {},
    flowHooks: {},
    auditLog: [],
    subworkflowMeta: childMeta,
  };

  for (const node of childWorkflow.nodes) {
    initialState.nodeStates[node.id] = {
      status: "pending",
      phase: node.phase,
      executor: node.executor.type,
    };
  }

  if (parentFlowState.commandSurface) {
    initialState.commandSurface = parentFlowState.commandSurface;
  }

  if (parentFlowState.input) {
    initialState.input = {
      ...parentFlowState.input,
      // A subworkflow may receive params mapped from the parent (for example
      // { domainId: "{{input.params.train_bench_domain_id}}" }). Keep the
      // inherited input metadata/files, but expose the resolved child params
      // through input.params as well so child templates can consistently use
      // either {{params.foo}} or {{input.params.foo}}. Preserve parent input
      // params for backwards compatibility, with child params taking priority.
      params: {
        ...parentFlowState.input.params,
        ...childParams,
      },
      digest: parentFlowState.input.digest,
      digestShort: parentFlowState.input.digestShort,
    };
  }

  if (parentFlowState.workflowPin) {
    initialState.workflowPin = parentFlowState.workflowPin;
  }

  return initialState;
}