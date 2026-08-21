import type {
  WorkflowNode,
  WorkflowSpec,
  ExecutorResult,
  FlowState,
  NodeState,
  MockConfig,
  MockSource,
  NodeExecutionReport,
} from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate, buildTemplateContext, findSkippableNodesFixedPoint, getReadyNodes, evaluateOnResult, computePhaseAndStatus, isWorkflowComplete } from "../runner.js";
import { MockRegistry } from "./mock-registry.js";

// ── Default Mock Behaviors (D6) ──

export function getDefaultMock(node: WorkflowNode): MockConfig {
  const id = node.id;
  const type = node.executor.type;

  switch (type) {
    case "embedded-agent":
      return {
        output: { text: `[dry-run] embedded-agent output for ${id}` },
      };
    case "subagent":
      return {
        output: { text: `[dry-run] subagent output for ${id}` },
      };
    case "human":
      return { autoConfirm: true };
    case "action":
      return { output: { success: true, dryRun: true } };
    case "cli-script": {
      const executor = node.executor as { type: "cli-script"; command: string; timeoutMs?: number };
      return {
        output: { exitCode: 0, stdout: `[dry-run] cli-script output for ${id}`, stderr: "" },
      };
    }
    case "mcp-call":
      return { output: { raw: `[dry-run] mcp-call output for ${id}` } };
    case "bcs-route":
      return { output: { response: `[dry-run] bcs-route response for ${id}` } };
    case "approval":
      return { output: { approved: true, action: "approve", reviewerId: "dry-run", reviewerName: "Dry Run Reviewer", reviewTime: new Date().toISOString(), opinion: "自动审批（dry-run）" } };
    case "collaboration":
      return { output: { response: `[dry-run] collaboration response for ${id}` } };
    case "done":
      return { output: {} };
    case "loop-group":
      return {};
    case "subworkflow":
      return { output: { text: `[dry-run] subworkflow output for ${id}` } };
    default:
      return { output: {} };
  }
}

// ── DryRunExecutor ──

export type DryRunExecutorOptions = {
  registry: MockRegistry;
  workflow: WorkflowSpec;
};

export function createDryRunDispatch(
  options: DryRunExecutorOptions,
): (node: WorkflowNode, templateCtx: TemplateContext, flowState: FlowState, flowId: string) => Promise<ExecutorResult> {
  const { registry, workflow } = options;

  return async (node, templateCtx, _flowState, _flowId) => {
    const resolved = registry.resolve(node.id);
    const mock = resolved.config ?? getDefaultMock(node);
    const source = resolved.config ? resolved.source : "default";

    // Approval nodes are handled by getDefaultMock above (returns approved: true)

    // Handle timeout mock
    if (mock.timeout) {
      const timeoutSeconds = (node.executor as { timeoutSeconds?: number }).timeoutSeconds
        ?? (node.executor as { timeout?: number }).timeout
        ?? 30;
      return {
        status: "failed",
        error: `[dry-run] timeout after ${timeoutSeconds}s`,
      };
    }

    // Handle error mock
    if (mock.error) {
      return { status: "failed", error: mock.error };
    }

    // Handle delay
    if (mock.delay && mock.delay > 0) {
      await new Promise((resolve) => setTimeout(resolve, mock.delay));
    }

    // Handle output mock (resolve template expressions)
    let output: Record<string, unknown>;
    if (mock.output) {
      output = resolveMockOutput(mock.output, templateCtx);
    } else {
      // Default mock for specific executor types
      output = getDefaultMock(node).output ?? {};
    }

    // Special handling for human executor
    if (node.executor.type === "human") {
      if (mock.autoConfirm) {
        return {
          status: "succeeded",
          result: { confirmed: true, autoConfirmed: true, ...output },
        };
      }
      if (mock.output) {
        return { status: "succeeded", result: output };
      }
      return {
        status: "succeeded",
        result: { confirmed: true, autoConfirmed: true },
      };
    }

    // Special handling for cli-script with non-zero exit code
    if (node.executor.type === "cli-script" && output.exitCode !== undefined && output.exitCode !== 0) {
      return {
        status: "failed",
        error: `cli-script exited with code ${output.exitCode}: ${output.stderr ?? ""}`,
      };
    }

    return { status: "succeeded", result: output };
  };
}

function resolveMockOutput(
  output: Record<string, unknown>,
  templateCtx: TemplateContext,
): Record<string, unknown> {
  const resolved: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(output)) {
    if (typeof value === "string") {
      resolved[key] = resolveTemplate(value, templateCtx);
    } else if (value != null && typeof value === "object" && !Array.isArray(value)) {
      resolved[key] = resolveMockOutput(value as Record<string, unknown>, templateCtx);
    } else {
      resolved[key] = value;
    }
  }
  return resolved;
}

// ── In-Memory FlowState ──

export function createInMemoryFlowState(
  workflow: WorkflowSpec,
  params: Record<string, string>,
): FlowState {
  const nodeStates: Record<string, NodeState> = {};
  for (const node of workflow.nodes) {
    nodeStates[node.id] = {
      status: "pending",
      phase: node.phase,
      executor: node.executor.type,
    };
  }

  return {
    workflowId: workflow.id,
    workflowVersion: workflow.version,
    params,
    executionMode: "private",
    businessStatus: "pending",
    currentPhase: workflow.nodes[0]?.phase ?? "",
    activeNodes: [],
    nodeStates,
    workflowData: {},
    actionOutputs: {},
    flowHooks: {},
    auditLog: [],
  };
}

// ── No-op ControllerDeps stubs ──

export function createNoOpDeps(): Pick<never, never> {
  return {};
}

export function createDryRunFlowStateUpdater(): {
  setState: () => void;
  appendEvent: () => void;
  appendLog: () => void;
} {
  return {
    setState: () => {},
    appendEvent: () => {},
    appendLog: () => {},
  };
}

// ── Dry-Run Workflow Execution ──

export type DryRunExecutionResult = {
  flowState: FlowState;
  nodeReports: NodeExecutionReport[];
};

export async function executeDryRun(
  workflow: WorkflowSpec,
  params: Record<string, string>,
  registry: MockRegistry,
  skillRoot = "",
): Promise<DryRunExecutionResult> {
  const state = createInMemoryFlowState(workflow, params);
  const dryRunDispatch = createDryRunDispatch({ registry, workflow });
  const nodeReports: NodeExecutionReport[] = [];

  const maxIterations = 100; // safety limit
  let iteration = 0;

  while (!isWorkflowComplete(workflow, state.nodeStates) && iteration < maxIterations) {
    iteration++;

    // Find ready nodes (reuse runner logic)
    const readyNodes = getReadyNodes(workflow, state.nodeStates);

    if (readyNodes.length === 0) {
      // Check for skippable nodes
      const skippable = findSkippableNodesFixedPoint(workflow, state.nodeStates);
      if (skippable.length === 0) break;

      for (const node of skippable) {
        state.nodeStates[node.id] = {
          ...state.nodeStates[node.id],
          status: "skipped",
          completedAt: Date.now(),
        };
        nodeReports.push({
          nodeId: node.id,
          nodeStatus: "skipped",
          mockSource: "default",
          assertions: [],
        });
      }
      continue;
    }

    // Execute ready nodes (serial for dry-run simplicity)
    for (const node of readyNodes) {
      const startedAt = Date.now();
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "running",
        startedAt,
      };

      const resolved = registry.resolve(node.id);
      const mockSource = resolved.config ? resolved.source : ("default" as MockSource);

      let result: ExecutorResult;
      try {
        const templateCtx = buildTemplateContext(state, skillRoot, undefined, { currentNodeId: node.id });
        result = await dryRunDispatch(node, templateCtx, state, `dry-run:${state.workflowId}`);
      } catch (err) {
        result = { status: "failed", error: String(err) };
      }

      const completedAt = Date.now();

      // Update node state
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: result.status === "succeeded" ? "succeeded"
          : result.status === "waiting" ? "waiting"
          : "failed",
        completedAt,
        result: result.result,
        error: result.error ?? null,
      };

      // Apply result to workflowData / actionOutputs
      if (result.status === "succeeded" && result.result) {
        state.workflowData[node.id] = result.result;
        if (node.executor.type === "action") {
          state.actionOutputs[node.id] = result.result;
        }
      }

      // Evaluate onResult branches and set matchedBranchId
      if (node.onResult && result.status === "succeeded" && result.result) {
        const onResultAction = evaluateOnResult(node.onResult, result.result);
        if (onResultAction.action === "branch" && onResultAction.matchedBranchIndex !== undefined) {
          const branch = node.onResult.branches![onResultAction.matchedBranchIndex];
          state.nodeStates[node.id] = {
            ...state.nodeStates[node.id],
            matchedBranchId: branch.branchId,
          };

          // Immediately skip non-matching branch nodes
          const skippable = findSkippableNodesFixedPoint(workflow, state.nodeStates);
          for (const skipNode of skippable) {
            if (state.nodeStates[skipNode.id]?.status === "pending") {
              state.nodeStates[skipNode.id] = {
                ...state.nodeStates[skipNode.id],
                status: "skipped",
                completedAt: Date.now(),
              };
              nodeReports.push({
                nodeId: skipNode.id,
                nodeStatus: "skipped",
                mockSource: "default",
                assertions: [],
              });
            }
          }
        }
      }

      // Compute phase/status
      const phaseInfo = computePhaseAndStatus(workflow, state.nodeStates);
      state.currentPhase = phaseInfo.currentPhase;
      if (phaseInfo.businessStatus) state.businessStatus = phaseInfo.businessStatus;

      // Handle retry in dry-run: if node has retry and result is failed,
      // simulate re-attempt (return error on each attempt)
      let finalResult = result;
      const maxAttempts = node.retry?.maxAttempts ?? 1;
      if (result.status === "failed" && maxAttempts > 1) {
        // In dry-run, each retry returns the same error
        state.nodeStates[node.id].attempts = maxAttempts;
        finalResult = result;
      }

      nodeReports.push({
        nodeId: node.id,
        nodeStatus: state.nodeStates[node.id].status,
        startedAt,
        completedAt,
        duration: completedAt - startedAt,
        mockSource,
        assertions: [],
      });

      // Process skippable nodes after a failure
      if (finalResult.status === "failed") {
        const skippable = findSkippableNodesFixedPoint(workflow, state.nodeStates);
        for (const skipNode of skippable) {
          if (state.nodeStates[skipNode.id].status === "pending") {
            state.nodeStates[skipNode.id] = {
              ...state.nodeStates[skipNode.id],
              status: "skipped",
              completedAt: Date.now(),
            };
            nodeReports.push({
              nodeId: skipNode.id,
              nodeStatus: "skipped",
              mockSource: "default",
              assertions: [],
            });
          }
        }
      }
    }
  }

  return { flowState: state, nodeReports };
}

// ── Branch-aware node readiness is now delegated to runner.getReadyNodes ──