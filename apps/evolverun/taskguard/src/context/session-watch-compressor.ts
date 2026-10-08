/**
 * Session watch compressor — OpenClaw hook-based session compression.
 *
 * Provides factory functions that create hook handlers for runEmbeddedPiAgent.
 * These hooks integrate with the OpenClaw agent loop to provide proactive
 * session compression that takes effect during the agent's execution.
 *
 * ## Why hooks instead of file-only compression?
 *
 * The previous approach (maybeCompactSessionFile) modified the session JSONL
 * file on disk BEFORE calling runEmbeddedPiAgent. However, the runtime loads
 * the session into memory at SessionManager.open() and appends new messages
 * to the in-memory session. File-based modifications after the session is
 * loaded have no effect on the current run.
 *
 * The hook approach solves this in two ways:
 *
 * 1. **toolResultPersist** (PREVENTIVE — most effective): Truncates large tool
 *    outputs BEFORE they are persisted to the session. This directly prevents
 *    unbounded session growth — each tool result is capped at a configurable
 *    max size. This works because the hook intercepts the result BEFORE it
 *    reaches the session manager, so the truncated version is what gets stored.
 *
 * 2. **beforeAgentRun** (PROGRESSIVE): Compresses the session file once per
 *    agent run, before any LLM call. The file compression takes effect on the
 *    NEXT runEmbeddedPiAgent invocation (e.g., rate-limit retry or JSON repair),
 *    when the session is re-loaded from disk. Threshold decisions are based on
 *    the actual LLM messages (accurate), not the session file (potentially stale).
 *
 * 3. **beforePromptBuild** (NOTICE INJECTION): A thin hook that injects a
 *    compaction notice via prependContext so the model understands that older
 *    context was compressed. Reads stats stored by beforeAgentRun — no file I/O.
 *
 * ## Hook lifecycle in runEmbeddedPiAgent
 *
 * ```
 * before_model_resolve   → override model/provider
 *         ↓
 * agent_turn_prepare     → consume queued plugin injections
 *         ↓
 * before_prompt_build    → [THIS] inject compaction notice (thin, stats from before_agent_run)
 *         ↓
 * before_agent_run       → [THIS] threshold check + session file compression (once per run)
 *         ↓
 * [LLM call]
 *         ↓
 * before_tool_call       → intercept/rewrite/approve tool calls
 *         ↓
 * [Tool execution]
 *         ↓
 * after_tool_call        → observe tool results
 *         ↓
 * tool_result_persist    → [THIS] truncate tool outputs before saving
 *         ↓
 * before_agent_finalize  → request extra model pass or force final
 * agent_end              → observe complete run result
 * ```
 *
 * @module context/session-watch-compressor
 */

import { stat } from "node:fs/promises";

import {
  maybeCompactSessionFileSafe,
  SESSION_COMPRESSION_DEFAULTS,
  type SessionCompressionConfig,
  type SessionCompressionStats,
} from "./session-compressor.js";
import { estimateTokenUsageFromMessages } from "../token-usage.js";
import { estimateTextTokens } from "./token-counter.js";

// ── Hook Event / Result Types ──

/**
 * Event object received by the beforePromptBuild hook.
 * Based on the OpenClaw prompt lifecycle hook specification.
 */
export type BeforePromptBuildEvent = {
  /** Current session ID. */
  sessionId?: string;
  /** Session file path. */
  sessionFile?: string;
  /** Current message count. */
  messageCount?: number;
  /** Estimated token count for current messages (provided by runtime). */
  estimatedTokens?: number;
  /** The actual messages about to be sent to the LLM. */
  messages?: unknown[];
  /** Additional data from the runtime. */
  [key: string]: unknown;
};

/**
 * Return value from the beforePromptBuild hook.
 * Injects context into the prompt at various positions.
 */
export type BeforePromptBuildResult = {
  /** Prepended before the user context. */
  prependContext?: string;
  /** Appended to the system prompt. */
  appendSystemContext?: string;
  /** Prepended to the system prompt. */
  prependSystemContext?: string;
  /** Overrides the system prompt entirely. */
  systemPrompt?: string;
};

/**
 * Event object received by the toolResultPersist hook.
 * Based on the OpenClaw tool lifecycle hook specification.
 */
export type ToolResultPersistEvent = {
  /** Name of the tool that produced this result. */
  toolName?: string;
  /** Tool use ID for matching with the original tool call. */
  toolUseId?: string;
  /** The tool result content (string or content blocks array). */
  content?: string | Array<Record<string, unknown>>;
  /** Whether this result represents an error. */
  isError?: boolean;
  /** Additional data from the runtime. */
  [key: string]: unknown;
};

/**
 * Return value from the toolResultPersist hook.
 * Transforms the tool result before it's persisted to the session.
 */
export type ToolResultPersistResult = {
  /** Transformed content to persist instead of the original. */
  content?: string | Array<Record<string, unknown>>;
  /** Additional data to pass through. */
  [key: string]: unknown;
};

/**
 * Event object received by the beforeToolCall hook.
 */
export type BeforeToolCallEvent = {
  /** Name of the tool being called. */
  toolName: string;
  /** Parameters for the tool call. */
  params: Record<string, unknown>;
  /** Additional data from the runtime. */
  [key: string]: unknown;
};

/**
 * Return value from the beforeToolCall hook.
 * Can block the call, rewrite parameters, or require user approval.
 */
export type BeforeToolCallResult = {
  /** Block the tool call entirely. */
  block?: boolean;
  /** Reason for blocking (shown to the model). */
  blockReason?: string;
  /** Rewritten tool parameters (merged with original params). */
  params?: Record<string, unknown>;
  /** Require user approval before execution. */
  requireApproval?: {
    title: string;
    description: string;
    severity: "info" | "warning" | "error";
    timeoutMs?: number;
    timeoutBehavior?: "deny" | "allow";
  };
};

// ── Configuration ──

/** Default cooldown period (ms) between beforePromptBuild file compressions. */
const DEFAULT_COMPRESSION_COOLDOWN_MS = 30_000;

/**
 * Configuration for the session watch compressor hooks.
 */
export type SessionWatchCompressorConfig = {
  /** Session compression config passed to maybeCompactSessionFile. */
  sessionCompression?: Partial<SessionCompressionConfig>;
  /** Human-readable node name for log correlation (e.g. "data-preprocessing"). */
  nodeName?: string;
  /** Flow / run ID for log correlation. */
  flowId?: string;
  /**
   * Maximum chars for a single tool result before truncation in toolResultPersist.
   * Default: 5000
   */
  toolResultMaxChars?: number;
  /** Whether to inject compaction notices via prependContext. Default: true */
  injectCompactionNotice?: boolean;
  /** Whether the toolResultPersist hook is enabled. Default: true */
  toolPersistEnabled?: boolean;
  /** Whether the beforePromptBuild hook is enabled. Default: true */
  promptBuildEnabled?: boolean;
  /**
   * Minimum interval (ms) between beforePromptBuild file compressions.
   * Prevents redundant I/O when the agent loop makes LLM calls in rapid
   * succession. The first call always compresses; subsequent calls within
   * the cooldown period are skipped (prependContext is still injected if
   * the previous compression evicted messages).
   * Default: 30000 (30 seconds)
   */
  compressionCooldownMs?: number;
  /**
   * Target token budget for the session within the context window.
   * Used to compute effectiveMaxSessionTokens (budget minus non-session overhead)
   * and as the denominator for budgetRatio calculations.
   * When absent, falls back to maxSessionTokens.
   */
  contextTokenBudget?: number;
  /**
   * The model's actual context window size (tokens).
   * Used for the overhead-dominated check: if non-session overhead alone
   * exceeds this ceiling, compression is skipped early.
   * Falls back to contextTokenBudget if unset.
   */
  modelContextWindow?: number;
  /**
   * Callback for logging compression events.
   * Only called by the beforePromptBuild hook — toolResultPersist
   * truncations are not reported through this callback.
   */
  onCompress?: (stats: SessionCompressionStats & { phase: string }) => void;
  /**
   * Callback for observability events from hook lifecycle (fire, skip, compress, error).
   * Unlike onCompress (compression-only), this fires for every hook decision point
   * so operators can see what happened even when no compression occurs.
   * Wired to emitAgentEvent by embedded-agent.ts.
   */
  onHookEvent?: (event: { hook: string; action: string; detail: string; data?: Record<string, unknown> }) => void;
  /**
   * Suppress prependContext injection during active agent loops to preserve
   * Anthropic prefix cache stability.
   *
   * When true, the beforePromptBuild hook still performs file-based compression
   * (maybeCompactSessionFile) but does NOT return prependContext. This prevents
   * the prompt prefix from changing between turns within a single
   * runEmbeddedPiAgent() call, which would otherwise invalidate the Anthropic
   * prompt cache and cause full fresh-token billing on subsequent turns.
   *
   * The compaction notice is instead available via the session file's metadata
   * and can be injected at session initialization time (start of next node).
   *
   * Default: false (backward compatible)
   */
  suppressPrependContext?: boolean;
};

const DEFAULT_WATCH_CONFIG: Required<
  Omit<SessionWatchCompressorConfig, "onCompress" | "onHookEvent" | "sessionCompression" | "contextTokenBudget" | "modelContextWindow" | "suppressPrependContext" | "nodeName" | "flowId">
> = {
  toolResultMaxChars: 5000,
  injectCompactionNotice: true,
  toolPersistEnabled: true,
  promptBuildEnabled: true,
  compressionCooldownMs: DEFAULT_COMPRESSION_COOLDOWN_MS,
};

// ── Debounce state (per factory invocation) ──

/**
 * Tracks the last compression time to implement cooldown debounce.
 * Shared across all hook invocations from the same factory call
 * (i.e., per agent run).
 */
type CompressionDebounceState = {
  /** Timestamp (ms) of the last successful file compression. */
  lastCompressionTime: number;
  /** Stats from the most recent compression (for prependContext injection). */
  lastCompressionStats: SessionCompressionStats | null;
  /**
   * Path to the most recent sidecar cache file (null when no compression has
   * occurred yet). The safe compactor writes compressed output to this sidecar
   * instead of rewriting the active session JSONL in place.
   */
  lastSidecarPath: string | null;
};

// ── beforePromptBuild Hook ──

/**
 * Create a beforePromptBuild hook that compresses the session file
 * before each LLM call in the agent loop.
 *
 * This hook provides two mechanisms:
 *
 * 1. **File compression** (debounced): Reads and compresses the session JSONL
 *    file, writing the compressed version back. This takes effect on the NEXT
 *    runEmbeddedPiAgent invocation (e.g., rate-limit retry or JSON repair)
 *    when the session is re-loaded from disk. A cooldown prevents redundant
 *    I/O on rapid successive LLM calls.
 *
 * 2. **prependContext injection** (immediate): When compression evicts messages,
 *    a compaction notice is injected via prependContext. This takes effect
 *    immediately in the current LLM turn, helping the model understand that
 *    older context was compressed.
 *
 * The `toolResultPersist` hook (created separately) is the primary mechanism
 * for preventing session growth — it truncates large tool outputs BEFORE they
 * are saved. This hook handles the reactive case: compressing sessions that
 * have already grown large.
 *
 * @param sessionFile - Path to the session JSONL file
 * @param config - Configuration for the compression hooks
 * @returns A hook function compatible with OpenClaw's beforePromptBuild lifecycle
 */
export function createBeforePromptBuildHook(
  sessionFile: string | undefined,
  config?: SessionWatchCompressorConfig,
): (event: BeforePromptBuildEvent) => Promise<BeforePromptBuildResult> {
  const sessionConfig = {
    ...SESSION_COMPRESSION_DEFAULTS,
    ...config?.sessionCompression,
  };
  const injectNotice =
    config?.injectCompactionNotice ?? DEFAULT_WATCH_CONFIG.injectCompactionNotice;
  const suppressPrependContext = config?.suppressPrependContext ?? false;
  const cooldownMs =
    config?.compressionCooldownMs ?? DEFAULT_WATCH_CONFIG.compressionCooldownMs;
  const onCompress = config?.onCompress;
  const bpbTag = buildLogTag(config?.nodeName, config?.flowId);

  // Debounce state: tracks last compression time and stats.
  // This prevents redundant file I/O on rapid successive LLM calls
  // while still allowing prependContext injection if compression recently occurred.
  const debounce: CompressionDebounceState = {
    lastCompressionTime: 0,
    lastCompressionStats: null,
    lastSidecarPath: null,
  };

  return async (event: BeforePromptBuildEvent): Promise<BeforePromptBuildResult> => {
    const targetFile = sessionFile ?? (event.sessionFile as string | undefined);
    if (!targetFile) return {};

    // ── Threshold check: estimate tokens from session file ──
    // The hook event's prompt/messages fields are INCOMPLETE — they only contain
    // the node instruction text (event.prompt) and conversation messages, but NOT
    // the systemPrompt (~11K tokens) or tool definitions (~8K tokens). Using them
    // for threshold estimation severely underestimates actual LLM context size
    // (e.g., estimatedTokens=666 when actual context is ~20K+ tokens).
    //
    // Session compression targets the JSONL session file — the growing history
    // that accumulates across turns. The file size is a good heuristic for
    // deciding whether to compress, since it represents exactly what the
    // compressor will operate on.
    //
    // IMPORTANT: JSONL session files contain ~12% actual message content and
    // ~88% JSON overhead (metadata: id, parentId, timestamp; non-message entries:
    // session, model_change, thinking_level_change, custom). Using /4 (raw text
    // ratio) overestimates by ~8x. Using /12 better matches actual message token
    // counts observed in production (~31 bytes per message token).
    //
    // Estimation priority:
    // 1. Session file size (primary source for compression threshold)
    // 2. Cached estimate from before_agent_start (when file is empty at prompt-build time)
    // 3. Runtime-provided estimatedTokens (if available)
    // 4. Messages-based estimation
    // 5. Prompt text fallback (first LLM call, empty messages)
    const FILE_SIZE_TOKEN_DIVISOR = 12;
    let estimatedTokens = 0;
    let estimationSource = "none";

    // 1. Session file size — primary estimation source for compression threshold
    if (targetFile) {
      try {
        const info = await stat(targetFile);
        if (info.size > 0) {
          estimatedTokens = Math.ceil(info.size / FILE_SIZE_TOKEN_DIVISOR);
          estimationSource = "session-file";
        } else {
          console.log(
            `${bpbTag} beforePromptBuild: targetFile exists but size=0, ` +
            `session may have just started`,
          );
        }
      } catch (err) {
        console.log(
          `${bpbTag} beforePromptBuild: stat failed for targetFile=${targetFile}: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    // 2. Cached estimate from before_agent_start (reliable when file is empty)
    // Note: createBeforePromptBuildHook doesn't have access to the registry entry,
    // so this step is only available in the inline handler (src/index.ts).
    // This hook is used by subagents which typically have existing session files.

    // 3. Runtime-provided estimate
    if (estimatedTokens <= 0 && event.estimatedTokens && event.estimatedTokens > 0) {
      estimatedTokens = event.estimatedTokens;
      estimationSource = "runtime";
    }

    // 4. Messages-based estimation
    if (estimatedTokens <= 0 && event.messages && Array.isArray(event.messages)) {
      const usage = estimateTokenUsageFromMessages(event.messages as unknown[]);
      estimatedTokens = usage?.totalTokens ?? 0;
      if (estimatedTokens > 0) estimationSource = "messages";
    }

    // 5. Prompt text fallback (first LLM call: messages=[] but prompt has content)
    if (estimatedTokens <= 0 && event.prompt && typeof event.prompt === "string") {
      estimatedTokens = estimateTextTokens(event.prompt);
      if (estimatedTokens > 0) estimationSource = "prompt";
    }

    const needsCompression = estimatedTokens >= sessionConfig.minTokensToCompact;
    console.log(
      `${bpbTag} beforePromptBuild: ` +
      `estimatedTokens=${estimatedTokens}, minTokensToCompact=${sessionConfig.minTokensToCompact}, ` +
      `needsCompression=${needsCompression}, source=${estimationSource}`,
    );

    // Context is within budget — skip compression entirely.
    if (!needsCompression) return {};

    // ── Debounce for file compression ──
    const now = Date.now();
    const timeSinceLastCompression = now - debounce.lastCompressionTime;
    const shouldCompressFile = timeSinceLastCompression >= cooldownMs;

    // Inject prependContext from a recent compression, even if we skip
    // file compression this turn due to cooldown.
    // When suppressPrependContext is true, skip injection to preserve
    // Anthropic prefix cache stability during agent loops.
    if (
      !shouldCompressFile
      && debounce.lastCompressionStats
      && injectNotice
      && !suppressPrependContext
      && debounce.lastCompressionStats.messagesEvicted > 0
    ) {
      console.log(
        `${bpbTag} beforePromptBuild: cooldown skip, injecting cached prependContext, ` +
        `timeSinceLastCompress=${timeSinceLastCompression}ms > cooldownMs=${cooldownMs}ms`,
      );
      const stats = debounce.lastCompressionStats;
      return {
        prependContext: [
          `[上下文压缩通知]`,
          `为节省 token，历史会话已自动压缩。`,
          `原始消息数：${stats.inputMessages}，压缩后：${stats.outputMessages}，节省约 ${stats.inputTokens - stats.outputTokens} tokens。`,
          `最近的消息保持完整，更早的冗长工具输出已被截断或移除。`,
          `如需查看被压缩的细节，请参考节点执行日志。`,
        ].join("\n"),
      };
    }
    if (
      !shouldCompressFile
      && debounce.lastCompressionStats
      && injectNotice
      && suppressPrependContext
    ) {
      console.log(
        `${bpbTag} beforePromptBuild: cooldown skip, suppressPrependContext=true, ` +
        `skipping prependContext injection to preserve cache prefix`,
      );
    }

    try {
      // Use the SAFE sidecar variant — compressed output goes to
      // <file>.compressed.jsonl, original JSONL is never modified.
      // This preserves the full tool_call/tool_result history so that
      // extractNodeStepTrace can read the complete session after the
      // node completes. Previously used in-place compression (maybeCompactSessionFile)
      // which overwrote the original JSONL, causing step traces to be lost
      // when the file was compressed multiple times during the agent loop.
      // See docs/openspec/step-trace-compression-fix/ for full analysis.
      const result = await maybeCompactSessionFileSafe(targetFile, {
        maxSessionTokens: sessionConfig.maxSessionTokens,
        minTokensToCompact: 0, // always try compression since we already know context exceeds threshold
        recencyWindow: sessionConfig.recencyWindow,
        toolPrepassEnabled: sessionConfig.toolPrepassEnabled,
        toolResultMaxChars: sessionConfig.toolResultMaxChars,
        deduplicateReads: sessionConfig.deduplicateReads,
        readDedupTtlMs: sessionConfig.readDedupTtlMs,
      });

      if (result.kind === "compressed") {
        debounce.lastCompressionTime = now;
        debounce.lastCompressionStats = result.stats;
        debounce.lastSidecarPath = result.sidecarPath;
        console.log(
          `${bpbTag} beforePromptBuild: compressed session file (sidecar), ` +
          `${result.stats.inputTokens}→${result.stats.outputTokens} tokens, ` +
          `messagesEvicted=${result.stats.messagesEvicted}, ` +
          `toolsCompressed=${result.stats.toolResultsCompressed}, ` +
          `sidecar=${result.sidecarPath}`,
        );
        onCompress?.({ ...result.stats, phase: "beforePromptBuild" });
      } else if (result.kind === "skipped") {
        const reason = result.reason;
        const inTok = result.inputTokens;
        const inMsg = result.inputMessages;
        console.log(
          `${bpbTag} beforePromptBuild: compression skipped (reason=${reason}, ` +
          `inputTokens=${inTok ?? "n/a"}, inputMessages=${inMsg ?? "n/a"})`,
        );
      } else if (result.kind === "error") {
        console.warn(
          `${bpbTag} beforePromptBuild: compression error: ${result.error}`,
        );
      }

      // Inject compaction notice based on actual context size.
      // When suppressPrependContext is true, skip injection to preserve
      // Anthropic prefix cache stability during agent loops.
      if (injectNotice && !suppressPrependContext) {
        if (result.kind === "compressed" && result.stats.messagesEvicted > 0) {
          return {
            prependContext: [
              `[上下文压缩通知]`,
              `为节省 token，历史会话已自动压缩。`,
              `原始消息数：${result.stats.inputMessages}，压缩后：${result.stats.outputMessages}，节省约 ${result.stats.inputTokens - result.stats.outputTokens} tokens。`,
              `最近的消息保持完整，更早的冗长工具输出已被截断或移除。`,
              `如需查看被压缩的细节，请参考节点执行日志。`,
            ].join("\n"),
          };
        }
        if (estimatedTokens >= sessionConfig.maxSessionTokens) {
          return {
            prependContext: [
              `[上下文压缩通知]`,
              `当前上下文约 ${estimatedTokens} tokens，已超过预算 ${sessionConfig.maxSessionTokens} tokens。`,
              `会话历史中较旧的冗长工具输出将在下次加载时被截断或移除。`,
              `如需查看被压缩的细节，请参考节点执行日志。`,
            ].join("\n"),
          };
        }
      }
      if (injectNotice && suppressPrependContext) {
        console.log(
          `${bpbTag} beforePromptBuild: suppressPrependContext=true, ` +
          `skipping prependContext injection to preserve cache prefix`,
        );
      }
    } catch (error) {
      // Don't block the agent loop on compression failure
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(
        `${bpbTag} beforePromptBuild compression failed: ${msg}`,
      );
    }

    return {};
  };
}

// ── toolResultPersist Hook ──

/**
 * Create a toolResultPersist hook that truncates large tool results
 * before they are persisted to the session file.
 *
 * This is the most effective compression strategy because it prevents
 * the session from growing in the first place. Each tool result is
 * checked against a maximum size limit and truncated if necessary.
 *
 * Error results are preserved in full — only successful tool outputs
 * are truncated, since errors may need to be seen completely for
 * debugging and recovery.
 *
 * For string content: truncated when it exceeds `toolResultMaxChars`.
 * For array content blocks: truncated when total text exceeds
 * `toolResultMaxChars * 2` (the 2x multiplier accounts for the
 * structural overhead of content blocks like tool_use and image blocks
 * that don't contain truncatable text).
 *
 * @param config - Configuration for the compression hooks
 * @returns A synchronous hook function compatible with OpenClaw's toolResultPersist lifecycle
 */
export function createToolResultPersistHook(
  config?: SessionWatchCompressorConfig,
): (event: ToolResultPersistEvent) => ToolResultPersistResult {
  const maxResultChars =
    config?.toolResultMaxChars ?? DEFAULT_WATCH_CONFIG.toolResultMaxChars;
  const enabled =
    config?.toolPersistEnabled ?? DEFAULT_WATCH_CONFIG.toolPersistEnabled;
  const trhTag = buildLogTag(config?.nodeName, config?.flowId);

  return (event: ToolResultPersistEvent): ToolResultPersistResult => {
    if (!enabled) return {};

    // Always preserve error results in full
    if (event.isError) return {};

    const content = event.content;
    if (!content) return {};

    // Handle string content
    if (typeof content === "string") {
      if (content.length <= maxResultChars) return {};
      const truncated = truncateToolResult(content, maxResultChars, event.toolName);
      console.log(
        `${trhTag} toolResultPersist: truncated tool=${event.toolName ?? "unknown"}, ` +
        `${content.length}→${truncated.length} chars (maxResultChars=${maxResultChars})`,
      );
      return { content: truncated };
    }

    // Handle array content blocks (Anthropic-style content blocks)
    // Use a 2x threshold for array content to account for structural overhead
    // (tool_use blocks, image blocks, etc. that don't contain truncatable text).
    if (Array.isArray(content)) {
      let totalChars = 0;
      for (const block of content) {
        if (
          typeof block === "object"
          && block !== null
          && block.type === "text"
          && typeof block.text === "string"
        ) {
          totalChars += block.text.length;
        }
      }

      // Only truncate if total text content exceeds 2x the limit
      if (totalChars <= maxResultChars * 2) return {};

      console.log(
        `${trhTag} toolResultPersist: truncating array content, ` +
        `tool=${event.toolName ?? "unknown"}, totalTextChars=${totalChars}, ` +
        `threshold=${maxResultChars * 2} (2×maxResultChars)`,
      );

      let remaining = maxResultChars;
      const truncated = content.map((block) => {
        if (
          typeof block === "object"
          && block !== null
          && block.type === "text"
          && typeof block.text === "string"
        ) {
          if (remaining <= 0) {
            // No budget left — replace with a truncation placeholder
            // (NOT an empty string, which would silently discard information)
            return {
              ...block,
              text: `[... 输出已省略，超出压缩预算 ...]`,
            };
          }
          if (block.text.length <= remaining) {
            remaining -= block.text.length;
            return block;
          }
          // Truncate this block's text
          const truncatedText =
            block.text.slice(0, remaining) +
            `\n\n[... 输出已截断，原始长度 ${block.text.length.toLocaleString()} 字符，保留前 ${remaining.toLocaleString()} 字符 ...]`;
          remaining = 0;
          return { ...block, text: truncatedText };
        }
        return block;
      });

      return { content: truncated };
    }

    return {};
  };
}

// ── beforeToolCall Hook ──

/**
 * Create a beforeToolCall hook that can monitor and optionally control
 * tool execution based on session state.
 *
 * Currently a no-op monitor — returns an empty result (allow all tool calls).
 * Reserved for future use:
 * - Block expensive operations when session is critically large
 * - Rewrite tool parameters (e.g., add --quiet flags, limit output sizes)
 * - Require user approval for risky operations
 *
 * @param _config - Configuration (reserved for future use)
 * @returns A hook function compatible with OpenClaw's beforeToolCall lifecycle
 */
export function createBeforeToolCallHook(
  _config?: SessionWatchCompressorConfig,
): (event: BeforeToolCallEvent) => BeforeToolCallResult {
  // Currently a no-op — allows all tool calls through.
  // Future: monitor session token count, block/warn when approaching limits.
  return (_event: BeforeToolCallEvent): BeforeToolCallResult => {
    return {};
  };
}

// ── Convenience Factory ──

/**
 * Compression hooks ready to be passed to runEmbeddedPiAgent.
 */
export type CompressionHooks = {
  /**
   * Called before each LLM prompt is built.
   * Compresses the session file (debounced) and injects a compaction notice
   * via prependContext if messages were evicted.
   */
  beforePromptBuild?: (event: BeforePromptBuildEvent) => Promise<BeforePromptBuildResult>;
  /**
   * Called before a tool result is persisted to the session.
   * Truncates large tool outputs to prevent unbounded session growth.
   * This is the primary mechanism for proactive compression.
   */
  toolResultPersist?: (event: ToolResultPersistEvent) => ToolResultPersistResult;
  /**
   * Called before a tool is executed.
   * Reserved for future use — currently a no-op that allows all tool calls.
   */
  beforeToolCall?: (event: BeforeToolCallEvent) => BeforeToolCallResult;
};

/**
 * Create the full set of compression hooks for runEmbeddedPiAgent.
 *
 * Returns an object with all hook functions ready to be passed
 * as parameters to the runtime. Hooks that are disabled in the
 * config are set to undefined.
 *
 * @param sessionFile - Path to the session JSONL file
 * @param config - Configuration for the compression hooks
 * @returns Hook functions for runEmbeddedPiAgent
 */
export function createCompressionHooks(
  sessionFile: string | undefined,
  config?: SessionWatchCompressorConfig,
): CompressionHooks {
  const promptBuildEnabled = config?.promptBuildEnabled !== false;
  const toolPersistEnabled = config?.toolPersistEnabled !== false;
  const suppressPrependContext = config?.suppressPrependContext ?? false;
  const cchTag = buildLogTag(config?.nodeName, config?.flowId);
  console.log(
    `${cchTag} createCompressionHooks: ` +
    `sessionFile=${sessionFile ?? "undefined"}, ` +
    `promptBuildEnabled=${promptBuildEnabled}, toolPersistEnabled=${toolPersistEnabled}, ` +
    `toolResultMaxChars=${config?.toolResultMaxChars ?? DEFAULT_WATCH_CONFIG.toolResultMaxChars}, ` +
    `compressionCooldownMs=${config?.compressionCooldownMs ?? DEFAULT_WATCH_CONFIG.compressionCooldownMs}, ` +
    `suppressPrependContext=${suppressPrependContext}`,
  );
  return {
    beforePromptBuild:
      promptBuildEnabled
        ? createBeforePromptBuildHook(sessionFile, config)
        : undefined,
    toolResultPersist:
      toolPersistEnabled
        ? createToolResultPersistHook(config)
        : undefined,
    // beforeToolCall is created even when disabled (it's a no-op),
    // but is intentionally NOT wired in the agent call. It's reserved
    // for future use and included in the type for API completeness.
    beforeToolCall: createBeforeToolCallHook(config),
  };
}

// ── Truncation Utility ──

/**
 * Truncate a tool result string with a smart boundary.
 *
 * Tries to truncate at a newline boundary, preserving complete lines.
 * Adds a truncation notice indicating the original size and the tool name.
 *
 * Note: The returned string may slightly exceed `maxChars` due to the
 * truncation notice suffix. The `maxChars` parameter controls the content
 * portion; the notice is added on top. This is intentional — the notice
 * is essential for the model to understand that content was removed.
 *
 * @param content - The tool result string to truncate
 * @param maxChars - Maximum characters for the content portion (before notice)
 * @param toolName - Optional tool name for the truncation notice
 * @returns The truncated string with a notice suffix, or the original if within limit
 */
export function truncateToolResult(
  content: string,
  maxChars: number,
  toolName?: string,
): string {
  if (content.length <= maxChars) return content;

  // Try to find a good truncation boundary (newline or space)
  let boundary = maxChars;
  const lastNewline = content.lastIndexOf("\n", maxChars);
  if (lastNewline > maxChars * 0.5) {
    boundary = lastNewline;
  } else {
    const lastSpace = content.lastIndexOf(" ", maxChars);
    if (lastSpace > maxChars * 0.5) {
      boundary = lastSpace;
    }
  }

  const toolLabel = toolName ? `工具 ${toolName} 输出` : "工具输出";
  const notice = `\n\n[... ${toolLabel}已截断，原始长度 ${content.length.toLocaleString()} 字符，保留前 ${boundary.toLocaleString()} 字符 ...]`;

  return content.slice(0, boundary) + notice;
}

// ── Per-session compression config registry ──────────────────────────────────
//
// Used by OpenClaw plugin hooks (registered via api.on()) to look up
// per-session compression settings.  The registry is populated by
// embedded-agent.ts before calling runEmbeddedPiAgent and cleaned up
// in a finally block after the agent completes.
//
// This replaces the previous approach of passing beforePromptBuild /
// toolResultPersist as parameters to runEmbeddedPiAgent, which OpenClaw's
// RunEmbeddedAgentParams type does not include — those params were silently
// ignored by the runtime.

type SessionCompressionRegistryEntry = {
  /** Path to the session JSONL file. */
  sessionFile: string;
  /** Compression config passed from the workflow node execution. */
  config: SessionWatchCompressorConfig;
  /** When this entry was registered (ms epoch). */
  registeredAt: number;
  /** Human-readable node name for log correlation (e.g. "data-preprocessing"). */
  nodeName?: string;
  /** Flow / run ID for log correlation. */
  flowId?: string;
  /** Callback for logging compression events from the hook handler. */
  onCompress?: (stats: SessionCompressionStats & { phase: string }) => void;
  /** Debounce state for before_prompt_build hook (per-session). */
  debounce: CompressionDebounceState;
  /**
   * Token count estimated by before_agent_run from the actual LLM messages.
   * Read by the thin before_prompt_build notice-injection hook.
   * Based on session-file size or prompt analysis — may severely
   * underestimate the full LLM prompt (missing system prompt, tool defs,
   * skill content). Used for SESSION FILE compression threshold checks.
   */
  lastEstimatedTokens?: number;
  /**
   * Actual effective input tokens from the last LLM API response
   * (input only, excluding cacheRead). Updated by llm_output hook.
   * Used for budgetRatio calculations and compression decisions.
   */
  lastActualInputTokens?: number;
  /**
   * History of LLM call token usage within this session (max 20 entries).
   * Used to diagnose cacheRead behavior — whether it occupies context window
   * or is served from external prefix cache.
   */
  llmCallHistory: Array<{
    callIndex: number;
    input: number;
    cacheRead: number;
    effective: number;
    sessionFileTokens: number;
    timestamp: number;
  }>;
};

const sessionCompressionRegistry = new Map<string, SessionCompressionRegistryEntry>();

/**
 * Build a structured log tag for session-watch-compressor messages.
 * Includes flowId and nodeName when available for log correlation.
 *
 * Examples:
 * - `[session-watch-compressor] [flowId=abc123, node=data-preprocessing]`
 * - `[session-watch-compressor] [node=data-preprocessing]`
 * - `[session-watch-compressor]`
 */
function buildLogTag(nodeName?: string, flowId?: string): string {
  const tags: string[] = [];
  if (flowId) tags.push(`flowId=${flowId}`);
  if (nodeName) tags.push(`node=${nodeName}`);
  if (tags.length > 0) {
    return `[session-watch-compressor] [${tags.join(", ")}]`;
  }
  return "[session-watch-compressor]";
}

/**
 * Build a log tag from a session compression registry entry (convenience wrapper).
 * Exported for use by index.ts hook handlers.
 */
export function compressionLogTag(entry?: SessionCompressionRegistryEntry | null): string {
  return buildLogTag(entry?.nodeName, entry?.flowId);
}

/**
 * Register per-session compression config so OpenClaw plugin hooks can
 * look it up during the agent loop.
 *
 * Called by embedded-agent.ts before invoking runEmbeddedPiAgent.
 */
export function registerSessionCompressionConfig(
  sessionKey: string,
  sessionFile: string,
  config: SessionWatchCompressorConfig,
  onCompress?: (stats: SessionCompressionStats & { phase: string }) => void,
  nodeName?: string,
  flowId?: string,
): void {
  const effectiveNodeName = nodeName ?? config.nodeName;
  const effectiveFlowId = flowId ?? config.flowId;
  sessionCompressionRegistry.set(sessionKey, {
    sessionFile,
    config,
    registeredAt: Date.now(),
    nodeName: effectiveNodeName,
    flowId: effectiveFlowId,
    onCompress,
    debounce: { lastCompressionTime: 0, lastCompressionStats: null, lastSidecarPath: null },
    llmCallHistory: [],
  });
  const regTag = buildLogTag(effectiveNodeName, effectiveFlowId);
  console.log(
    `${regTag} registerSessionCompressionConfig: ` +
    `sessionKey=${sessionKey}, sessionFile=${sessionFile}`,
  );
}

/**
 * Unregister per-session compression config after the agent run completes.
 *
 * Called by embedded-agent.ts in a finally block after runEmbeddedPiAgent.
 */
export function unregisterSessionCompressionConfig(
  sessionKey: string,
): void {
  const entry = sessionCompressionRegistry.get(sessionKey);
  const tag = buildLogTag(entry?.nodeName, entry?.flowId);
  if (entry && entry.llmCallHistory.length > 0) {
    const h = entry.llmCallHistory;
    const summary = h.map((c) =>
      `#${c.callIndex}[in=${c.input},cache=${c.cacheRead},eff=${c.effective},sess=${c.sessionFileTokens}]`
    ).join(" → ");
    console.log(
      `${tag} CACHE_DIAG_SUMMARY: sessionKey=${sessionKey}, ` +
      `calls=${h.length}, history: ${summary}`,
    );
  }
  const deleted = sessionCompressionRegistry.delete(sessionKey);
  console.log(
    `${tag} unregisterSessionCompressionConfig: ` +
    `sessionKey=${sessionKey}, wasRegistered=${deleted}`,
  );
}

/**
 * Look up a registered session compression entry.
 * Used by plugin hook handlers in index.ts.
 */
export function getSessionCompressionEntry(
  sessionKey: string,
): SessionCompressionRegistryEntry | undefined {
  return sessionCompressionRegistry.get(sessionKey);
}

/**
 * Update the actual token estimate from an LLM API response.
 *
 * Called by embedded-agent.ts after each `runEmbeddedPiAgent` call
 * with the real (input + cacheRead) token count from the API.
 * This provides the ground-truth prompt size that the session-file-based
 * estimate (`lastEstimatedTokens`) cannot capture, because the session
 * file excludes system prompt, tool definitions, and skill content
 * (typically 10-50x overhead).
 *
 * The next `tool_result_persist` hook will use this for budgetRatio
 * calculations instead of the session-file-based estimate.
 */
export function updateSessionActualTokenEstimate(
  sessionKey: string,
  effectiveInputTokens: number,
): void {
  const entry = sessionCompressionRegistry.get(sessionKey);
  if (!entry) return;
  const previous = entry.lastActualInputTokens;
  entry.lastActualInputTokens = effectiveInputTokens;
  const budget = entry.config.contextTokenBudget ?? entry.config.sessionCompression?.maxSessionTokens ?? 50_000;
  const ratio = budget > 0 ? effectiveInputTokens / budget : 0;
  const tag = buildLogTag(entry.nodeName, entry.flowId);
  console.log(
    `${tag} updateSessionActualTokenEstimate: ` +
    `sessionKey=${sessionKey}, effectiveInputTokens=${effectiveInputTokens}, ` +
    `previous=${previous ?? "n/a"}, budget=${budget}, budgetRatio=${(ratio * 100).toFixed(1)}%`,
  );
}

// ── OpenClaw Plugin Hook Handlers ────────────────────────────────────────────
//
// These handlers are registered once at plugin init via api.on() and apply
// to ALL sessions.  They check the per-session registry to determine whether
// compression should be applied; if the session key is not registered, the
// handler returns an empty result (no-op), so non-ClawMind sessions are
// unaffected.

/**
 * Create a `before_agent_run` plugin hook handler for OpenClaw.
 *
 * The handler:
 * 1. Checks the registry for the current session key.
 * 2. Estimates tokens from the actual LLM messages in the hook event
 *    (not from the session file, which may be stale).
 * 3. If tokens exceed the threshold, compresses the session file.
 * 4. Stores compression stats in the registry for the thin
 *    before_prompt_build notice-injection hook.
 *
 * This is a gate hook — it can only pass or block the run. We always pass.
 * The actual compression (file I/O) happens here; the before_prompt_build
 * hook only injects a prependContext notice based on the stats we store.
 *
 * Fires once per agent run (not per LLM call), so no debounce is needed.
 *
 * Type signature matches OpenClaw's PluginHookBeforeAgentRunEvent /
 * PluginHookAgentContext.
 */
export function createBeforeAgentRunPluginHandler(): (
  event: { prompt: string; messages: unknown[] },
  context: { sessionKey?: string; agentId?: string; sessionId?: string; runId?: string; [key: string]: unknown },
) => Promise<void> {
  return async (
    event: { prompt: string; messages: unknown[] },
    context: { sessionKey?: string; agentId?: string; sessionId?: string; runId?: string; [key: string]: unknown },
  ): Promise<void> => {
    const sessionKey = context.sessionKey;
    if (!sessionKey) return;

    const entry = getSessionCompressionEntry(sessionKey);
    if (!entry) return;

    const { sessionFile, config, onCompress, debounce } = entry;
    const bauTag = buildLogTag(entry.nodeName, entry.flowId);
    if (config.promptBuildEnabled === false) return;

    const sessionConfig = {
      ...SESSION_COMPRESSION_DEFAULTS,
      ...config.sessionCompression,
    };
    const minTokensToCompact = sessionConfig.minTokensToCompact;
    const maxSessionTokens = sessionConfig.maxSessionTokens;

    // ── Threshold check: estimate tokens from session file ──
    // See createBeforePromptBuildHook for rationale: event.prompt/messages
    // miss systemPrompt (~11K tokens) and tool definitions (~8K tokens),
    // leading to severe underestimation. Session file size is the right metric
    // because it represents what the compressor actually operates on.
    let estimatedTokens = 0;
    let estimationSource = "none";

    const FILE_SIZE_TOKEN_DIVISOR = 12; // JSONL: ~31 bytes per actual message token (88% JSON overhead)

    // 1. Session file size — most accurate for compression target
    if (sessionFile) {
      try {
        const info = await stat(sessionFile);
        if (info.size > 0) {
          estimatedTokens = Math.ceil(info.size / FILE_SIZE_TOKEN_DIVISOR);
          estimationSource = "session-file";
        } else {
          console.log(
            `${bauTag} beforeAgentRunPlugin: sessionFile exists but size=0, ` +
            `session may have just started`,
          );
        }
      } catch (err) {
        console.log(
          `${bauTag} beforeAgentRunPlugin: stat failed for sessionFile=${sessionFile}: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    // 2. Messages-based estimation
    const messages = event.messages;
    if (estimatedTokens <= 0 && messages && Array.isArray(messages)) {
      const usage = estimateTokenUsageFromMessages(messages);
      estimatedTokens = usage?.totalTokens ?? 0;
      if (estimatedTokens > 0) estimationSource = "messages";
    }

    // 3. Prompt text fallback (first LLM call: messages=[] but prompt has content)
    if (estimatedTokens <= 0 && event.prompt && typeof event.prompt === "string") {
      estimatedTokens = estimateTextTokens(event.prompt);
      if (estimatedTokens > 0) estimationSource = "prompt";
    }

    const needsCompression = estimatedTokens >= minTokensToCompact;
    console.log(
      `${bauTag} before_agent_run: sessionKey=${sessionKey}, ` +
      `estimatedTokens=${estimatedTokens}, minTokensToCompact=${minTokensToCompact}, ` +
      `needsCompression=${needsCompression}, source=${estimationSource}`,
    );

    // Store the token estimate for before_prompt_build to read.
    entry.lastEstimatedTokens = estimatedTokens;

    // Context is within budget — skip compression.
    if (!needsCompression) return;

    try {
      // Use the SAFE sidecar variant — compressed output goes to
      // <file>.compressed.jsonl, original JSONL is never modified.
      // This preserves the full tool_call/tool_result history so that
      // extractNodeStepTrace can read the complete session after the
      // node completes. See docs/openspec/step-trace-compression-fix/.
      const result = await maybeCompactSessionFileSafe(sessionFile, {
        maxSessionTokens,
        minTokensToCompact: 0, // always try since we already know context exceeds threshold
        recencyWindow: sessionConfig.recencyWindow,
        toolPrepassEnabled: sessionConfig.toolPrepassEnabled,
        toolResultMaxChars: sessionConfig.toolResultMaxChars,
        deduplicateReads: sessionConfig.deduplicateReads,
        readDedupTtlMs: sessionConfig.readDedupTtlMs,
      });

      if (result.kind === "compressed") {
        const now = Date.now();
        debounce.lastCompressionTime = now;
        debounce.lastCompressionStats = result.stats;
        debounce.lastSidecarPath = result.sidecarPath;
        console.log(
          `${bauTag} before_agent_run: compressed session file (sidecar), ` +
          `${result.stats.inputTokens}→${result.stats.outputTokens} tokens, ` +
          `messagesEvicted=${result.stats.messagesEvicted}, ` +
          `toolsCompressed=${result.stats.toolResultsCompressed}, ` +
          `sidecar=${result.sidecarPath}`,
        );
        onCompress?.({ ...result.stats, phase: "beforeAgentRun" });
      } else if (result.kind === "skipped") {
        const reason = result.reason;
        const inTok = result.inputTokens;
        const inMsg = result.inputMessages;
        console.log(
          `${bauTag} before_agent_run: compression skipped (reason=${reason}, ` +
          `inputTokens=${inTok ?? "n/a"}, inputMessages=${inMsg ?? "n/a"})`,
        );
      } else if (result.kind === "error") {
        console.warn(
          `${bauTag} before_agent_run: compression error: ${result.error}`,
        );
      }
    } catch (error) {
      // Don't block the agent run on compression failure
      const msg = error instanceof Error ? error.message : String(error);
      console.warn(
        `${bauTag} before_agent_run compression failed: ${msg}`,
      );
    }

    // Always pass — we never block the agent run.
  };
}

/**
 * Create a thin `before_prompt_build` plugin hook handler for OpenClaw.
 *
 * This handler only injects a compaction notice via prependContext.
 * It reads stats stored by the `before_agent_run` handler — no file I/O.
 *
 * Type signature matches OpenClaw's PluginHookBeforePromptBuildEvent /
 * PluginHookAgentContext.
 */
export function createBeforePromptBuildPluginHandler(): (
  event: { prompt: string; messages: unknown[] },
  context: { sessionKey?: string; agentId?: string; sessionId?: string; runId?: string; [key: string]: unknown },
) => BeforePromptBuildResult {
  return (
    _event: { prompt: string; messages: unknown[] },
    context: { sessionKey?: string; agentId?: string; sessionId?: string; runId?: string; [key: string]: unknown },
  ): BeforePromptBuildResult => {
    const sessionKey = context.sessionKey;
    if (!sessionKey) return {};

    const entry = getSessionCompressionEntry(sessionKey);
    if (!entry) return {};

    const { config, debounce } = entry;
    if (config.promptBuildEnabled === false) return {};

    const injectNotice = config.injectCompactionNotice ?? DEFAULT_WATCH_CONFIG.injectCompactionNotice;
    if (!injectNotice) return {};

    const sc = config.sessionCompression ?? {};
    const maxSessionTokens = sc.maxSessionTokens ?? SESSION_COMPRESSION_DEFAULTS.maxSessionTokens;

    // If before_agent_run compressed and evicted messages, inject a notice.
    if (debounce.lastCompressionStats && debounce.lastCompressionStats.messagesEvicted > 0) {
      const stats = debounce.lastCompressionStats;
      return {
        prependContext: [
          `[上下文压缩通知]`,
          `为节省 token，历史会话已自动压缩。`,
          `原始消息数：${stats.inputMessages}，压缩后：${stats.outputMessages}，节省约 ${stats.inputTokens - stats.outputTokens} tokens。`,
          `最近的消息保持完整，更早的冗长工具输出已被截断或移除。`,
          `如需查看被压缩的细节，请参考节点执行日志。`,
        ].join("\n"),
      };
    }

    // If context exceeds budget but file compression didn't help, inject a generic notice.
    const estimatedTokens = entry.lastEstimatedTokens ?? 0;
    if (estimatedTokens >= maxSessionTokens) {
      return {
        prependContext: [
          `[上下文压缩通知]`,
          `当前上下文约 ${estimatedTokens} tokens，已超过预算 ${maxSessionTokens} tokens。`,
          `会话历史中较旧的冗长工具输出将在下次加载时被截断或移除。`,
          `如需查看被压缩的细节，请参考节点执行日志。`,
        ].join("\n"),
      };
    }

    return {};
  };
}

/**
 * Create a `tool_result_persist` plugin hook handler for OpenClaw.
 *
 * The handler:
 * 1. Checks the registry for the current session key.
 * 2. If registered, truncates large tool outputs before they are persisted.
 * 3. Returns `{ message: modifiedMessage }` if truncation occurred.
 *
 * This replaces the previous approach of passing `toolResultPersist` as a
 * parameter to `runEmbeddedPiAgent`, which the OpenClaw runtime ignored.
 *
 * The OpenClaw `tool_result_persist` hook operates on AgentMessage objects,
 * not raw content strings. The hook receives a message containing the tool
 * result and can return a modified message. The handler is synchronous
 * (returns the result directly, not a Promise).
 *
 * Type signature matches OpenClaw's PluginHookToolResultPersistEvent /
 * PluginHookToolResultPersistContext.
 */
export function createToolResultPersistPluginHandler(): (
  event: { toolName?: string; toolCallId?: string; message: unknown; isSynthetic?: boolean },
  context: { agentId?: string; sessionKey?: string; toolName?: string; toolCallId?: string },
) => { message?: unknown } | void {
  return (
    event: { toolName?: string; toolCallId?: string; message: unknown; isSynthetic?: boolean },
    context: { agentId?: string; sessionKey?: string; toolName?: string; toolCallId?: string },
  ): { message?: unknown } | void => {
    const sessionKey = context.sessionKey;
    if (!sessionKey) return;

    const entry = getSessionCompressionEntry(sessionKey);
    if (!entry) return;

    const { config } = entry;
    const trpTag = buildLogTag(entry.nodeName, entry.flowId);
    const enabled = config.toolPersistEnabled ?? DEFAULT_WATCH_CONFIG.toolPersistEnabled;
    if (!enabled) return;

    const toolName = event.toolName ?? context.toolName;

    // The message is an AgentMessage object. We need to inspect its content
    // and truncate if necessary. AgentMessage can be a tool_result message
    // with content in message.content (string or content blocks array).
    const message = event.message as Record<string, unknown> | null;
    if (!message || typeof message !== "object") return;

    // Skip error results and synthetic messages
    if (event.isSynthetic) return;
    const isError = message.is_error as boolean | undefined;
    if (isError) return;

    const content = message.content as string | Array<Record<string, unknown>> | undefined;
    if (!content) return;

    const maxResultChars = config.toolResultMaxChars ?? DEFAULT_WATCH_CONFIG.toolResultMaxChars;

    // Handle string content
    if (typeof content === "string") {
      if (content.length <= maxResultChars) return;
      const truncated = truncateToolResult(content, maxResultChars, toolName);
      console.log(
        `${trpTag} tool_result_persist: truncated tool=${toolName ?? "unknown"}, ` +
        `${content.length}→${truncated.length} chars (maxResultChars=${maxResultChars})`,
      );
      return { message: { ...message, content: truncated } };
    }

    // Handle array content blocks (Anthropic-style content blocks)
    if (Array.isArray(content)) {
      let totalChars = 0;
      for (const block of content) {
        if (
          typeof block === "object"
          && block !== null
          && block.type === "text"
          && typeof block.text === "string"
        ) {
          totalChars += block.text.length;
        }
      }

      // Only truncate if total text content exceeds 2x the limit
      if (totalChars <= maxResultChars * 2) return;

      console.log(
        `${trpTag} tool_result_persist: truncating array content, ` +
        `tool=${toolName ?? "unknown"}, totalTextChars=${totalChars}, ` +
        `threshold=${maxResultChars * 2} (2×maxResultChars)`,
      );

      let remaining = maxResultChars;
      const truncated = content.map((block) => {
        if (
          typeof block === "object"
          && block !== null
          && block.type === "text"
          && typeof block.text === "string"
        ) {
          if (remaining <= 0) {
            return {
              ...block,
              text: `[... 输出已省略，超出压缩预算 ...]`,
            };
          }
          if (block.text.length <= remaining) {
            remaining -= block.text.length;
            return block;
          }
          const truncatedText =
            block.text.slice(0, remaining) +
            `\n\n[... 输出已截断，原始长度 ${block.text.length.toLocaleString()} 字符，保留前 ${remaining.toLocaleString()} 字符 ...]`;
          remaining = 0;
          return { ...block, text: truncatedText };
        }
        return block;
      });

      return { message: { ...message, content: truncated } };
    }

    return;
  };
}