/**
 * Platform Adapter Types — Abstract interface for ClawMind's platform dependencies.
 *
 * The PlatformAdapter encapsulates the platform-specific APIs that ClawMind
 * depends on (OpenClaw Plugin SDK, MCP Server, CLI, etc.) so that the core
 * engine (Runner + Controller) remains platform-agnostic.
 *
 * Phase 1 only introduces the types and the OpenClaw adapter.
 * Future phases will add McpServerAdapter and HermesAdapter.
 *
 * @module platform/types
 */

import type { ControllerDeps } from "../controller.js";

// ── TaskFlow Adapter ──

/**
 * Platform-agnostic adapter for workflow state persistence.
 *
 * Extracted directly from ControllerDeps["boundTaskFlow"] to guarantee
 * type compatibility without `as unknown as` casts.
 */
export type TaskFlowAdapter = ControllerDeps["boundTaskFlow"];

// ── ChatInject Adapter ──

/** Message types for chatInject, controlling delivery channel and guarantee. */
export enum ChatInjectMessageType {
  /** Progress notification — best-effort via notification + HTTP dual-send. */
  Progress = "progress",
  /** Informational notification — best-effort via notification + HTTP dual-send. */
  Info = "info",
  /** Error alert — best-effort with highlighting via notification + HTTP dual-send. */
  Error = "error",
  /** Approval request — must-deliver via HTTP chat/inject only. */
  Approval = "approval",
}

/** Action button for approval-type chatInject messages. */
export interface ChatInjectAction {
  label: string;
  action: string;
  style?: "primary" | "danger" | "default";
}

/** Options for structured chatInject messages (RFC-003 §6). */
export interface ChatInjectOptions {
  /** Message type — controls delivery channel. */
  messageType: ChatInjectMessageType;
  /** Flow ID for context. */
  flowId?: string;
  /** Node ID for context. */
  nodeId?: string;
  /** Workflow ID for context. */
  workflowId?: string;
  /** Action buttons (approval type only). */
  actions?: ChatInjectAction[];
  /** Additional metadata. */
  metadata?: Record<string, unknown>;
  /** ISO 8601 timestamp. */
  timestamp?: string;
}

/** Platform-agnostic adapter for injecting messages into the conversation. */
export interface ChatInjectAdapter {
  /**
   * Inject a message into the conversation.
   *
   * Backwards-compatible: the two-arg form uses default messageType=progress.
   * The three-arg form enables structured messages per RFC-003 §6.
   */
  inject(message: string, idempotencyKey: string, options?: ChatInjectOptions): Promise<void>;
}

// ── Session Adapter ──

/** Platform-agnostic session context. */
export interface SessionAdapter {
  sessionKey: string;
  sessionId?: string;
  user?: { id?: string; name?: string };
  skillRoot: string;
  /** OpenClaw-specific: delivery context for runtime user resolution and execution mode detection. */
  deliveryContext?: Record<string, unknown>;
}

// ── Progress Adapter ──

/** Platform-agnostic progress notification. */
export interface ProgressAdapter {
  onProgress?: (text: string, details?: Record<string, unknown>) => void;
}

// ── Abort Adapter ──

/** Platform-agnostic abort control. */
export interface AbortAdapter {
  signal?: AbortSignal;
  onRequestStop?: () => void | Promise<void>;
}

// ── Command Runner ──
// Re-export from existing command-runner module to avoid duplication.

export type { CommandRunner, CommandRunOptions, CommandRunResult } from "../command-runner.js";

// ── Platform Adapter (main interface) ──

/** Supported platform types. */
export type PlatformType = "openclaw" | "mcp-server" | "hermes" | "cli";

/**
 * Engine name — a more specific identifier than PlatformType.
 *
 * While PlatformType groups TeClaw and Claude Code under "mcp-server",
 * EngineName distinguishes them for observability (e.g. flow_runs.engine).
 *
 * Priority: CLAWMIND_ENGINE env var > config `engine` field > auto-detection.
 */
export type EngineName = "openclaw" | "claudecode" | "teclaw" | "hermes" | "cli";

/**
 * PlatformAdapter — abstracts platform-specific dependencies.
 *
 * The adapter wraps the *construction* of ControllerDeps fields, not the
 * full ControllerDeps itself. Runtime context (actionRegistry, executeNode,
 * resolvedWorkflows, etc.) is provided separately via AdapterDepsExtras.
 */
export interface PlatformAdapter {
  readonly platform: PlatformType;
  readonly taskFlow: TaskFlowAdapter;
  readonly chatInject: ChatInjectAdapter;
  readonly session: SessionAdapter;
  readonly progress: ProgressAdapter;
  readonly abort: AbortAdapter;
  readonly capabilities: CapabilityMatrix;
  /** Transport mode — "stdio" (MCP local) or "http-sse" (Hermes SSE). Undefined for OpenClaw. */
  readonly transportMode?: "stdio" | "http-sse";
}

// ── Hermes Adapter Options ──

/** Options specific to the Hermes adapter, extending MCP Server options. */
export interface HermesAdapterOptions {
  /** SSE response callback — sends events to the connected browser client. */
  sseSend?: (event: string, data: unknown) => void;
  /** Approval UI callback — triggers a confirmation dialog in the Hermes console. */
  approvalRequest?: (flowId: string, nodeId: string, message: string) => Promise<{ approved: boolean; note?: string }>;
  /** Tenant ID for multi-tenant session isolation. */
  tenantId?: string;
  /** Team ID — mapped to sessionKey namespace when provided. */
  teamId?: string;
}

/** Extended chatInject for platforms supporting server-push (SSE, WebSocket). */
export interface ChatInjectSSE extends ChatInjectAdapter {
  /** Push a message to the client via SSE/WebSocket. */
  pushEvent?(event: string, data: unknown): void;
}

// ── Capability Matrix ──

/** Declared capabilities for a platform. Enables the Controller to skip unsupported features at startup. */
export interface CapabilityMatrix {
  /** embedded-agent execution via embeddedAgentFn or MCP sampling. */
  embeddedAgent: boolean;
  /** scheduling (cron) — requires CronScheduler, not available on MCP platforms. */
  scheduling: boolean;
  /** webhook triggers — requires HTTP listener or clawweb proxy. */
  webhook: boolean;
  /** human-wait approval UI — platform-specific (DingTalk card, Hermes console, Claude tool call). */
  approvalUI: boolean;
  /** command execution — sandbox (OpenClaw) vs child_process (MCP) vs container. */
  commandExecution: "sandbox" | "child_process" | "container" | "none";
  /** real-time progress push — SSE event (Hermes) vs MCP notification (Claude/TeClaw) vs event (OpenClaw). */
  progressPush: boolean;
  /** event hooks (before_agent_reply) — only OpenClaw supports natively. */
  eventHooks: boolean;
}

/** Pre-defined capability matrices per platform. */
export const PLATFORM_CAPABILITIES: Record<PlatformType, CapabilityMatrix> = {
  openclaw: {
    embeddedAgent: true,
    scheduling: true,
    webhook: true,
    approvalUI: true,
    commandExecution: "sandbox",
    progressPush: true,
    eventHooks: true,
  },
  "mcp-server": {
    embeddedAgent: true, // via TeClaw WebSocket (full multi-turn) or sampling/createMessage (single-turn fallback)
    scheduling: false,
    webhook: false,
    approvalUI: true, // via workflow_confirm tool call
    commandExecution: "child_process",
    progressPush: true, // via MCP notification
    eventHooks: false,
  },
  hermes: {
    embeddedAgent: true, // via TeClaw WebSocket (full multi-turn) or sampling/createMessage (single-turn fallback)
    scheduling: false, // external trigger
    webhook: false, // clawweb proxy
    approvalUI: true, // native Hermes console
    commandExecution: "child_process", // TODO: container in Phase 4
    progressPush: true, // via SSE event
    eventHooks: false,
  },
  cli: {
    embeddedAgent: false,
    scheduling: false,
    webhook: false,
    approvalUI: false,
    commandExecution: "child_process",
    progressPush: false,
    eventHooks: false,
  },
};

// ── Engine Name Resolution ──

/**
 * Resolve the engine name from the current runtime context.
 *
 * Priority:
 *   1. CLAWMIND_ENGINE env var (explicit override)
 *   2. Config `engine` field (from application.yaml)
 *   3. Auto-detection from PlatformType + environment
 *
 * Auto-detection logic:
 *   - openclaw → "openclaw"
 *   - hermes   → "hermes"
 *   - cli      → "cli"
 *   - mcp-server + TECLAW_ENABLED=true → "teclaw"
 *   - mcp-server + CLAUDE_CODE_EXECUTABLE set → "claudecode"
 *   - mcp-server (fallback) → "claudecode"
 */
export function resolveEngineName(
  platform: PlatformType,
  options?: {
    /** Explicit engine from config (application.yaml `engine` field). */
    configEngine?: string;
    /** TECLAW_ENABLED env var value. */
    teclawEnabled?: boolean;
    /** CLAUDE_CODE_EXECUTABLE env var presence. */
    hasClaudeCodeExecutable?: boolean;
  },
): EngineName {
  // 1. Env var override (highest priority)
  const envEngine = process.env.CLAWMIND_ENGINE;
  if (envEngine) {
    const valid: EngineName[] = ["openclaw", "claudecode", "teclaw", "hermes", "cli"];
    if (valid.includes(envEngine as EngineName)) {
      return envEngine as EngineName;
    }
    console.warn(`[clawmind] CLAWMIND_ENGINE="${envEngine}" is not a valid EngineName. Valid values: ${valid.join(", ")}. Falling back to auto-detection.`);
  }

  // 2. Config override
  if (options?.configEngine) {
    const valid: EngineName[] = ["openclaw", "claudecode", "teclaw", "hermes", "cli"];
    if (valid.includes(options.configEngine as EngineName)) {
      return options.configEngine as EngineName;
    }
    console.warn(`[clawmind] config engine="${options.configEngine}" is not a valid EngineName. Valid values: ${valid.join(", ")}. Falling back to auto-detection.`);
  }

  // 3. Auto-detection from platform type + env
  switch (platform) {
    case "openclaw":
      return "openclaw";
    case "hermes":
      return "hermes";
    case "cli":
      return "cli";
    case "mcp-server": {
      // TeClaw takes priority when explicitly enabled
      const teclawEnabled = options?.teclawEnabled ?? (process.env.TECLAW_ENABLED === "true");
      if (teclawEnabled) {
        return "teclaw";
      }
      // Claude Code detected by CLAUDE_CODE_EXECUTABLE env var
      const hasClaudeCode = options?.hasClaudeCodeExecutable ?? !!process.env.CLAUDE_CODE_EXECUTABLE;
      if (hasClaudeCode) {
        return "claudecode";
      }
      // Fallback: when teclaw is not enabled and no Claude Code, still default to claudecode
      // (this covers local dev / unknown MCP host scenarios)
      return "claudecode";
    }
    default:
      return "claudecode";
  }
}