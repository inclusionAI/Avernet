/**
 * Shared MCP tool registrations — used by both mcp-entry.ts and hermes-entry.ts.
 *
 * This module extracts the 8+1 workflow tool definitions so that both the stdio
 * entry point (mcp-entry.ts) and the SSE entry point (hermes-entry.ts) can
 * register the same tools with different adapter factories.
 *
 * @module platform/mcp-tools
 */

import type { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { z } from "zod";
import { createDefaultExecutorDispatch } from "./default-executor.js";
import { buildControllerDeps } from "./adapter-to-deps.js";
import { ExecutionStepLogRepository } from "../db/repositories/execution-step-log-repository.js";
import { ExecutionStepLogger } from "../execution-log/logger.js";
import { DynamicWorkflowEventEmitter } from "../observability/emitter.js";
import { executeAction } from "../dispatch.js";
import { parseWorkflowCommandWithFacade, tokenizeCommand } from "../command-parser.js";
import type { PlatformAdapter } from "./types.js";
import type { EmbeddedAgentResult } from "./mcp-adapter.js";
import type { ControllerAction, WorkflowCommandSurface } from "../types.js";
import type { ActionRegistry } from "../actions/types.js";
import type { FlowRunApiRepository } from "../db/api-repositories/flow-run-api-repository.js";
import type { IDatabase } from "../db/types.js";
import type { WorkflowSpecApiRepository } from "../db/api-repositories/workflow-spec-api-repository.js";
import type { CommandRunner } from "../command-runner.js";
import type { WorkflowPackCatalog } from "../packs/types.js";
import type { FacadeRegistry, ResolvedWorkflowFacade } from "../facades/registry.js";
import { formatWorkflowCommand as formatFacadeWorkflowCommand } from "../facades/registry.js";
import { loadBotId, loadOwnerId } from "../credentials.js";
import { workflowRegistryFromResolved } from "../packs/resolver.js";
import { loadConfig } from "../config/loader.js";
import { getWorkflowEventBuffer } from "./workflow-event-buffer.js";

// ── Types ──

/**
 * Context passed to the adapter factory for each tool invocation.
 * Both stdio and SSE entry points construct this from request metadata.
 */
export interface AdapterContext {
  /** Session key for flow persistence. */
  sessionKey: string;
  /** Optional session ID. */
  sessionId?: string;
  /** User identity resolved from request context. */
  user?: { id?: string; name?: string };
  /** Abort signal for cancelling the workflow run. */
  abortSignal?: AbortSignal;
  /**
   * MCP server instance for sending progress notifications.
   * When provided, chatInject will send `notifications/message` to the
   * connected MCP client.
   */
  mcpServer?: { server: { notification: (params: unknown) => Promise<void> } };
  /**
   * TeClaw conversation的真实 session_key，来自 x-teclaw-session-key HTTP header。
   * 由 TeClaw 的 inject_tfe_headers 注入（仅 enterprise 模式）。
   * 优先于 args.sessionKey 用于 chat.inject / chat.send 消息路由。
   */
  teclawSessionKey?: string;
  /**
   * TeClaw bot 的 user_id，来自 x-teclaw-bot-id HTTP header。
   * 由 TeClaw 的 inject_tfe_headers 注入（仅 enterprise 模式）。
   * 用于工作流执行的用户上下文。
   */
  teclawBotId?: string;
}

/**
 * Extended adapter context for Hermes (SSE) platform.
 *
 * Adds multi-tenant isolation, SSE push events, and approval UI callbacks
 * that are only available when ClawMind runs behind the Hermes console.
 * The stdio entry point (Claude Code) uses plain AdapterContext.
 */
export interface HermesContext extends AdapterContext {
  /** Tenant ID for multi-tenant session isolation. Namespaced as `tenantId:sessionKey`. */
  tenantId?: string;
  /** Team ID — mapped to sessionKey namespace when tenantId is absent. */
  teamId?: string;
  /** SSE callback — pushes events to the connected browser client. */
  sseSend?: (event: string, data: unknown) => void;
  /** Approval UI callback — triggers a confirmation dialog in the Hermes console. */
  approvalRequest?: (flowId: string, nodeId: string, message: string) => Promise<{ approved: boolean; note?: string }>;
}

/**
 * Factory function: creates a PlatformAdapter + optional embeddedAgentFn
 * for a single tool invocation.
 *
 * The stdio entry uses `createMcpServerAdapter`; the SSE entry uses
 * `createHermesAdapter`. Both conform to the same shape.
 */
/**
 * Factory function: creates a PlatformAdapter + optional embeddedAgentFn
 * for a single tool invocation.
 *
 * The stdio entry uses `createMcpServerAdapter`; the SSE entry uses
 * `createHermesAdapter`. Both conform to the same shape.
 *
 * @typeParam C - Adapter context type. Defaults to AdapterContext (stdio).
 *   Use HermesContext for SSE/Hermes entry points.
 */
export type AdapterFactory<C extends AdapterContext = AdapterContext> = (context: C) => {
  adapter: PlatformAdapter;
  embeddedAgentFn?: (params: Record<string, unknown>) => Promise<EmbeddedAgentResult>;
};

/**
 * Shared dependencies needed by all tool registrations.
 * These are initialized once at server startup and passed to
 * `registerWorkflowTools`.
 */
export interface WorkflowToolDeps {
  /** Action registry for action-node execution. */
  actionRegistry: ActionRegistry;
  /** Resolved workflow pack catalog (may be undefined if packs fail to load). */
  workflowCatalog?: WorkflowPackCatalog;
  /** Facade registry for resolving slash commands (e.g. /marketing-flow-dispatch). */
  facadeRegistry?: FacadeRegistry;
  /** API-backed repository for flow_runs persistence. */
  flowRunApiRepo?: FlowRunApiRepository;
  /** MCP sampling agent function (from getMcpSamplingAgent). */
  samplingAgent?: (params: Record<string, unknown>) => Promise<EmbeddedAgentResult>;
  /** MCP agent runner function (from getMcpAgentRunner). Preferred over samplingAgent. */
  agentRunner?: (params: Record<string, unknown>) => Promise<EmbeddedAgentResult>;
  /** Database instance for DB-first facade and workflow spec resolution. */
  db?: IDatabase;
  /** API-backed workflow spec repository (api database mode). */
  workflowSpecApiRepo?: WorkflowSpecApiRepository;
}

// ── MCP SDK tool handler extra parameter ──

// ── TeClaw Session Context Registry ──
//
// The MCP SDK may not pass custom HTTP headers (x-teclaw-bot-id,
// x-teclaw-session-key) through to `extra.requestInfo.headers`. As a fallback,
// the HTTP transport middleware (mcp-entry.ts) captures these headers from the
// raw Express request and stores them here, keyed by MCP session ID.
// `extractTeClawHeaders()` checks this registry when SDK headers are missing.

/** Per-session TeClaw context captured from HTTP headers. */
export interface TeClawSessionContext {
  teclawBotId?: string;
  teclawSessionKey?: string;
}

const teclawSessionRegistry = new Map<string, TeClawSessionContext>();

/** Store TeClaw context for an MCP session (called from mcp-entry.ts HTTP middleware). */
export function setTeClawSessionContext(sessionId: string, ctx: TeClawSessionContext): void {
  teclawSessionRegistry.set(sessionId, ctx);
}

/** Remove TeClaw context when an MCP session ends. */
export function deleteTeClawSessionContext(sessionId: string): void {
  teclawSessionRegistry.delete(sessionId);
}

/** Get TeClaw context for an MCP session. */
export function getTeClawSessionContext(sessionId: string): TeClawSessionContext | undefined {
  return teclawSessionRegistry.get(sessionId);
}

/**
 * Minimal type for the `extra` parameter received by MCP tool handlers.
 *
 * The MCP SDK passes `extra.requestInfo.headers` containing all original
 * HTTP headers from the transport request. TeClaw injects custom headers
 * (`x-teclaw-session-key`, `x-teclaw-bot-id`) that ClawMind reads from
 * this object.
 *
 * Only the fields we consume are declared here — the SDK may provide more.
 */
export interface ToolHandlerExtra {
  /** MCP session ID for the current connection. */
  sessionId?: string;
  /** Request metadata from the transport layer (headers, URL). */
  requestInfo?: {
    /** HTTP headers from the original request (all header names are lowercase). */
    headers?: Record<string, string | string[] | undefined>;
    /** Request URL. */
    url?: URL;
  };
}

/**
 * Extract TeClaw custom headers from the MCP SDK `extra.requestInfo.headers`.
 *
 * TeClaw's `inject_tfe_headers` (enterprise mode) adds these headers to
 * every MCP request targeting the workflow engine:
 * - `x-teclaw-session-key`: the real conversation session_key from TeClaw DB
 * - `x-teclaw-bot-id`: the bot's user_id (ctx.user_id)
 *
 * These headers are critical for routing `chat.inject` / `chat.send` to the
 * correct TeClaw session. Without them, ClawMind falls back to `args.sessionKey`
 * which may be missing or incorrect.
 *
 * @param extra - The `extra` parameter from MCP SDK tool handler
 * @returns Extracted header values, or empty object if unavailable
 */
export function extractTeClawHeaders(
  extra: ToolHandlerExtra | undefined,
): { teclawSessionKey?: string; teclawBotId?: string } {
  const headers = extra?.requestInfo?.headers;
  const sessionId = extra?.sessionId;

  // Try SDK headers first
  if (headers) {
    const sk = headers["x-teclaw-session-key"];
    const bid = headers["x-teclaw-bot-id"];

    // Log all teclaw-related headers for debugging (headers may be string or string[])
    const teclawRelatedHeaders = Object.entries(headers)
      .filter(([k]) => k.startsWith("x-teclaw-"))
      .map(([k, v]) => `${k}=${Array.isArray(v) ? v.join(",") : v}`);
    if (teclawRelatedHeaders.length > 0) {
      console.error(`[clawmind:mcp] extractTeClawHeaders: found in SDK headers: ${teclawRelatedHeaders.join(", ")}`);
    }

    // If we got values from SDK headers, use them
    const result = {
      teclawSessionKey: typeof sk === "string" ? sk : undefined,
      teclawBotId: typeof bid === "string" ? bid : undefined,
    };
    if (result.teclawSessionKey || result.teclawBotId) {
      console.error(`[clawmind:mcp] extractTeClawHeaders: from SDK headers → botId=${result.teclawBotId ?? "none"} sessionKey=${result.teclawSessionKey ?? "none"}`);
      return result;
    }
  } else {
    console.error("[clawmind:mcp] extractTeClawHeaders: no SDK request headers available");
  }

  // Fallback: check session registry (populated by HTTP middleware in mcp-entry.ts)
  if (sessionId) {
    const ctx = getTeClawSessionContext(sessionId);
    if (ctx && (ctx.teclawBotId || ctx.teclawSessionKey)) {
      console.error(`[clawmind:mcp] extractTeClawHeaders: from session registry (sessionId=${sessionId}) → botId=${ctx.teclawBotId ?? "none"} sessionKey=${ctx.teclawSessionKey ?? "none"}`);
      return {
        teclawSessionKey: ctx.teclawSessionKey,
        teclawBotId: ctx.teclawBotId,
      };
    }
  }

  console.error("[clawmind:mcp] extractTeClawHeaders: no x-teclaw-* headers found (non-enterprise teclaw or missing inject_tfe_headers)");
  return {};
}

// ── Tool schemas (Zod) ──

const WorkflowDispatchSchema = {
  command: z.string().describe(
    "ClawMind workflow command. First token is the verb, rest is arguments.\n" +
    "\n" +
    "📋 Definition — what workflows exist:\n" +
    "  list [filter]           List all workflow definitions (id, title, pack, nodes)\n" +
    "  detail <id>             Show workflow spec details (nodes, version, schema)\n" +
    "  packs                   List installed workflow packs\n" +
    "  validate <id>           Validate a workflow spec\n" +
    "\n" +
    "🚀 Execution — run & monitor:\n" +
    "  run <id> [msg]          Start a workflow execution\n" +
    "  runs [--limit N]        List recent run instances (running/completed/failed)\n" +
    "  runs cleanup            Clean up failed run records\n" +
    "  inspect <flowId>        Inspect a run (status, nodes, events; auto cross-session)\n" +
    "  inspect <flowId> --analyze  Deep analysis report (token, degradation, tool calls)\n" +
    "  inspect <flowId> --full     Raw JSON dump\n" +
    "  resume <flowId> <rev>   Resume a run from a revision\n" +
    "\n" +
    "📦 Version control — local ↔ DB:\n" +
    "  deploy <id> [--file]    Push local workflow to DB (local → remote)\n" +
    "  pull <id>               Pull workflow from DB (remote → local)\n" +
    "  rollback <id>           Rollback to a previous version\n" +
    "  install-pack <dir> [--only] [--force] [--move]  Install whole pack + deploy one workflow\n" +
    "  status [id] [--diff] [--git-diff]    Sync status and tracked files\n" +
    "    --diff:      Show YAML diff (DB vs local), lightweight, no git network I/O\n" +
    "    --git-diff:  Show git diff (remote branch vs local working tree), heavy, involves git fetch\n" +
    "                 Use --git-diff to compare scripts and other non-YAML files against remote\n" +
    "  deploys <id>            View deploy history (version, action, timestamp)\n" +
    "  share <id> --to         Authorize workflow to another bot\n" +
    "  unshare <id> --from     Revoke authorization\n" +
    "\n" +
    "✋ Human approval — at wait nodes:\n" +
    "  confirm [note]          Approve current node\n" +
    "  revise [note]           Send back for revision\n" +
    "  reject [note]           Reject the workflow\n" +
    "  retry                   Retry a failed node\n" +
    "  skip                    Skip current node\n" +
    "  submit                  Submit external input\n" +
    "  reopen <id>             Reopen a completed workflow\n" +
    "\n" +
    "Examples: 'run my-workflow --key value', 'runs --limit 20', 'inspect abc-123', 'deploy hello-world', 'deploys my-workflow'"
  ),
  sessionKey: z.string().optional().describe("Session key for flow persistence. Defaults to 'mcp-default'"),
  chatInjectLevel: z.enum(["perf", "simple", "full"]).optional().describe(
    "Per-run chatInject level override (highest priority). " +
    "Overrides workflow YAML chatInject.level and the global CLAWMIND_CHAT_INJECT_LEVEL/env/DB default."
  ),
};

const WorkflowInspectSchema = {
  flowId: z.string().describe("The flow ID to inspect"),
  analyze: z.boolean().optional().describe("Produce deep analysis report (token usage, degradation, tool call chain)"),
  full: z.boolean().optional().describe("Output raw JSON dump of state and wait data"),
  sessionKey: z.string().optional().describe("Session key"),
};

const WorkflowStateSchema = {
  flowId: z.string().describe("The flow ID to query"),
  sessionKey: z.string().optional().describe("Session key"),
};

const WorkflowRunsSchema = {
  limit: z.number().optional().describe("Maximum runs to return (default 10)"),
  sessionKey: z.string().optional().describe("Session key"),
};

const WorkflowConfirmSchema = {
  note: z.string().optional().describe("Optional confirmation note"),
  sessionKey: z.string().optional().describe("Session key"),
};

const WorkflowRejectSchema = {
  note: z.string().optional().describe("Optional rejection note"),
  sessionKey: z.string().optional().describe("Session key"),
};

const WorkflowRetrySchema = {
  flowId: z.string().describe("Flow ID to retry"),
  nodeId: z.string().optional().describe("Specific node ID to retry"),
  sessionKey: z.string().optional().describe("Session key"),
};

const WorkflowValidateSchema = {
  workflowId: z.string().describe("Workflow ID to validate"),
};

const WorkflowHelpSchema = {
  workflowId: z.string().optional().describe("Workflow ID to show help for"),
};

// ── Debug segment schema ──

const WorkflowDebugSegmentSchema = {
  workflowId: z.string().describe("目标工作流 ID"),
  fromNode: z.string().describe("起始节点 ID（从此节点开始执行）"),
  toNode: z.string().optional().describe("终止节点 ID（不传则跑到流程结尾；等于 fromNode 时只执行单节点）"),
  nodeOutput: z
    .record(z.string(), z.record(z.string(), z.unknown()))
    .describe("上游节点输出。key 为 nodeId，value 为该节点的 result 对象。模型从历史运行中构造。")
    .optional(),
  workflowData: z.record(z.string(), z.unknown()).optional().describe("workflowData 上下文。目标节点或下游读取 workflowData 时需提供。"),
  input: z.record(z.string(), z.unknown()).optional().describe("flow 级别输入参数。目标节点引用 {{input.xxx}} 时需提供。"),
  sessionKey: z.string().optional().describe("Session key"),
};

// ── Dev-workflow phase callback schema ──

const SubmitPhaseResultSchema = {
  workflowId: z.string().describe("研发工作流 ID"),
  phaseId: z.string().describe("阶段 ID"),
  status: z.enum(["success", "failed", "timeout"]).describe("阶段执行状态"),
  resultSummary: z.string().optional().describe("阶段执行结果摘要"),
  documentUrl: z.string().optional().describe("主要产出文档 URL（如 PR 链接）"),
  documentTitle: z.string().optional().describe("主要产出文档标题"),
  error: z.string().optional().describe("错误信息（status 非 success 时填写）"),
  baasRunId: z.string().optional().describe("BaaS 运行 ID"),
  gitOps: z.array(z.object({
    operation: z.enum(["clone", "pull", "checkout", "commit", "push"]),
    repoUrl: z.string(),
    branch: z.string(),
    commitSha: z.string().optional(),
    commitMessage: z.string().optional(),
    remoteBranch: z.string().optional(),
    summary: z.string().optional(),
    result: z.enum(["success", "failed", "timeout"]),
    errorMessage: z.string().optional(),
    executedBy: z.string().optional(),
  })).optional().describe("Git 操作记录"),
  artifacts: z.array(z.object({
    artifactType: z.string().describe("产物类型：design-doc|code-review|test-plan|pr-link|analysis-report|git-summary"),
    title: z.string(),
    content: z.string().optional().describe("完整内容（轻量产物）或摘要（大产物配合 contentUrl 使用）"),
    contentUrl: z.string().optional().describe("语雀等外部文档 URL（大产物）"),
    format: z.enum(["markdown", "yaml", "json", "html"]).optional(),
    source: z.enum(["bot", "human", "imported"]).optional(),
    authoredBy: z.string().optional(),
  })).optional().describe("阶段产物"),
};

const WorkflowRecentEventsSchema = {
  flowId: z.string().optional().describe("Filter events by flow ID"),
  sinceSeq: z.number().optional().describe("Return events with seq > this value (incremental polling)"),
  limit: z.number().optional().describe("Maximum events to return (default 20, max 50)"),
  eventType: z.string().optional().describe("Filter events by type (e.g., node_materialized, node_injected, llm_evaluation, budget_warning, budget_exhausted)"),
};

// ── Name list ──

/** Names of all workflow MCP tools registered by registerWorkflowTools. */
export const WORKFLOW_TOOL_NAMES = [
  "workflow_engine_dispatch",
  "workflow_list",
  "workflow_inspect",
  "workflow_state",
  "workflow_runs",
  "workflow_recent_events",
  "workflow_confirm",
  "workflow_reject",
  "workflow_retry",
  "workflow_validate",
  "workflow_help",
  "workflow_submit_phase_result",
  "workflow_debug_segment",
] as const;

// ── Utilities ──

/** Generate a session key if not provided. */
export function resolveSessionKey(explicit?: string): string {
  if (explicit) return explicit;
  return `mcp-${process.pid}-${Date.now().toString(36)}`;
}

export interface ParsedMcpWorkflowDispatchCommand {
  action: ControllerAction;
  commandSurface: WorkflowCommandSurface;
  commandName: string;
  parserRaw: string;
  facade?: ResolvedWorkflowFacade;
}

export interface McpWorkflowDispatchParseOptions {
  knownWorkflowIds?: Iterable<string>;
}

export function parseMcpWorkflowDispatchCommand(
  command: string,
  facadeRegistry?: FacadeRegistry,
  options: McpWorkflowDispatchParseOptions = {},
): ParsedMcpWorkflowDispatchCommand {
  const trimmed = command.trim();
  const firstToken = trimmed.split(/\s+/, 1)[0] ?? "";
  const commandName = firstToken.replace(/^\/+/, "");
  const facade = facadeRegistry?.resolve(commandName);
  const parserRaw = facade
    ? trimmed.slice(firstToken.length).trimStart()
    : command;
  const facadeParserRaw = parserRaw.trim();
  // Use parser semantics for token identity, but keep the original suffix below.
  const [verb = "", firstArgument = ""] = tokenizeCommand(facadeParserRaw);
  const normalizedVerb = verb.toLowerCase();
  const parserOptions = { commandName, facade };
  const knownWorkflowIds = new Set(options.knownWorkflowIds);
  let action: ControllerAction;

  if (facade && normalizedVerb === "run") {
    const isExplicitWorkflow = firstArgument === facade.defaultWorkflow
      || knownWorkflowIds.has(firstArgument);
    const parserInput = firstArgument
      && !firstArgument.startsWith("--")
      && !isExplicitWorkflow
      ? `run ${facade.defaultWorkflow} ${facadeParserRaw.slice(verb.length).trimStart()}`
      : parserRaw;
    action = parseWorkflowCommandWithFacade(parserInput, parserOptions);
  } else {
    action = parseWorkflowCommandWithFacade(parserRaw, parserOptions);
    if (facade && action.action === "run" && action.workflowId === facade.defaultWorkflow) {
      // Preserve parser-recognized management verbs without duplicating its verb table.
      const directAction = parseWorkflowCommandWithFacade(parserRaw);
      if (directAction.action !== "run") {
        action = normalizedVerb === "state" && !firstArgument
          ? parseWorkflowCommandWithFacade(`state ${facade.defaultWorkflow}`)
          : directAction;
      } else {
        action = parseWorkflowCommandWithFacade(
          `run ${facade.defaultWorkflow} ${facadeParserRaw}`,
          parserOptions,
        );
      }
    }
  }
  const commandSurface: WorkflowCommandSurface = facade
    ? { type: "facade", command: facade.command }
    : { type: "workflow" };

  return {
    action,
    commandSurface,
    commandName,
    parserRaw,
    ...(facade ? { facade } : {}),
  };
}

/**
 * TeClaw dispatch envelope action types (per §4.4 contract).
 *
 * When ClawMind tools are dispatched by TeClaw's DispatchRuleHook, the
 * tool output must be a JSON envelope with an `action` field. Missing or
 * non-JSON output triggers Reject + warn — the user sees
 * `[Blocked by plugin: ...]`.
 *
 * - `reply`: final assistant response (most common for workflow results)
 * - `continue`: enter loop with optional rewrite
 * - `reject`: reject the turn with a reason
 */
type EnvelopeAction = "reply" | "continue" | "reject";

/**
 * Format a dispatch-envelope payload for TeClaw's DispatchRuleHook (§4.4).
 *
 * ClawMind MCP tools are often invoked via TeClaw's dispatch rule pipeline.
 * The pipeline requires tool output to be a JSON envelope:
 *   `{"action":"reply","content":"..."}`  — final response
 *   `{"action":"continue","rewrite":"..."}` — rewrite + loop
 *   `{"action":"reject","reason":"..."}` — reject the turn
 *
 * Plain-text responses (what `formatResult` used to return) are rejected by
 * `parse_envelope` as "not valid JSON" or "missing action field", causing
 * `[Blocked by plugin]` for every dispatched workflow tool call.
 */
function formatEnvelope(
  action: EnvelopeAction,
  payload: { content?: string; rewrite?: string; reason?: string },
): { content: Array<{ type: "text"; text: string }> } {
  const envelope: Record<string, string> = { action, ...payload };
  return {
    content: [{ type: "text" as const, text: JSON.stringify(envelope) }],
  };
}

/** Format executeAction result for MCP tool response (reply envelope). */
export function formatResult(text: string): { content: Array<{ type: "text"; text: string }> } {
  return formatEnvelope("reply", { content: text });
}

/** Format an error for MCP tool response (reject envelope). */
export function formatError(error: unknown): { content: Array<{ type: "text"; text: string }>; isError: boolean } {
  const message = error instanceof Error ? error.message : String(error);
  return {
    ...formatEnvelope("reject", { reason: `[taskguard] Error: ${message}` }),
    isError: true,
  };
}

// ── Internal helpers ──

/**
 * Build ControllerDeps for a single MCP tool invocation.
 *
 * Creates the adapter from the factory, builds the executor dispatch,
 * and assembles the full ControllerDeps.
 */
function buildToolDeps(
  context: AdapterContext,
  adapterFactory: AdapterFactory,
  deps: WorkflowToolDeps,
  commandRunnerFn?: CommandRunner,
) {
  const { adapter, embeddedAgentFn } = adapterFactory(context);
  const executeNode = createDefaultExecutorDispatch({
    sessionKey: context.sessionKey,
    actionRegistry: deps.actionRegistry,
    abortSignal: context.abortSignal,
    embeddedAgentFn: embeddedAgentFn ?? deps.agentRunner ?? deps.samplingAgent,
    commandRunnerFn,
  });

  // Populate workflow catalog fields so handleRun/resolveWorkflow can find workflows.
  // Without these, the MCP path returns "pack 未安装或未被发现" for every workflow.
  const resolvedWorkflows = deps.workflowCatalog?.workflows;
  const failedWorkflows = deps.workflowCatalog?.failedWorkflows;
  const resolvedPacks = deps.workflowCatalog?.packs;
  const workflowRegistry = resolvedWorkflows
    ? workflowRegistryFromResolved(resolvedWorkflows)
    : undefined;
  const formatWorkflowCommand = deps.facadeRegistry
    ? (workflowId: string, command: string, args: string[] = [], options: { surface?: WorkflowCommandSurface } = {}) =>
        formatFacadeWorkflowCommand(deps.facadeRegistry!, workflowId, command, args, options)
    : undefined;

  console.error(
    `[clawmind:mcp] buildToolDeps: session=${context.sessionKey}` +
    ` workflows=${resolvedWorkflows?.length ?? 0}` +
    ` packs=${resolvedPacks?.length ?? 0}` +
    ` registry=${workflowRegistry ? Object.keys(workflowRegistry).length + " ids" : "none"}` +
    ` facadeRegistry=${deps.facadeRegistry ? deps.facadeRegistry.commands().length + " cmds" : "undefined"}` +
    ` formatWorkflowCommand=${formatWorkflowCommand ? "yes" : "no"}`,
  );

  return buildControllerDeps(adapter, {
    actionRegistry: deps.actionRegistry,
    executeNode,
    // Inject the resolved workflow catalog so run <workflowId> can locate workflow
    // specs via findWorkflowLookup's in-memory fallback.
    resolvedWorkflows,
    failedWorkflows,
    resolvedPacks,
    workflowRegistry,
    formatWorkflowCommand,
    // DB-first facade and workflow spec resolution (DB overrides Pack YAML)
    db: deps.db,
    workflowSpecApiRepo: deps.workflowSpecApiRepo,
    chatInjectLevel: loadConfig().app.chatInject.level,
    // Version management: pass config for deploy/rollback/pull/history
    clawWebBaseUrl: loadConfig().app.api.clawwebUrl || loadConfig().app.api.baseUrl,
    botId: loadBotId(),
    ownerId: loadOwnerId(),
    signatureKey: loadConfig().app.api.privateKeyB64 || process.env.CLAWMIND_PRIVATE_KEY,
    // Git config — read once and cached in deps
    gitRemoteUrl: loadConfig().app.git.remoteUrl || undefined,
    gitUsername: loadConfig().app.git.username || undefined,
    gitToken: process.env.CLAWMIND_GIT_TOKEN || loadConfig().app.git.token || undefined,
    gitEmail: process.env.CLAWMIND_GIT_EMAIL || loadConfig().app.git.email || undefined,
    // Dynamic workflow observability emitter
    eventEmitter: (() => {
      const db = deps.db;
      const stepLogRepo = db && db.dbType !== "noop" ? new ExecutionStepLogRepository(db) : null;
      const stepLogger = stepLogRepo ? new ExecutionStepLogger(stepLogRepo) : undefined;
      if (!stepLogger) return undefined;
      return new DynamicWorkflowEventEmitter({ logger: stepLogger });
    })(),
  });
}

/** Helper to run a workflow action and return MCP-formatted result. */
async function runAction(
  action: ControllerAction,
  context: AdapterContext,
  adapterFactory: AdapterFactory,
  deps: WorkflowToolDeps,
  options?: {
    commandSurface?: WorkflowCommandSurface;
    sessionKey?: string;
    commandRunnerFn?: CommandRunner;
  },
) {
  const logPrefix = "[clawmind:mcp]";
  try {
    console.error(`${logPrefix} runAction: action=${action.action} session=${context.sessionKey} surface=${options?.commandSurface?.type ?? "workflow"}`);
    const toolDeps = buildToolDeps(context, adapterFactory, deps, options?.commandRunnerFn);
    const result = await executeAction(action, toolDeps, undefined, options?.commandSurface, options?.sessionKey);
    const preview = typeof result === "string" ? result.substring(0, 120) : String(result).substring(0, 120);
    console.error(`${logPrefix} runAction result: action=${action.action} ok len=${typeof result === "string" ? result.length : -1} preview="${preview}"`);
    return formatResult(result);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.error(`${logPrefix} runAction error: action=${action.action} msg="${msg}"`);
    return formatError(error);
  }
}

// ── Main registration function ──

/**
 * Register all workflow MCP tools on the given server.
 *
 * Both stdio (mcp-entry.ts) and SSE (hermes-entry.ts) entry points call
 * this function with their own AdapterFactory. The factory determines how
 * the PlatformAdapter is created for each tool invocation:
 *
 * - **stdio**: uses `createMcpServerAdapter`
 * - **SSE**: uses `createHermesAdapter` (with SSE/approval/multi-tenant support)
 *
 * @param server - The MCP server instance
 * @param adapterFactory - Factory that creates a fresh adapter per request
 * @param deps - Shared dependencies (action registry, packs, etc.)
 * @param options - Optional configuration
 */
export function registerWorkflowTools(
  server: McpServer,
  adapterFactory: AdapterFactory,
  deps: WorkflowToolDeps,
  options?: {
    /** Custom command runner (defaults to child_process.spawn). */
    commandRunnerFn?: CommandRunner;
    /** Log prefix for console messages. Defaults to "[clawmind:mcp]". */
    logPrefix?: string;
  },
): void {
  const commandRunnerFn = options?.commandRunnerFn;
  const logPrefix = options?.logPrefix ?? "[clawmind:mcp]";

  // ── Eagerly initialize the event buffer ──
  // Without this, bufferWorkflowEvent() in mcp-chat-inject.ts would silently
  // discard events because globalBuffer is undefined until someone calls
  // getWorkflowEventBuffer() for the first time (lazy singleton pattern).
  getWorkflowEventBuffer();
  console.error(`${logPrefix} Event buffer initialized (MAX_EVENTS=200, TTL=5min)`);

  const toolNames = [
    "workflow_engine_dispatch",
    "workflow_list",
    "workflow_inspect",
    "workflow_state",
    "workflow_runs",
    "workflow_confirm",
    "workflow_reject",
    "workflow_retry",
    "workflow_validate",
    "workflow_help",
    "workflow_debug_segment",
  ];
  console.error(`${logPrefix} registerWorkflowTools: registering ${toolNames.length} tools [${toolNames.join(", ")}]`);
  console.error(`${logPrefix} registerWorkflowTools: deps keys=[${Object.keys(deps).join(", ")}]`);
  console.error(`${logPrefix} registerWorkflowTools: facadeRegistry=${deps.facadeRegistry ? `${deps.facadeRegistry.commands().length} cmds` : "undefined"} workflowCatalog=${deps.workflowCatalog ? `${deps.workflowCatalog.workflows?.length ?? 0} workflows, ${deps.workflowCatalog.failedWorkflows?.length ?? 0} failed, ${deps.workflowCatalog.packs?.length ?? 0} packs` : "undefined"}`);

  // ── workflow_engine_dispatch ──

  server.tool(
    "workflow_engine_dispatch",
    "ClawMind workflow engine. Pass a command string; first token is the verb, rest is arguments. See command parameter for full command reference.",
    WorkflowDispatchSchema,
    async (args, extra) => {
      try {
        const sessionKey = resolveSessionKey(args.sessionKey);
        const command = args.command;
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_engine_dispatch (session: ${sessionKey})`);

        const parsedCommand = parseMcpWorkflowDispatchCommand(
          command,
          deps.facadeRegistry,
          { knownWorkflowIds: deps.workflowCatalog?.workflows.map((workflow) => workflow.id) },
        );
        console.error(
          `${logPrefix} facade resolve: commandName="${parsedCommand.commandName}" ` +
          `hit=${parsedCommand.facade
            ? `yes:${parsedCommand.facade.command}->${parsedCommand.facade.defaultWorkflow}`
            : "no"} ` +
          `registry=${deps.facadeRegistry
            ? `(${deps.facadeRegistry.commands().length} cmds: ${deps.facadeRegistry.commands().join(",")})`
            : "undefined"}`,
        );
        // Thread per-run chatInjectLevel override (MCP schema → run action → handleRun).
        if (parsedCommand.action.action === "run" && args.chatInjectLevel) {
          parsedCommand.action.chatInjectLevel = args.chatInjectLevel;
        }
        const context: AdapterContext = { sessionKey, ...teclaw };
        return runAction(parsedCommand.action, context, adapterFactory, deps, {
          commandSurface: parsedCommand.commandSurface,
          sessionKey,
          commandRunnerFn,
        });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_list ──

  server.tool(
    "workflow_list",
    "List available workflow packs and workflows.",
    {},
    async () => {
      try {
        if (!deps.workflowCatalog) {
          return formatResult("No workflow packs loaded.");
        }
        const lines: string[] = ["## Workflow Packs"];
        for (const pack of deps.workflowCatalog.packs) {
          lines.push(`- **${pack.manifest.id}** (v${pack.manifest.version})${pack.manifest.title ? ": " + pack.manifest.title : ""}`);
          for (const wf of pack.workflows) {
            lines.push(`  - ${wf.id}`);
          }
        }
        lines.push(`\nTotal: ${deps.workflowCatalog.packs.length} packs, ${deps.workflowCatalog.workflows.length} workflows`);
        return formatResult(lines.join("\n"));
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_inspect ──

  server.tool(
    "workflow_inspect",
    "Inspect a workflow flow: status summary, node details, and recent events. Supports cross-session lookup (auto-fallback from session-local to global store). Use --analyze for deep analysis report or --full for raw JSON dump.",
    WorkflowInspectSchema,
    async (args, extra) => {
      try {
        const sessionKey = resolveSessionKey(args.sessionKey);
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_inspect: flowId=${args.flowId} analyze=${args.analyze ?? false} full=${args.full ?? false} (session: ${sessionKey})`);
        const action: ControllerAction = {
          action: "inspect",
          flowId: args.flowId,
          ...(args.analyze ? { analyze: true } : {}),
          ...(args.full ? { full: true } : {}),
        };
        const context: AdapterContext = { sessionKey, ...teclaw };
        return runAction(action, context, adapterFactory, deps, { sessionKey, commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_state ── (deprecated alias for workflow_inspect)

  server.tool(
    "workflow_state",
    "Deprecated: use workflow_inspect instead. Query the state of a running workflow flow.",
    WorkflowStateSchema,
    async (args, extra) => {
      try {
        const sessionKey = resolveSessionKey(args.sessionKey);
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_state (deprecated): flowId=${args.flowId} (session: ${sessionKey})`);
        const action: ControllerAction = { action: "inspect", flowId: args.flowId };
        const context: AdapterContext = { sessionKey, ...teclaw };
        return runAction(action, context, adapterFactory, deps, { sessionKey, commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_runs ──

  server.tool(
    "workflow_runs",
    "List workflow run instances (running, completed, failed).",
    WorkflowRunsSchema,
    async (args, extra) => {
      try {
        const sessionKey = resolveSessionKey(args.sessionKey);
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_runs: limit=${args.limit ?? 10} (session: ${sessionKey})`);
        const action: ControllerAction = { action: "runs", limit: args.limit ?? 10 };
        const context: AdapterContext = { sessionKey, ...teclaw };
        return runAction(action, context, adapterFactory, deps, { sessionKey, commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_recent_events ──

  server.tool(
    "workflow_recent_events",
    "Query recent workflow progress events. Use this to poll for workflow progress when Channel notifications are unavailable (e.g., VS Code plugin mode). Pass flowId to filter by a specific flow, and sinceSeq for incremental polling.",
    WorkflowRecentEventsSchema,
    async (args) => {
      try {
        const buffer = getWorkflowEventBuffer();
        const events = buffer.query({
          flowId: args.flowId,
          sinceSeq: args.sinceSeq,
          limit: args.limit,
          eventType: args.eventType,
        });
        const stats = buffer.stats();

        if (events.length === 0) {
          return formatResult(
            "No recent workflow events found.\n" +
            (args.flowId ? `No events for flowId=${args.flowId}. ` : "") +
            (args.eventType ? `No events with eventType=${args.eventType}. ` : "") +
            "If a workflow is running, events may take a few seconds to appear.\n" +
            "Use workflow_inspect to check full execution status.",
          );
        }

        // Format events for readability
        const header = `📋 Recent Workflow Events (${events.length}/${stats.count} buffered, sinceSeq=${args.sinceSeq ?? "none"})\n`;
        const lines = events.map(e => {
          const attrs = [
            e.flowId && `flow=${e.flowId.slice(0, 12)}`,
            e.nodeId && `node=${e.nodeId}`,
            e.eventType,
          ].filter(Boolean).join(" ");
          return `[${e.seq}] ${e.timestamp.slice(11, 19)} ${attrs}\n    ${e.message.slice(0, 200)}`;
        });
        const nextPollHint = `\n\nFor incremental polling, pass sinceSeq=${events[0].seq} in your next call.`;

        return formatResult(header + lines.join("\n") + nextPollHint);
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_confirm ──

  server.tool(
    "workflow_confirm",
    "Confirm/approve a waiting workflow node.",
    WorkflowConfirmSchema,
    async (args, extra) => {
      try {
        const sessionKey = resolveSessionKey(args.sessionKey);
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_confirm: note=${args.note ?? "(none)"} (session: ${sessionKey})`);
        const action: ControllerAction = { action: "confirm", note: args.note };
        const context: AdapterContext = { sessionKey, ...teclaw };
        return runAction(action, context, adapterFactory, deps, { sessionKey, commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_reject ──

  server.tool(
    "workflow_reject",
    "Reject a waiting workflow node.",
    WorkflowRejectSchema,
    async (args, extra) => {
      try {
        const sessionKey = resolveSessionKey(args.sessionKey);
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_reject: note=${args.note ?? "(none)"} (session: ${sessionKey})`);
        const action: ControllerAction = { action: "reject", note: args.note };
        const context: AdapterContext = { sessionKey, ...teclaw };
        return runAction(action, context, adapterFactory, deps, { sessionKey, commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_retry ──

  server.tool(
    "workflow_retry",
    "Retry a failed workflow node.",
    WorkflowRetrySchema,
    async (args, extra) => {
      try {
        const sessionKey = resolveSessionKey(args.sessionKey);
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_retry: flowId=${args.flowId} nodeId=${args.nodeId ?? "(root)"} (session: ${sessionKey})`);
        const action: ControllerAction = { action: "retry", flowId: args.flowId, nodeId: args.nodeId };
        const context: AdapterContext = { sessionKey, ...teclaw };
        return runAction(action, context, adapterFactory, deps, { sessionKey, commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_validate ──

  server.tool(
    "workflow_validate",
    "Validate a workflow YAML definition.",
    WorkflowValidateSchema,
    async (args, extra) => {
      try {
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_validate: workflowId=${args.workflowId}`);
        const action: ControllerAction = { action: "validate", workflowId: args.workflowId };
        const context: AdapterContext = { sessionKey: "validate", ...teclaw };
        return runAction(action, context, adapterFactory, deps, { commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_help ──

  server.tool(
    "workflow_help",
    "Show workflow command help.",
    WorkflowHelpSchema,
    async (args, extra) => {
      try {
        const teclaw = extractTeClawHeaders(extra);
        console.error(`${logPrefix} workflow_help: workflowId=${args.workflowId ?? "(all)"}`);
        const action: ControllerAction = { action: "help", workflowId: args.workflowId };
        const context: AdapterContext = { sessionKey: "help", ...teclaw };
        return runAction(action, context, adapterFactory, deps, { commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_debug_segment ──
  // Executes a workflow segment starting at `fromNode`, with upstream outputs
  // provided by the caller. Debug execution is side-effect free: it uses a
  // no-op boundTaskFlow (no flow_run records) and a no-op chatInject (silent).

  server.tool(
    "workflow_debug_segment",
    "调试工作流片段：从指定节点开始执行，上游输出由模型提供。"
    + "用于跳过已验证的上游节点、单独调试某个节点、或跑一段工作流。"
    + "调试执行不写入生产 TaskFlow、不发进度通知，结果仅返回给调用方。",
    WorkflowDebugSegmentSchema,
    async (args, extra) => {
      const logPrefix = "[clawmind:mcp]";
      try {
        const sessionKey = resolveSessionKey(args.sessionKey);
        const teclaw = extractTeClawHeaders(extra);
        console.error(
          `${logPrefix} workflow_debug_segment:`
          + ` workflowId=${args.workflowId} fromNode=${args.fromNode} toNode=${args.toNode ?? "(end)"}`,
        );

        const action: ControllerAction = {
          action: "debug-segment",
          workflowId: args.workflowId,
          fromNode: args.fromNode,
          toNode: args.toNode,
          nodeOutput: args.nodeOutput ?? {},
          workflowData: args.workflowData,
          input: args.input,
        };

        const context: AdapterContext = { sessionKey, ...teclaw };
        const toolDeps = buildToolDeps(context, adapterFactory, deps, commandRunnerFn);

        // Override boundTaskFlow + chatInject with no-ops so the debug segment
        // never persists to production TaskFlow and never sends chat notifications.
        // The real executor dispatch (and all other deps) are inherited as-is.
        let noOpRevision = 0;
        const noOpTaskFlow = {
          createManaged: async () => ({} as Record<string, unknown>),
          setWaiting: async () => ({ applied: true, flow: {} as Record<string, unknown> }),
          resume: async () => ({ applied: true, flow: { revision: noOpRevision } as Record<string, unknown> }),
          finish: async () => undefined,
          fail: async () => undefined,
          list: async () => ({ flows: [] as Array<Record<string, unknown>> }),
          get: async () => null,
          findLatest: async () => null,
          runTask: async () => ({} as Record<string, unknown>),
        };
        const debugDeps = {
          ...toolDeps,
          boundTaskFlow: noOpTaskFlow,
          chatInject: (async () => undefined) as typeof toolDeps.chatInject,
        };

        const result = await executeAction(action, debugDeps, undefined, undefined, sessionKey);
        return formatResult(result);
      } catch (error) {
        return formatError(error);
      }
    },
  );

  // ── workflow_submit_phase_result ──

  server.tool(
    "workflow_submit_phase_result",
    "将研发工作流阶段的执行结果（代码变更、产物、状态）上报到 clawweb。"
    + "BOT 执行完研发工作流阶段后调用此工具，将 gitOps 和 artifacts 上报到 clawweb 前端可展示。",
    SubmitPhaseResultSchema,
    async (args, extra) => {
      try {
        const teclaw = extractTeClawHeaders(extra);
        console.error(
          `${logPrefix} workflow_submit_phase_result:`
          + ` workflowId=${args.workflowId} phaseId=${args.phaseId} status=${args.status}`
          + ` gitOps=${args.gitOps?.length ?? 0} artifacts=${args.artifacts?.length ?? 0}`,
        );

        const action: ControllerAction = {
          action: "dev-workflow-callback",
          params: {
            workflowId: args.workflowId,
            phaseId: args.phaseId,
            status: args.status,
            resultSummary: args.resultSummary,
            documentUrl: args.documentUrl,
            documentTitle: args.documentTitle,
            error: args.error,
            baasRunId: args.baasRunId,
            gitOps: args.gitOps,
            artifacts: args.artifacts,
          },
        };

        // dev-workflow-callback does not need a user session key
        const context: AdapterContext = { sessionKey: "dev-workflow-callback", ...teclaw };
        return runAction(action, context, adapterFactory, deps, { commandRunnerFn });
      } catch (error) {
        return formatError(error);
      }
    },
  );
}
