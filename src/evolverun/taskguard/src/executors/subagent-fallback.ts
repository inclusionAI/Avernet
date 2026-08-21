import type {
  ExecutorResult,
  OutputContractSpec,
  WorkflowNode,
} from "../types.js";
import { getLegacyApprovalExecutor } from "../legacy-runtime.js";

const GATEWAY_SUBAGENT_RUNTIME_UNAVAILABLE =
  "Plugin runtime subagent methods are only available during a gateway request.";

const EMBEDDED_FALLBACK_RUNNER = "embedded-agent-fallback";
const EMBEDDED_FALLBACK_REASON = "gateway-subagent-runtime-unavailable";

const APPROVAL_FALLBACK_CONTRACT: OutputContractSpec = {
  required: true,
  schema: {
    type: "object",
    required: ["approved", "note"],
    properties: {
      approved: { type: "boolean" },
      note: { type: "string" },
    },
  },
};

export function shouldFallbackSubagentToEmbedded(result: ExecutorResult): boolean {
  return (
    result.status === "failed" &&
    typeof result.error === "string" &&
    result.error.includes(GATEWAY_SUBAGENT_RUNTIME_UNAVAILABLE)
  );
}

export function toEmbeddedFallbackNode(node: WorkflowNode): WorkflowNode {
  const approvalExecutor = getLegacyApprovalExecutor(node);
  if (approvalExecutor) {
    return {
      ...node,
      executor: {
        type: "embedded-agent",
        skillName: approvalExecutor.skillName,
        prompt: approvalExecutor.message,
        outputMode: "json",
        timeoutSeconds: approvalExecutor.timeoutSeconds ?? 1200,
        contextPolicy: approvalExecutor.contextPolicy,
      },
      outputContract: node.outputContract ?? APPROVAL_FALLBACK_CONTRACT,
    };
  }

  if (node.executor.type === "subagent") {
    return {
      ...node,
      executor: {
        type: "embedded-agent",
        skillName: node.executor.skillName,
        prompt: node.executor.prompt,
        outputMode: node.outputContract ? "json" : "text",
        timeoutSeconds: node.executor.timeoutSeconds ?? 1200,
        contextPolicy: node.executor.contextPolicy,
      },
    };
  }

  if (node.executor.type === "collaboration") {
    return {
      ...node,
      executor: {
        type: "embedded-agent",
        skillName: node.executor.skillName ?? "",
        prompt: node.executor.message,
        outputMode: node.outputContract ? "json" : "text",
        timeoutSeconds: node.executor.timeoutSeconds ?? 1200,
        contextPolicy: node.executor.contextPolicy,
      },
    };
  }

  return node;
}

export function decorateEmbeddedFallbackResult(result: ExecutorResult): ExecutorResult {
  if (result.status !== "succeeded") return result;

  return {
    ...result,
    result: {
      ...(result.result ?? {}),
      runner: EMBEDDED_FALLBACK_RUNNER,
      fallbackReason: EMBEDDED_FALLBACK_REASON,
    },
  };
}

export function validateApprovalFallbackResult(
  node: WorkflowNode,
  result: ExecutorResult,
): ExecutorResult {
  if (!getLegacyApprovalExecutor(node) || result.status !== "succeeded") return result;
  if (typeof result.result?.approved !== "boolean") {
    return {
      status: "failed",
      result: result.result,
      error: "embedded-agent fallback approval JSON missing boolean approved",
    };
  }
  if (typeof result.result?.note !== "string") {
    return {
      status: "failed",
      result: result.result,
      error: "embedded-agent fallback approval JSON missing string note",
    };
  }

  return result;
}

export async function runEmbeddedFallbackAfterSubagentFailure(params: {
  node: WorkflowNode;
  subagentResult: ExecutorResult;
  runEmbedded: (fallbackNode: WorkflowNode) => Promise<ExecutorResult>;
}): Promise<ExecutorResult> {
  if (!shouldFallbackSubagentToEmbedded(params.subagentResult)) {
    return params.subagentResult;
  }

  const fallbackNode = toEmbeddedFallbackNode(params.node);
  const embeddedResult = await params.runEmbedded(fallbackNode);
  const decoratedResult = decorateEmbeddedFallbackResult(embeddedResult);
  return validateApprovalFallbackResult(params.node, decoratedResult);
}
