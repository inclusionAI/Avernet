/**
 * Default executor dispatch for MCP Server mode.
 *
 * This provides a full executor dispatch that works without OpenClaw's
 * `api.runtime.agent` and `api.runtime.system`. It:
 * - Reuses existing platform-agnostic executors where possible (human-wait, mcp-call, baas-call)
 * - Uses MCP sampling callback for embedded-agent/subagent/bcs-route/collaboration nodes
 * - Uses child_process.spawn for cli-script nodes
 * - Returns clear error messages for DingTalk/SecOC-specific executors (approval-card-*)
 * - Supports subworkflow via injected SubworkflowDeps
 *
 * The dispatch function has the same signature as the one in index.ts,
 * enabling direct use by the Controller via `ControllerDeps.executeNode`.
 *
 * @module platform/default-executor
 */

import type { ControllerDeps } from "../controller.js";
import type { WorkflowNode, FlowState, ExecutorResult, WorkflowSpec, WorkflowRuntimeUser } from "../types.js";
import type { TemplateContext } from "../runner.js";
import { buildActionTemplateExtras, resolveTemplate } from "../runner.js";
import type { ActionRegistry, ActionExecutionContext } from "../actions/types.js";
import { resolveActionArgs } from "../actions/template.js";
import type { EmbeddedAgentResult } from "./mcp-adapter.js";
import { extractJsonResult } from "../executors/json-repair.js";
import { jsonFailureError } from "../executors/json-failure.js";
import type { CommandRunner } from "../command-runner.js";
import type { SubworkflowDeps } from "../executors/subworkflow.js";
import { executeHumanWait } from "../executors/human-wait.js";
import { executeMcpCall } from "../executors/mcp-call.js";
import { executeBaasCall } from "../executors/baas-call.js";
import { executeBcsRoute } from "../executors/bcs-route.js";
import { executeSubworkflow } from "../executors/subworkflow.js";
import { buildSkipResult, evaluateSkipWhenConditions, readNodeSkipWhen } from "../skip-when.js";

export interface DefaultExecutorOptions {
  sessionKey: string;
  actionRegistry: ActionRegistry;
  abortSignal?: AbortSignal;
  /** MCP sampling callback — replaces runEmbeddedPiAgent for embedded-agent/subagent/bcs-route/collaboration */
  embeddedAgentFn?: (params: Record<string, unknown>) => Promise<EmbeddedAgentResult>;
  commandRunnerFn?: CommandRunner;
  user?: WorkflowRuntimeUser;
  workflow?: WorkflowSpec;
  /** Subworkflow support — if absent, subworkflow nodes return an error */
  subworkflowDeps?: SubworkflowDeps;
  /** Callback for reporting node progress (e.g. baas-call polling status) */
  onNodeProgress?: (flowId: string, workflowId: string, nodeId: string, executorType: string, attempt: number, message: string) => void;
}

const TECLAW_EMPTY_RESPONSE_FALLBACK = "I'm not sure how to respond to that.";

function isTeClawEmptyResponseFallback(output: unknown): boolean {
  return typeof output === "string" && output.trim() === TECLAW_EMPTY_RESPONSE_FALLBACK;
}

function buildAgentOutputResult(result: EmbeddedAgentResult): Record<string, unknown> {
  return {
    output: result.output,
    ...(result.meta ? { meta: result.meta } : {}),
  };
}

/**
 * Create an executor dispatch function for MCP Server mode.
 *
 * The returned function has the same signature as the dispatch in index.ts,
 * but uses MCP-compatible implementations instead of OpenClaw's
 * `api.runtime.agent` and `api.runtime.system`.
 */
export function createDefaultExecutorDispatch(
  options: DefaultExecutorOptions,
): ControllerDeps["executeNode"] {
  const {
    sessionKey,
    actionRegistry,
    abortSignal,
    embeddedAgentFn,
    commandRunnerFn,
    user,
    workflow,
    subworkflowDeps,
    onNodeProgress,
  } = options;

  return async (
    node: WorkflowNode,
    templateCtx: TemplateContext,
    flowState: FlowState,
    flowId: string,
  ): Promise<ExecutorResult> => {
    const nodeType = node.executor.type;

    // ── skipWhen: 通用闸,所有节点类型,在 type-specific 分发之前 ──
    const skipWhen = readNodeSkipWhen(node);
    if (skipWhen && evaluateSkipWhenConditions(skipWhen, templateCtx)) {
      const isApproval = nodeType === "approval";
      return { status: "succeeded", result: buildSkipResult(node, isApproval) };
    }

    // ── embedded-agent / subagent: Use MCP sampling callback ──

    if (nodeType === "embedded-agent" || nodeType === "subagent") {
      if (!embeddedAgentFn) {
        return {
          status: "failed",
          error: `Node type "${nodeType}" requires MCP sampling support. Configure an embeddedAgentFn or use OpenClaw mode.`,
        };
      }

      try {
        const rawPrompt = node.executor.type === "embedded-agent"
          ? (node.executor as { prompt?: string }).prompt ?? node.id
          : (node.executor as { prompt?: string; goal?: string }).prompt
            ?? (node.executor as { prompt?: string; goal?: string }).goal
            ?? node.id;
        const prompt = resolveTemplate(rawPrompt, templateCtx);
        console.error(`[clawmind:executor] embeddedAgentFn called: node=${node.id} type=${nodeType} flowId=${flowId} promptLen=${prompt.length} embeddedAgentFn=${embeddedAgentFn.name || "anonymous"}`);
        // ── Timeout wrapper — prevent indefinite hang ──
        // Honor the node-level timeout while preserving the historical
        // 10-minute fallback for workflows that do not configure one.
        const agentFnTimeoutMs = (
          (node.executor as { timeoutSeconds?: number }).timeoutSeconds ?? 10 * 60
        ) * 1000;
        const result = await Promise.race([
          embeddedAgentFn({
            prompt,
            sessionKey,
            flowId,
            nodeId: node.id,
            workflowId: flowState.workflowId,
          }),
          new Promise<never>((_, reject) =>
            setTimeout(() => reject(new Error(`embeddedAgentFn timed out after ${agentFnTimeoutMs}ms`)), agentFnTimeoutMs)
          ),
        ]);
        console.error(`[clawmind:executor] embeddedAgentFn returned: node=${node.id} error=${result.error ? result.error.slice(0, 200) : "none"} outputLen=${result.output?.length ?? 0} meta=${JSON.stringify(result.meta ?? {}).slice(0, 200)}`);

        if (result.error) {
          return { status: "failed", error: result.error };
        }

        // ── JSON output mode ──
        // When outputMode=json or an outputContract is defined, extract
        // structured JSON from the agent's text output.  The agent may
        // return prose + code-fenced JSON; extractJsonResult handles
        // code-fence stripping, prefix/suffix removal, and trailing
        // comma repair.
        const outputMode = (node.executor as { outputMode?: string }).outputMode;
        if (outputMode === "json" || node.outputContract) {
          if (isTeClawEmptyResponseFallback(result.output)) {
            return {
              status: "failed",
              result: buildAgentOutputResult(result),
              error: "TeClaw Agent Loop 返回空响应兜底文本，无法满足 outputMode=json 要求",
            };
          }

          const parsed = extractJsonResult(result.output ?? "");
          if (parsed) {
            const failureError = jsonFailureError(parsed);
            if (failureError) {
              return {
                status: "failed",
                result: parsed,
                error: failureError,
              };
            }
            return {
              status: "succeeded",
              result: parsed,
              warnings: [{ code: "json_repair_needed", message: "JSON extracted from embedded-agent output via lightweight repair" }],
            };
          }
          // JSON extraction failed — the whole node fails
          return {
            status: "failed",
            result: buildAgentOutputResult(result),
            error: `embedded-agent 输出不是有效 JSON，无法满足 outputMode=json 要求`,
          };
        }

        // Text mode — wrap raw output
        return {
          status: "succeeded",
          result: {
            output: result.output,
            ...(result.meta ? { meta: result.meta } : {}),
          },
        };
      } catch (err) {
        return {
          status: "failed",
          error: err instanceof Error ? err.message : String(err),
        };
      }
    }

    // ── cli-script: Use child_process.spawn or custom runner ──

    if (nodeType === "cli-script") {
      const runner = commandRunnerFn ?? defaultSpawnRunner;
      const cliExecutor = node.executor as { command?: string; timeoutMs?: number };
      const argv = cliExecutor.command ? cliExecutor.command.split(/\s+/) : [];
      if (argv.length === 0) {
        return { status: "failed", error: "cli-script node has no command" };
      }

      try {
        const result = await runner({
          argv,
          timeoutMs: cliExecutor.timeoutMs ?? 60_000,
          cwd: process.cwd(),
        });
        if (result.code === 0) {
          const cliOutputMode = (node.executor as { outputMode?: string }).outputMode;
          if (cliOutputMode === "json" || node.outputContract) {
            try {
              const parsed = JSON.parse(result.stdout.trim());
              return { status: "succeeded", result: parsed };
            } catch {
              return {
                status: "failed",
                error: `cli-script stdout is not valid JSON. stdout: ${result.stdout.substring(0, 500)}`,
              };
            }
          }
          return { status: "succeeded", result: { output: result.stdout.trim() } };
        }
        return {
          status: "failed",
          error: `cli-script exited with code ${result.code}: ${result.stderr.trim() || result.stdout.trim()}`,
        };
      } catch (err) {
        return { status: "failed", error: err instanceof Error ? err.message : String(err) };
      }
    }

    // ── mcp-call: Delegate to existing executeMcpCall (platform-agnostic) ──
    // executeMcpCall uses CommandRunner, which we provide.

    if (nodeType === "mcp-call") {
      return executeMcpCall(node, templateCtx, commandRunnerFn);
    }

    // ── done: Terminal node ──

    if (nodeType === "done") {
      const doneExecutor = node.executor as { message?: string };
      return { status: "succeeded", result: { output: doneExecutor.message ?? "done" } };
    }

    // ── action: Execute action registry actions ──

    if (nodeType === "action") {
      const actionExecutor = node.executor as { action?: string; args?: Record<string, unknown> };
      const actionName = actionExecutor.action;
      if (!actionName) {
        return { status: "failed", error: "action node has no action name" };
      }

      try {
        const templateExtras = buildActionTemplateExtras(flowState, node.id);
        const context: ActionExecutionContext = {
          flowId,
          workflowId: flowState.workflowId,
          actionId: actionName,
          nodeId: node.id,
          sessionKey,
          executionMode: flowState.executionMode ?? "private",
          bcsGroupId: flowState.bcsGroupId,
          params: flowState.params ?? {},
          input: flowState.input,
          workflowData: flowState.workflowData ?? {},
          nodeOutput: templateExtras.nodeOutput,
          actionOutputs: flowState.actionOutputs ?? {},
          loop: templateExtras.loop,
          templateAliases: templateExtras.templateAliases,
          user: user ?? {},
          workflow,
        };
        const args = resolveActionArgs(actionExecutor.args ?? {}, context);
        const result = await actionRegistry.execute(actionName, args, context);
        return { status: "succeeded", result };
      } catch (err) {
        return { status: "failed", error: err instanceof Error ? err.message : String(err) };
      }
    }

    // ── human / human-wait: Delegate to existing executeHumanWait (platform-agnostic) ──
    // executeHumanWait just returns { status: "waiting" } — no OpenClaw dependency.

    if (nodeType === "human") {
      return executeHumanWait(node, templateCtx);
    }

    // ── bcs-route: Delegate to existing executeBcsRoute (with no-op api) ──
    // When no bcsGroupId, executeBcsRoute returns a succeeded result with routing info.
    // When bcsGroupId exists, it needs api.runtime.agent.runEmbeddedPiAgent —
    // we provide a shim that delegates to the MCP sampling agent.

    if (nodeType === "bcs-route") {
      const bcsRouteApi = {
        runtime: {
          agent: {
            runEmbeddedPiAgent: embeddedAgentFn ?? unsupportedBcsAgent,
          },
        },
      };
      return executeBcsRoute(node, templateCtx, bcsRouteApi, flowState, { workflow });
    }

    // ── collaboration: Route to the appropriate sub-executor ──
    // Collaboration nodes delegate to subagent/embedded-agent/bcs-route based on delivery config.
    // Map each delivery mode to our MCP-compatible implementations.

    if (nodeType === "collaboration") {
      return executeCollaborationMcp(node, templateCtx, flowState, flowId, options);
    }

    // ── baas-call: Delegate to existing executeBaasCall (platform-agnostic) ──
    // executeBaasCall only uses fetch() and doesn't depend on OpenClaw APIs.

    if (nodeType === "baas-call") {
      return executeBaasCall(node, templateCtx, undefined, flowState, (msg) => {
        if (onNodeProgress) {
          onNodeProgress(flowId, flowState.workflowId, node.id, "baas-call", flowState.nodeStates[node.id]?.attempts ?? 1, msg);
        }
      });
    }

    // ── subworkflow: Delegate to executeSubworkflow with injected deps ──

    if (nodeType === "subworkflow") {
      if (!subworkflowDeps) {
        return {
          status: "failed",
          error: `subworkflow node ${node.id}: subworkflow execution is not available in MCP Server mode (no SubworkflowDeps provided)`,
        };
      }
      return executeSubworkflow(node, templateCtx, flowState, flowId, subworkflowDeps);
    }

    // ── loop-group: Must be materialized by controller before execution ──

    if (nodeType === "loop-group") {
      return { status: "failed", error: "loop-group must be materialized by controller before execution" };
    }

    // ── approval: Map to human-wait pattern in MCP mode ──
    // Approval nodes in OpenClaw resolve to a delivery channel (card-dingtalk, bcs-route, etc.)
    // In MCP mode, we map them to the "waiting" pattern — the host (Claude/Hermes) uses
    // confirm/reject tools to resume the flow, just like human-wait.
    // We can't delegate to executeHumanWait() because it checks for type==="human".

    if (nodeType === "approval") {
      const approvalExecutor = node.executor as { prompt?: string; waitKind?: string; [key: string]: unknown };
      const resolvedPrompt = approvalExecutor.prompt
        ? resolveTemplate(approvalExecutor.prompt, templateCtx)
        : `Approval required for node ${node.id}`;
      return {
        status: "waiting",
        waitConfig: {
          prompt: resolvedPrompt,
          hint: resolvedPrompt,
          waitKind: approvalExecutor.waitKind ?? "human-confirm",
        },
      };
    }

    // ── Unknown node type ──

    return {
      status: "failed",
      error: `Unknown node type: "${nodeType}"`,
    };
  };
}

// ── Collaboration delivery (MCP mode) ──

/**
 * Execute a collaboration node in MCP Server mode.
 *
 * Collaboration nodes route to different delivery channels based on their
 * `delivery.collaboration.primary` config. In MCP mode:
 * - subagent/embedded-agent → use MCP sampling
 * - bcs-route → use BCS route with MCP sampling shim
 * - bcs-cli → execute the referenced action
 * - default → use MCP sampling as fallback
 */
async function executeCollaborationMcp(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  flowState: FlowState,
  flowId: string,
  options: DefaultExecutorOptions,
): Promise<ExecutorResult> {
  if (node.executor.type !== "collaboration") {
    return { status: "failed", error: "not a collaboration node" };
  }

  const collaborationExecutor = node.executor as { delivery?: { collaboration?: { primary?: string; action?: string } }; message?: string; prompt?: string };
  const delivery = collaborationExecutor.delivery?.collaboration;
  const primary = delivery?.primary ?? "embedded-agent";

  switch (primary) {
    case "subagent":
    case "embedded-agent": {
      // Use MCP sampling for embedded-agent/subagent delivery
      if (!options.embeddedAgentFn) {
        return {
          status: "failed",
          error: `collaboration node ${node.id} uses "${primary}" delivery, which requires MCP sampling support.`,
        };
      }
      const prompt = collaborationExecutor.prompt ?? collaborationExecutor.message ?? node.id;
      try {
        const result = await options.embeddedAgentFn({
          prompt,
          sessionKey: options.sessionKey,
          flowId,
          nodeId: node.id,
          workflowId: flowState.workflowId,
        });
        if (result.error) {
          return { status: "failed", error: result.error };
        }
        return {
          status: "succeeded",
          result: {
            output: result.output,
            ...(result.meta ? { meta: result.meta } : {}),
          },
        };
      } catch (err) {
        return { status: "failed", error: err instanceof Error ? err.message : String(err) };
      }
    }

    case "bcs-route": {
      const bcsRouteApi = {
        runtime: {
          agent: {
            runEmbeddedPiAgent: options.embeddedAgentFn ?? unsupportedBcsAgent,
          },
        },
      };
      return executeBcsRoute(node, templateCtx, bcsRouteApi, flowState, { workflow: options.workflow });
    }

    case "bcs-cli": {
      // bcs-cli delivery means executing an action from the collaboration config
      if (!delivery?.action) {
        return {
          status: "failed",
          error: `collaboration delivery bcs-cli for node ${node.id} requires an action name`,
        };
      }
      try {
        const templateExtras = buildActionTemplateExtras(flowState, node.id);
        const context: ActionExecutionContext = {
          flowId,
          workflowId: flowState.workflowId,
          actionId: delivery.action,
          nodeId: node.id,
          sessionKey: options.sessionKey,
          executionMode: flowState.executionMode ?? "private",
          bcsGroupId: flowState.bcsGroupId,
          params: flowState.params ?? {},
          input: flowState.input,
          workflowData: flowState.workflowData ?? {},
          nodeOutput: templateExtras.nodeOutput,
          actionOutputs: flowState.actionOutputs ?? {},
          loop: templateExtras.loop,
          templateAliases: templateExtras.templateAliases,
          user: options.user ?? {},
          workflow: options.workflow,
        };
        const result = await options.actionRegistry.execute(delivery.action, {}, context);
        return { status: "succeeded", result };
      } catch (err) {
        return { status: "failed", error: err instanceof Error ? err.message : String(err) };
      }
    }

    default:
      return {
        status: "failed",
        error: `Unknown collaboration delivery primary: ${primary}`,
      };
  }
}

// ── Fallback BCS agent (when no MCP sampling is available) ──

async function unsupportedBcsAgent(): Promise<{ output?: string; error?: string }> {
  return {
    error: "BCS routing requires an embedded agent (MCP sampling). Configure embeddedAgentFn or use OpenClaw mode.",
  };
}

// ── Default spawn runner ──

const defaultSpawnRunner: CommandRunner = async (options) => {
  const { spawn } = await import("node:child_process");
  return new Promise<{ code: number; stdout: string; stderr: string }>((resolve, reject) => {
    const proc = spawn(options.argv[0], options.argv.slice(1), {
      cwd: options.cwd,
      env: options.env ?? process.env,
      timeout: options.timeoutMs,
    });
    let stdout = "";
    let stderr = "";
    proc.stdout.on("data", (chunk: Buffer) => { stdout += chunk.toString(); });
    proc.stderr.on("data", (chunk: Buffer) => { stderr += chunk.toString(); });
    proc.on("close", (code) => {
      resolve({ code: code ?? 1, stdout, stderr });
    });
    proc.on("error", (err) => {
      reject(err);
    });
  });
};
