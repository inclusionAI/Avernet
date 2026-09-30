/**
 * MCP Sampling Agent — replaces OpenClaw's runEmbeddedPiAgent with MCP sampling.
 *
 * When ClawMind runs as an MCP server, it can use the MCP `sampling/createMessage`
 * protocol to request the host LLM (Claude, etc.) to execute sub-tasks. This is
 * the MCP-equivalent of `api.runtime.agent.runEmbeddedPiAgent`.
 *
 * Features:
 * - Exponential backoff retry for transient errors (timeout, rate limit, 429, 503)
 * - Configurable max retries and base delay
 * - Non-transient errors return immediately without retry
 *
 * Usage:
 *   const embeddedAgentFn = getMcpSamplingAgent(mcpServerServer);
 *   const adapter = createMcpServerAdapter({ embeddedAgentFn, ... });
 *
 * @module platform/mcp-sampling-agent
 */

import type { EmbeddedAgentResult } from "./mcp-adapter.js";

/**
 * Parameters for MCP sampling requests.
 */
export interface McpSamplingParams {
  /** The prompt to send to the host LLM. */
  prompt: string;
  /** Session key for context isolation. */
  sessionKey: string;
  /** Flow ID for tracking. */
  flowId: string;
  /** Node ID that triggered this sampling. */
  nodeId: string;
  /** Workflow ID for context. */
  workflowId: string;
  /** Maximum tokens for the response. Defaults to 4096. */
  maxTokens?: number;
  /** System prompt to prepend. */
  systemPrompt?: string;
  /** Temperature for generation. Defaults to 0.7. */
  temperature?: number;
}

/** Error message patterns that indicate transient (retryable) failures. */
const TRANSIENT_PATTERNS = [
  "timeout",
  "timed out",
  "rate",
  "rate_limit",
  "429",
  "503",
  "502",
  "econnreset",
  "econnrefused",
  "etimedout",
  "epipe",
  "overloaded",
  "capacity",
];

function isTransientError(err: unknown): boolean {
  const msg = (err instanceof Error ? err.message : String(err)).toLowerCase();
  return TRANSIENT_PATTERNS.some((pattern) => msg.includes(pattern));
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Create an embedded agent function backed by MCP sampling.
 *
 * This function is designed to be passed as `embeddedAgentFn` in
 * `McpServerAdapterOptions`. When a workflow node of type `embedded-agent`
 * or `subagent` needs to run, the function uses MCP `sampling/createMessage`
 * to ask the host LLM to generate a response.
 *
 * Includes exponential backoff retry for transient errors.
 *
 * @param serverLike - An object with a `server` property that supports `createMessage`.
 *   This matches the McpServer class shape from @modelcontextprotocol/sdk.
 * @param options - Optional configuration for sampling behavior
 * @returns An embeddedAgentFn suitable for McpServerAdapterOptions
 */
export function getMcpSamplingAgent(
  serverLike: McpSamplingCapable,
  options: McpSamplingAgentOptions = {},
): (params: Record<string, unknown>) => Promise<EmbeddedAgentResult> {
  const {
    defaultMaxTokens = 4096,
    defaultTemperature = 0.7,
    systemPromptPrefix = "",
    transformResult = defaultTransformResult,
    maxRetries = 2,
    retryBaseDelay = 1000,
  } = options;

  return async (params: Record<string, unknown>): Promise<EmbeddedAgentResult> => {
    const prompt = String(params.prompt ?? params.goal ?? "");
    if (!prompt) {
      return { error: "MCP sampling agent requires a prompt" };
    }

    const maxTokens = typeof params.maxTokens === "number" ? params.maxTokens : defaultMaxTokens;
    const temperature = typeof params.temperature === "number" ? params.temperature : defaultTemperature;
    const systemPrompt = systemPromptPrefix
      ? `${systemPromptPrefix}\n\nWorkflow: ${params.workflowId ?? "unknown"}\nNode: ${params.nodeId ?? "unknown"}\nFlow: ${params.flowId ?? "unknown"}`
      : undefined;

    let lastError: unknown;

    for (let attempt = 0; attempt <= maxRetries; attempt++) {
      try {
        const result = await serverLike.server.createMessage({
          messages: [
            {
              role: "user",
              content: { type: "text", text: prompt },
            },
          ],
          maxTokens,
          systemPrompt,
          temperature,
        });

        return transformResult(result);
      } catch (err) {
        lastError = err;
        const msg = err instanceof Error ? err.message : String(err);

        // Only retry on transient errors
        if (!isTransientError(err) || attempt === maxRetries) {
          break;
        }

        const delayMs = retryBaseDelay * Math.pow(2, attempt);
        console.warn(
          `[clawmind:mcp] Sampling attempt ${attempt + 1}/${maxRetries + 1} failed (transient): ${msg.slice(0, 100)}. Retrying in ${delayMs}ms...`,
        );
        await delay(delayMs);
      }
    }

    return {
      error: lastError instanceof Error ? lastError.message : String(lastError),
      meta: {
        samplingDiagnostic: {
          path: "mcp-sampling/createMessage",
          attempts: maxRetries + 1,
          lastError: (lastError instanceof Error ? lastError.message : String(lastError)).slice(0, 300),
          lastErrorType: lastError instanceof Error ? lastError.constructor.name : typeof lastError,
          isTransient: isTransientError(lastError),
          // The "Not connected" error from @modelcontextprotocol/sdk means
          // this._transport === null — the MCP client-server transport has
          // been closed (e.g., HTTP request completed, SSE disconnected).
          // This is the expected root cause when embedding-agent runs
          // asynchronously after the original MCP request has returned.
          likelyCause: (lastError instanceof Error && lastError.message === "Not connected")
            ? "MCP SDK Protocol._transport is null — MCP transport (HTTP/SSE) was closed before sampling request was sent. This happens when embedded-agent executes asynchronously after the original MCP request has already returned."
            : undefined,
        },
      },
    };
  };
}

/**
 * Default transform: convert MCP sampling result to EmbeddedAgentResult.
 */
function defaultTransformResult(result: McpCreateMessageResult): EmbeddedAgentResult {
  const content = result.content;

  // Extract text from response content
  let output = "";
  const payloads: Array<{ text?: string; isError?: boolean; isReasoning?: boolean }> = [];

  if (Array.isArray(content)) {
    for (const block of content) {
      if (block.type === "text") {
        output += block.text;
        payloads.push({ text: block.text });
      } else if (block.type === "image") {
        payloads.push({ text: "[image]" });
      } else if (block.type === "resource") {
        payloads.push({ text: `[resource: ${block.resource?.uri ?? "unknown"}]` });
      }
    }
  } else if (typeof content === "string") {
    output = content;
    payloads.push({ text: content });
  } else if (content && typeof content === "object" && "text" in content) {
    output = content.text;
    payloads.push({ text: content.text });
  }

  return {
    output: output.trim() || undefined,
    payloads,
    meta: {
      model: result.model,
      stopReason: result.stopReason,
    },
  };
}

// ── MCP Protocol Types (subset for sampling) ──

/**
 * Minimal interface for objects that support MCP sampling.
 * This avoids depending on the full McpServer type from @modelcontextprotocol/sdk.
 */
export interface McpSamplingCapable {
  server: {
    createMessage(params: McpCreateMessageParams): Promise<McpCreateMessageResult>;
  };
}

/**
 * Subset of MCP `sampling/createMessage` params needed by our agent.
 * These match the @modelcontextprotocol/sdk types.
 */
export interface McpCreateMessageParams {
  messages: Array<{
    role: "user" | "assistant";
    content: { type: "text"; text: string } | unknown;
  }>;
  maxTokens: number;
  systemPrompt?: string;
  temperature?: number;
}

/**
 * Subset of MCP `sampling/createMessage` result.
 */
export interface McpCreateMessageResult {
  content: Array<{ type: string; text?: string; resource?: { uri?: string } }> | string | { text?: string };
  model: string;
  stopReason?: string;
}

/**
 * Options for configuring the MCP sampling agent.
 */
export interface McpSamplingAgentOptions {
  /** Default max tokens for sampling responses. Defaults to 4096. */
  defaultMaxTokens?: number;
  /** Default temperature for sampling. Defaults to 0.7. */
  defaultTemperature?: number;
  /** Prefix to prepend to all system prompts. */
  systemPromptPrefix?: string;
  /** Custom transform function for MCP results. */
  transformResult?: (result: McpCreateMessageResult) => EmbeddedAgentResult;
  /** Max retry attempts for transient errors. Default: 2 */
  maxRetries?: number;
  /** Base delay in ms for exponential backoff. Default: 1000 */
  retryBaseDelay?: number;
}