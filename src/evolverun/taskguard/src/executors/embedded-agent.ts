import type {
  ExecutionMode,
  WorkflowNode,
  ExecutorResult,
  EmbeddedAgentExecutor,
  WorkflowSpec,
  ExecutionWarning,
} from "../types.js";
import type { TemplateContext } from "../runner.js";
import { resolveTemplate } from "../runner.js";
import { buildNodeExecutionContext } from "../execution-context.js";
import { jsonFailureError } from "./json-failure.js";
import { isRecord, tryParseJson, lightweightJsonRepair } from "./json-repair.js";
import {
  resolveRequiredSkill,
  type ResolvedRequiredSkill,
  type SkillResolutionDirs,
} from "../skill-resolver.js";
import { filterSnapshotToRequiredSkill, normalizeRequiredSkillSnapshotPaths, buildFallbackSkillSnapshot } from "../skill-snapshot.js";
import {
  resolveEmbeddedAgentContext,
  type EmbeddedAgentRuntimeApi,
  type EmbeddedAgentToolContext,
} from "./embedded-context.js";
import {
  addTokenUsage,
  collectTokenUsageFromSessionFile,
  estimateTokenUsageFromSessionFile,
  extractTokenUsageFromMetadata,
  hasPositiveTokenCounts,
  mergeTokenUsageWithEstimate,
  mergeTokenUsageWithFallback,
} from "../token-usage.js";
import { maybeCompactSessionFileSafe, cleanupAbandonedSession, type SessionCompressionConfig } from "../context/session-compressor.js";
import {
  registerSessionCompressionConfig,
  unregisterSessionCompressionConfig,
} from "../context/session-watch-compressor.js";
import { extractSessionErrors } from "../session-error-extractor.js";
import { acquireLlmPermit } from "./llm-semaphore.js";
import { appendWorkflowJsonlLog, buildDirectLogRecord, formatLocalIsoWithOffset } from "../workflow-log.js";

export type EmbeddedAgentApi = EmbeddedAgentRuntimeApi & {
  runtime: {
    config?: {
      loadConfig?: () => Promise<unknown> | unknown;
    };
    agent: {
      defaults?: unknown;
      resolveAgentWorkspaceDir?: (config: unknown, agentId: string) => string | undefined;
      resolveAgentDir?: (config: unknown, agentId: string) => string | undefined;
      ensureAgentWorkspace?: (params: { dir: string; ensureBootstrapFiles?: boolean }) => Promise<{ dir?: string } | void> | { dir?: string } | void;
      session?: {
        resolveStorePath?: (unused?: unknown, opts?: { agentId?: string }) => Promise<unknown> | unknown;
        loadSessionStore?: (storePath?: unknown, opts?: { skipCache?: boolean }) => Promise<unknown> | unknown;
        resolveSessionFilePath?: (
          sessionId: string,
          entry?: Record<string, unknown>,
          opts?: { agentId?: string },
        ) => string | undefined;
      };
      runEmbeddedPiAgent: (params: Record<string, unknown>) => Promise<{
        output?: string;
        error?: string;
        payloads?: Array<{
          text?: string;
          isError?: boolean;
          isReasoning?: boolean;
        }>;
        messagingToolSentTexts?: string[];
        meta?: Record<string, unknown>;
      }>;
    };
  };
};

export type EmbeddedAgentProgressFn = (message: string, idempotencyKey: string) => Promise<void> | void;

export type EmbeddedAgentLoopEvent = {
  event: string;
  message: string;
  runId?: string;
  stream?: string;
  data?: Record<string, unknown>;
};

export type EmbeddedAgentLoopEventFn = (event: EmbeddedAgentLoopEvent) => Promise<void> | void;
export type EmbeddedAgentFinalOutputFn = (message: string) => Promise<void> | void;

export type ExecuteEmbeddedAgentOptions = {
  sessionKey?: string;
  toolCtx?: EmbeddedAgentToolContext;
  runId?: string;
  flowId?: string;
  attempt?: number;
  workflow?: WorkflowSpec;
  executionMode?: ExecutionMode;
  abortSignal?: AbortSignal;
  skillDirs?: SkillResolutionDirs;
  progress?: EmbeddedAgentProgressFn;
  agentEvent?: EmbeddedAgentLoopEventFn;
  finalOutput?: EmbeddedAgentFinalOutputFn;
  /** Global context compression defaults from application config. */
  compressionDefaults?: import("../context/types.js").ContextCompressionDefaults;
  /** Session compression config from application config. */
  sessionCompressionConfig?: SessionCompressionConfig;
  /** Bot ID for JSONL log records. */
  botId?: string;
};

/**
 * Backwards-compatible alias — delegates to the shared jsonFailureError.
 */
function failedJsonStatusError(result: Record<string, unknown>): string | null {
  return jsonFailureError(result);
}

function normalizeOptions(options: string | ExecuteEmbeddedAgentOptions): ExecuteEmbeddedAgentOptions {
  if (typeof options === "string") return { sessionKey: options };
  return options;
}

function minimalWorkflow(node: WorkflowNode): WorkflowSpec {
  return {
    id: "unknown-workflow",
    version: 1,
    title: "Unknown Workflow",
    nodes: [node],
  };
}

function buildSkillPrompt(
  executor: EmbeddedAgentExecutor,
  resolvedPrompt: string,
  resolvedSkill?: ResolvedRequiredSkill,
): string {
  const skillName = executor.skillName?.trim();
  if (!skillName) return resolvedPrompt;
  if (resolvedSkill) {
    return [
      `请使用 ${skillName} skill 执行本节点。`,
      `Skill 目录：${resolvedSkill.skillDir}`,
      `Skill 入口：${resolvedSkill.skillFile}`,
      "",
      resolvedPrompt,
    ].join("\n");
  }
  return `请使用 ${skillName} skill 执行本节点。\n\n${resolvedPrompt}`;
}

function buildJsonOnlyInstruction(node: WorkflowNode): string {
  const lines = [
    "输出要求：",
    "1. 只输出一个合法 JSON object。",
    "2. 不要输出 Markdown。",
    "3. 不要输出代码块围栏。",
    "4. 不要输出解释性自然语言。",
    "5. 字段缺失时使用空字符串、空数组或 false，不要省略 required 字段。",
    "6. 如果需要面向用户展示自然语言，请放入 JSON 字段，不要写在 JSON object 外部。",
  ];
  if (node.outputContract) {
    lines.push("", "输出契约：", JSON.stringify(node.outputContract.schema));
  }
  return lines.join("\n");
}

function needsJsonOnlyInstruction(node: WorkflowNode, executor: EmbeddedAgentExecutor): boolean {
  return executor.outputMode === "json" || node.outputContract !== undefined;
}

function appendJsonOnlyInstruction(
  prompt: string,
  node: WorkflowNode,
  executor: EmbeddedAgentExecutor,
): string {
  if (!needsJsonOnlyInstruction(node, executor)) return prompt;
  return [prompt, "", buildJsonOnlyInstruction(node)].join("\n");
}

function buildJsonRepairPrompt(params: {
  node: WorkflowNode;
  rawOutput: string;
}): string {
  const contractLines = params.node.outputContract
    ? ["", "输出契约：", JSON.stringify(params.node.outputContract.schema)]
    : [];
  return [
    "上一轮输出不是合法 JSON。",
    "请基于上一轮输出修复格式，只返回合法 JSON object。",
    "不要 Markdown，不要代码块围栏，不要自然语言说明。",
    ...contractLines,
    "",
    "上一轮输出：",
    params.rawOutput.slice(0, 12_000),
  ].join("\n");
}

function formatJsonParseFailureError(node: WorkflowNode): string {
  return [
    `节点 ${node.title} (${node.id}) 的 embedded-agent JSON 输出解析失败。`,
    "已自动尝试一次 JSON repair 但仍未得到合法 JSON object。",
    "排障提示：检查该节点 prompt/outputContract 是否要求单个 JSON object；查看 clawmind JSONL 或 embedded session 日志中的 parseFailureRawOutput 定位模型原始输出。",
  ].join(" ");
}

function extractEmbeddedRunText(run: {
  output?: string;
  payloads?: Array<{ text?: string; isReasoning?: boolean }>;
  messagingToolSentTexts?: string[];
}): string {
  const payloadText = Array.isArray(run.payloads)
    ? run.payloads
        .filter((payload) => payload?.isReasoning !== true)
        .map((payload) => payload?.text?.trim() ?? "")
        .filter(Boolean)
        .join("\n\n")
    : "";
  if (payloadText) return payloadText;
  const messagingText = Array.isArray(run.messagingToolSentTexts)
    ? run.messagingToolSentTexts.map((text) => text.trim()).filter(Boolean).join("\n\n")
    : "";
  if (messagingText) return messagingText;
  return run.output ?? "";
}

/**
 * Extract agent-level error (API failure, timeout, etc.).
 * Always indicates the run failed, regardless of output.
 */
function extractAgentError(run: { error?: string }): string | undefined {
  if (run.error) return `embedded-agent error: ${run.error}`;
  return undefined;
}

/**
 * Extract tool-level errors (individual tool calls that returned isError).
 * The agent may have recovered and produced valid output despite tool errors,
 * so this should only be used as a failure reason when there is no valid output.
 */
function extractToolErrors(run: {
  payloads?: Array<{ text?: string; isError?: boolean }>;
}): string | undefined {
  const errorText = Array.isArray(run.payloads)
    ? run.payloads
        .filter((payload) => payload?.isError)
        .map((payload) => payload?.text?.trim() ?? "")
        .filter(Boolean)
        .join("\n\n")
    : "";
  return errorText ? `embedded-agent error: ${errorText}` : undefined;
}

/**
 * Count tool-level errors in the run result (for warning messages).
 */
function countToolErrors(run: {
  payloads?: Array<{ text?: string; isError?: boolean }>;
}): number {
  if (!Array.isArray(run.payloads)) return 0;
  return run.payloads.filter((payload) => payload?.isError).length;
}

/**
 * Collect ExecutionWarning objects from the run result and session file.
 * Called when the node succeeds — these warnings indicate potential quality
 * issues (tool errors the agent recovered from, abandoned sessions, etc.)
 * that the workflow engine should persist but not treat as failures.
 */
function collectRunWarnings(
  run: {
    payloads?: Array<{ text?: string; isError?: boolean }>;
    meta?: Record<string, unknown>;
  },
  jsonRepairNeeded: boolean,
  resultObject?: Record<string, unknown>,
): ExecutionWarning[] {
  const warnings: ExecutionWarning[] = [];

  // 1. Tool errors in run payloads
  const toolErrorCount = countToolErrors(run);
  if (toolErrorCount > 0) {
    const toolErrorText = extractToolErrors(run);
    warnings.push({
      code: "tool_errors",
      message: `执行中 ${toolErrorCount} 个工具调用报错，Agent 仍产出了输出`,
      detail: toolErrorText
        ? { toolErrorSummary: truncateText(toolErrorText.replace(/^embedded-agent error: /, ""), 2000), toolErrorCount }
        : { toolErrorCount },
    });
  }

  // 2. JSON repair was needed (original output was malformed)
  if (jsonRepairNeeded) {
    warnings.push({
      code: "json_repair_needed",
      message: "embedded-agent 输出不是合法 JSON，经过自动修复后解析成功",
    });
  }

  // 3. Abandoned session (stopReason: toolUse)
  const livenessState = (run.meta as Record<string, unknown> | undefined)?.livenessState;
  if (livenessState === "abandoned") {
    warnings.push({
      code: "recovered_from_error",
      message: "会话被标记为 abandoned（stopReason: toolUse），输出可能不完整",
      detail: { livenessState: String(livenessState) },
    });
  }

  // 4. Result object contains error-indicating fields
  if (resultObject) {
    const hasErrorField = resultObject.error !== undefined && resultObject.error !== null && resultObject.error !== "";
    const hasFalseSuccess = resultObject.success === false;
    const hasErrorStatus = resultObject.status === "error";
    if (hasErrorField || hasFalseSuccess || hasErrorStatus) {
      warnings.push({
        code: "partial_output",
        message: "JSON 输出含错误状态字段，可能在工具调用失败后产生了不可靠结果",
        detail: {
          ...(hasErrorField ? { error: truncateText(String(resultObject.error), 500) } : {}),
          ...(hasFalseSuccess ? { success: false } : {}),
          ...(hasErrorStatus ? { status: "error" } : {}),
        },
      });
    }
  }

  return warnings;
}

/**
 * Augment a succeeded ExecutorResult with session-level errors extracted
 * from the .jsonl session file. This catches tool errors and API errors
 * that exist in the session log but were not surfaced in `run.payloads`.
 *
 * Only runs for succeeded results — failed results already have error info.
 * Diagnostic extraction is best-effort and never causes a success to become
 * a failure.
 */
function augmentWithSessionErrors(result: ExecutorResult, sessionFile: string | undefined, skillName?: string | null, resolvedPrompt?: string): ExecutorResult {
  if (result.status !== "succeeded") return { ...result, resolvedPrompt };
  if (!sessionFile) return result;

  try {
    const sessionErrors = extractSessionErrors(sessionFile);
    if (!sessionErrors.hasErrors) {
      // No session errors — still attach sessionFile and skillName for Controller step trace
      return { ...result, sessionFile, skillName: skillName ?? null, resolvedPrompt };
    }

    const warnings = [...(result.warnings ?? [])];
    warnings.push({
      code: "session_errors",
      message: `Session 日志中发现 ${sessionErrors.errorCount} 个错误` +
        `（${sessionErrors.toolErrors.length} 工具错误, ${sessionErrors.apiErrors.length} API 错误）`,
      detail: {
        toolErrors: sessionErrors.toolErrors.slice(0, 5).map((e) => ({
          name: e.toolName,
          error: e.errorMessage,
        })),
        apiErrors: sessionErrors.apiErrors.slice(0, 3).map((e) => ({
          code: e.errorCode,
          error: e.errorMessage,
        })),
        totalErrorCount: sessionErrors.errorCount,
      },
    });

    console.log(
      `[embedded-agent] warnings: node session errors detected: ` +
      `${sessionErrors.toolErrors.length} tool errors, ${sessionErrors.apiErrors.length} API errors ` +
      `in ${sessionFile}`,
    );

    return { ...result, warnings, sessionFile, skillName: skillName ?? null, resolvedPrompt };
  } catch (e) {
    // Diagnostic extraction must never break the result
    console.warn(
      `[embedded-agent] session error extraction failed: ${e instanceof Error ? e.message : String(e)}`,
    );
    // Still attach sessionFile and skillName even if error extraction failed
    return { ...result, sessionFile, skillName: skillName ?? null, resolvedPrompt };
  }
}

function resolveUsageWithSessionFallback(
  metadataUsage: ExecutorResult["usage"],
  sessionFile: string | undefined,
): ExecutorResult["usage"] {
  if (hasPositiveTokenCounts(metadataUsage)) return metadataUsage;
  const reportedUsage = mergeTokenUsageWithFallback(metadataUsage, collectTokenUsageFromSessionFile(sessionFile));
  if (hasPositiveTokenCounts(reportedUsage)) return reportedUsage;
  return mergeTokenUsageWithEstimate(reportedUsage, estimateTokenUsageFromSessionFile(sessionFile));
}

function withoutUndefined(input: Record<string, unknown>): Record<string, unknown> {
  return Object.fromEntries(Object.entries(input).filter(([, value]) => value !== undefined));
}

function truncateText(value: string, max = 2_000): string {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function normalizeLoopValue(value: unknown): unknown {
  if (typeof value === "string") return truncateText(value);
  if (Array.isArray(value)) return value.map((item) => normalizeLoopValue(item));
  if (!isRecord(value)) return value;
  return Object.fromEntries(
    Object.entries(value).map(([key, item]) => [key, normalizeLoopValue(item)]),
  );
}

function loopDataPhase(value: unknown): string | undefined {
  if (!isRecord(value)) return undefined;
  const phase = value.phase;
  return typeof phase === "string" ? phase : undefined;
}

function shouldPreserveFullLoopData(params: {
  event: string;
  stream?: string;
  data?: unknown;
}): boolean {
  if (params.event === "tool_result") return true;
  if (params.event !== "agent_event") return false;
  if (params.stream === "command_output") return loopDataPhase(params.data) === "end";
  return false;
}

function normalizeLoopData(
  value: unknown,
  params: { event: string; stream?: string },
): Record<string, unknown> {
  if (!isRecord(value)) return {};
  if (shouldPreserveFullLoopData({ ...params, data: value })) return value;
  return normalizeLoopValue(value) as Record<string, unknown>;
}

// ── Staggered Start ──────────────────────────────────────────────────────────
//
// When multiple flows are released simultaneously by flow-control, they all
// reach the embedded-agent executor at nearly the same instant and fire LLM
// requests in a burst.  This frequently triggers 429 rate-limit errors, which
// cascade into exponential backoff retries that make the concurrent scenario
// slower than serial execution.
//
// Staggered start inserts a deterministic short delay (0–5 s) based on a hash
// of a composite key (flowId + nodeId) before each LLM call.  Different flows
// and different nodes get different delays, spreading the LLM requests across
// a 5-second window.  Same flow+node combination always gets the same delay,
// so timing is predictable.
//
// Enhancement over original:
// - Window expanded from 2s to 5s to better match LLM Provider rate-limit windows
// - Slots expanded from 10 to 20 for finer granularity
// - Key changed from flowId-only to flowId:nodeId — every embedded-agent node
//   is staggered, not just the first one per flow
// - Set cleanup when it grows beyond threshold to prevent unbounded memory growth

const STAGGER_MAX_DELAY_MS = 5000;
const STAGGER_SLOTS = 20;
const STAGGER_CLEANUP_THRESHOLD = 1000;

// ── Embedded Session Key Derivation ─────────────────────────────────────────
//
// When a workflow runs inside a subagent, the parent subagent's sessionKey
// (e.g. "agent:main:subagent:31fe14ed-xxx") is inherited by the embedded-agent.
// OpenClaw's command queue enforces maxConcurrent=1 per session lane (keyed by
// sessionKey).  If the embedded-agent enqueues into the SAME lane as its parent
// subagent, a self-deadlock occurs: the parent waits for the embedded-agent to
// finish, while the embedded-agent waits for the parent's lane slot to free up.
//
// deriveEmbeddedSessionKey() creates a unique lane key for the embedded-agent
// so it never competes with its parent for the same lane slot.

function deriveEmbeddedSessionKey(
  parentKey: string,
  nodeId: string,
  flowId: string,
): string {
  if (!parentKey) return "";
  return `${parentKey}:embedded:${nodeId}:${flowId}`;
}

// Track which flowId+nodeId combinations have already been staggered.
// Each unique (flowId, nodeId) pair is staggered once — this ensures every
// embedded-agent node gets a stagger delay, not just the first one per flow.
// The set is periodically cleaned up to prevent unbounded memory growth.
const staggeredKeys = new Set<string>();

/** Build a composite stagger key from flowId and nodeId for per-node staggering. */
function getStaggerKey(flowId: string | undefined, nodeId: string): string {
  return flowId ? `${flowId}:${nodeId}` : nodeId;
}

/** Remove stale stagger keys when the set grows beyond the cleanup threshold. */
function cleanupStaggeredKeys(): void {
  if (staggeredKeys.size > STAGGER_CLEANUP_THRESHOLD) {
    const removed = staggeredKeys.size;
    staggeredKeys.clear();
    console.log(
      `[embedded-agent] staggeredKeys cleared (removed ${removed} entries, ` +
      `threshold=${STAGGER_CLEANUP_THRESHOLD})`,
    );
  }
}

/** Deterministic hash for stagger calculation — fast, no crypto needed. */
function simpleHash(str: string): number {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = ((hash << 5) - hash + str.charCodeAt(i)) | 0;
  }
  // Unsigned right shift forces non-negative; avoids Math.abs(Integer.MIN_VALUE)
  // which incorrectly returns a negative value due to two's complement overflow.
  return hash >>> 0;
}

export async function executeEmbeddedAgent(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  api: EmbeddedAgentApi,
  options: string | ExecuteEmbeddedAgentOptions,
): Promise<ExecutorResult> {
  if (node.executor.type !== "embedded-agent") {
    return { status: "failed", error: "not an embedded-agent node" };
  }

  const executor = node.executor as EmbeddedAgentExecutor;
  const resolvedPrompt = resolveTemplate(executor.prompt, templateCtx);
  /** Truncated resolved prompt for inclusion in ExecutorResult (max 4000 chars). */
  const resolvedPromptTruncated = resolvedPrompt.length > 4000
    ? resolvedPrompt.substring(0, 3989) + "... [TRUNCATED]"
    : resolvedPrompt;
  const timeoutMs = (executor.timeoutSeconds ?? 300) * 1000;
  const executionOptions = normalizeOptions(options);
  const progressBaseKey = executionOptions.runId ?? `embedded-agent:${node.id}`;

  // Diagnostics: log compression config received by this executor
  console.log(
    `[context-compression] executeEmbeddedAgent: node=${node.id} (${node.title}), ` +
    `compressionDefaults=${executionOptions.compressionDefaults ? `enabled=${executionOptions.compressionDefaults.enabled}, maxTokens=${executionOptions.compressionDefaults.defaultMaxTokens}` : "undefined"}, ` +
    `sessionCompression=${executionOptions.sessionCompressionConfig ? `maxSessionTokens=${executionOptions.sessionCompressionConfig.maxSessionTokens}, minTokensToCompact=${executionOptions.sessionCompressionConfig.minTokensToCompact}` : "undefined"}`,
  );
  let assistantStartReported = false;
  let partialReplyReported = false;
  let reasoningReported = false;
  // ── [diag] Track lifecycle phases to pinpoint "loop ended but RPC never
  // resolved" stalls. Set from onAgentEvent/runtime callbacks; read by the
  // gateway watchdog heartbeat to distinguish "still waiting on model" from
  // "model finished, gateway never closed the RPC". See flow 16a56472.
  let _lifecycleEndAt = 0; // epoch ms when stream="lifecycle" phase="end" arrived
  let _runEmbeddedResolveAt = 0; // epoch ms when the runEmbedded promise settled
  // Track the LLM semaphore release function for cleanup in the finally block.
  // Acquired inside the try block before the first LLM call.
  let releaseLlmPermit: (() => void) | null = null;
  // Track the effective session key for cleanup in the finally block.
  // Set inside the try block after embeddedContext is resolved.
  let effectiveSessionKey = "";
  const emitProgress = async (message: string, suffix: string) => {
    if (!executionOptions.progress) return;
    await executionOptions.progress(message, `${progressBaseKey}:${suffix}`);
  };
  const emitAgentEvent = async (
    event: string,
    message: string,
    data?: Record<string, unknown>,
    stream?: string,
  ) => {
    if (!executionOptions.agentEvent) return;
    await executionOptions.agentEvent(withoutUndefined({
      event,
      message,
      runId: executionOptions.runId,
      stream,
      data: normalizeLoopData(data, { event, stream }),
    }) as EmbeddedAgentLoopEvent);
  };

  // Track sessionFile for step trace persistence in finally block
  let _sessionFileForTrace: string | undefined;
  // Declare run before try so it's accessible in catch (for meta preservation).
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  let run: any;

  // ── [diag] Structured RPC-settlement diagnostics, routed through the JSONL
  // logger (NOT console — console.* is NOT captured in this deployment's log
  // files; see workflow-log.ts). event_type="diag_gateway_rpc" lands in
  // clawmind.log so post-hoc analysis of stuck nodes (e.g. flow
  // 00d87945 analysis-strategy-hit) can pinpoint exactly which milestone the
  // executor reached before stalling:
  //   executor_enter → permit_acquired → rpc_enter → lifecycle_phase_end →
  //   rpc_resolved → output_extracted → (abandoned_cleanup_*)? →
  //   final_output_invoked → rpc_exit → rpc_finally
  // Declared before `try` so catch/finally can emit rpc_caught / rpc_finally.
  // Uses executionOptions.flowId / node directly (effectiveFlowId is scoped to
  // try). effectiveSessionKey is `let ""` here, reassigned inside try; read at
  // call-time so it's populated by the time later stages fire.
  const _rpcDiagEnterAt = Date.now();
  function recordRpcDiag(stage: string, fields: Record<string, unknown> = {}): void {
    void appendWorkflowJsonlLog(
      buildDirectLogRecord({
        flowId: executionOptions.flowId ?? "unknown-flow",
        eventType: "diag_gateway_rpc",
        message: `[diag-rpc] ${stage} node=${node.title ?? node.id}`,
        nodeId: node.id,
        botId: executionOptions.botId,
        sessionKey: effectiveSessionKey || null,
        details: {
          stage,
          node_id: node.id,
          node_title: node.title ?? null,
          flow_id: executionOptions.flowId ?? "unknown-flow",
          run_id: executionOptions.runId ?? null,
          timeout_ms: timeoutMs,
          elapsed_since_enter_ms: Date.now() - _rpcDiagEnterAt,
          lifecycle_end_at_ms: _lifecycleEndAt > 0 ? _lifecycleEndAt : null,
          rpc_resolved_at_ms: _runEmbeddedResolveAt > 0 ? _runEmbeddedResolveAt : null,
          assistant_started: assistantStartReported,
          partial_reply: partialReplyReported,
          abort_aborted: executionOptions.abortSignal?.aborted ?? null,
          ...fields,
        },
      }),
    );
  }

  try {
    const embeddedContext = await resolveEmbeddedAgentContext(
      api,
      executionOptions.toolCtx,
      executionOptions.sessionKey,
    );
    const currentSessionFile = embeddedContext.sessionFile;
    if (!currentSessionFile) {
      throw new Error("无法解析当前 OpenClaw 会话文件，embedded-agent 未启动。");
    }
    const ensuredWorkspace = embeddedContext.workspaceDir
      ? await api.runtime.agent.ensureAgentWorkspace?.({
          dir: embeddedContext.workspaceDir,
          ensureBootstrapFiles: false,
        })
      : undefined;
    const workspaceDir = ensuredWorkspace
      && typeof ensuredWorkspace === "object"
      && typeof ensuredWorkspace.dir === "string"
      ? ensuredWorkspace.dir
      : embeddedContext.workspaceDir;

    const skillName = executor.skillName?.trim();
    const resolvedSkill = skillName
      ? await resolveRequiredSkill(skillName, executionOptions.skillDirs)
      : undefined;
    const prompt = appendJsonOnlyInstruction(
      buildSkillPrompt(executor, resolvedPrompt, resolvedSkill),
      node,
      executor,
    );
    let skillsSnapshot: Record<string, unknown> | undefined;
    if (resolvedSkill) {
      try {
        skillsSnapshot = filterSnapshotToRequiredSkill(
          normalizeRequiredSkillSnapshotPaths(embeddedContext.skillsSnapshot, resolvedSkill),
          resolvedSkill.name,
        );
      } catch (snapshotError) {
        // Skill found on disk but missing from session snapshot — build a
        // minimal fallback so the embedded agent can still load the skill.
        // This handles two scenarios:
        //   1. Session created before the skill was installed/linked
        //   2. Human-node confirm triggered a snapshot rebuild that
        //      excluded workflow-pack skills
        console.warn(
          `[embedded-agent] skill "${resolvedSkill.name}" found on disk but missing in session snapshot, ` +
          `falling back to skill-only context (snapshot skipped)`,
        );
        skillsSnapshot = buildFallbackSkillSnapshot(resolvedSkill);
      }
    } else {
      skillsSnapshot = embeddedContext.skillsSnapshot;
    }
    const nodeContext = await buildNodeExecutionContext({
      workflow: executionOptions.workflow ?? minimalWorkflow(node),
      node,
      flowId: executionOptions.flowId ?? "unknown-flow",
      attempt: executionOptions.attempt ?? 1,
      prompt,
      templateCtx,
      currentSessionFile,
      executionMode: executionOptions.executionMode ?? "private",
      compressionDefaults: executionOptions.compressionDefaults,
      sessionCompressionConfig: executionOptions.sessionCompressionConfig,
    });

    // Capture session file for step trace persistence in finally block
    _sessionFileForTrace = nodeContext.sessionFile;

    if (skillName) {
      await emitProgress(`${node.title} 已开始，正在使用 ${skillName} skill`, "started");
    }

    // For structured history mode, inject the Workflow Context JSON directly
    // into the prompt. The runtime may overwrite the session file created by
    // buildNodeExecutionContext, so we cannot rely on it for context delivery.
    //
    // When Workflow Context JSON is present, it includes the full nodeOutput and params.
    // The prompt template may already contain rendered {{nodeOutput.xxx}} values
    // in an "上游数据："/"输入数据：" section, which duplicates the data.
    // We strip these sections to avoid double-injection.
    let effectivePrompt = prompt;
    if (nodeContext.history === "structured" && nodeContext.workflowContext) {
      // Remove "上游数据：" and "输入数据：" sections from the prompt to avoid
      // double-injection, since the Workflow Context JSON already contains
      // the full nodeOutput and params.
      const strippedPrompt = prompt
        .replace(
          /上游数据[：:][\s\S]*?(?=\n【|\n⚠️|\n执行要求|\n输出要求|\n输出契约|$)/,
          "上游数据：见下方 Workflow Context JSON 中的 nodeOutput 字段",
        )
        .replace(
          /输入数据[：:][\s\S]*?(?=\n执行要求|\n⚠️|\n边界检测|\n【|$)/,
          "输入数据：见下方 Workflow Context JSON 中的 params 字段",
        );

      effectivePrompt = [
        strippedPrompt,
        "",
        "Workflow Context JSON:",
        JSON.stringify(nodeContext.workflowContext, null, 2),
      ].join("\n");
    }

    // Append graceful termination instruction to reduce the chance of the agent
    // ending with stopReason="toolUse", which triggers livenessState="abandoned"
    // and causes the OpenClaw SDK to mark the session as replayInvalid (cache loss).
    // Only append for non-JSON-strict modes (JSON mode already constrains output).
    if (executor.outputMode !== "json") {
      effectivePrompt = [
        effectivePrompt,
        "",
        "[系统规则] 完成所有信息收集后，必须以文本形式给出最终答案。不要以另一个工具调用结束你的回复。",
      ].join("\n");
    }

    await emitAgentEvent("context_prepared", `${node.title} embedded-agent 上下文已准备`, {
      history: nodeContext.history,
      sessionFile: nodeContext.sessionFile,
      inheritedSessionFile: nodeContext.inheritedSessionFile,
      includedNodeOutputs: nodeContext.includedNodeOutputs,
      compressionStats: nodeContext.compressionStats,
    });
    await emitAgentEvent("started", `${node.title} embedded-agent 启动`, {
      skillName: executor.skillName,
      outputMode: executor.outputMode,
      resolvedSkillSource: resolvedSkill?.source,
      resolvedSkillDir: resolvedSkill?.skillDir,
      sessionKey: embeddedContext.sessionKey ?? executionOptions.sessionKey,
    });

    // Session compression: compact session files before running the agent
    // to avoid sending bloated histories to the LLM.
    // Applies to ALL history modes — for "inherit"/"compacted" the session
    // may already be large from previous nodes; for "structured"/"isolated"
    // the session starts small (~2K tokens) and minTokensToCompact will skip it.
    {
      const sc = executionOptions.sessionCompressionConfig;
      const maxSessionTokens = sc?.maxSessionTokens ?? 50000;
      const minTokensToCompact = sc?.minTokensToCompact ?? 30000;
      console.log(
        `[session-compaction] pre-run: node=${node.id} (${node.title}), ` +
        `history=${nodeContext.history}, sessionFile=${nodeContext.sessionFile}, ` +
        `maxSessionTokens=${maxSessionTokens}, minTokensToCompact=${minTokensToCompact}`,
      );
      const compactResult = await maybeCompactSessionFileSafe(nodeContext.sessionFile, {
        maxSessionTokens,
        minTokensToCompact,
        recencyWindow: sc?.recencyWindow,
        toolPrepassEnabled: sc?.toolPrepassEnabled,
        toolResultMaxChars: sc?.toolResultMaxChars,
        deduplicateReads: sc?.deduplicateReads,
        readDedupTtlMs: sc?.readDedupTtlMs,
      });
      if (compactResult.kind === "compressed") {
        console.log(
          `[session-compaction] pre-run compressed (sidecar): node=${node.id}, ` +
          `${compactResult.stats.inputTokens}→${compactResult.stats.outputTokens} tokens, ` +
          `messagesEvicted=${compactResult.stats.messagesEvicted}, ` +
          `toolsCompressed=${compactResult.stats.toolResultsCompressed}, ` +
          `sidecar=${compactResult.sidecarPath}`,
        );
        await emitAgentEvent("agent_event",
          `${node.title} Session 压缩完成：${compactResult.stats.inputTokens}→${compactResult.stats.outputTokens} tokens`,
          { compaction: compactResult.stats },
          "lifecycle",
        );
      } else if (compactResult.kind === "skipped") {
        const reason = compactResult.reason;
        const inTok = compactResult.inputTokens;
        console.log(
          `[session-compaction] pre-run skipped: node=${node.id}, ` +
          `reason=${reason}, inputTokens=${inTok ?? "n/a"}`,
        );
      } else if (compactResult.kind === "error") {
        console.warn(
          `[session-compaction] pre-run error: node=${node.id}, ` +
          `error=${compactResult.error}`,
        );
      }
    }

    // ── Staggered start: delay before LLM call to avoid burst ──
    // When flow-control releases multiple flows simultaneously, they all
    // reach this point at nearly the same instant and fire LLM requests
    // in a burst, triggering 429 rate limits.  A deterministic delay
    // based on a composite key (flowId + nodeId) hash spreads requests
    // across a 5-second window.
    //
    // The delay is applied ONCE per (flowId, nodeId) combination (tracked
    // in staggeredKeys).  This ensures every embedded-agent node in every
    // flow gets staggered, preventing wavefront bursts on the 2nd and 3rd
    // LLM nodes that the original flowId-only approach missed.
    const staggerKey = getStaggerKey(executionOptions.flowId, node.id);
    if (!staggeredKeys.has(staggerKey)) {
      staggeredKeys.add(staggerKey);
      cleanupStaggeredKeys();
      const slot = simpleHash(staggerKey) % STAGGER_SLOTS;
      const staggerDelay = slot * (STAGGER_MAX_DELAY_MS / STAGGER_SLOTS);
      if (staggerDelay > 0) {
        console.log(
          `[embedded-agent] staggered start: key=${staggerKey}, ` +
          `node=${node.id} (${node.title ?? node.id}), slot=${slot}, delay=${staggerDelay}ms`,
        );
        await emitAgentEvent(
          "staggered_start",
          `${node.title} 错峰等待 ${staggerDelay}ms`,
          { key: staggerKey, flowId: executionOptions.flowId, slot, delayMs: staggerDelay },
        );
        await new Promise((resolve) => setTimeout(resolve, staggerDelay));
      }
    }

    // Register per-session compression config for OpenClaw plugin hooks.
    //
    // OpenClaw's runtime invokes before_prompt_build and tool_result_persist
    // via the plugin hook system (api.on), NOT via runEmbeddedPiAgent params.
    // The previous approach of passing these as params was silently ignored
    // by the runtime because RunEmbeddedAgentParams doesn't include them.
    //
    // The registry approach:
    // 1. We register the session's compression config + session file here.
    // 2. The plugin hooks (registered in index.ts via api.on) look up the
    //    config by sessionKey and apply compression during the agent loop.
    // 3. We unregister in a finally block after the agent completes.
    const sc = executionOptions.sessionCompressionConfig;
    const parentSessionKey = embeddedContext.sessionKey ?? executionOptions.sessionKey ?? "";
    const effectiveFlowId = executionOptions.flowId ?? "unknown-flow";
    const effectiveNodeName = node.title ?? node.id;
    effectiveSessionKey = deriveEmbeddedSessionKey(parentSessionKey, node.id, effectiveFlowId);

    recordRpcDiag("executor_enter");
    console.log(
      `[session-watch-compressor] [flowId=${effectiveFlowId}, node=${effectiveNodeName}] embedded-agent: about to register compression for ` +
      `effectiveSessionKey="${effectiveSessionKey}", ` +
      `parentSessionKey="${parentSessionKey}", ` +
      `embeddedContext.sessionKey="${embeddedContext.sessionKey ?? ""}", ` +
      `executionOptions.sessionKey="${executionOptions.sessionKey ?? ""}", ` +
      `sessionFile="${nodeContext.sessionFile}"`,
    );
    registerSessionCompressionConfig(
      effectiveSessionKey,
      nodeContext.sessionFile,
      {
        sessionCompression: {
          maxSessionTokens: sc?.maxSessionTokens ?? 50000,
          minTokensToCompact: sc?.minTokensToCompact ?? 30000,
          recencyWindow: sc?.recencyWindow ?? 6,
          toolPrepassEnabled: sc?.toolPrepassEnabled ?? true,
          toolResultMaxChars: sc?.toolResultMaxChars ?? 5000,
          deduplicateReads: sc?.deduplicateReads ?? true,
          readDedupTtlMs: sc?.readDedupTtlMs ?? 300_000,
          insertCompactionNotice: sc?.insertCompactionNotice ?? true,
          contextTokenBudget: sc?.contextTokenBudget,
          modelContextWindow: sc?.modelContextWindow,
        },
        toolResultMaxChars: sc?.toolResultMaxChars ?? 5000,
        contextTokenBudget: sc?.contextTokenBudget,
        modelContextWindow: sc?.modelContextWindow,
        injectCompactionNotice: true,
        nodeName: effectiveNodeName,
        flowId: effectiveFlowId,
        // Suppress prependContext injection during agent loop to preserve
        // Anthropic prefix cache stability. When prependContext is injected,
        // the prompt prefix changes between turns, causing the Anthropic API
        // to treat each turn as a new conversation (0% cache hit).
        // File-based compression still runs — only the prompt injection is skipped.
        suppressPrependContext: true,
        onCompress: (stats) => {
          emitAgentEvent("agent_event",
            `${node.title} Hook Session 压缩[${stats.phase}]：${stats.inputTokens}→${stats.outputTokens} tokens`,
            { compaction: stats, source: "hook" },
            "lifecycle",
          ).catch(() => {});
        },
        onHookEvent: (evt) => {
          emitAgentEvent("agent_event",
            `${node.title} [${evt.hook}] ${evt.action}: ${evt.detail}`,
            { ...evt.data, hook: evt.hook, action: evt.action },
            "compression",
          ).catch(() => {});
        },
      },
    );
    console.log(
      `[session-watch-compressor] [flowId=${effectiveFlowId}, node=${effectiveNodeName}] embedded-agent: registered compression for effectiveSessionKey="${effectiveSessionKey}"`,
    );

    const runEmbedded = async (runPrompt: string) => api.runtime.agent.runEmbeddedPiAgent(withoutUndefined({
      sessionId: embeddedContext.sessionId,
      sessionKey: effectiveSessionKey,
      agentId: embeddedContext.agentId,
      messageChannel: embeddedContext.messageChannel,
      trigger: "manual",
      senderIsOwner: embeddedContext.senderIsOwner,
      sessionFile: nodeContext.sessionFile,
      workspaceDir,
      agentDir: embeddedContext.agentDir,
      config: embeddedContext.config,
      provider: embeddedContext.provider,
      model: embeddedContext.model,
      authProfileId: embeddedContext.authProfileId,
      authProfileIdSource: embeddedContext.authProfileIdSource,
      skillsSnapshot,
      prompt: runPrompt,
      timeoutMs,
      thinkLevel: "off",
      disableMessageTool: true,
      silentExpected: true,
      toolResultFormat: "markdown",
      runId: executionOptions.runId,
      abortSignal: executionOptions.abortSignal,
      // ── Existing event callbacks ──
      onAssistantMessageStart: async () => {
        if (assistantStartReported) return;
        assistantStartReported = true;
        await emitAgentEvent("assistant_started", `${node.title} 模型已开始分析`);
      },
      onReasoningStream: async (payload: Record<string, unknown>) => {
        if (!reasoningReported) {
          reasoningReported = true;
        }
        await emitAgentEvent("reasoning_stream", `${node.title} 模型正在推理`, payload);
      },
      onReasoningEnd: async () => {
        await emitAgentEvent("reasoning_end", `${node.title} 模型推理结束`);
      },
      onPartialReply: async (payload: { text?: string }) => {
        if (partialReplyReported || !payload.text?.trim()) return;
        partialReplyReported = true;
        await emitAgentEvent("partial_reply", `${node.title} 模型正在生成结果`, payload);
      },
      onToolResult: async (payload: Record<string, unknown>) => {
        await emitAgentEvent("tool_result", `${node.title} 工具调用已返回，继续分析`, payload);
      },
      onAgentEvent: async (event: { stream?: string; data?: Record<string, unknown> }) => {
        const stream = event.stream ?? "unknown";
        // ── [diag] Capture the "loop done" milestone. runtime fires
        // stream="lifecycle" phase="end" (→ agent_end hook) when the agent
        // loop finishes — this does NOT mean the runEmbedded RPC settled.
        // Normal nodes resolve ~0.3s after phase=end; flow 16a56472 emitted
        // phase=end then the RPC never resolved. Record the moment so the
        // watchdog can report how long we've been stuck post-loop.
        const phase = (event.data as { phase?: string } | undefined)?.phase;
        const livenessState = (event.data as { livenessState?: string } | undefined)?.livenessState;
        if (stream === "lifecycle" && (phase === "end" || phase === "error")) {
          if (phase === "end" && _lifecycleEndAt === 0) {
            _lifecycleEndAt = Date.now();
            // Durable jsonl record of the "agent loop finished" milestone. If a
            // node emits lifecycle_phase_end but never rpc_resolved, the hang is
            // in `await runEmbedded` (gateway RPC not settling) — the exact form
            // seen in flow 00d87945 / 16a56472.
            recordRpcDiag("lifecycle_phase_end", {
              phase,
              liveness_state: livenessState ?? null,
              lifecycle_data: event.data ?? null,
              post_loop_ms_since_end: 0,
            });
            console.log(
              `[embedded-agent][lifecycle] phase=end ARRIVED: node=${node.id} (${node.title ?? node.id}) ` +
              `flowId=${executionOptions.flowId ?? "unknown"} runId=${executionOptions.runId} ` +
              `livenessState=${livenessState ?? "n/a"} — agent loop finished; awaiting runEmbedded RPC to settle ` +
              `(runEmbeddedResolved=${_runEmbeddedResolveAt > 0})`,
            );
          }
        }
        await emitAgentEvent(
          "agent_event",
          `${node.title} agent loop ${stream}`,
          event.data,
          stream,
        );
      },
    }));

    // ── [diag] Gateway-call watchdog ──────────────────────────────────────
    // runEmbeddedPiAgent is an OpenClaw gateway RPC. When the gateway is
    // saturated (e.g. verbose chat.inject subprocess storm — see verboseChatInject
    // & inject-queue.ts) this call has been observed to hang indefinitely: emit
    // "started", then never return and never fire onAssistantMessageStart,
    // stalling Promise.allSettled on parallel nodes (flow 573077e7, 2026-07-14).
    // This wraps each runEmbedded call with phase logs + a periodic still-waiting
    // heartbeat so the next hang pinpoints the stuck phase (permit acquisition
    // vs gateway RPC) without guesswork. Pure diagnostic — no behaviour change.
    const _abortSignal = executionOptions.abortSignal;
    const _wrapGatewayCall = async <T,>(
      label: string,
      fn: () => Promise<T>,
    ): Promise<T> => {
      const gwStart = Date.now();
      recordRpcDiag("rpc_enter", {
        label,
        prompt_len: effectivePrompt.length,
      });
      console.log(
        `[embedded-agent][gateway] ENTER ${label}: node=${node.id} (${effectiveNodeName}) ` +
        `flowId=${effectiveFlowId} runId=${executionOptions.runId} timeoutMs=${timeoutMs} ` +
        `sessionKey=${effectiveSessionKey} promptLen=${effectivePrompt.length} ` +
        `abortAborted=${_abortSignal?.aborted ?? "no-signal"}`,
      );
      const timer = setInterval(() => {
        const elapsedMs = Date.now() - gwStart;
        const pct = timeoutMs > 0 ? Math.round((elapsedMs / timeoutMs) * 100) : 0;
        const overTimeout = timeoutMs > 0 && elapsedMs > timeoutMs;
        // post-loop stall: the agent loop already finished (phase=end) but the
        // RPC still hasn't settled. This is the flow-16a56472 hang signature.
        const postLoopMs = _lifecycleEndAt > 0 ? Date.now() - _lifecycleEndAt : 0;
        const postLoopStall = _lifecycleEndAt > 0 && _runEmbeddedResolveAt === 0;
        const level = (overTimeout || postLoopStall) ? "error" : "warn";
        const tag = postLoopStall
          ? `POST-LOOP-STALL (agent loop finished ${postLoopMs}ms ago, gateway RPC not settling!)`
          : overTimeout
            ? `STILL-WAITING-PAST-TIMEOUT (gateway RPC ignoring timeoutMs=${timeoutMs}!)`
            : `STILL-WAITING`;
        // Durable heartbeat so a stuck node shows repeating diag_gateway_rpc
        // records in the jsonl even when no other event fires.
        recordRpcDiag("rpc_heartbeat", {
          label,
          elapsed_ms: elapsedMs,
          pct_of_timeout: pct,
          over_timeout: overTimeout,
          post_loop_ms: postLoopMs,
          post_loop_stall: postLoopStall,
          stall_tag: tag,
        });
        console[level](
          `[embedded-agent][gateway] ${tag} ${label}: node=${node.id} elapsedMs=${elapsedMs} ` +
          `(${pct}% of timeout ${timeoutMs}ms) assistantStarted=${assistantStartReported} ` +
          `partialReply=${partialReplyReported} lifecycleEndAt=${_lifecycleEndAt > 0 ? `${postLoopMs}ms ago` : "not-yet"} ` +
          `resolveAt=${_runEmbeddedResolveAt > 0 ? "settled" : "NOT-RESOLVED"} ` +
          `abortAborted=${_abortSignal?.aborted ?? "no-signal"}`,
        );
      }, 30_000);
      if (timer.unref) timer.unref();
      try {
        const result = await fn();
        // ── [diag] RPC settled. If this log never appears but phase=end did,
        // the runEmbeddedPiAgent promise stuck open — gateway never closed it.
        _runEmbeddedResolveAt = Date.now();
        const postLoopMs = _lifecycleEndAt > 0 ? _runEmbeddedResolveAt - _lifecycleEndAt : 0;
        recordRpcDiag("rpc_resolved", {
          label,
          elapsed_ms: Date.now() - gwStart,
          post_loop_ms: postLoopMs,
          lifecycle_end_seen: _lifecycleEndAt > 0,
        });
        console.log(
          `[embedded-agent][gateway] RPC-RESOLVED ${label}: node=${node.id} ` +
          `elapsedMs=${Date.now() - gwStart} postLoopMs=${postLoopMs} ` +
          `(time from phase=end → RPC settle; healthy nodes ≲1000ms) ` +
          `lifecycleEndSeen=${_lifecycleEndAt > 0} assistantStarted=${assistantStartReported}`,
        );
        return result;
      } finally {
        clearInterval(timer);
        const elapsedMs = Date.now() - gwStart;
        const liveness =
          (run?.meta as Record<string, unknown> | undefined)?.livenessState ?? "n/a";
        recordRpcDiag("rpc_exit", {
          label,
          elapsed_ms: elapsedMs,
          run_present: Boolean(run),
          liveness_state: liveness,
        });
        console.log(
          `[embedded-agent][gateway] EXIT ${label}: node=${node.id} ` +
          `elapsedMs=${elapsedMs} runPresent=${Boolean(run)} livenessState=${liveness} ` +
          `assistantStarted=${assistantStartReported} rpcResolved=${_runEmbeddedResolveAt > 0}`,
        );
      }
    };

    // Acquire LLM semaphore permit before issuing any LLM requests.
    // This prevents concurrent workflow instances from flooding the LLM provider
    console.log(
      `[embedded-agent][permit] ENTER acquire: node=${node.id} (${effectiveNodeName}) ` +
      `flowId=${effectiveFlowId} runId=${executionOptions.runId}`,
    );
    const _permitAcquireStart = Date.now();
    recordRpcDiag("permit_acquire_enter");
    releaseLlmPermit = await acquireLlmPermit({
      flowId: executionOptions.flowId,
      nodeId: node.id,
      nodeTitle: node.title ?? node.id,
    });
    recordRpcDiag("permit_acquired", { waited_ms: Date.now() - _permitAcquireStart });
    console.log(
      `[embedded-agent][permit] ACQUIRED: node=${node.id} waitedMs=${Date.now() - _permitAcquireStart} ` +
      `flowId=${effectiveFlowId}`,
    );

    const MAX_RATE_LIMIT_RETRIES = 3;
    const RATE_LIMIT_BASE_DELAY_MS = 5_000;
    run = await _wrapGatewayCall("primary", () => runEmbedded(effectivePrompt));
    let runMetadataUsage = extractTokenUsageFromMetadata(run.meta);
    let runUsage = resolveUsageWithSessionFallback(runMetadataUsage, nodeContext.sessionFile);

    // Agent-level error (API failure, timeout) — retry on 429 rate-limit errors
    let agentError = extractAgentError(run);
    if (agentError) {
      const isRateLimit =
        agentError.includes("429") ||
        agentError.toLowerCase().includes("rate limit") ||
        agentError.includes("模型全量限流") ||
        agentError.includes("限流");
      if (isRateLimit) {
        for (let attempt = 1; attempt <= MAX_RATE_LIMIT_RETRIES; attempt++) {
          const delay = RATE_LIMIT_BASE_DELAY_MS * attempt;
          await emitAgentEvent(
            "agent_event",
            `${node.title} 遇到 API 限流 (429)，${(delay / 1000).toFixed(0)}s 后重试 (${attempt}/${MAX_RATE_LIMIT_RETRIES})`,
            { retryAttempt: attempt, maxRetries: MAX_RATE_LIMIT_RETRIES, delayMs: delay },
            "lifecycle",
          );
          await new Promise((resolve) => setTimeout(resolve, delay));
          // Compact session before retry — it may have grown from the failed attempt
          {
            const retryCompact = await maybeCompactSessionFileSafe(nodeContext.sessionFile, {
              maxSessionTokens: executionOptions.sessionCompressionConfig?.maxSessionTokens ?? 50000,
              minTokensToCompact: executionOptions.sessionCompressionConfig?.minTokensToCompact ?? 30000,
            });
            if (retryCompact.kind === "compressed") {
              await emitAgentEvent("agent_event",
                `${node.title} 限流重试前 Session 压缩 (sidecar)：${retryCompact.stats.inputTokens}→${retryCompact.stats.outputTokens} tokens`,
                { compaction: retryCompact.stats, phase: "rate-limit-retry" },
                "lifecycle",
              );
            }
          }
          run = await _wrapGatewayCall(`rate-limit-retry-${attempt}`, () => runEmbedded(effectivePrompt));
          runMetadataUsage = extractTokenUsageFromMetadata(run.meta);
          runUsage = resolveUsageWithSessionFallback(runMetadataUsage, nodeContext.sessionFile);
          agentError = extractAgentError(run);
          if (!agentError) break;
          const stillRateLimit =
            agentError.includes("429") ||
            agentError.toLowerCase().includes("rate limit") ||
            agentError.includes("模型全量限流") ||
            agentError.includes("限流");
          if (!stillRateLimit) break;
        }
      }
      if (agentError) {
        return {
          status: "failed",
          error: agentError,
          usage: runUsage,
          sessionFile: nodeContext.sessionFile,
          skillName: executor.skillName?.trim() || null,
          resolvedPrompt: resolvedPromptTruncated,
          // Preserve run.meta (includes teclawDiagnostic) in result so
          // controller can propagate it into nodeState.result → result_json.
          ...(run.meta ? { result: { meta: run.meta } } : {}),
        };
      }
    }

    // Extract output before checking tool errors — if the agent produced valid output,
    // intermediate tool errors (isError payloads) should not cause node failure.
    // The agent may have recovered from a tool error and produced correct results.
    const output = extractEmbeddedRunText(run);
    // ── [diag] Post-RPC checkpoint. If RPC-RESOLVED fired but this line never
    // logs, extraction itself threw. If it logs but no final_output persists,
    // the stall is in JSON parsing/repair, not the RPC.
    {
      const livenessState = (run.meta as Record<string, unknown> | undefined)?.livenessState ?? "n/a";
      const stopReason = (run.meta as Record<string, unknown> | undefined)?.stopReason ?? "n/a";
      recordRpcDiag("output_extracted", {
        output_len: output.length,
        output_trim_len: output.trim().length,
        liveness_state: livenessState,
        stop_reason: stopReason,
        output_mode: executor.outputMode ?? null,
      });
      console.log(
        `[embedded-agent][output] EXTRACTED: node=${node.id} outputLen=${output.length} ` +
        `outputTrimLen=${output.trim().length} livenessState=${livenessState} stopReason=${stopReason} ` +
        `outputMode=${executor.outputMode ?? "n/a"}`,
      );
    }

    // Fix abandoned session: if the run ended with livenessState "abandoned"
    // (agent's last turn had stopReason="toolUse") but produced valid output,
    // clean up the session file so it's safe for future replay/cache reuse.
    {
      const livenessState = (run.meta as Record<string, unknown> | undefined)?.livenessState;
      const hasValidOutput = output.trim().length > 0;
      if (livenessState === "abandoned" && hasValidOutput) {
        try {
          recordRpcDiag("abandoned_cleanup_attempt", { session_file: nodeContext.sessionFile });
          const cleaned = await cleanupAbandonedSession(nodeContext.sessionFile, true);
          recordRpcDiag("abandoned_cleanup_result", { cleaned: Boolean(cleaned) });
          if (cleaned) {
            await emitAgentEvent("agent_event",
              `${node.title} 已清理 abandoned session（stopReason: toolUse → stop）`,
              { livenessState, sessionFile: nodeContext.sessionFile },
              "lifecycle",
            );
          }
        } catch (cleanupErr) {
          recordRpcDiag("abandoned_cleanup_threw", {
            error: cleanupErr instanceof Error ? cleanupErr.message : String(cleanupErr),
          });
          // Don't block on cleanup failure — logging only
          console.warn(
            `[embedded-agent] cleanupAbandonedSession failed: ${
              cleanupErr instanceof Error ? cleanupErr.message : String(cleanupErr)
            }`,
          );
        }
      }
    }

    if (executor.outputMode === "json") {
      if (!output.trim()) {
        // No output at all — use tool errors as the failure reason if available
        const toolError = extractToolErrors(run);
        console.warn(
          `[embedded-agent][output] EMPTY-OUTPUT (json): node=${node.id} toolError=${toolError ? "present" : "none"}`,
        );
        return {
          status: "failed",
          error: toolError ?? "embedded-agent 未返回可见模型输出，无法推进 JSON 节点",
          usage: runUsage,
          resolvedPrompt: resolvedPromptTruncated,
        };
      }

      const parsed = tryParseJson(output);
      if (parsed) {
        // Valid JSON output — succeed regardless of intermediate tool errors
        console.log(`[embedded-agent][output] JSON-VALID direct: node=${node.id} calling finalOutput…`);
        recordRpcDiag("final_output_invoking", { mode: "json-valid" });
        await executionOptions.finalOutput?.(output);
        recordRpcDiag("final_output_invoked", { mode: "json-valid" });
        console.log(`[embedded-agent][output] JSON-VALID finalOutput returned: node=${node.id}`);
        const failedStatusError = failedJsonStatusError(parsed);
        if (failedStatusError) {
          return { status: "failed", result: parsed, error: failedStatusError, usage: runUsage, resolvedPrompt: resolvedPromptTruncated };
        }
        const jsonWarnings = collectRunWarnings(run, false, parsed);
        const jsonResult: ExecutorResult = { status: "succeeded", result: parsed, warnings: jsonWarnings.length > 0 ? jsonWarnings : undefined, usage: runUsage };
        return augmentWithSessionErrors(jsonResult, nodeContext.sessionFile, executor.skillName, resolvedPromptTruncated);
      }

      // Lightweight JSON repair (no LLM call) — handles code fences, trailing commas, prefix/suffix noise
      const lightweightRepaired = lightweightJsonRepair(output);
      if (lightweightRepaired) {
        const lightweightParsed = tryParseJson(lightweightRepaired);
        if (lightweightParsed) {
          console.log(
            `[embedded-agent] JSON lightweight repair succeeded: node=${node.id} (${node.title ?? node.id})`,
          );
          recordRpcDiag("final_output_invoking", { mode: "json-lightweight-repair" });
          await executionOptions.finalOutput?.(lightweightRepaired);
          recordRpcDiag("final_output_invoked", { mode: "json-lightweight-repair" });
          const failedStatusError = failedJsonStatusError(lightweightParsed);
          if (failedStatusError) {
            return { status: "failed", result: lightweightParsed, error: failedStatusError, usage: runUsage, resolvedPrompt: resolvedPromptTruncated };
          }
          const repairWarnings = collectRunWarnings(run, true, lightweightParsed);
          const lightweightResult: ExecutorResult = { status: "succeeded", result: lightweightParsed, warnings: repairWarnings.length > 0 ? repairWarnings : undefined, usage: runUsage };
          return augmentWithSessionErrors(lightweightResult, nodeContext.sessionFile, executor.skillName, resolvedPromptTruncated);
        }
      }
      console.warn(`[embedded-agent][output] JSON-INVALID → will attempt LLM repair: node=${node.id}`);

      // Invalid JSON — attempt LLM repair
      // Compact the session before the repair LLM call. The session may have
      // bloated to 100K+ tokens through tool_use/tool_result rounds inside
      // the initial run. This applies to ALL history modes — the bug is that
      // "structured" sessions grow large inside runEmbeddedPiAgent too.
      {
        const repairCompact = await maybeCompactSessionFileSafe(nodeContext.sessionFile, {
          maxSessionTokens: executionOptions.sessionCompressionConfig?.maxSessionTokens ?? 50000,
          minTokensToCompact: executionOptions.sessionCompressionConfig?.minTokensToCompact ?? 30000,
        });
        if (repairCompact.kind === "compressed") {
          await emitAgentEvent("agent_event",
            `${node.title} JSON修复前 Session 压缩 (sidecar)：${repairCompact.stats.inputTokens}→${repairCompact.stats.outputTokens} tokens`,
            { compaction: repairCompact.stats, phase: "json-repair" },
            "lifecycle",
          );
        }
      }
      const repairRun = await _wrapGatewayCall("json-repair", () => runEmbedded(buildJsonRepairPrompt({ node, rawOutput: output })));
      const repairMetadataUsage = extractTokenUsageFromMetadata(repairRun.meta);
      const combinedUsage = resolveUsageWithSessionFallback(
        addTokenUsage(runUsage, repairMetadataUsage),
        nodeContext.sessionFile,
      );
      // Agent-level error on repair run always fails
      const repairAgentError = extractAgentError(repairRun);
      if (repairAgentError) {
        return {
          status: "failed",
          result: {
            parseFailureRawOutput: truncateText(output),
            repairRunError: truncateText(repairAgentError),
          },
          error: `${formatJsonParseFailureError(node)} Repair runner returned an error; see repairRunError in debug details.`,
          usage: combinedUsage,
          resolvedPrompt: resolvedPromptTruncated,
        };
      }
      const repairOutput = extractEmbeddedRunText(repairRun);
      const repaired = tryParseJson(repairOutput);
      if (!repaired) {
        // Repair also failed to produce valid JSON — include tool errors if any
        const repairToolError = extractToolErrors(repairRun);
        return {
          status: "failed",
          result: {
            parseFailureRawOutput: truncateText(output),
            repairParseFailureRawOutput: truncateText(repairOutput),
            ...(repairToolError && { repairToolError: truncateText(repairToolError) }),
          },
          error: formatJsonParseFailureError(node),
          usage: combinedUsage,
          resolvedPrompt: resolvedPromptTruncated,
        };
      }
      // Repair produced valid JSON — succeed regardless of tool errors
      recordRpcDiag("final_output_invoking", { mode: "json-llm-repair" });
      await executionOptions.finalOutput?.(repairOutput);
      recordRpcDiag("final_output_invoked", { mode: "json-llm-repair" });
      const failedStatusError = failedJsonStatusError(repaired);
      if (failedStatusError) {
        return { status: "failed", result: repaired, error: failedStatusError, usage: combinedUsage, resolvedPrompt: resolvedPromptTruncated };
      }
      const repairWarnings = collectRunWarnings(run, true, repaired);
      const llmRepairResult: ExecutorResult = { status: "succeeded", result: repaired, warnings: repairWarnings.length > 0 ? repairWarnings : undefined, usage: combinedUsage };
      return augmentWithSessionErrors(llmRepairResult, nodeContext.sessionFile, executor.skillName, resolvedPromptTruncated);
    }

    // Text mode — if agent produced output, succeed regardless of tool errors
    if (output.trim()) {
      recordRpcDiag("final_output_invoking", { mode: "text" });
      await executionOptions.finalOutput?.(output);
      recordRpcDiag("final_output_invoked", { mode: "text" });
    }
    const textWarnings = collectRunWarnings(run, false);
    const textResult: ExecutorResult = { status: "succeeded", result: { output }, warnings: textWarnings.length > 0 ? textWarnings : undefined, usage: runUsage };
    return augmentWithSessionErrors(textResult, nodeContext.sessionFile, executor.skillName, resolvedPromptTruncated);
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    recordRpcDiag("rpc_caught", {
      error: message,
      run_present: Boolean(run),
      liveness_state: (run?.meta as Record<string, unknown> | undefined)?.livenessState ?? null,
    });
    console.error(
      `[embedded-agent][gateway] CAUGHT exception: node=${node.id} (${node.title ?? node.id}) ` +
      `flowId=${executionOptions.flowId ?? "unknown"} runId=${executionOptions.runId} ` +
      `assistantStarted=${assistantStartReported} runPresent=${Boolean(run)} ` +
      `livenessState=${(run?.meta as Record<string, unknown> | undefined)?.livenessState ?? "n/a"} ` +
      `error=${message}`,
    );
    const livenessStateCatch =
      (run?.meta as Record<string, unknown> | undefined)?.livenessState;
    if (livenessStateCatch === "abandoned") {
      // The OpenClaw runtime abandoned this executor context (an agent's last
      // stopReason=toolUse never settled). Best-effort cleanup of the session
      // file so future replay/cache reuse is safe; the run result is lost.
      console.error(
        `[embedded-agent][gateway] ABANDONED runtime context: node=${node.id} ` +
        `— agent loop did not settle (stopReason=toolUse), result discarded. ` +
        `This is a known hang form: the gateway stopped emitting callbacks but ` +
        `never threw, so runEmbedded never returned.`,
      );
    }
    // Preserve run.meta (teclawDiagnostic) if run was assigned before the exception.
    const catchMeta = run?.meta ? { meta: run.meta } : undefined;
    return {
      status: "failed",
      error: `embedded-agent execution failed: ${message}`,
      sessionFile: _sessionFileForTrace,
      skillName: executor.skillName?.trim() || null,
      resolvedPrompt: resolvedPromptTruncated,
      rawError: err,
      ...(catchMeta ? { result: catchMeta } : {}),
    };
  } finally {
    // ── [diag] Proves executeEmbeddedAgent's try-block exited. If this never
    // logs for a stuck node, the executor itself is hung inside `await
    // runEmbedded` (runEmbeddedPiAgent RPC never settled) — controller's
    // executeNodeWithRetry is blocked on this promise forever.
    recordRpcDiag("rpc_finally", {
      rpc_resolved: _runEmbeddedResolveAt > 0,
      lifecycle_end_seen: _lifecycleEndAt > 0,
      permit_acquired: releaseLlmPermit !== null,
    });
    console.log(
      `[embedded-agent][gateway] FINALLY: node=${node.id} (${node.title ?? node.id}) ` +
      `flowId=${executionOptions.flowId ?? "unknown"} runId=${executionOptions.runId} ` +
      `rpcResolved=${_runEmbeddedResolveAt > 0} lifecycleEndSeen=${_lifecycleEndAt > 0} ` +
      `permitAcquired=${releaseLlmPermit !== null}`,
    );
    // Always release the LLM semaphore permit so other waiting requests can proceed.
    // This runs regardless of success, failure, or exception.
    if (releaseLlmPermit) {
      releaseLlmPermit();
    }

    // Clean up the sidecar file created by maybeCompactSessionFileSafe.
    // The sidecar (<file>.compressed.jsonl) is only needed during the agent
    // loop for compression; after the agent completes, the original JSONL
    // (which is never modified) will be read by extractNodeStepTrace and
    // session-error-extractor. Removing the sidecar avoids stale temporary
    // files accumulating on disk.
    if (_sessionFileForTrace) {
      try {
        const { sidecarPathFor } = await import("../context/session-compressor.js");
        const { unlink } = await import("node:fs/promises");
        const sidecarPath = sidecarPathFor(_sessionFileForTrace);
        try { await unlink(sidecarPath); } catch { /* file may not exist — ignore */ }
        console.log(
          `[embedded-agent] sidecar cleanup: removed ${sidecarPath}`,
        );
      } catch {
        // import or sidecarPathFor failed — non-critical, ignore
      }
    }

    // Always unregister the per-session compression config so the registry
    // doesn't leak entries.  This runs regardless of success or failure.
    if (effectiveSessionKey) {
      unregisterSessionCompressionConfig(effectiveSessionKey);
    }

    // NOTE: Step trace persistence (persistNodeStepTrace) has been MOVED from
    // this finally block to Controller.emitNodeEvent(). The executor context
    // may be abandoned by the OpenClaw runtime (livenessState="abandoned"),
    // causing the async import().then() inside persistNodeStepTrace to never
    // resolve. The Controller's event loop is never abandoned — the same
    // proven path used for flow_runs/node_executions writes. sessionFile and
    // skillName are now passed back via ExecutorResult to the Controller.
  }
}
