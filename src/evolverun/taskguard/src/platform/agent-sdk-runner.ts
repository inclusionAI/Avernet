/**
 * Agent SDK Runner — Path C: embedded-agent via `query()` (进程内函数调用).
 *
 * Uses the Claude Agent SDK's `query()` to launch a full multi-turn Agent Loop
 * inside the same process. This is equivalent to OpenClaw's `runEmbeddedPiAgent`
 * — the call happens in-process, NOT over stdio or HTTP transport.
 *
 * Key characteristics:
 * - **进程内函数调用**: `query()` runs the entire Agent Loop within the
 *   current Node.js process. No child process, no stdio transport.
 * - **Multi-turn**: The agent can invoke tools, observe results, and loop.
 * - **Deterministic tools**: Unlike Plugin Agent (Path B), the Agent SDK agent
 *   gets a fixed set of tools via `allowedTools` + inline MCP server.
 * - **Budget cap**: `maxBudgetUsd` prevents runaway agent execution.
 *
 * Path selection priority: TeClaw WS (Channel 2) > Agent SDK (Path C) >
 * Plugin Agent (Path B) > Sampling (Path A).
 *
 * @module platform/agent-sdk-runner
 */

import { query } from "@anthropic-ai/claude-agent-sdk";
import type { McpSdkServerConfigWithInstance, SDKResultSuccess, SDKResultError } from "@anthropic-ai/claude-agent-sdk";
import type { EmbeddedAgentResult } from "./mcp-adapter.js";
import { createLogger } from "./logger.js";
import { execSync } from "node:child_process";
import { existsSync } from "node:fs";
import { resolve as resolvePath } from "node:path";

// ── Types ──

/** Configuration for creating an Agent SDK runner. */
export interface AgentSdkRunnerConfig {
  /**
   * Anthropic API key. Optional — when set, passed to the claude CLI subprocess
   * via ANTHROPIC_API_KEY env var. When absent, the claude CLI uses its own
   * built-in auth (OAuth, stored credentials, etc.).
   */
  apiKey?: string;
  /** Model to use. Defaults to "claude-sonnet-4-20250514". */
  model?: string;
  /** Inline MCP server from `createClawmindInlineServer()`. */
  inlineMcpServer?: McpSdkServerConfigWithInstance;
  /** Default max turns per agent loop. Defaults to 20. */
  defaultMaxTurns?: number;
  /** Default max budget in USD per agent loop. Defaults to 0.5. */
  defaultMaxBudgetUsd?: number;
  /**
   * Explicit path to the `claude` CLI binary for `pathToClaudeCodeExecutable`.
   * If not set, resolved automatically via `resolveClaudeCodePath()`.
   */
  claudeCodeExecutablePath?: string;
}

// ── Constants ──

const DEFAULT_MODEL = "claude-sonnet-4-20250514";
const DEFAULT_MAX_TURNS = 20;
const DEFAULT_MAX_BUDGET_USD = 0.5;

/** Tool names always allowed in Agent SDK embedded-agent loops. */
const ALWAYS_ALLOWED_TOOLS = [
  "mcp__clawmind__workflow_inspect",
  "mcp__clawmind__workflow_state",
  "mcp__clawmind__workflow_runs",
];

// ── Claude Code Binary Resolution ──

/**
 * Resolve the path to the `claude` CLI binary for `pathToClaudeCodeExecutable`.
 *
 * The SDK's auto-resolution (`require.resolve` on the platform-specific package)
 * fails in these scenarios:
 * - **esbuild bundling**: the SDK is inlined into the bundle, so `require.resolve`
 *   loses its `node_modules` context
 * - **Marketplace plugin directory**: `node_modules/` isn't synced to the plugin dir
 * - **Cross-platform packaging**: only the host platform's binary is installed,
 *   but the target may be different (e.g., macOS dev → Linux deploy)
 *
 * Resolution order:
 * 1. `CLAUDE_CODE_EXECUTABLE` env var (explicit user override)
 * 2. `config.claudeCodeExecutablePath` (programmatic override)
 * 3. Platform-specific SDK package in `node_modules/` (if bundled)
 * 4. Common install paths (`/opt/homebrew/bin/claude`, `/usr/local/bin/claude`)
 * 5. `which claude` via `execSync` (PATH lookup)
 *
 * Returns `undefined` if no binary is found — the SDK will then attempt its
 * own auto-resolution (which may throw "Native CLI binary not found").
 */
export function resolveClaudeCodePath(explicitPath?: string): string | undefined {
  // 1. Environment variable override
  const envPath = process.env.CLAUDE_CODE_EXECUTABLE;
  if (envPath && existsSync(envPath)) {
    return envPath;
  }

  // 2. Programmatic override
  if (explicitPath && existsSync(explicitPath)) {
    return explicitPath;
  }

  // 3. Try the platform-specific SDK package via require.resolve
  //    This works when node_modules/ is available (dev mode, not bundled)
  const platform = process.platform;
  const arch = process.arch;
  const binName = platform === "win32" ? "claude.exe" : "claude";
  const pkgName = `@anthropic-ai/claude-agent-sdk-${platform}-${arch}`;
  try {
    const pkgRoot = require.resolve(`${pkgName}/package.json`);
    const binPath = resolvePath(pkgRoot, "..", binName);
    if (existsSync(binPath)) {
      return binPath;
    }
  } catch {
    // Package not installed or not resolvable — fall through
  }

  // 4. Common install paths
  const commonPaths = [
    "/usr/bin/claude",            // Linux remote Bot (standard PATH)
    "/opt/homebrew/bin/claude",   // macOS Homebrew (Apple Silicon)
    "/usr/local/bin/claude",      // macOS Homebrew (Intel) / Linux manual
    "/snap/bin/claude",           // Linux Snap
    process.env.HOME + "/.claude/local/bin/claude", // User-local install
  ];
  for (const p of commonPaths) {
    if (existsSync(p)) {
      return p;
    }
  }

  // 5. PATH lookup via `which`
  try {
    const whichResult = execSync("which claude 2>/dev/null", {
      encoding: "utf-8",
      timeout: 3000,
    }).trim();
    if (whichResult && existsSync(whichResult)) {
      return whichResult;
    }
  } catch {
    // which failed — not on PATH
  }

  return undefined;
}

// ── Logger ──

const log = createLogger("clawmind:agent-sdk");

// ── Factory ──

/**
 * Create an Agent SDK runner function for embedded-agent Path C.
 *
 * The returned function satisfies the same signature as TeClaw's agent loop
 * and the sampling agent — `(params: Record<string, unknown>) => Promise<EmbeddedAgentResult>`.
 *
 * ### API key handling
 *
 * The Agent SDK does NOT accept `apiKey` in Options. Instead, it reads
 * `ANTHROPIC_API_KEY` from the subprocess environment via `Options.env`.
 * We pass the API key through `env` so the SDK subprocess can authenticate.
 *
 * ### System prompt assembly
 *
 * The system prompt combines:
 * 1. An optional `systemPromptPrefix` (workflow-wide instructions)
 * 2. Workflow context (workflowId, flowId, nodeId) for traceability
 * 3. Rules: stay on task, use tools, return structured results
 *
 * ### Tool configuration
 *
 * - `tools: []` disables ALL built-in Claude Code tools (Read, Write, Bash, etc.)
 * - `mcpServers: { clawmind: inlineServer }` provides workflow state tools
 * - `allowedTools` merges the caller's requested tools with the always-allowed
 *   `mcp__clawmind__*` tools
 *
 * ### Result extraction
 *
 * `query()` returns an `AsyncGenerator<SDKMessage, void>`. We iterate with
 * `for await` and extract the final `result` message (type='result') to build
 * the `EmbeddedAgentResult`.
 *
 * @param config — Runner configuration (API key, model, inline server, etc.)
 * @returns Agent runner function compatible with McpAgentRunnerOptions.agentSdkRunner
 */
export function getAgentSdkRunner(
  config: AgentSdkRunnerConfig,
): (params: Record<string, unknown>) => Promise<EmbeddedAgentResult> {
  const {
    apiKey,
    model = DEFAULT_MODEL,
    inlineMcpServer,
    defaultMaxTurns = DEFAULT_MAX_TURNS,
    defaultMaxBudgetUsd = DEFAULT_MAX_BUDGET_USD,
    claudeCodeExecutablePath: configExecutablePath,
  } = config;

  // ── Resolve Claude Code binary path ──
  // This prevents "Native CLI binary for <platform> not found" errors when:
  // - The SDK is bundled by esbuild (loses require.resolve context)
  // - node_modules/ isn't available (marketplace plugin dir)
  // - Cross-platform packaging (macOS dev, Linux deploy)
  const claudeCodePath = resolveClaudeCodePath(configExecutablePath);
  if (claudeCodePath) {
    log.info(`Claude Code binary resolved: ${claudeCodePath}`);
  } else {
    log.warn("Claude Code binary not found via resolveClaudeCodePath() — SDK will attempt auto-resolution (may fail if platform package is missing)");
  }

  return async (params: Record<string, unknown>): Promise<EmbeddedAgentResult> => {
    const prompt = String(params.prompt ?? params.goal ?? "");
    if (!prompt) {
      return { error: "Agent SDK runner requires a prompt" };
    }

    // ── Assemble system prompt ──
    const systemPromptPrefix = String(params.systemPromptPrefix ?? "");
    const workflowId = String(params.workflowId ?? "unknown");
    const flowId = String(params.flowId ?? "unknown");
    const nodeId = String(params.nodeId ?? "unknown");

    const systemPrompt = buildSystemPrompt(systemPromptPrefix, { workflowId, flowId, nodeId });

    // ── Build MCP servers config ──
    const mcpServers: Record<string, import("@anthropic-ai/claude-agent-sdk").McpServerConfig> = {};
    if (inlineMcpServer) {
      mcpServers.clawmind = inlineMcpServer;
    }

    // ── Build allowed tools (avoid Set spread — needs downlevelIteration) ──
    const callerTools: string[] = Array.isArray(params.allowedTools) ? params.allowedTools : [];
    const toolSet: Record<string, boolean> = {};
    for (const t of callerTools) toolSet[t] = true;
    for (const t of ALWAYS_ALLOWED_TOOLS) toolSet[t] = true;
    const allowedTools = Object.keys(toolSet);

    // ── Extract limits ──
    const maxTurns = typeof params.maxTurns === "number" ? params.maxTurns : defaultMaxTurns;
    const maxBudgetUsd = typeof params.maxBudgetUsd === "number" ? params.maxBudgetUsd : defaultMaxBudgetUsd;

    log.info(
      `Agent SDK query() starting: model=${model} maxTurns=${maxTurns} ` +
      `budget=$${maxBudgetUsd} tools=[${allowedTools.join(", ")}]`,
    );

    // ── Call query() — 进程内函数调用 ──
    // The claude CLI has its own auth (OAuth, stored credentials, etc.).
    // We optionally pass ANTHROPIC_API_KEY via env if explicitly provided,
    // but it is NOT required — the CLI authenticates on its own.
    try {
      const envOverride: Record<string, string> = { ...process.env as Record<string, string> };
      if (apiKey) {
        envOverride.ANTHROPIC_API_KEY = apiKey;
      }
      const queryResult = query({
        prompt,
        options: {
          model,
          systemPrompt,
          maxTurns,
          maxBudgetUsd,
          tools: [], // Disable ALL built-in Claude Code tools (Read, Write, Bash, etc.)
          mcpServers: Object.keys(mcpServers).length > 0 ? mcpServers : undefined,
          allowedTools,
          permissionMode: "acceptEdits", // Auto-accept tool calls — workflow nodes are deterministic
          effort: "medium", // Balanced reasoning for workflow node execution
          env: envOverride,
          // ── Critical: point SDK to the claude CLI binary ──
          // Without this, the SDK tries require.resolve() on the platform-specific
          // package (e.g., claude-agent-sdk-darwin-arm64), which fails when:
          //  - The SDK is bundled by esbuild (loses node_modules context)
          //  - The plugin dir has no node_modules (marketplace install)
          //  - Cross-platform packaging (macOS dev, Linux deploy)
          // See: https://code.claude.com/docs/en/agent-sdk/typescript
          ...(claudeCodePath ? { pathToClaudeCodeExecutable: claudeCodePath } : {}),
        },
      });

      // ── Iterate async generator to extract final result ──
      let resultText = "";
      let turnsUsed = 0;
      let sessionId = "";
      let totalCostUsd = 0;
      const payloads: Array<{ text?: string; isError?: boolean; isReasoning?: boolean }> = [];

      for await (const message of queryResult) {
        // ── Handle result messages ──
        if (message.type === "result") {
          if (message.subtype === "success") {
            const successMsg = message as SDKResultSuccess;
            resultText = successMsg.result;
            turnsUsed = successMsg.num_turns;
            sessionId = successMsg.session_id;
            totalCostUsd = successMsg.total_cost_usd;
          } else {
            // SDKResultError subtypes: 'error_during_execution' | 'error_max_turns' |
            // 'error_max_budget_usd' | 'error_max_structured_output_retries'
            const errorMsg = message as SDKResultError;
            const errDesc = errorMsg.errors.length > 0
              ? errorMsg.errors.join("; ")
              : `Agent SDK error: ${errorMsg.subtype} after ${errorMsg.num_turns} turns`;

            log.error(`Agent SDK query() error: ${errDesc.slice(0, 300)}`);

            return {
              error: errDesc,
              payloads,
              meta: {
                executionPath: "agent-sdk",
                model,
                turnsUsed: errorMsg.num_turns,
                sessionId: errorMsg.session_id,
                totalCostUsd: errorMsg.total_cost_usd,
                errorSubtype: errorMsg.subtype,
              },
            };
          }
          break;
        }

        // ── Capture assistant messages as payloads for observability ──
        if (message.type === "assistant") {
          const content = (message as { content?: Array<{ type: string; text?: string; name?: string }> }).content;
          if (Array.isArray(content)) {
            for (const block of content) {
              if (block.type === "text" && block.text) {
                payloads.push({ text: block.text, isReasoning: false });
              } else if (block.type === "tool_use") {
                payloads.push({
                  text: `tool_use: ${block.name ?? "unknown"}`,
                  isReasoning: false,
                });
              }
            }
          }
        }
      }

      log.info(
        `Agent SDK query() complete: turns=${turnsUsed} cost=$${totalCostUsd.toFixed(4)} ` +
        `sessionId=${sessionId.slice(0, 8)}... result=${resultText.slice(0, 100).replace(/\n/g, " ")}`,
      );

      return {
        output: resultText,
        payloads,
        meta: {
          executionPath: "agent-sdk",
          model,
          stopReason: "endTurn",
          turnsUsed,
          sessionId,
          totalCostUsd,
        },
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      log.error(`Agent SDK query() exception: ${msg.slice(0, 300)}`);
      return {
        error: `Agent SDK exception: ${msg.slice(0, 200)}`,
        meta: { executionPath: "agent-sdk", model, exception: msg.slice(0, 300) },
      };
    }
  };
}

// ── System Prompt Builder ──

/**
 * Build the full system prompt for the Agent SDK agent loop.
 *
 * Structure:
 * 1. Optional prefix (workflow-wide instructions from config)
 * 2. Workflow identity context (for traceability in logs and tool calls)
 * 3. Behavioral rules (stay on task, use provided tools, return structured output)
 */
function buildSystemPrompt(
  prefix: string,
  context: { workflowId: string; flowId: string; nodeId: string },
): string {
  const parts: string[] = [];

  if (prefix) {
    parts.push(prefix);
    parts.push("");
  }

  parts.push("## Workflow Context");
  parts.push(`- Workflow: ${context.workflowId}`);
  parts.push(`- Flow Run: ${context.flowId}`);
  parts.push(`- Current Node: ${context.nodeId}`);
  parts.push("");

  parts.push("## Rules");
  parts.push("1. Execute ONLY the current workflow node's task. Do not deviate.");
  parts.push("2. Use the provided MCP tools (mcp__clawmind__workflow_inspect, mcp__clawmind__workflow_runs) to query workflow state when needed.");
  parts.push("3. Return a clear, structured result when done. The output will be captured as the node's execution result.");
  parts.push("4. If you encounter an error you cannot resolve, describe it clearly in your final response.");
  parts.push("5. Do NOT attempt to use file system tools (Read, Write, Bash) — they are disabled. Use only the provided MCP tools.");

  return parts.join("\n");
}