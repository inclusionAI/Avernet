/**
 * MCP Server Adapter — wraps an MCP Server context into a PlatformAdapter.
 *
 * Used when ClawMind runs as an MCP server (Claude Code, Hermes, etc.)
 * instead of as an OpenClaw plugin. This adapter:
 * - Uses DatabaseTaskFlowAdapter for flow state (API-backed or in-memory)
 * - Uses console.log or MCP notifications for chatInject
 * - Uses MCP sampling for embedded-agent execution (no runEmbeddedPiAgent)
 * - Uses child_process.spawn for command running (no OpenClaw sandbox)
 *
 * @module platform/mcp-adapter
 */

import type { PlatformAdapter, TaskFlowAdapter, ChatInjectAdapter, SessionAdapter, ProgressAdapter, AbortAdapter, CommandRunner, CapabilityMatrix } from "./types.js";
import { PLATFORM_CAPABILITIES } from "./types.js";
import { DatabaseTaskFlowAdapter } from "./database-taskflow.js";
import { createDualChannelChatInject } from "./mcp-chat-inject.js";
import type { FlowRunApiRepository } from "../db/api-repositories/flow-run-api-repository.js";
import { TeClawProvider, createTeClawProviderFromEnv, createTeClawProviderFromConfig } from "./teclaw-provider.js";
import type { TeClawConfig } from "../config/types.js";

/**
 * Result shape matching OpenClaw's runEmbeddedPiAgent return type.
 * Used by embedded-agent, bcs-route, and bcs-approval-batch executors.
 */
export interface EmbeddedAgentResult {
  output?: string;
  error?: string;
  payloads?: Array<{ text?: string; isError?: boolean; isReasoning?: boolean }>;
  messagingToolSentTexts?: string[];
  meta?: Record<string, unknown>;
}

/** Options for creating an MCP Server adapter. */
export interface McpServerAdapterOptions {
  /** API-backed repository for flow_runs persistence. When provided, state is persisted via clawweb API. */
  flowRunApiRepo?: FlowRunApiRepository;
  /** Session key identifying this conversation. */
  sessionKey: string;
  /** Optional session ID. */
  sessionId?: string;
  /** User identity resolved from MCP request context. */
  user?: { id?: string; name?: string };
  /** Skill root directory. Defaults to current working directory. */
  skillRoot?: string;
  /** Delivery context (typically empty in MCP mode). */
  deliveryContext?: Record<string, unknown>;
  /** Progress callback forwarded from the controller. */
  onProgress?: (text: string, details?: Record<string, unknown>) => void;
  /** Abort signal for cancelling the workflow run. */
  abortSignal?: AbortSignal;
  /**
   * Chat inject callback. In MCP mode, messages are typically returned
   * as part of the tool response rather than injected into a chat stream.
   * Defaults to console.log if not provided.
   */
  chatInjectFn?: (message: string, idempotencyKey: string) => Promise<void>;
  /**
   * Embedded agent callback — replaces OpenClaw's runEmbeddedPiAgent.
   * In MCP mode, this uses the MCP sampling/createMessage protocol to
   * ask the host LLM to execute a sub-task.
   */
  embeddedAgentFn?: (params: Record<string, unknown>) => Promise<EmbeddedAgentResult>;
  /**
   * Command runner function — replaces OpenClaw's runPluginCommandWithTimeout.
   * Defaults to child_process.spawn if not provided.
   */
  commandRunnerFn?: CommandRunner;
  /**
   * MCP server instance for sending progress notifications.
   * When provided, chatInject will send `notifications/message` to the
   * connected MCP client, enabling real-time progress feedback.
   * Accepts the McpServer from @modelcontextprotocol/sdk.
   */
  mcpServer?: { server: { notification: (params: unknown) => Promise<void> } };
  /**
   * TeClaw chat/inject HTTP endpoint URL.
   * When provided alongside chatInjectKey, enables HTTP push for approvals
   * and backup delivery for progress/error messages.
   * Set via TECLAW_CHAT_INJECT_URL env var (injected by TeClaw on spawn).
   * @deprecated Prefer WebSocket chat.inject via TECLAW_WS_URL/TECLAW_WS_TOKEN.
   */
  chatInjectUrl?: string;
  /**
   * API key for chat/inject HTTP endpoint authentication.
   * Set via TECLAW_CHAT_INJECT_KEY env var (injected by TeClaw on spawn).
   * @deprecated Prefer TECLAW_WS_TOKEN for WebSocket authentication.
   */
  chatInjectKey?: string;
  /**
   * TeClaw Server base URL for Channel 2 (Agent Loop + chatInject).
   * Set via TECLAW_BASE_URL env var (injected by TeClaw on connect).
   * When set, TeClawProvider is created and used for embeddedAgentFn.
   * @deprecated Prefer TECLAW_WS_URL for WebSocket Channel 2.
   */
  teclawBaseUrl?: string;
  /**
   * TeClaw WebSocket URL for Channel 2 (/ws/v1/chat).
   * Set via TECLAW_WS_URL env var. When set alongside teclawWsToken,
   * TeClawProvider connects via WebSocket for Agent Loop, chat.inject,
   * abort, and approval resolution.
   */
  teclawWsUrl?: string;
  /**
   * MCP Token for TeClaw WebSocket authentication.
   * Set via TECLAW_WS_TOKEN env var.
   */
  teclawWsToken?: string;
  /**
   * Additional HTTP headers for TeClaw WebSocket handshake.
   * Set via TECLAW_WS_HEADERS env var (JSON string, e.g., '{"x-andc-target-service":"tautie"}').
   */
  teclawWsHeaders?: Record<string, string>;
  /**
   * TeClaw config from application.yaml. When provided, takes priority over
   * individual teclaw* fields and env vars for creating TeClawProvider.
   */
  teclawConfig?: TeClawConfig;
  /**
   * TeClaw conversation的真实 session_key，来自 x-teclaw-session-key HTTP header。
   * 由 TeClaw 的 inject_tfe_headers 注入（仅 enterprise 模式）。
   * 优先于 sessionKey 用于 TeClawProvider 的 chat.inject / chat.send 消息路由。
   */
  teclawSessionKey?: string;
  /**
   * TeClaw bot 的 user_id，来自 x-teclaw-bot-id HTTP header。
   * 由 TeClaw 的 inject_tfe_headers 注入（仅 enterprise 模式）。
   * 当 user 未提供时，作为 SessionAdapter.user.id 的回退值。
   */
  teclawBotId?: string;
}

// ── Default implementations ──

/** Default chatInject: log to stderr in MCP mode. */
async function defaultMcpChatInject(message: string, _idempotencyKey: string): Promise<void> {
  // In MCP mode, chat messages are part of the tool response.
  // Logging to stderr so it doesn't interfere with MCP stdio protocol.
  console.error("[clawmind:mcp] chatInject:", message.slice(0, 200));
}


/** Default embeddedAgentFn: returns an error indicating sampling is unavailable. */
async function unsupportedEmbeddedAgent(_params: Record<string, unknown>): Promise<EmbeddedAgentResult> {
  return {
    error: "embedded-agent requires MCP sampling support. Configure the MCP server with sampling capabilities or provide a custom embeddedAgentFn.",
  };
}

/** Default command runner using child_process.spawn. */
const defaultCommandRunner: CommandRunner = async (options) => {
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

// ── Factory ──

/** Return type of createMcpServerAdapter — adapter plus per-session TeClaw context. */
export interface McpServerAdapterResult {
  /** The PlatformAdapter for workflow execution. */
  adapter: PlatformAdapter;
  /**
   * Per-session TeClawProvider with correct x-target-bot-id header for WS handshake.
   * When present, use this (NOT the global provider) for agentRunner/embeddedAgentFn
   * to ensure chat.send is routed to the correct bot session.
   * Undefined when TeClaw WS is not configured.
   */
  wsTeClawProvider?: TeClawProvider;
}

/**
 * Create a PlatformAdapter backed by an MCP Server context.
 *
 * This adapter is used when ClawMind runs as a standalone MCP server
 * (e.g., for Claude Code or Hermes), without the OpenClaw Plugin SDK.
 *
 * Returns both the adapter and the per-session TeClawProvider (if configured).
 * The per-session provider has the correct x-target-bot-id header from the
 * current request, making it suitable for runAgentLoop / chat.send.
 * Use it to create a per-session agentRunner that routes to the right bot.
 */
export function createMcpServerAdapter(options: McpServerAdapterOptions): McpServerAdapterResult {
  const { sessionKey, sessionId, skillRoot, deliveryContext, onProgress, abortSignal } = options;

  // ── TeClaw sessionKey for WS routing ──
  // TeClaw injects the real conversation session_key via x-teclaw-session-key
  // header. This is the authoritative value for TeClawProvider's chat.inject /
  // chat.send WS routing. When absent (e.g., stdio transport, non-enterprise
  // TeClaw), fall back to the caller-provided sessionKey.
  //
  // IMPORTANT: This is NOT used for TaskFlow/SessionAdapter.sessionKey.
  // TaskFlow always uses `options.sessionKey` (which may be tenant-namespaced
  // by HermesAdapter). The TeClaw WS sessionKey must NOT bypass namespacing —
  // it only tells TeClawProvider which WS conversation to route messages to.
  const teclawWsSessionKey = options.teclawSessionKey ?? options.sessionKey;
  const effectiveUser = options.user ?? (options.teclawBotId ? { id: options.teclawBotId } : undefined);

  // Log TeClaw context for debugging — these headers are critical for
  // WS chat.inject routing (x-teclaw-session-key → sessionKey param,
  // x-teclaw-bot-id → x-target-bot-id WS handshake header).
  console.error(`[clawmind:mcp] adapterFactory: transport=stdio session=${sessionKey.slice(0, 50)}`);
  if (options.teclawBotId || options.teclawSessionKey) {
    console.error(`[clawmind:mcp] createMcpServerAdapter: TeClaw context teclawBotId=${options.teclawBotId ?? "none"} teclawSessionKey=${options.teclawSessionKey ?? "none"} → wsSessionKey=${teclawWsSessionKey.slice(0, 50)}`);
  } else {
    console.error(`[clawmind:mcp] createMcpServerAdapter: TeClaw context: none (falling back to sessionKey=${options.sessionKey.slice(0, 50)})`);
  }

  // ── TaskFlow: API-backed (preferred) or in-memory adapter ──
  const taskFlow: TaskFlowAdapter = new DatabaseTaskFlowAdapter({
    sessionKey,
    flowRunApiRepo: options.flowRunApiRepo,
  });

  // ── ChatInject: dual-channel (notification + WS chat.inject or HTTP) per RFC-003 §6 ──
  // Build TeClawProvider for WS chat.inject (preferred over HTTP)
  // Priority: teclawConfig > direct WS options > env vars
  //
  // TeClaw WS handshake requires 3 mandatory HTTP headers (per test_adapter_ws.py):
  //   x-andc-target-service — routing identifier (config: wsHeaders)
  //   x-target-bot-id       — session tenant key (from per-request x-teclaw-bot-id)
  //   x-tracer-traceid      — trace ID (auto-generated)
  // Missing any of these → HTTP 400 / non-101 → WS connection fails.
  // ── Resolve x-target-bot-id for WS handshake ──
  // TeClaw WS handshake requires 3 mandatory HTTP headers (per test_adapter_ws.py):
  //   x-andc-target-service — routing identifier (config: wsHeaders)
  //   x-target-bot-id       — session tenant key (from per-request x-teclaw-bot-id)
  //   x-tracer-traceid      — trace ID (auto-generated)
  // Missing any of these → HTTP 400 / non-101 → WS connection fails.
  //
  // Priority for x-target-bot-id:
  //   1. Explicit x-teclaw-bot-id header (options.teclawBotId)
  //   2. Extracted from teclawSessionKey format: session:<conv_id>:user:<bot_id>
  //      e.g., "session:c_01KW1N8TVV7GT1CDPVNBS4AT58:user:346131" → bot_id = "346131"
  let resolvedBotId = options.teclawBotId;
  if (!resolvedBotId && teclawWsSessionKey && teclawWsSessionKey.includes(":user:")) {
    // Extract bot_id from session key: "...:user:<bot_id>"
    const userIdx = teclawWsSessionKey.lastIndexOf(":user:");
    if (userIdx >= 0) {
      resolvedBotId = teclawWsSessionKey.substring(userIdx + ":user:".length);
    }
  }
  const teclawRequiredHeaders: Record<string, string> = {
    "x-tracer-traceid": `clawmind-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  };
  if (resolvedBotId) {
    teclawRequiredHeaders["x-target-bot-id"] = resolvedBotId;
    console.error(`[clawmind:mcp] WS handshake: injecting x-target-bot-id=${resolvedBotId}` +
      (resolvedBotId === options.teclawBotId ? " (from x-teclaw-bot-id header)" : ` (extracted from sessionKey: ...user:${resolvedBotId})`));
  } else {
    console.error(`[clawmind:mcp] WS handshake: WARNING — x-target-bot-id NOT set (neither x-teclaw-bot-id header nor extractable from sessionKey, teclaw WS will likely return 400)`);
  }

  let wsTeClawProvider: TeClawProvider | undefined;
  if (options.teclawConfig?.enabled) {
    // Merge config wsHeaders with required headers (required take precedence)
    const mergedHeaders: Record<string, string> = {
      ...(options.teclawConfig.wsHeaders ?? {}),
      ...teclawRequiredHeaders,
    };
    // CRITICAL: pass teclawWsSessionKey so TeClawProvider uses a valid teclaw
    // session_key (from x-teclaw-session-key) instead of the default
    // 'clawmind-${Date.now()}' which is NOT a valid teclaw session.
    wsTeClawProvider = createTeClawProviderFromConfig(
      {
        ...options.teclawConfig,
        wsHeaders: mergedHeaders,
      },
      teclawWsSessionKey,
    ) ?? undefined;
  }
  if (!wsTeClawProvider && options.teclawWsUrl && options.teclawWsToken && options.teclawConfig?.enabled !== false) {
    try {
      const mergedHeaders: Record<string, string> = {
        ...(options.teclawWsHeaders ?? {}),
        ...teclawRequiredHeaders,
      };
      wsTeClawProvider = new TeClawProvider({
        wsUrl: options.teclawWsUrl,
        token: options.teclawWsToken,
        headers: mergedHeaders,
        sessionKey: teclawWsSessionKey,
      });
    } catch (err) {
      console.warn(`[clawmind:mcp] Failed to create TeClaw WS provider: ${err instanceof Error ? err.message : String(err)}`);
    }
  }
  // Fall back to env-based provider if neither config nor direct options provided
  if (!wsTeClawProvider) {
    const envProvider = createTeClawProviderFromEnv();
    if (envProvider) {
      // Inject per-session context into env-based provider
      if (teclawWsSessionKey) {
        envProvider.setSessionKey(teclawWsSessionKey);
      }
      // Inject x-target-bot-id if resolved (headers is Record<string,string>, mutable despite readonly)
      if (resolvedBotId && !envProvider.headers["x-target-bot-id"]) {
        envProvider.headers["x-target-bot-id"] = resolvedBotId;
        if (!envProvider.headers["x-tracer-traceid"]) {
          envProvider.headers["x-tracer-traceid"] = `clawmind-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
        }
        console.error(`[clawmind:mcp] Injected x-target-bot-id=${resolvedBotId} into env-based TeClawProvider`);
      }
    }
    wsTeClawProvider = envProvider ?? undefined;
  }

  const chatInject: ChatInjectAdapter = options.chatInjectFn
    ? { inject: options.chatInjectFn }
    : createDualChannelChatInject(
        options.mcpServer as Parameters<typeof createDualChannelChatInject>[0],
        {
          chatInjectUrl: options.chatInjectUrl,
          chatInjectKey: options.chatInjectKey,
          teclawProvider: wsTeClawProvider,
          teclawSessionKey: teclawWsSessionKey,
        },
      );

  // ── Session: from MCP request metadata ──
  const session: SessionAdapter = {
    sessionKey,
    sessionId,
    skillRoot: skillRoot ?? process.cwd(),
    user: effectiveUser,
    deliveryContext,
  };

  // ── Progress: forward callback ──
  const progress: ProgressAdapter = {
    onProgress,
  };

  // ── Abort: forward signal ──
  const abort: AbortAdapter = {
    signal: abortSignal,
  };

  return {
    adapter: {
      platform: "mcp-server",
      taskFlow,
      chatInject,
      session,
      progress,
      abort,
      capabilities: PLATFORM_CAPABILITIES["mcp-server"],
      transportMode: "stdio",
    },
    wsTeClawProvider,
  };
}

/**
 * Get the embedded agent function from the MCP adapter options.
 * This is used by executors that need runEmbeddedPiAgent.
 *
 * NOTE: This is NOT part of the PlatformAdapter interface because
 * embedded-agent execution is per-invocation context, not per-platform.
 * The MCP entry point passes this to the executor dispatch separately.
 */
export function getMcpEmbeddedAgentFn(options: McpServerAdapterOptions): (params: Record<string, unknown>) => Promise<EmbeddedAgentResult> {
  return options.embeddedAgentFn ?? unsupportedEmbeddedAgent;
}

/**
 * Get the command runner function from the MCP adapter options.
 * Falls back to child_process.spawn if not provided.
 */
export function getMcpCommandRunner(options: McpServerAdapterOptions): CommandRunner {
  return options.commandRunnerFn ?? defaultCommandRunner;
}