/**
 * MCP Agent Runner — Agent Loop execution via TeClaw WebSocket, Agent SDK, or sampling.
 *
 * ### Path selection priority (RFC-003 §5.4.4):
 *
 * 1. **TeClaw WebSocket** (Channel 2) — Full multi-turn agent loop via TeClaw WS.
 *    When configured, it is the SOLE execution path — no degradation to
 *    sampling. Sampling (createMessage) is fundamentally broken in async MCP
 *    transport mode because `_transport` is null after the HTTP response
 *    completes (see @modelcontextprotocol/sdk Protocol.request() line 619:
 *    "Not connected"). Workflow nodes execute asynchronously, so the MCP
 *    transport that served the original request has already been cleaned up.
 *
 * 2. **Agent SDK** (Path C) — Multi-turn agent loop via `query()` (进程内函数调用).
 *    Used when `ANTHROPIC_API_KEY` is set and the node requires multi-turn
 *    execution with tools. The Agent SDK launches a full agent loop in-process
 *    with deterministic tool access via inline MCP server.
 *
 * 3. **MCP Sampling** (Path A) — Single-turn sampling/createMessage fallback.
 *    Only used when no TeClaw or Agent SDK is configured (stdio mode with
 *    persistent transport).
 *
 * ### TeClaw WebSocket note
 *
 * chat.send ALWAYS creates a dedicated agent session via
 * POST /api/v1/sessions before execution. The user's conversation session
 * (x-teclaw-session-key) is for chatInject routing only — agent loops
 * need their own session.
 *
 * @module platform/mcp-agent-runner
 */

import type { EmbeddedAgentResult } from "./mcp-adapter.js";
import {
  ChatInjectMessageType,
  type ChatInjectAdapter,
} from "./types.js";
import type {
  TeClawAgentProgressEvent,
  TeClawProvider,
} from "./teclaw-provider.js";

// ── Protocol Types ──

/** Parameters for an agent loop request (RFC-003 §5.2). */
export interface McpAgentLoopParams {
  prompt: string;
  sessionKey?: string;
  flowId?: string;
  nodeId?: string;
  workflowId?: string;
  maxTokens?: number;
  maxTurns?: number;
  allowedTools?: string[];
  systemPrompt?: string;
  nodeOutputs?: Record<string, string>;
}

/** Response from a createAgentLoop request (RFC-003 §5.2). */
export interface McpAgentLoopResult {
  output?: string;
  error?: string;
  payloads?: Array<{ text?: string; isError?: boolean; isReasoning?: boolean; turn?: number }>;
  messagingToolSentTexts?: string[];
  meta?: {
    model?: string;
    stopReason?: string;
    totalTurns?: number;
    totalTokens?: { input?: number; output?: number };
  };
}

/** TeClaw progress event enriched with the current workflow context. */
export interface McpTeClawProgressEvent extends TeClawAgentProgressEvent {
  flowId?: string;
  nodeId?: string;
  workflowId?: string;
}

/** Interface for MCP servers with sampling/createMessage support (fallback path). */
export interface McpSamplingCapable {
  server: {
    /** @deprecated Use TeClaw HTTP API instead. Kept for backward compat. */
    createAgentLoop?(params: unknown): Promise<McpAgentLoopResult>;
    /** MCP sampling/createMessage (single-turn, fallback). */
    createMessage?(params: unknown): Promise<unknown>;
  };
}

/** @deprecated Use McpSamplingCapable. Kept for backward compat. */
export type McpAgentLoopCapable = McpSamplingCapable;

/** Options for the MCP agent runner. */
export interface McpAgentRunnerOptions {
  /**
   * Standalone sampling agent for stdio mode without TeClaw.
   * This is NOT a degradation fallback — it is only used when no
   * TeClawProvider is configured at all (local stdio mode).
   * When TeClawProvider exists, it is the sole execution path.
   */
  fallbackSamplingAgent?: (params: Record<string, unknown>) => Promise<EmbeddedAgentResult>;
  /** Default system prompt prefix. */
  systemPromptPrefix?: string;
  /** Default max turns. */
  defaultMaxTurns?: number;
  /** Default max tokens. */
  defaultMaxTokens?: number;
  /** TeClawProvider instance for WebSocket Channel 2. When set, takes priority. */
  teclawProvider?: TeClawProvider;
  /**
   * Agent SDK runner for multi-turn embedded-agent via `query()` (进程内函数调用).
   * Path C — used when ANTHROPIC_API_KEY is set and no TeClaw is configured,
   * OR when the node requires multi-turn tool access.
   * Priority: TeClaw WS > Agent SDK > Sampling.
   */
  agentSdkRunner?: ((params: Record<string, unknown>) => Promise<EmbeddedAgentResult>) | null;
  /** Business progress emitted only by the TeClaw WebSocket path. */
  onTeClawProgress?: (
    event: McpTeClawProgressEvent,
  ) => void | Promise<void>;
}

type EmbeddedAgentFn = (params: Record<string, unknown>) => Promise<EmbeddedAgentResult>;

/** Inputs available when mcp-entry constructs an embedded-agent runner per request. */
export interface PerSessionEmbeddedAgentFnOptions {
  teclawProvider?: TeClawProvider;
  chatInject?: ChatInjectAdapter;
  globalAgentRunner?: EmbeddedAgentFn;
  samplingAgent?: EmbeddedAgentFn;
}

/**
 * Forward TeClaw report_progress output through the current request's adapter.
 * This closure is intentionally created per session so local idempotency keys and
 * the target conversation cannot leak across requests.
 */
export function createTeClawProgressForwarder(
  chatInject: ChatInjectAdapter,
): (event: McpTeClawProgressEvent) => Promise<void> {
  let localIndex = 0;

  return async (event) => {
    const text = event.text.trim();
    if (!text) return;

    localIndex += 1;
    const flowId = event.flowId ?? "unknown-flow";
    const nodeId = event.nodeId ?? "unknown-node";
    const suffix = event.seq === undefined
      ? `local-${localIndex}`
      : `seq-${event.seq}`;
    const idempotencyKey = `${flowId}:${nodeId}:teclaw-progress:${suffix}`;

    try {
      await chatInject.inject(text, idempotencyKey, {
        messageType: ChatInjectMessageType.Progress,
        flowId: event.flowId,
        nodeId: event.nodeId,
        workflowId: event.workflowId,
        metadata: {
          source: "teclaw-report-progress",
          ...(event.seq === undefined ? {} : { seq: event.seq }),
        },
      });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      console.error(
        `[clawmind:mcp] TeClaw progress inject failed flowId=${flowId} ` +
        `nodeId=${nodeId} seq=${event.seq ?? "none"} text_len=${text.length} ` +
        `error=${message.slice(0, 200)}`,
      );
    }
  };
}

// ── Capability Detection ──

/**
 * Detect the best available Agent Loop channel.
 *
 * Priority:
 * 1. "teclaw-ws" — Full multi-turn agent loop via TeClaw WebSocket (Channel 2)
 * 2. "agent-sdk" — Multi-turn agent loop via Agent SDK `query()` (进程内函数调用)
 * 3. "sampling" — Single-turn sampling/createMessage (fallback)
 * 4. "none" — No agent loop capability
 *
 * @returns The best available channel identifier
 */
export async function detectAgentLoopSupport(
  serverLike: McpSamplingCapable,
  teclawConfig?: import("../config/types.js").TeClawConfig,
): Promise<"teclaw-ws" | "agent-sdk" | "sampling" | "none"> {
  // 0. Safety guard: TeClaw WS is for TeClaw/BCS platforms only.
  //    When CLAUDE_CODE_EXECUTABLE is set or MCP_TRANSPORT is stdio,
  //    TeClaw WS must NOT be used — the "sole path" design means a WS
  //    failure is NOT degraded to Agent SDK, causing ECONNREFUSED errors
  //    on 127.0.0.1:8080 (which doesn't exist in Claude Code environments).
  const isClaudeCode = !!process.env.CLAUDE_CODE_EXECUTABLE;
  const isStdio = (process.env.MCP_TRANSPORT ?? "").toLowerCase() === "stdio";
  const teclawDisallowed = isClaudeCode || isStdio;

  // 1. TeClaw WebSocket (Channel 2) — full multi-turn agent loop
  //    Config file takes priority, then check env vars
  //    BUT: skip when running in Claude Code / stdio mode
  if (!teclawDisallowed && teclawConfig?.enabled && (teclawConfig.wsUrl || teclawConfig.baseUrl || teclawConfig.agentLoopUrl)) {
    return "teclaw-ws";
  }

  if (!teclawDisallowed && process.env.TECLAW_WS_URL) {
    return "teclaw-ws";
  }

  // Backward compat: TECLAW_BASE_URL → derive WS URL → treat as teclaw-ws
  if (!teclawDisallowed && process.env.TECLAW_BASE_URL) {
    console.warn(
      "[clawmind:mcp] DEPRECATED: TECLAW_BASE_URL is deprecated. " +
      "Use TECLAW_WS_URL instead.",
    );
    return "teclaw-ws";
  }

  // Backward compat: TECLAW_AGENT_LOOP_URL → treat as teclaw-ws
  if (!teclawDisallowed && process.env.TECLAW_AGENT_LOOP_URL) {
    console.warn(
      "[clawmind:mcp] DEPRECATED: TECLAW_AGENT_LOOP_URL is deprecated. " +
      "Use TECLAW_WS_URL instead.",
    );
    return "teclaw-ws";
  }

  // 2. Agent SDK `query()` — multi-turn, 进程内函数调用 (Path C)
  //    The claude CLI authenticates on its own (OAuth, stored credentials).
  //    ANTHROPIC_API_KEY is optional — when set, it's passed through to the
  //    subprocess; when not set, the CLI uses its built-in auth.
  //    Available whenever the claude CLI binary exists (check via env or path).
  if (process.env.CLAUDE_CODE_EXECUTABLE || process.env.ANTHROPIC_API_KEY) {
    return "agent-sdk";
  }

  // 3. MCP sampling/createMessage — single-turn fallback
  //    NOTE: sampling/createAgentLoop is DEPRECATED; do not check for it.
  if (typeof serverLike.server.createMessage === "function") {
    return "sampling";
  }

  // Legacy compat: createAgentLoop still counts as a capability
  if (typeof serverLike.server.createAgentLoop === "function") {
    return "sampling";
  }

  return "none";
}

// ── Agent Runner Factory ──

/**
 * Create an embedded agent function with 3-path selection.
 *
 * Priority:
 * 1. **TeClaw WebSocket** — SOLE path when configured. No degradation.
 * 2. **Agent SDK `query()`** — 进程内函数调用, multi-turn with deterministic tools.
 *    Used when `agentSdkRunner` is provided AND the node needs multi-turn
 *    (has allowedTools or explicit multi-turn flag).
 * 3. **Sampling** — single-turn fallback for stdio mode without TeClaw or SDK.
 *
 * If TeClaw fails, the error is returned directly — no fallback.
 * If Agent SDK fails, the error is returned directly — no fallback to sampling.
 * Degradation between paths is explicit, not automatic.
 */
export function getMcpAgentRunner(
  _serverLike: McpSamplingCapable,
  options: McpAgentRunnerOptions = {},
): (params: Record<string, unknown>) => Promise<EmbeddedAgentResult> {
  const {
    fallbackSamplingAgent,
    systemPromptPrefix = "",
    defaultMaxTurns = 20,
    defaultMaxTokens = 4096,
  } = options;

  const teclawProvider = options.teclawProvider;
  const agentSdkRunner = options.agentSdkRunner;
  const onTeClawProgress = options.onTeClawProgress;

  return async (params: Record<string, unknown>): Promise<EmbeddedAgentResult> => {
    const prompt = String(params.prompt ?? params.goal ?? "");
    if (!prompt) {
      return { error: "Agent runner requires a prompt" };
    }

    const maxTokens = typeof params.maxTokens === "number" ? params.maxTokens : defaultMaxTokens;
    const maxTurns = typeof params.maxTurns === "number" ? params.maxTurns : defaultMaxTurns;
    const allowedTools = Array.isArray(params.allowedTools) ? params.allowedTools : undefined;
    const systemPrompt = systemPromptPrefix
      ? `${systemPromptPrefix}\n\nWorkflow: ${params.workflowId ?? "unknown"}\nNode: ${params.nodeId ?? "unknown"}\nFlow: ${params.flowId ?? "unknown"}`
      : params.systemPrompt as string | undefined;

    const workflowContext = {
      flowId: params.flowId as string | undefined,
      nodeId: params.nodeId as string | undefined,
      workflowId: params.workflowId as string | undefined,
      nodeOutputs: params.nodeOutputs as Record<string, string> | undefined,
      params: params.params as Record<string, unknown> | undefined,
    };

    // ── Diagnostics: log which paths are available ──
    console.error(`[clawmind:mcp] Agent runner path selection: teclawProvider=${!!teclawProvider} agentSdkRunner=${!!agentSdkRunner} fallbackSamplingAgent=${!!fallbackSamplingAgent} prompt="${prompt.slice(0, 80)}"`);

    // ── Path 1: TeClaw WebSocket (Channel 2) — SOLE path when configured ──
    if (teclawProvider) {
      console.error(`[clawmind:mcp] Agent runner: Path 1 — TeClaw WebSocket (multi-turn, Channel 2)`);
      try {
        const result = await teclawProvider.runAgentLoop({
          prompt,
          systemPrompt: systemPrompt ?? "",
          maxTokens,
          maxTurns,
          allowedTools,
          workflowContext,
          onProgress: onTeClawProgress
            ? (event) => onTeClawProgress({
                ...event,
                flowId: workflowContext.flowId,
                nodeId: workflowContext.nodeId,
                workflowId: workflowContext.workflowId,
              })
            : undefined,
        });
        // Return result directly — success OR error.
        // No degradation. If TeClaw WS fails, report the error so the
        // workflow can surface it to the user instead of silently falling
        // back to a broken sampling path.
        return result;
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        console.error(`[clawmind:mcp] TeClaw WebSocket agent loop exception: ${msg.slice(0, 300)}`);
        return {
          error: `TeClaw Agent Loop exception: ${msg.slice(0, 200)}`,
          meta: { teclawException: msg.slice(0, 300), hasTeClawProvider: true },
        };
      }
    }

    // ── Path 2: Agent SDK `query()` (进程内函数调用, Path C) ──
    console.error(`[clawmind:mcp] Agent runner: Path 2 — Agent SDK query() (multi-turn, agentSdkRunner=${!!agentSdkRunner})`);
    // Used when Agent SDK runner is available AND the node needs multi-turn
    // execution (has allowedTools or multi-turn is needed).
    // Falls through to sampling when Agent SDK is not available or node
    // is single-turn only.
    if (agentSdkRunner) {
      const hasAllowedTools = allowedTools && allowedTools.length > 0;
      const needsMultiTurn = hasAllowedTools || (maxTurns > 1);
      if (needsMultiTurn) {
        console.error(`[clawmind:mcp] Agent runner: Agent SDK query() (multi-turn + tools, 进程内函数调用)`);
        try {
          return await agentSdkRunner({
            ...params,
            prompt,
            systemPrompt: systemPrompt ?? "",
            maxTokens,
            maxTurns,
            allowedTools,
          });
        } catch (err) {
          const msg = err instanceof Error ? err.message : String(err);
          console.error(`[clawmind:mcp] Agent SDK runner exception: ${msg.slice(0, 300)}`);
          return {
            error: `Agent SDK exception: ${msg.slice(0, 200)}`,
            meta: { agentSdkException: msg.slice(0, 300) },
          };
        }
      }
    }

    // ── Path 3: No TeClaw/SDK — standalone sampling (stdio mode only) ──
    // This path is only reached when there's no TeClaw WS or Agent SDK
    // configured, or when the node is single-turn only.
    // It is NOT a degradation from TeClaw/SDK failure — it's the primary
    // path for local stdio mode where MCP transport persists.
    console.error(`[clawmind:mcp] Agent runner: Path 3 — fallback sampling (teclawProvider=NO agentSdkRunner=NO)`);
    if (fallbackSamplingAgent) {
      console.error(`[clawmind:mcp] WARNING: Using sampling fallback — this will LIKELY HANG in async MCP mode (transport disconnected after HTTP response). Ensure ANTHROPIC_API_KEY is set for Agent SDK path.`);
      return fallbackSamplingAgent(params);
    }

    return {
      error: "No agent loop capability available. Configure TECLAW_WS_URL for TeClaw WebSocket or set ANTHROPIC_API_KEY for Agent SDK.",
      meta: { hasTeClawProvider: false, hasAgentSdkRunner: !!agentSdkRunner, hasFallbackSamplingAgent: false },
    };
  };
}

/**
 * Select the embedded-agent runner for one MCP request without importing the
 * server entry point. TeClaw remains the sole path when its request-scoped
 * provider exists; progress forwarding is attached only to that path.
 */
export function createPerSessionEmbeddedAgentFn(
  options: PerSessionEmbeddedAgentFnOptions,
): EmbeddedAgentFn | undefined {
  if (options.teclawProvider) {
    return getMcpAgentRunner(
      { server: {} },
      {
        teclawProvider: options.teclawProvider,
        fallbackSamplingAgent: options.samplingAgent,
        systemPromptPrefix: "You are a workflow step executor in the ClawMind workflow engine.",
        onTeClawProgress: options.chatInject
          ? createTeClawProgressForwarder(options.chatInject)
          : undefined,
      },
    );
  }

  return options.globalAgentRunner ?? options.samplingAgent;
}
