/**
 * Agent SDK Inline MCP Server — ClawMind workflow tools for Agent SDK query().
 *
 * Creates an in-process MCP server using `createSdkMcpServer()` + `tool()` from
 * the Claude Agent SDK. These tools are available only inside the Agent SDK
 * Agent Loop (embedded-agent Path C), not through the external MCP stdio transport.
 *
 * The inline tools directly call ClawMind's TaskFlowAdapter — no MCP
 * transport overhead, no extra process. Error handling uses `isError: true` so
 * the Agent Loop continues and the LLM can retry or adapt.
 *
 * ### Why TaskFlowAdapter instead of raw repositories?
 *
 * TaskFlowAdapter is the same abstraction the Controller uses. It handles
 * API mode, in-memory mode, and session scoping transparently. The inline
 * server's tools only need read-only access (get/list), so we call those
 * methods and serialize the result.
 *
 * @module platform/agent-sdk-inline-server
 */

import { tool, createSdkMcpServer } from "@anthropic-ai/claude-agent-sdk";
import type { McpSdkServerConfigWithInstance } from "@anthropic-ai/claude-agent-sdk";
import { z } from "zod";
import type { TaskFlowAdapter } from "./types.js";

// ── Dependency Injection ──

/**
 * Callbacks the inline tools need to query workflow state.
 *
 * Deliberately narrow — only the read operations that embedded-agent nodes
 * need. The TaskFlowAdapter is not exposed directly to keep the contract
 * minimal and testable.
 */
export interface InlineServerDeps {
  /** Get the current state of a flow run via TaskFlowAdapter.get(). */
  getFlowState: (flowId: string) => Promise<Record<string, unknown> | null>;
  /** List flow runs via TaskFlowAdapter.list(). Returns JSON-safe array. */
  listFlows: (opts: { workflowId?: string; limit?: number }) => Promise<Array<Record<string, unknown>>>;
}

/**
 * Create InlineServerDeps from a TaskFlowAdapter instance.
 *
 * The deps wrap TaskFlowAdapter's `get()` and `list()` methods with
 * post-processing (filtering, limiting) appropriate for inline tool responses.
 */
export function createInlineServerDepsFromTaskFlow(taskFlow: TaskFlowAdapter): InlineServerDeps {
  return {
    getFlowState: async (flowId: string) => {
      return taskFlow.get(flowId);
    },

    listFlows: async (opts: { workflowId?: string; limit?: number }) => {
      const result = await taskFlow.list();
      // TaskFlowAdapter.list() returns { flows: [...] } or Flow[] directly
      let flows: Array<Record<string, unknown>> = [];
      if (Array.isArray(result)) {
        flows = result;
      } else if (result && "flows" in result && Array.isArray(result.flows)) {
        flows = result.flows;
      }

      // Optional filter by workflowId (matching the goal/workflow field)
      if (opts.workflowId) {
        flows = flows.filter(f => {
          const goal = String(f.goal ?? "");
          return goal.includes(opts.workflowId!);
        });
      }

      // Apply limit
      if (typeof opts.limit === "number" && opts.limit > 0) {
        flows = flows.slice(0, opts.limit);
      }

      return flows;
    },
  };
}

// ── Tool Definitions ──

/**
 * Create the ClawMind inline MCP server with workflow_state and workflow_runs tools.
 *
 * @param deps — Injected callbacks for querying flow state (from TaskFlowAdapter)
 * @returns McpSdkServerConfigWithInstance — pass this to `query({ options: { mcpServers: { clawmind: result } } })`
 */
export function createClawmindInlineServer(deps: InlineServerDeps): McpSdkServerConfigWithInstance {
  // workflow_state — query a single flow run's current state
  const workflowStateTool = tool(
    "workflow_state",
    "Query the current state of a ClawMind workflow run. Returns node statuses, current step, and flow metadata.",
    {
      flowId: z.string().describe("The flow run ID to query (e.g., 'flow_abc123')"),
    },
    async (args) => {
      try {
        const state = await deps.getFlowState(args.flowId);
        if (!state) {
          return {
            content: [{ type: "text" as const, text: `Flow run '${args.flowId}' not found.` }],
            isError: true,
          };
        }
        return {
          content: [{ type: "text" as const, text: JSON.stringify(state, null, 2) }],
        };
      } catch (err) {
        return {
          content: [{
            type: "text" as const,
            text: `Failed to query workflow state: ${err instanceof Error ? err.message : String(err)}`,
          }],
          isError: true,
        };
      }
    },
    { annotations: { readOnlyHint: true } },
  );

  // workflow_runs — list historical workflow runs
  const workflowRunsTool = tool(
    "workflow_runs",
    "List ClawMind workflow runs. Returns recent flow runs with status, workflow ID, and timing information.",
    {
      workflowId: z.string().optional().describe("Filter by workflow ID (e.g., 'risk-review-pipeline')"),
      limit: z.number().int().min(1).max(50).default(10).describe("Maximum number of results to return (1-50, default 10)"),
    },
    async (args) => {
      try {
        const flows = await deps.listFlows({
          workflowId: args.workflowId,
          limit: args.limit,
        });
        return {
          content: [{ type: "text" as const, text: JSON.stringify(flows, null, 2) }],
        };
      } catch (err) {
        return {
          content: [{
            type: "text" as const,
            text: `Failed to list workflow flows: ${err instanceof Error ? err.message : String(err)}`,
          }],
          isError: true,
        };
      }
    },
    { annotations: { readOnlyHint: true } },
  );

  return createSdkMcpServer({
    name: "clawmind",
    version: "1.0.0",
    tools: [workflowStateTool, workflowRunsTool],
    alwaysLoad: true, // Always include these tools in prompt (they're few and focused)
  });
}