import { readFileSync, existsSync, statSync } from "node:fs";
import type { TaskguardExtensions } from "./community/types.js";

// Re-export the extensions type so corp packages can import it
export type { TaskguardExtensions } from "./community/types.js";

// ── Module-level extensions holder ──
// Set by registerTaskguardPlugin(); checked by createApiClient() and other
// corp-extension injection points. When extensions provide a factory,
// it overrides the community stub.
let _extensions: TaskguardExtensions | undefined;

// ── Module-level holders for corp extension injections ──
// These are populated by registerTaskguardPlugin() when corp code provides
// implementations via TaskguardExtensions. Community code checks these
// before falling back to default behavior.
let _corpNotifier: ((config: unknown) => unknown) | undefined;
let _corpApprovalProvider: ((config: unknown) => unknown) | undefined;
let _corpAuthMethods: unknown | undefined;


import { stat as fsStat } from "node:fs/promises";
import { join } from "node:path";
import { homedir } from "node:os";
import { cleanupExpiredApprovalCards } from "./card/approval-card-registry.js";
import { resolveOriginalConversationId } from "./card/approval-card-service.js";
import { startCardWebPoller, updatePollerDeps, stopCardWebPoller } from "./card/approval-card-web-poller.js";
import { startCallbackTimeoutPoller, captureCallbackPollerDeps, stopCallbackTimeoutPoller } from "./callback/timeout-poller.js";
import type { PluginApi } from "./platform/openclaw-types.js";
import type {
  ControllerAction,
  ExecutorResult,
  FlowEvent,
  FlowState,
  ApprovalDeliveryMode,
  CollaborationDeliveryMode,
  ExecutionMode,
  WorkflowNode,
  WorkflowApprover,
  WorkflowCommandSurface,
  WorkflowRuntimeUser,
  WorkflowSpec,
} from "./types.js";
import type { DevWorkflowCallbackParams } from "./types.js";
import { handleDevWorkflowCallback } from "./dev-workflow-callback.js";
import { createDatabase } from "./db/factory.js";
import { FlowEventRepository } from "./db/repositories/event-repository.js";
import { FlowMetricsRepository } from "./db/repositories/metrics-repository.js";
import { TriggeredAlertRepository } from "./db/repositories/alert-repository.js";
import { NodeExecutionRepository } from "./db/repositories/node-execution-repository.js";
import { FlowRunRepository } from "./db/repositories/flow-run-repository.js";
import { NodeStepTraceRepository } from "./db/repositories/node-step-traces-repository.js";
import { ExecutionStepLogRepository } from "./db/repositories/execution-step-log-repository.js";
import { RunLogRepository } from "./db/repositories/run-log-repository.js";
import { RunLogApiRepository } from "./db/api-repositories/run-log-api-repository.js";
import type { IRunLogRepository } from "./db/repositories/types.js";
import { HallucinationCheckRepository } from "./db/repositories/hallucination-check-repository.js";
import { HallucinationCheckApiRepository } from "./db/api-repositories/hallucination-check-api-repository.js";
import { FacadeBindingRepository } from "./db/repositories/facade-binding-repository.js";
import { WorkflowSpecRepository } from "./db/repositories/workflow-spec-repository.js";
import type { IWorkflowSpecRepository, IBotWorkflowPermissionRepository, INotificationConfigRepository } from "./db/repositories/types.js";
import { ExecutionStepLogger } from "./execution-log/logger.js";
import { DynamicWorkflowEventEmitter } from "./observability/emitter.js";
import { RunLogUploader, RunArchiveBuilder, RunArchiveApiBuilder, formatArchiveSummary } from "./run-archive/index.js";
import { GuardianAgent } from "./guardian/guardian-agent.js";
import type { GuardianConfig } from "./guardian/types.js";
import { NotificationConfigRepository } from "./db/repositories/notification-config-repository.js";
import { HttpCallbackConfigRepository } from "./db/repositories/http-callback-config-repository.js";
import { HttpCallbackConfigApiRepository } from "./db/api-repositories/http-callback-config-api-repository.js";
import { HttpCallbackLogRepository } from "./db/repositories/http-callback-log-repository.js";
import { HttpCallbackLogApiRepository } from "./db/api-repositories/http-callback-log-api-repository.js";
import { ValidationTemplateRepository } from "./db/repositories/validation-template-repository.js";
import { setEventRepository, setDatabase, getDatabase, setMetricsRepository, setAlertRepository, setNodeExecutionRepository, setFlowRunRepository, setNodeStepTraceRepository, getNodeStepTraceRepository, setHallucinationCheckRepository, setKnowledgeBases, setKnowledgeBaseManager, setRetryConfig, setAnalysisConfig, setAlertingConfig, setWorkflowNotificationConfig, setNotificationConfigRepository, setValidationTemplateResolver, reportNodeProgress, reapStaleRunningFlows, setHttpCallbackRepositories, setHttpCallbackLogRepository, reloadHttpCallbackConfigs, setEngineName, setRunLogUploader, recoverOrphanedFlows, getEngineName, getFlowRunRepository, setGuardianAgent } from "./controller.js";
import { loadConfig, initConfig } from "./config/loader.js";
import { startApiServer } from "./api/server.js";
import { ScheduledTriggerRepository } from "./scheduler/trigger-store.js";
import { CronScheduler, type WorkflowLauncher } from "./scheduler/scheduler.js";
import { loadBotId, loadOwnerId } from "./credentials.js";
import { initFlowControl, stopFlowControl, getFlowControlService } from "./flow-control/index.js";
import { configureLlmSemaphore } from "./executors/llm-semaphore.js";
import { enqueueInject } from "./inject-queue.js";
import { shouldInjectForFlow } from "./inject-gating.js";
import { parseScheduleArgs, handleScheduleCommand, type ScheduleCommandDeps } from "./scheduler/schedule-command.js";
import { parseWebhookArgs, handleWebhookCommand, type WebhookCommandDeps } from "./webhook/webhook-command.js";
import { WebhookTriggerRepository } from "./db/repositories/webhook-trigger-repository.js";
import { WebhookEventRepository } from "./db/repositories/webhook-event-repository.js";
import { ApiClient, type ApiClientConfig } from "./db/api-client.js";
import { FlowRunApiRepository } from "./db/api-repositories/flow-run-api-repository.js";
import { NodeExecutionApiRepository } from "./db/api-repositories/node-execution-api-repository.js";
import { FlowEventApiRepository } from "./db/api-repositories/event-api-repository.js";
import { FlowMetricsApiRepository } from "./db/api-repositories/metrics-api-repository.js";
import { TriggeredAlertApiRepository } from "./db/api-repositories/alert-api-repository.js";
import { WorkflowSpecApiRepository } from "./db/api-repositories/workflow-spec-api-repository.js";
import { ValidationTemplateApiRepository } from "./db/api-repositories/validation-template-api-repository.js";
import { FacadeBindingApiRepository } from "./db/api-repositories/facade-binding-api-repository.js";
import { NodeStepTraceApiRepository } from "./db/api-repositories/node-step-traces-api-repository.js";
import { ScheduledTriggerApiRepository } from "./db/api-repositories/scheduled-trigger-api-repository.js";
import { WebhookTriggerApiRepository } from "./db/api-repositories/webhook-trigger-api-repository.js";
import { WebhookEventApiRepository } from "./db/api-repositories/webhook-event-api-repository.js";
import { BotWorkflowPermissionApiRepository } from "./db/api-repositories/bot-workflow-permission-api-repository.js";
import { NotificationConfigApiRepository } from "./db/api-repositories/notification-config-api-repository.js";
import type { ApiConfig } from "./db/types.js";
import { loadDatabaseConfig } from "./db/config.js";
import { computeNextFireTime } from "./scheduler/cron-parser.js";
import { YuQueAdapter } from "./knowledge/yuque-adapter.js";
import { AgentMindAdapter } from "./knowledge/agentmind-adapter.js";
import { KnowledgeBaseManager } from "./knowledge/manager.js";
import { resolveSessionId } from "./session-resolver.js";
import { buildActionTemplateExtras, buildTemplateContext, type TemplateContext } from "./runner.js";
import {
  handleRun,
  handleConfirm,
  handleRevise,
  handleReject,
  handleResume,
  handleState,
  handleDebug,
  handleFlows,
  handleFlowsCleanup,
  handleRepairLegacyIdentity,
  handleRepairExternalPackPin,
  handleFlowExport,
  handleFlowImport,
  handleRetry,
  handleSubmit,
  handleSkip,
  handleLogs,
  handleBcsCallback,
  handleBcsCollaborationMessage,
  handleList,
  handleDetail,
  handleValidate,
  handlePacks,
  handlePackInspect,
  handlePackValidate,
  handleCutoverCheck,
  handleReopen,
  handleTest,
  handleHelp,
  resumeQueuedWorkflow,
  type ControllerDeps,
  abortAsyncExecutionsForSession,
} from "./controller.js";
import { classifyBcsCollaborationMessage } from "./bcs-collaboration-protocol.js";
import {
  loadWorkflowPackCatalog,
  resolveWorkflow,
  resolveWorkflowByIdFromPacks,
} from "./packs/resolver.js";
import { resolvePackRootFromId } from "./packs/pack-root.js";
import type { ResolvedWorkflow } from "./packs/types.js";
import { executeEmbeddedAgent, type EmbeddedAgentLoopEvent, type ExecuteEmbeddedAgentOptions } from "./executors/embedded-agent.js";
import {
  getSessionCompressionEntry,
  updateSessionActualTokenEstimate,
  compressionLogTag,
} from "./context/session-watch-compressor.js";

/** Find a good truncation boundary (newline or space) near maxChars. */
function findTruncationBoundary(text: string, maxChars: number): number {
  const lastNewline = text.lastIndexOf("\n", maxChars);
  if (lastNewline > maxChars * 0.5) return lastNewline;
  const lastSpace = text.lastIndexOf(" ", maxChars);
  if (lastSpace > maxChars * 0.5) return lastSpace;
  return maxChars;
}
import {
  maybeCompactSessionFileSafe,
  type SessionCompressionStats,
} from "./context/session-compressor.js";
import { detectHumanGateIntent, isExactMatch, type WaitingFlowInfo, type DetectedIntent } from "./input/intent-detector.js";
import { ENUM_SYNONYM_MAP, REJECT_KEYWORDS, CONFIRM_KEYWORDS } from "./input/choice-keywords.js";
import { renderWaitingHint } from "./input/waiting-hint.js";
import { estimateTokenUsageFromMessages } from "./token-usage.js";
import { estimateTextTokens } from "./context/token-counter.js";
import { sidecarWriter } from "./context/tool-result-sidecar.js";
import {
  formatEmbeddedAgentFinalOutput,
  formatEmbeddedAgentLoopProgress,
  shouldRecordEmbeddedAgentLoopEvent,
} from "./embedded-agent-events.js";
import { executeCliScript } from "./executors/cli-script.js";
import { executeMcpCall } from "./executors/mcp-call.js";
import { executeHumanWait } from "./executors/human-wait.js";
import { executeAsyncCallback, type AsyncCallbackExecutorDeps } from "./executors/async-callback.js";
import { executeSubagent } from "./executors/subagent.js";
import {
  runEmbeddedFallbackAfterSubagentFailure,
  shouldFallbackSubagentToEmbedded,
  toEmbeddedFallbackNode,
  validateApprovalFallbackResult,
} from "./executors/subagent-fallback.js";
import { executeSubworkflow, type SubworkflowDeps } from "./executors/subworkflow.js";
import { executeBcsRoute } from "./executors/bcs-route.js";
import { executeApprovalCardDingtalk } from "./executors/approval-card-dingtalk.js";
import { executeApprovalCardWeb, type ApprovalCardWebApi } from "./executors/approval-card-web.js";
import { buildApprovalCardData, renderAixUICard } from "./approval-card-data.js";
import { validateApprover, extractSenderIdFromMessage } from "./approver-validator.js";
import { executeBaasCall } from "./executors/baas-call.js";
import type { EmbeddedAgentToolContext } from "./executors/embedded-context.js";
import { decorateApprovalCallbackResult } from "./approval-actors.js";
import { createActionRegistry } from "./actions/registry.js";
import type { ActionExecutionContext, ActionRegistry } from "./actions/types.js";
import { resolveActionArgs } from "./actions/template.js";
import { registerPackPythonActions } from "./actions/pack-python.js";
import { parseWorkflowCommandWithFacade, tokenizeCommand } from "./command-parser.js";
import {
  buildFacadeRegistry,
  formatWorkflowCommand as formatFacadeWorkflowCommand,
  loadDbFacadeBindings,
  loadApiFacadeBindings,
  type DbFacadeBinding,
  type FacadeRegistry,
} from "./facades/registry.js";
import { parseRawFlowState, readFlowId } from "./flow-record.js";
import { appendWorkflowJsonlLog, buildDirectLogRecord, buildWorkflowLogRecord, formatLocalIsoWithOffset } from "./workflow-log.js";
import { extractWrappedWorkflowSlashCommand } from "./wrapped-slash-command.js";
import { resolveRuntimeUserContext } from "./runtime/user-context.js";
import { resolveUserIdentity } from "./runtime/user-identity.js";
import { createOpenClawAdapter, type OpenClawAdapterOptions } from "./platform/openclaw-adapter.js";
import { resolveEngineName } from "./platform/types.js";
import { buildControllerDeps } from "./platform/adapter-to-deps.js";
import { executeAction, resolveExecutionMode, isGroupSessionKey, extractGroupIdFromSessionKey, type DispatchDepsBuilders } from "./dispatch.js";
import { getLegacyApprovalExecutor } from "./legacy-runtime.js";
import { buildSkipResult, evaluateSkipWhenConditions, readNodeSkipWhen } from "./skip-when.js";

const COMMAND_PARAMETERS = {
  type: "object" as const,
  properties: {
    command: {
      type: "string" as const,
      description: "命令字符串，如 'run <workflowId> --key value', 'flows --limit 20', 'debug <flowId>', 'confirm 备注', 'list'",
    },
    commandName: {
      type: "string" as const,
      description: "触发该工具的 slash command 名称，如 workflow 或 pack manifest 声明的 facade command",
    },
    skillName: {
      type: "string" as const,
      description: "触发该工具的 OpenClaw skill 名称",
    },
    // ── debug-segment 专用：上游 context 透传 ──
    // command 字符串只能承载标量,debug-segment 需要的 nodeOutput/workflowData/input
    // 这些嵌套对象无法编进 command 串。command-parser 因此把 action.nodeOutput 硬编码 {},
    // 原本指望 handler 兜底 merge 但一直没实现(→ 模板渲染空、executedNodes=[])。
    // 这里暴露 optional 字段,execute 读出后经 dispatchWorkflowCommand 的 inlineDebugContext
    // 仅在 action.action==="debug-segment" 时 merge 进 action。其他子命令忽略这三个字段。
    nodeOutput: {
      type: "object" as const,
      description: "仅 debug-segment 生效:上游节点输出。key 为 nodeId,value 为该节点 result。其他命令忽略。",
      additionalProperties: { type: "object" as const },
    },
    workflowData: {
      type: "object" as const,
      description: "仅 debug-segment 生效:workflowData 上下文。其他命令忽略。",
    },
    input: {
      type: "object" as const,
      description: "仅 debug-segment 生效:flow 级别输入参数。其他命令忽略。",
    },
  },
  required: ["command"] as const,
};
const CHOICE_PARAMETERS = {
  type: "object" as const,
  properties: {
    action: {
      type: "string" as const,
      enum: ["confirm", "reject"] as const,
      description: "用户的选择动作",
    },
    choice: {
      type: "string" as const,
      description: "用户选择的分支标识，如 fast/thorough",
    },
    note: {
      type: "string" as const,
      description: "用户的原始自然语言输入",
    },
  },
  required: ["action"] as const,
};
const MAX_RUNTIME_FLOW_EVENTS = 200;
let runtimeFlowEventSeq = 0;
let runtimeEmbeddedChatSeq = 0;

// Parameters for the standalone workflow_debug_segment plugin tool. Mirrors the
// Zod schema in platform/mcp-tools.ts (kept in JSON-schema form here because the
// OpenClaw plugin tool API takes a plain JSON-schema object, not a Zod schema).
const DEBUG_SEGMENT_PARAMETERS = {
  type: "object" as const,
  properties: {
    workflowId: { type: "string" as const, description: "目标工作流 ID" },
    fromNode: { type: "string" as const, description: "起始节点 ID（从此节点开始执行）" },
    toNode: {
      type: "string" as const,
      description: "终止节点 ID（不传则跑到流程结尾；等于 fromNode 时只执行单节点）",
    },
    nodeOutput: {
      type: "object" as const,
      description: "上游节点输出。key 为 nodeId，value 为该节点的 result 对象。模型从历史运行中构造。",
      additionalProperties: { type: "object" as const },
    },
    workflowData: {
      type: "object" as const,
      description: "workflowData 上下文。目标节点或下游读取 workflowData 时需提供。",
    },
    input: {
      type: "object" as const,
      description: "flow 级别输入参数。目标节点引用 {{input.xxx}} 时需提供。",
    },
  },
  required: ["workflowId", "fromNode"] as const,
};
let activeAbortRunSeq = 0;
/** Ensures orphaned flow recovery runs only once per plugin instance lifetime. */
let _recoveryExecuted = false;

type CommandStopHookEvent = {
  sessionKey: string;
};
type WorkflowCommandEntrypoint = "before_agent_reply" | "before_agent_run" | "workflow_engine_dispatch" | "workflow_engine_start_async";

const AGENT_EXECUTOR_TYPES_REQUIRING_DETACHED_DISPATCH = new Set([
  "embedded-agent",
  "subagent",
  "collaboration",
]);

function workflowContainsAgentRuntimeNode(workflow: WorkflowSpec | undefined): boolean {
  if (!workflow) return false;
  return workflow.nodes.some((node) => {
    const executorType = node.executor?.type;
    if (AGENT_EXECUTOR_TYPES_REQUIRING_DETACHED_DISPATCH.has(executorType)) return true;
    if (getLegacyApprovalExecutor(node)) return true;
    return false;
  });
}

function shouldAutoDetachToolDispatch(params: {
  entrypoint: WorkflowCommandEntrypoint;
  startAsync: boolean;
  action: ControllerAction;
  workflow?: WorkflowSpec;
}): boolean {
  if (params.startAsync) return false;
  if (params.entrypoint !== "workflow_engine_dispatch") return false;
  if (params.action.action !== "run") return false;
  if (process.env.CLAWMIND_WORKFLOW_DISPATCH_SYNC_AGENT_NODES === "1") return false;
  return workflowContainsAgentRuntimeNode(params.workflow);
}
type RegisterHookCapableApi = PluginApi & {
  registerHook: (
    event: "command:stop",
    handler: (event: CommandStopHookEvent) => void | Promise<void>,
    options: { name: string; description?: string },
  ) => void;
};

type ActiveAbortRun = {
  id: string;
  sessionKey: string;
  normalizedSessionKey: string;
  controller: AbortController;
  command?: string;
  commandName?: string;
  startedAt: number;
};

const activeAbortRunsBySession = new Map<string, Map<string, ActiveAbortRun>>();

function normalizeAbortSessionKey(sessionKey: string): string {
  return sessionKey.trim().toLowerCase();
}

function registerActiveAbortRun(params: {
  sessionKey: string;
  commandName?: string;
  command?: string;
}): ActiveAbortRun {
  activeAbortRunSeq += 1;
  const normalizedSessionKey = normalizeAbortSessionKey(params.sessionKey);
  const run: ActiveAbortRun = {
    id: `active_abort_run_${activeAbortRunSeq}`,
    sessionKey: params.sessionKey,
    normalizedSessionKey,
    controller: new AbortController(),
    command: params.command,
    commandName: params.commandName,
    startedAt: Date.now(),
  };

  let runs = activeAbortRunsBySession.get(normalizedSessionKey);
  if (!runs) {
    runs = new Map<string, ActiveAbortRun>();
    activeAbortRunsBySession.set(normalizedSessionKey, runs);
  }
  runs.set(run.id, run);
  return run;
}

function unregisterActiveAbortRun(run: ActiveAbortRun): void {
  const runs = activeAbortRunsBySession.get(run.normalizedSessionKey);
  if (!runs) return;
  runs.delete(run.id);
  if (runs.size === 0) {
    activeAbortRunsBySession.delete(run.normalizedSessionKey);
  }
}

function abortActiveRunsForSession(sessionKey: string): number {
  const runs = activeAbortRunsBySession.get(normalizeAbortSessionKey(sessionKey));
  if (!runs) return 0;

  let aborted = 0;
  for (const run of runs.values()) {
    if (run.controller.signal.aborted) continue;
    run.controller.abort(new Error("OpenClaw /stop requested"));
    aborted += 1;
  }
  return aborted;
}

function createDefaultActionRegistry(resolvedPacks: ControllerDeps["resolvedPacks"] = []): ControllerDeps["actionRegistry"] {
  const registry = createActionRegistry();
  registerPackPythonActions(registry, resolvedPacks);
  return registry;
}

// ── M1: Timeout Protection for Long-Running Commands ──

/** Actions that may invoke executeLoop and block for minutes. */
const LONG_RUNNING_ACTIONS = new Set(["run", "reopen"]);

/**
 * Wrap a long-running dispatch with a configurable timeout.
 * When the timeout fires, returns a friendly message instead of blocking forever.
 * The workflow itself continues running in the background — only the tool call returns.
 *
 * Short commands (state, flows, confirm, etc.) bypass the timeout entirely.
 */
async function dispatchWithTimeout(
  dispatchFn: () => Promise<string>,
  action: string,
  sessionKey: string,
): Promise<string> {
  const { execution } = loadConfig().app;
  const timeoutMs = execution.runTimeoutMs;

  // Timeout disabled or short command — no wrapping needed
  if (timeoutMs <= 0 || !LONG_RUNNING_ACTIONS.has(action)) {
    return dispatchFn();
  }

  const timeoutPromise = new Promise<string>((_resolve) => {
    setTimeout(() => {
      _resolve(
        `[taskguard] ⏱ 工作流命令执行超过 ${Math.round(timeoutMs / 1000)} 秒超时，工具调用已返回。` +
        `\n工作流仍在后台运行中，请使用 \`/workflow inspect\` 查看执行状态。` +
        `\n如需取消，请使用 \`/stop\` 命令。` +
        `\n(session: ${sessionKey.slice(0, 16)}...)`,
      );
    }, timeoutMs);
  });

  const result = await Promise.race([dispatchFn(), timeoutPromise]);
  return result;
}

// ── SessionKey Recovery Helper ──

/**
 * Recover the sessionKey needed to resume a queued flow/node.
 *
 * 1. Try parsing from queue payload JSON ({ sessionKey: "..." })
 * 2. Fallback: look up owner_key from local OpenClaw TaskFlow registry via node:sqlite
 *
 * Uses try/finally to guarantee DatabaseSync handle cleanup.
 * Returns a sessionKey string — either recovered or a synthetic fallback.
 */
function recoverSessionKey(flowId: string, payload: string | null): string {
  // Step 1: extract from payload
  if (payload) {
    try {
      const parsed = JSON.parse(payload);
      if (parsed.sessionKey && typeof parsed.sessionKey === "string") {
        return parsed.sessionKey;
      }
    } catch { /* payload is not JSON */ }
  }

  // Step 2: fallback — look up owner_key from local TaskFlow registry
  try {
    // eslint-disable-next-line @typescript-eslint/no-require-imports
    const { DatabaseSync } = require("node:sqlite") as {
      DatabaseSync: new (path: string) => {
        prepare(sql: string): { get(...args: unknown[]): Record<string, unknown> | undefined };
        close(): void;
      };
    };
    const os = require("node:os") as { homedir(): string };
    const pathModule = require("node:path") as { join(...parts: string[]): string };
    const regPath = pathModule.join(os.homedir(), ".openclaw", "flows", "registry.sqlite");
    const regDb = new DatabaseSync(regPath);
    try {
      const row = regDb.prepare("SELECT owner_key FROM flow_runs WHERE flow_id = ?").get(flowId);
      if (row && typeof row.owner_key === "string") {
        console.log(`[flow-control] recovered sessionKey from registry for ${flowId}`);
        return row.owner_key;
      }
    } finally {
      regDb.close();
    }
  } catch (regErr) {
    console.warn(`[flow-control] failed to look up sessionKey from registry for ${flowId}:`, regErr);
  }

  // Step 3: synthetic fallback (resume will likely fail with wrong session)
  const fallback = `flow-control:resume:${flowId}`;
  console.warn(`[flow-control] no sessionKey for ${flowId}, using fallback — resume will likely fail`);
  return fallback;
}

// ── Chat Inject Helper ──

const DEFAULT_CHAT_INJECT_LABEL = "clawmind";

function truncateForLog(value: unknown, max = 300): string {
  const text = value instanceof Error ? value.message : String(value);
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function formatChatInjectLabel(workflowId?: string): string {
  const trimmed = workflowId?.trim();
  return trimmed || DEFAULT_CHAT_INJECT_LABEL;
}

function inferWorkflowIdFromAction(action: ControllerAction): string | undefined {
  if ("workflowId" in action && typeof action.workflowId === "string" && action.workflowId.trim()) {
    return action.workflowId.trim();
  }
  return undefined;
}

async function injectChatMessage(
  api: PluginApi,
  sessionKey: string,
  message: string,
  idempotencyKey: string,
  label = DEFAULT_CHAT_INJECT_LABEL,
  options: { throwOnError?: boolean } = {},
): Promise<void> {
  console.info("[taskguard] chat.inject start", {
    sessionKey,
    idempotencyKey,
    label,
    messagePreview: message.slice(0, 120),
  });
  recordInjectTrace("chat_inject_start", {
    session_key: sessionKey,
    idempotency_key: idempotencyKey,
    label,
    message_preview: message.slice(0, 120),
  });
  const paramsJson = JSON.stringify({
    sessionKey,
    message,
    label,
  });
  let result: SpawnOutput;
  try {
    result = await api.runtime.system.runCommandWithTimeout(
      ["openclaw", "gateway", "call", "chat.inject", "--json", "--params", paramsJson],
      { timeoutMs: 10_000 },
    ) as SpawnOutput;
  } catch (err) {
    // runCommandWithTimeout throws on timeout / spawn failure — surface it explicitly
    console.warn("[taskguard] chat.inject threw", {
      sessionKey,
      idempotencyKey,
      error: truncateForLog(err),
    });
    recordInjectTrace("chat_inject_threw", {
      session_key: sessionKey,
      idempotency_key: idempotencyKey,
      error: truncateForLog(err),
    });
    if (options.throwOnError) {
      throw new Error(`chat.inject threw (sessionKey=${sessionKey}): ${truncateForLog(err)}`);
    }
    return;
  }
  // runCommandWithTimeout does NOT throw on non-zero exit codes — check SpawnResult.code
  console.info("[taskguard] chat.inject done", {
    sessionKey,
    idempotencyKey,
    exitCode: result.code,
    stdoutPreview: result.stdout?.slice(0, 200),
    stderrPreview: result.stderr?.slice(0, 200),
  });
  recordInjectTrace("chat_inject_done", {
    session_key: sessionKey,
    idempotency_key: idempotencyKey,
    exit_code: result.code,
    stderr_preview: result.stderr?.slice(0, 200),
  });
  if (result.code !== 0) {
    const errorDetail = result.stderr?.trim() || `exit code ${result.code}`;
    console.warn("[taskguard] chat.inject failed", {
      sessionKey,
      idempotencyKey,
      message: message.slice(0, 120),
      exitCode: result.code,
      stderr: errorDetail.slice(0, 500),
    });
    recordInjectTrace("chat_inject_failed", {
      session_key: sessionKey,
      idempotency_key: idempotencyKey,
      message_preview: message.slice(0, 120),
      exit_code: result.code,
      stderr: errorDetail.slice(0, 500),
    });
    if (options.throwOnError) {
      throw new Error(`chat.inject failed (sessionKey=${sessionKey}): ${errorDetail}`);
    }
  }
}

/** Minimal subset of SpawnResult for reading command output. */
interface SpawnOutput {
  code: number | null;
  stdout: string;
  stderr: string;
}

// ── DingTalk Direct Message ──────────────────────────────────────────────────
// Sends a message directly to a DingTalk user via the robot oToMessages API.
// This does NOT require an active session — it uses the robot's accessToken
// obtained from clientId/clientSecret, then calls the proactive messaging API.
// Used for approval card delivery in direct (non-group) mode.

const DINGTALK_TOKEN_URL = "https://api.dingtalk.com/v1.0/oauth2/accessToken";
const DINGTALK_SEND_URL = "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend";
const DINGTALK_GROUP_SEND_URL = "https://api.dingtalk.com/v1.0/robot/groupMessages/send";

/** Cached DingTalk access token with expiry. */
let dingtalkTokenCache: { token: string; expiresAt: number } | null = null;

async function getDingTalkAccessToken(clientId: string, clientSecret: string): Promise<string> {
  const now = Date.now();
  if (dingtalkTokenCache && dingtalkTokenCache.expiresAt > now + 60_000) {
    return dingtalkTokenCache.token;
  }
  const res = await fetch(DINGTALK_TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ appKey: clientId, appSecret: clientSecret }),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`DingTalk token request failed: HTTP ${res.status} ${text.slice(0, 200)}`);
  }
  const data = await res.json() as { accessToken?: string; expireIn?: number };
  if (!data.accessToken) {
    throw new Error(`DingTalk token response missing accessToken: ${JSON.stringify(data).slice(0, 200)}`);
  }
  const expireIn = data.expireIn ?? 7200;
  dingtalkTokenCache = { token: data.accessToken, expiresAt: now + expireIn * 1000 };
  return data.accessToken;
}

/**
 * Send a DingTalk message directly to a user without requiring an active session.
 *
 * Uses the robot proactive messaging API (`oToMessages/batchSend`), which does
 * not depend on sessions at all. Reads clientId/clientSecret from the dingtalk
 * channel config in OpenClaw's openclaw.json.
 */
/**
 * Read DingTalk channel credentials from ~/.openclaw/openclaw.json.
 * Returns the first configured DingTalk account's clientId, clientSecret, and robotCode.
 */
function readDingTalkCredentials(): { clientId: string; clientSecret: string; robotCode: string } | null {
  try {
    const configPath = join(homedir(), ".openclaw", "openclaw.json");
    if (!existsSync(configPath)) return null;
    const raw = readFileSync(configPath, "utf-8");
    const config = JSON.parse(raw) as Record<string, unknown>;
    const channels = config.channels as Record<string, unknown> | undefined;
    const dingtalk = channels?.dingtalk as Record<string, unknown> | undefined;
    if (!dingtalk) return null;

    // Support both single config and accounts object
    let clientId: string | undefined;
    let clientSecret: string | undefined;
    let robotCode: string | undefined;

    if (dingtalk.accounts && typeof dingtalk.accounts === "object") {
      const accounts = dingtalk.accounts as Record<string, Record<string, unknown>>;
      for (const acct of Object.values(accounts)) {
        if (acct.clientId && acct.clientSecret) {
          clientId = acct.clientId as string;
          clientSecret = acct.clientSecret as string;
          robotCode = (acct.robotCode ?? acct.clientId) as string;
          break;
        }
      }
    }
    if (!clientId) {
      clientId = dingtalk.clientId as string | undefined;
      clientSecret = dingtalk.clientSecret as string | undefined;
      robotCode = (dingtalk.robotCode ?? dingtalk.clientId) as string | undefined;
    }

    if (!clientId || !clientSecret) return null;
    return { clientId, clientSecret, robotCode: robotCode ?? clientId };
  } catch (err) {
    console.warn("[taskguard] Failed to read DingTalk credentials", { error: err instanceof Error ? err.message : String(err) });
    return null;
  }
}

/**
 * Send a DingTalk message directly to a user without requiring an active session.
 *
 * Uses the robot proactive messaging API (`oToMessages/batchSend`), which does
 * not depend on sessions at all. Reads clientId/clientSecret from the dingtalk
 * channel config in OpenClaw's openclaw.json.
 */
async function sendDingTalkToUser(
  _api: PluginApi,
  userId: string,
  content: string,
): Promise<{ ok: boolean; error?: string }> {
  console.log("[taskguard] sendDingTalkToUser", { userId, contentLen: content.length });
  try {
    const creds = readDingTalkCredentials();
    if (!creds) {
      return { ok: false, error: "DingTalk clientId/clientSecret not configured in ~/.openclaw/openclaw.json channels.dingtalk" };
    }

    const token = await getDingTalkAccessToken(creds.clientId, creds.clientSecret);
    const code = creds.robotCode;

    // Detect markdown: if content has markdown indicators, use sampleMarkdown; otherwise sampleText
    const hasMarkdown = /[#*_`\n]/.test(content) || content.length > 200;
    const msgKey = hasMarkdown ? "sampleMarkdown" : "sampleText";
    const title = "审批通知";
    const msgParam = hasMarkdown
      ? JSON.stringify({ title, text: content })
      : JSON.stringify({ content });

    const payload = {
      robotCode: code,
      userIds: [userId],
      msgKey,
      msgParam,
    };

    console.log("[taskguard] sendDingTalkToUser calling API", { userId, robotCode: code, msgKey });
    const res = await fetch(DINGTALK_SEND_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-acs-dingtalk-access-token": token,
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      console.error("[taskguard] sendDingTalkToUser API error", { userId, status: res.status, body: text.slice(0, 500) });
      return { ok: false, error: `DingTalk API HTTP ${res.status}: ${text.slice(0, 300)}` };
    }

    const result = await res.json() as Record<string, unknown>;
    console.log("[taskguard] sendDingTalkToUser succeeded", { userId, result: JSON.stringify(result).slice(0, 200) });
    return { ok: true };
  } catch (err) {
    const errorDetail = err instanceof Error ? err.message : String(err);
    console.error("[taskguard] sendDingTalkToUser threw", { userId, error: errorDetail });
    return { ok: false, error: errorDetail };
  }
}

/**
 * Send a DingTalk message directly to a group conversation (openConversationId).
 *
 * Uses the robot proactive group messaging API (`groupMessages/send`), which does
 * not depend on sessions. Reuses the same credentials as sendDingTalkToUser.
 */
async function sendDingTalkToGroup(
  conversationId: string,
  content: string,
): Promise<{ ok: boolean; error?: string }> {
  // DingTalk openConversationId is case-sensitive. OpenClaw normalizes
  // session keys to lowercase, so we must restore the original casing.
  const resolvedConversationId = resolveOriginalConversationId(conversationId);
  console.log("[taskguard] sendDingTalkToGroup", { conversationId, resolvedConversationId: resolvedConversationId !== conversationId ? resolvedConversationId : undefined, contentLen: content.length });
  try {
    const creds = readDingTalkCredentials();
    if (!creds) {
      return { ok: false, error: "DingTalk clientId/clientSecret not configured in ~/.openclaw/openclaw.json channels.dingtalk" };
    }

    const token = await getDingTalkAccessToken(creds.clientId, creds.clientSecret);
    const code = creds.robotCode;

    const hasMarkdown = /[#*_`\n]/.test(content) || content.length > 200;
    const msgKey = hasMarkdown ? "sampleMarkdown" : "sampleText";
    const title = "审批通知";
    const msgParam = hasMarkdown
      ? JSON.stringify({ title, text: content })
      : JSON.stringify({ content });

    const payload = {
      robotCode: code,
      openConversationId: resolvedConversationId,
      msgKey,
      msgParam,
    };

    console.log("[taskguard] sendDingTalkToGroup calling API", { conversationId: resolvedConversationId, robotCode: code, msgKey });
    const res = await fetch(DINGTALK_GROUP_SEND_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "x-acs-dingtalk-access-token": token,
      },
      body: JSON.stringify(payload),
    });

    if (!res.ok) {
      const text = await res.text().catch(() => "");
      console.error("[taskguard] sendDingTalkToGroup API error", { conversationId, status: res.status, body: text.slice(0, 500) });
      return { ok: false, error: `DingTalk group API HTTP ${res.status}: ${text.slice(0, 300)}` };
    }

    const result = await res.json() as Record<string, unknown>;
    console.log("[taskguard] sendDingTalkToGroup succeeded", { conversationId, result: JSON.stringify(result).slice(0, 200) });
    return { ok: true };
  } catch (err) {
    const errorDetail = err instanceof Error ? err.message : String(err);
    console.error("[taskguard] sendDingTalkToGroup threw", { conversationId, error: errorDetail });
    return { ok: false, error: errorDetail };
  }
}

function optionalStringParam(params: unknown, key: string): string | undefined {
  const value = (params as Record<string, unknown> | undefined)?.[key];
  return typeof value === "string" && value.trim() ? value.trim() : undefined;
}

function buildCommandEchoText(raw: string, commandName?: string): string {
  const name = commandName?.trim().replace(/^\/+/, "");
  const args = raw.trim();
  const command = name ? `/${name}${args ? ` ${args}` : ""}` : args;
  return command.length > 500 ? `${command.slice(0, 500)}...` : command;
}

async function injectCommandEchoMessage(
  api: PluginApi,
  sessionKey: string,
  raw: string,
  commandName?: string,
  skillName?: string,
  workflowId?: string,
): Promise<void> {
  const command = buildCommandEchoText(raw, commandName);
  if (!command) {
    console.info("[taskguard] command-echo skipped (empty command)", { sessionKey, commandName, skillName });
    recordInjectTrace("command_echo_skipped", {
      session_key: sessionKey,
      command_name: commandName,
      skill_name: skillName,
    });
    return;
  }
  const label = formatChatInjectLabel(workflowId);
  console.info("[taskguard] command-echo before inject", {
    sessionKey,
    command,
    label,
  });
  recordInjectTrace("command_echo_before_inject", {
    session_key: sessionKey,
    command,
    label,
  });
  await injectChatMessage(api, sessionKey, `收到命令：${command}\n<!-- triggerChatSubscribe:true -->`, `command-echo:${sessionKey}:${command}`, label);
  console.info("[taskguard] command-echo after inject", { sessionKey });
}

// ── Real Executor Dispatch ──

/**
 * Map an EmbeddedAgentLoopEvent type to a progress step tool_name value.
 */
function mapEventToProgressType(event: string): string {
  switch (event) {
    case "skill_invoked": return "skill_invoked";
    case "tool_completed": return "tool_completed";
    case "assistant_started": return "assistant_started";
    case "assistant_text": return "assistant_text";
    default: return event;
  }
}

/** Monotonic counter for progress step_seq within a node execution. */
let _progressStepSeq = 0;
/** Track per-node progress step seq so it doesn't conflict across nodes. */
const _nodeProgressSeq = new Map<string, number>();

function recordEmbeddedAgentEvent(
  flowState: FlowState,
  node: WorkflowNode,
  flowId: string,
  sessionKey: string,
  loopEvent: EmbeddedAgentLoopEvent,
): void {
  if (!shouldRecordEmbeddedAgentLoopEvent(loopEvent)) return;

  flowState.flowEvents ??= [];
  runtimeFlowEventSeq += 1;
  const event: FlowEvent = {
    id: `runtime_evt_${runtimeFlowEventSeq}`,
    time: Date.now(),
    type: "embedded_agent_event",
    flowId,
    workflowId: flowState.workflowId,
    nodeId: node.id,
    data: {
      runId: loopEvent.runId,
      event: loopEvent.event,
      stream: loopEvent.stream ?? null,
      message: loopEvent.message,
      data: loopEvent.data ?? {},
    },
    error: null,
  };

  flowState.flowEvents.push(event);
  if (flowState.flowEvents.length > MAX_RUNTIME_FLOW_EVENTS) {
    flowState.flowEvents.splice(0, flowState.flowEvents.length - MAX_RUNTIME_FLOW_EVENTS);
  }
  void appendWorkflowJsonlLog(buildWorkflowLogRecord({ event, sessionKey, botId: _activeBotId, ownerId: _activeOwnerId }), { baseDir: _activeLogDir }).catch(() => { /* best-effort log */ });

  // ── Store progress message in node_step_traces ──
  // This allows clawweb to display the same human-readable messages
  // that users see in their chat (e.g., "调用 xxx 技能", "工具调用完成：...")
  const stepTraceRepo = getNodeStepTraceRepository();
  if (stepTraceRepo) {
    const message = formatEmbeddedAgentLoopProgress(node.title, loopEvent);
    if (message) {
      // Per-node progress counter to avoid seq conflicts across nodes.
      // Progress steps get negative seq (e.g. -1, -2, -3) so they sort
      // before the positive-seq batch steps from extractNodeStepTrace
      // when the frontend orders by stepSeq ASC.
      const nodeKey = `${flowId}:${node.id}`;
      const prevCount = _nodeProgressSeq.get(nodeKey) ?? 0;
      const progressSeq = -(prevCount + 1);
      _nodeProgressSeq.set(nodeKey, prevCount + 1);

      const embeddedSessionKey = sessionKey
        ? `${sessionKey}:embedded:${node.id}:${flowId}`
        : null;
      void stepTraceRepo.insert({
        flowId,
        nodeId: node.id,
        attempt: flowState.nodeStates[node.id]?.attempts ?? 1,
        stepSeq: progressSeq,
        stepType: "progress",
        textContent: message,
        toolName: mapEventToProgressType(loopEvent.event),
        skillName: node.executor.type === "embedded-agent"
          ? (node.executor as { skillName?: string }).skillName?.trim() ?? null
          : null,
        sessionKey: embeddedSessionKey,
      }).catch((e) => {
        const msg = e instanceof Error ? e.message : String(e);
        console.warn(`[controller] progress step insert failed: flowId=${flowId} nodeId=${node.id} error=${msg}`);
      });
    }
  }
}

function recordEmbeddedAgentFinalOutput(
  flowState: FlowState,
  node: WorkflowNode,
  flowId: string,
  sessionKey: string,
  output: string,
): void {
  runtimeFlowEventSeq += 1;
  const event: FlowEvent = {
    id: `runtime_evt_${runtimeFlowEventSeq}`,
    time: Date.now(),
    type: "embedded_agent_final_output",
    flowId,
    workflowId: flowState.workflowId,
    nodeId: node.id,
    data: { output },
    error: null,
  };
  flowState.flowEvents ??= [];
  flowState.flowEvents.push(event);
  if (flowState.flowEvents.length > MAX_RUNTIME_FLOW_EVENTS) {
    flowState.flowEvents.splice(0, flowState.flowEvents.length - MAX_RUNTIME_FLOW_EVENTS);
  }
  void appendWorkflowJsonlLog(buildWorkflowLogRecord({ event, sessionKey, botId: _activeBotId, ownerId: _activeOwnerId }), { baseDir: _activeLogDir }).catch(() => { /* best-effort log */ });
}

async function injectEmbeddedAgentLoopMessage(
  api: PluginApi,
  sessionKey: string,
  workflowId: string,
  node: WorkflowNode,
  flowId: string,
  loopEvent: EmbeddedAgentLoopEvent,
): Promise<void> {
  const message = formatEmbeddedAgentLoopProgress(node.title, loopEvent);
  if (!message) {
    console.info("[taskguard] embedded-loop skipped (empty message)", {
      flowId, nodeId: node.id, eventType: loopEvent.event ?? "unknown",
    });
    recordInjectTrace("embedded_loop_skipped", {
      flow_id: flowId,
      node_id: node.id,
      event_type: loopEvent.event ?? "unknown",
    }, flowId);
    return;
  }
  console.info("[taskguard] embedded-loop before inject", {
    flowId, nodeId: node.id, eventType: loopEvent.event ?? "unknown",
    messagePreview: message.slice(0, 120),
  });
  recordInjectTrace("embedded_loop_before_inject", {
    flow_id: flowId,
    node_id: node.id,
    event_type: loopEvent.event ?? "unknown",
    message_preview: message.slice(0, 120),
  }, flowId);
  runtimeEmbeddedChatSeq += 1;
  await injectChatMessage(
    api,
    sessionKey,
    message,
    `embedded-agent-loop:${flowId}:${node.id}:${runtimeEmbeddedChatSeq}`,
    formatChatInjectLabel(workflowId),
  );
}

async function injectEmbeddedAgentFinalOutput(
  api: PluginApi,
  sessionKey: string,
  workflowId: string,
  node: WorkflowNode,
  flowId: string,
  output: string,
): Promise<void> {
  const message = formatEmbeddedAgentFinalOutput(node.title, output);
  if (!message) {
    console.info("[taskguard] embedded-final skipped (empty message)", {
      flowId, nodeId: node.id, outputPreview: output.slice(0, 120),
    });
    recordInjectTrace("embedded_final_skipped", {
      flow_id: flowId,
      node_id: node.id,
      output_preview: output.slice(0, 120),
    }, flowId);
    return;
  }
  console.info("[taskguard] embedded-final before inject", {
    flowId, nodeId: node.id, messagePreview: message.slice(0, 120),
  });
  recordInjectTrace("embedded_final_before_inject", {
    flow_id: flowId,
    node_id: node.id,
    message_preview: message.slice(0, 120),
  }, flowId);
  runtimeEmbeddedChatSeq += 1;
  await injectChatMessage(
    api,
    sessionKey,
    message,
    `embedded-agent-final:${flowId}:${node.id}:${runtimeEmbeddedChatSeq}`,
    formatChatInjectLabel(workflowId),
  );
}

export function createExecutorDispatch(
  api: PluginApi,
  sessionKey: string,
  actionRegistry: ActionRegistry,
  user?: WorkflowRuntimeUser,
  toolCtx?: EmbeddedAgentToolContext,
  abortSignal?: AbortSignal,
  workflow?: WorkflowSpec,
  subworkflowDepsGetter?: () => SubworkflowDeps | undefined,
  packRoot?: string,
): (node: WorkflowNode, templateCtx: TemplateContext, flowState: FlowState, flowId: string) => Promise<ExecutorResult> {
  const compressionDefaults = loadConfig().app.contextCompression;
  const sessionCompressionConfig = loadConfig().app.sessionCompression;
  const llmConfig = loadConfig().app.llm;
  configureLlmSemaphore(llmConfig);

  function embeddedAgentOptions(
    node: WorkflowNode,
    flowState: FlowState,
    flowId: string,
  ): ExecuteEmbeddedAgentOptions {
    // ── Per-node inject budget (方案 B) ──
    // Each node gets a fixed budget of agent-event inject messages. Once
    // exhausted, a single "folded" notice is sent and subsequent non-critical
    // events are silently skipped (data path still records them). This
    // prevents message storms from agent trial-and-error loops.
    const AGENT_EVENT_INJECT_BUDGET = 15;
    let agentEventInjectCount = 0;
    let agentEventFoldedNotified = false;
    let agentEventSeq = 0;

    return {
      sessionKey,
      toolCtx,
      runId: `clawmind:${flowState.workflowId}:${flowId}:${node.id}:${Date.now()}`,
      flowId,
      attempt: flowState.nodeStates[node.id]?.attempts ?? 1,
      workflow,
      executionMode: flowState.executionMode,
      abortSignal,
      compressionDefaults,
      sessionCompressionConfig,
      botId: _activeBotId ?? undefined,
      progress: (message, idempotencyKey) => {
        // Detached: display-only progress must not block the agent event loop.
        // Serialized per flowId so a flow's injects preserve transcript order
        // (at most one chat.inject subprocess per flow at a time; see inject-queue.ts).
        // Sharing the flowId lane with the controller's verbose/wait injects is
        // what keeps an upstream node's finalOutput ahead of a downstream review card.
        //
        // Performance mode: progress streams are mid-execution display noise —
        // skip the inject entirely (no subprocess spawned). Nothing downstream
        // reads the inject result, so suppressing is correctness-safe.
        //
        // Coalesce: droppable progress messages within a 2s window are merged
        // into a single inject subprocess to reduce subprocess count under burst
        // load (same pattern as controller's verboseChatInject).
        if (shouldInjectForFlow(flowId, "agent-progress")) {
          const label = formatChatInjectLabel(flowState.workflowId);
          const sender = (msg: string, key: string) =>
            injectChatMessage(api, sessionKey, msg, key, label);
          enqueueInject(
            flowId,
            () => sender(message, idempotencyKey),
            {
              droppable: true,
              coalesceMessage: message,
              coalesceSender: sender,
              coalesceIdempotencyKey: idempotencyKey,
            },
          );
        }
        return Promise.resolve();
      },
      agentEvent: async (event) => {
        // ── Ghost event guard ──
        // The underlying OpenClaw agent process may continue running briefly
        // after the controller has marked the node as failed/succeeded (e.g. on
        // network timeout). Suppress these stale events to avoid confusing the
        // UI into showing "node still running" after failure.
        const nodeStatus = flowState.nodeStates[node.id]?.status;
        if (nodeStatus === "failed" || nodeStatus === "succeeded" || nodeStatus === "blocked") {
          console.warn(`[controller] GHOST_EVENT_SUPPRESSED flowId=${flowId} node=${node.id} event=${event.event ?? "unknown"} nodeStatus=${nodeStatus} — node already finalized, ignoring stale agent event`);
          return;
        }

        // ── Data path: always record (flowState + step traces) ──
        recordEmbeddedAgentEvent(flowState, node, flowId, sessionKey, event);

        // ── Display path: coalesce + per-node budget ──
        if (!shouldInjectForFlow(flowId, "agent-agentEvent")) return;

        // Critical events bypass the budget (error / approval)
        const isCritical = event.event === "error"
          || event.stream === "error"
          || event.stream === "approval"
          || (event.stream === "lifecycle" && event.data?.phase === "error");

        if (!isCritical) {
          if (agentEventInjectCount >= AGENT_EVENT_INJECT_BUDGET) {
            // Budget exhausted — send a one-time folded notice
            if (!agentEventFoldedNotified) {
              agentEventFoldedNotified = true;
              const foldedMsg = `⏳ ${node.title} 正在执行中，请耐心等待。已发送 ${agentEventInjectCount} 条进度消息，后续消息已折叠，完整执行记录可在 ClawWeb 上查看`;
              const label = formatChatInjectLabel(flowState.workflowId);
              const foldedKey = `${flowId}:${node.id}:agent-event-folded`;
              const foldedSender = (msg: string, key: string) =>
                injectChatMessage(api, sessionKey, msg, key, label);
              enqueueInject(
                flowId,
                () => foldedSender(foldedMsg, foldedKey),
                { droppable: false },
              );
            }
            return;
          }
          agentEventInjectCount += 1;
        }

        // ── Coalesce: format early, use injectChatMessage as sender ──
        const message = formatEmbeddedAgentLoopProgress(node.title, event);
        if (!message) return;

        agentEventSeq += 1;
        const idempotencyKey = `embedded-agent-loop:${flowId}:${node.id}:${agentEventSeq}`;
        const label = formatChatInjectLabel(flowState.workflowId);
        const sender = (msg: string, key: string) =>
          injectChatMessage(api, sessionKey, msg, key, label);

        enqueueInject(
          flowId,
          () => sender(message, idempotencyKey),
          {
            droppable: true,
            coalesceMessage: message,
            coalesceSender: sender,
            coalesceIdempotencyKey: idempotencyKey,
          },
        );
      },
      finalOutput: async (output) => {
        const nodeStatus = flowState.nodeStates[node.id]?.status;
        if (nodeStatus === "failed" || nodeStatus === "succeeded" || nodeStatus === "blocked") {
          console.warn(`[controller] GHOST_OUTPUT_SUPPRESSED flowId=${flowId} node=${node.id} nodeStatus=${nodeStatus} — node already finalized, ignoring stale agent output`);
          return;
        }
        // Detached but NOT droppable: the final-output display should survive
        // back-pressure. recordEmbeddedAgentFinalOutput stays synchronous (data path).
        //
        // Performance mode: skip the per-node final-output display inject (mid-
        // execution noise); the flow-completion bookend still surfaces results.
        // KEEP recordEmbeddedAgentFinalOutput (data path) — node state must persist.
        if (shouldInjectForFlow(flowId, "agent-finalOutput")) {
          enqueueInject(
            flowId,
            () => injectEmbeddedAgentFinalOutput(api, sessionKey, flowState.workflowId, node, flowId, output),
            { droppable: false },
          );
        }
        recordEmbeddedAgentFinalOutput(flowState, node, flowId, sessionKey, output);
      },
    };
  }

  async function executeSubagentWithEmbeddedFallback(
    node: WorkflowNode,
    templateCtx: TemplateContext,
    flowState: FlowState,
    flowId: string,
  ): Promise<ExecutorResult> {
    const subagentResult = await executeSubagent(node, templateCtx, api, {
      flowId,
      workflow,
      executionMode: flowState.executionMode,
      compressionDefaults,
    });

    if (flowState.executionMode !== "private" || !shouldFallbackSubagentToEmbedded(subagentResult)) {
      return subagentResult;
    }

    await injectChatMessage(
      api,
      sessionKey,
      `${node.title} subagent runtime 不可用，已降级为 embedded-agent 执行`,
      `${flowId}:${node.id}:embedded-fallback`,
      formatChatInjectLabel(flowState.workflowId),
    );

    return runEmbeddedFallbackAfterSubagentFailure({
      node,
      subagentResult,
      runEmbedded: async (fallbackNode) => executeEmbeddedAgent(
        fallbackNode,
        templateCtx,
        api as Parameters<typeof executeEmbeddedAgent>[2],
        {
          ...embeddedAgentOptions(fallbackNode, flowState, flowId),
          runId: `clawmind:${flowState.workflowId}:${flowId}:${node.id}:fallback:${Date.now()}`,
        },
      ),
    });
  }

  async function executeApprovalEmbeddedAgent(
    node: WorkflowNode,
    templateCtx: TemplateContext,
    flowState: FlowState,
    flowId: string,
  ): Promise<ExecutorResult> {
    const embeddedNode = toEmbeddedFallbackNode(node);
    const result = await executeEmbeddedAgent(
      embeddedNode,
      templateCtx,
      api as Parameters<typeof executeEmbeddedAgent>[2],
      embeddedAgentOptions(embeddedNode, flowState, flowId),
    );
    return validateApprovalFallbackResult(node, result);
  }

  function resolveApprovalDelivery(node: WorkflowNode, flowState: FlowState): ApprovalDeliveryMode {
    const executor = getLegacyApprovalExecutor(node);
    if (!executor) {
      return { primary: "subagent" };
    }
    if (flowState.executionMode === "private") {
      // Single/direct chat: send approval card to each approver via DingTalk API (sendToUser).
      // No active session needed — direct messaging.
      return executor.delivery?.private ?? { primary: "card-dingtalk" };
    }
    if (flowState.executionMode === "dingtalk-group") {
      // DingTalk group chat: send approval card directly in the group via chatInject.
      // Reads from YAML delivery.dingtalkGroup, defaults to card-dingtalk.
      return executor.delivery?.dingtalkGroup ?? { primary: "card-dingtalk" };
    }
    // BCS group chat: use YAML delivery.collaboration config, default to bcs-route.
    return executor.delivery?.collaboration ?? { primary: "bcs-route" };
  }

  function resolveCollaborationDelivery(node: WorkflowNode, flowState: FlowState): CollaborationDeliveryMode {
    if (node.executor.type !== "collaboration") {
      return { primary: "subagent" };
    }
    if (flowState.executionMode === "private") {
      return node.executor.delivery?.private ?? { primary: "subagent" };
    }
    if (flowState.executionMode === "dingtalk-group") {
      return node.executor.delivery?.dingtalkGroup ?? { primary: "subagent" };
    }
    // BCS group chat
    return node.executor.delivery?.collaboration ?? { primary: "bcs-route" };
  }

  async function executeCollaborationEmbeddedAgent(
    node: WorkflowNode,
    templateCtx: TemplateContext,
    flowState: FlowState,
    flowId: string,
  ): Promise<ExecutorResult> {
    const embeddedNode = toEmbeddedFallbackNode(node);
    return executeEmbeddedAgent(
      embeddedNode,
      templateCtx,
      api as Parameters<typeof executeEmbeddedAgent>[2],
      embeddedAgentOptions(embeddedNode, flowState, flowId),
    );
  }

  async function executeApprovalDelivery(
    node: WorkflowNode,
    templateCtx: TemplateContext,
    flowState: FlowState,
    flowId: string,
  ): Promise<ExecutorResult> {
    const delivery = resolveApprovalDelivery(node, flowState);
    console.log("[taskguard] executeApprovalDelivery", {
      flowId, nodeId: node.id, deliveryPrimary: delivery.primary, executionMode: flowState.executionMode, sessionKey,
    });
    switch (delivery.primary) {
      case "subagent":
        return executeSubagentWithEmbeddedFallback(node, templateCtx, flowState, flowId);
      case "embedded-agent":
        return executeApprovalEmbeddedAgent(node, templateCtx, flowState, flowId);
      case "bcs-route":
        return executeBcsRoute(node, templateCtx, api, flowState, { workflow });
      case "bcs-cli":
        return executeApprovalDeliveryAction(node, templateCtx, flowState, flowId, delivery, actionRegistry, sessionKey, user, workflow);
      case "card-dingtalk": {
        console.log("[taskguard] approval card-dingtalk", {
          flowId, nodeId: node.id, executionMode: flowState.executionMode, sessionKey, bcsGroupId: flowState.bcsGroupId,
        });
        const cardApi = {
          runtime: {
            agent: {
              runEmbeddedPiAgent: (api as any).runtime?.agent?.runEmbeddedPiAgent ?? (() => Promise.resolve({})),
            },
          },
          // chatInject is deprecated — only writes to session transcript, does NOT deliver to DingTalk
          chatInject: (message: string, idempotencyKey: string) =>
            injectChatMessage(api, sessionKey, message, idempotencyKey, formatChatInjectLabel(flowState.workflowId), { throwOnError: true }),
          // sendToUser: direct message to a DingTalk user via oToMessages/batchSend API
          sendToUser: async (userId: string, content: string): Promise<{ ok: boolean; error?: string }> => {
            return sendDingTalkToUser(api, userId, content);
          },
          // sendToGroup: send message to a DingTalk group conversation via groupMessages/send API
          sendToGroup: async (conversationId: string, content: string): Promise<{ ok: boolean; error?: string }> => {
            return sendDingTalkToGroup(conversationId, content);
          },
          // getDingTalkToken: obtain a valid DingTalk access token for Card API calls
          getDingTalkToken: async (): Promise<string> => {
            const creds = readDingTalkCredentials();
            if (!creds) throw new Error("DingTalk credentials not configured in ~/.openclaw/openclaw.json");
            return getDingTalkAccessToken(creds.clientId, creds.clientSecret);
          },
          // getRobotCode: read the DingTalk robot code from credentials
          getRobotCode: (): string => {
            const creds = readDingTalkCredentials();
            if (!creds) throw new Error("DingTalk credentials not configured in ~/.openclaw/openclaw.json");
            return creds.robotCode;
          },
          // Flow context for approval card registry
          flowId,
          nodeId: node.id,
          workflowId: workflow.id,
        } as Parameters<typeof executeApprovalCardDingtalk>[2];
        const result = await executeApprovalCardDingtalk(node, templateCtx, cardApi, flowState, flowId, { workflow });
        console.log("[taskguard] approval card-dingtalk result", {
          flowId, nodeId: node.id, status: result.status, error: result.error ?? undefined,
        });
        return result;
      }
      case "card-secoc": {
        // card-secoc: 1) Inject AixUI secoc card markup into the session transcript
        // via chatInject (for BCS/secoc connector rendering), 2) Send a DingTalk
        // notification message so the user can see the approval request in the
        // DingTalk chat. AixUI rendering depends on the channel connector.
        console.log("[taskguard] approval card-secoc", {
          flowId, nodeId: node.id, executionMode: flowState.executionMode, sessionKey,
        });
        const secocExecutor = getLegacyApprovalExecutor(node);
        if (!secocExecutor) {
          return { status: "failed", error: `node ${node.id} is not an approval node` };
        }
        const secocApprovalConfig = buildApprovalCardData({
          node, executor: secocExecutor, templateCtx, flowState, workflow, flowId,
        });
        const secocApproverNames = (secocExecutor.approvers ?? []).map((a: WorkflowApprover) => a.name).join("、");

        // Step 1: Inject AixUI card markup into session transcript
        const secocCardId = secocExecutor.cardId ?? "card_0440e96c";
        const secocCardMessage = renderAixUICard(secocCardId, secocApprovalConfig);
        try {
          await injectChatMessage(
            api,
            sessionKey,
            secocCardMessage,
            `approval-card:${flowId}:${node.id}`,
            formatChatInjectLabel(flowState.workflowId),
          );
        } catch (err) {
          const errMsg = err instanceof Error ? err.message : String(err);
          console.error("[taskguard] approval card-secoc chatInject failed", {
            flowId, nodeId: node.id, error: errMsg,
          });
          // Don't fail — DingTalk notification is the primary delivery; chatInject is supplementary
        }
        console.log("[taskguard] approval card-secoc AixUI injected to session", {
          flowId, nodeId: node.id, sessionKey,
        });

        // Step 2: Send DingTalk notification message so the user sees it in the chat
        const secocTitle = String(secocApprovalConfig.title ?? "审批通知");
        const secocApplicant = String(secocApprovalConfig.applicant ?? "系统");
        const secocWorkflowTitle = String(secocApprovalConfig.workflowTitle ?? "");
        const secocFields = Array.isArray(secocApprovalConfig.fields)
          ? (secocApprovalConfig.fields as Array<{ label: string; value: string }>)
          : [];
        const secocDetailUrl = String(secocApprovalConfig.workflowDetailUrl ?? "");
        const secocMarkdownLines = [
          `## ${secocTitle}`,
          "",
          `**状态**: ⏳ 待审批`,
          `**申请人**: ${secocApplicant}`,
        ];
        if (secocWorkflowTitle) {
          secocMarkdownLines.push(`**流程**: ${secocWorkflowTitle}`);
        }
        if (secocFields.length > 0) {
          secocMarkdownLines.push("", "---", "");
          for (const f of secocFields) {
            secocMarkdownLines.push(`- **${f.label}**: ${f.value}`);
          }
        }
        if (secocApproverNames) {
          secocMarkdownLines.push("", "---", "");
          secocMarkdownLines.push(`**审批人**: ${secocApproverNames}`);
        }
        if (secocDetailUrl) {
          secocMarkdownLines.push("", "---", "");
          secocMarkdownLines.push(`👉 [点击查看详情/审批](${secocDetailUrl})`);
        }
        const secocNotification = secocMarkdownLines.join("\n");

        const secocIsGroup = flowState.executionMode === "dingtalk-group";
        const secocIsPrivate = flowState.executionMode === "private";
        if (secocIsGroup || secocIsPrivate) {
          const secocConversationId = flowState.bcsGroupId ?? "";
          if (secocIsGroup && secocConversationId) {
            const sendResult = await sendDingTalkToGroup(secocConversationId, secocNotification);
            if (!sendResult.ok) {
              console.error("[taskguard] approval card-secoc sendToGroup failed", {
                flowId, nodeId: node.id, error: sendResult.error,
              });
            } else {
              console.log("[taskguard] approval card-secoc notification sent to group", {
                flowId, nodeId: node.id, conversationId: secocConversationId,
              });
            }
          } else if (secocIsPrivate) {
            // Send to each approver individually
            const secocApprovers = secocExecutor.approvers ?? [];
            for (const approver of secocApprovers) {
              if (approver.empId) {
                const sendResult = await sendDingTalkToUser(api, approver.empId, secocNotification);
                if (!sendResult.ok) {
                  console.error("[taskguard] approval card-secoc sendToUser failed", {
                    flowId, nodeId: node.id, userId: approver.empId, error: sendResult.error,
                  });
                } else {
                  console.log("[taskguard] approval card-secoc notification sent to user", {
                    flowId, nodeId: node.id, userId: approver.empId,
                  });
                }
              }
            }
          }
        } else {
          console.log("[taskguard] approval card-secoc skipping DingTalk notification (not dingtalk-group or private mode)", {
            flowId, nodeId: node.id, executionMode: flowState.executionMode,
          });
        }

        return {
          status: "waiting",
          waitConfig: {
            prompt: secocApproverNames ? `等待 ${secocApproverNames} 审批` : "等待审批",
            hint: "审批卡片已发送（secoc通道）",
            waitKind: "bcs-approval",
          },
        };
      }
      case "card-web": {
        console.log("[taskguard] approval card-web", {
          flowId, nodeId: node.id, executionMode: flowState.executionMode, sessionKey,
        });
        const webApi: ApprovalCardWebApi = {
          sendToUser: async (userId: string, content: string): Promise<{ ok: boolean; error?: string }> => {
            return sendDingTalkToUser(api, userId, content);
          },
          sendToGroup: async (conversationId: string, content: string): Promise<{ ok: boolean; error?: string }> => {
            return sendDingTalkToGroup(conversationId, content);
          },
          flowId,
          nodeId: node.id,
          workflowId: workflow.id,
          clawwebUrl: loadConfig().app.api.clawwebUrl,
          corpId: loadConfig().app.api.corpId,
          insertApprovalCard: async (card) => {
            // If clawweb API is configured, write via internal API; otherwise write DB directly
            const cfg = loadConfig().app;
            if (cfg.api.baseUrl) {
              const apiClient = createApiClient(cfg.api);
              const res = await apiClient.post<{ id: number }>("/approval-cards", {
                flow_id: card.flowId,
                node_id: card.nodeId,
                workflow_id: card.workflowId,
                workflow_title: card.workflowTitle ?? null,
                approval_type: card.approvalType ?? null,
                message: card.message ?? null,
                card_fields_json: card.cardFields ? JSON.stringify(card.cardFields) : null,
                approver_ids: card.approverIds.join(","),
                approver_names: card.approverNames?.join(",") ?? null,
                approval_policy: card.approvalPolicy ?? "any",
                delivery_mode: card.deliveryMode ?? "card-web",
              });
              if (!res.ok) {
                throw new Error(`API insertApprovalCard failed: ${res.error ?? `HTTP ${res.status}`}`);
              }
              return res.data?.id ?? 0;
            }
            const db = getDatabase();
            if (!db) throw new Error("Database not available for approval card insertion");
            const result = await db.exec(
              `INSERT INTO approval_cards
                (flow_id, node_id, workflow_id, workflow_title, approval_type, message,
                 card_fields_json, approver_ids, approver_names, approval_policy,
                 approved_by, rejected_by, status, delivery_mode, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', '', 'pending', ?, unixepoch())`,
              [
                card.flowId,
                card.nodeId,
                card.workflowId,
                card.workflowTitle ?? null,
                card.approvalType ?? null,
                card.message ?? null,
                card.cardFields ? JSON.stringify(card.cardFields) : null,
                card.approverIds.join(","),
                card.approverNames?.join(",") ?? null,
                card.approvalPolicy ?? "any",
                card.deliveryMode ?? "card-web",
              ],
            );
            return result.insertId ?? 0;
          },
        };
        const webResult = await executeApprovalCardWeb(node, templateCtx, webApi, flowState, flowId, { workflow });
        console.log("[taskguard] approval card-web result", {
          flowId, nodeId: node.id, status: webResult.status, error: webResult.error ?? undefined,
        });
        return webResult;
      }
      default:
        return { status: "failed", error: `Unknown approval delivery primary: ${(delivery as { primary: string }).primary}` };
    }
  }

  async function executeCollaborationDelivery(
    node: WorkflowNode,
    templateCtx: TemplateContext,
    flowState: FlowState,
    flowId: string,
  ): Promise<ExecutorResult> {
    const delivery = resolveCollaborationDelivery(node, flowState);
    switch (delivery.primary) {
      case "subagent":
        return executeSubagentWithEmbeddedFallback(node, templateCtx, flowState, flowId);
      case "embedded-agent":
        return executeCollaborationEmbeddedAgent(node, templateCtx, flowState, flowId);
      case "bcs-route":
        return executeBcsRoute(node, templateCtx, api, flowState, { workflow });
      case "bcs-cli":
        return executeCollaborationDeliveryAction(node, templateCtx, flowState, flowId, delivery, actionRegistry, sessionKey, user, workflow);
      default:
        return { status: "failed", error: `Unknown collaboration delivery primary: ${(delivery as { primary: string }).primary}` };
    }
  }

  return async (node, templateCtx, flowState, flowId) => {
    // ── skipWhen: 通用闸,所有节点类型,在 type-specific 分发之前 ──
    const skipWhen = readNodeSkipWhen(node);
    if (skipWhen && evaluateSkipWhenConditions(skipWhen, templateCtx)) {
      const isApproval = !!getLegacyApprovalExecutor(node);
      const result = buildSkipResult(node, isApproval);
      console.log("[taskguard] skipWhen matched, auto-succeeding", {
        flowId, nodeId: node.id, isApproval,
      });
      return { status: "succeeded", result };
    }

    if (getLegacyApprovalExecutor(node)) {
      return executeApprovalDelivery(node, templateCtx, flowState, flowId);
    }

    switch (node.executor.type) {
      case "embedded-agent":
        return executeEmbeddedAgent(node, templateCtx, api as Parameters<typeof executeEmbeddedAgent>[2], {
          ...embeddedAgentOptions(node, flowState, flowId),
        });
      case "cli-script":
        return executeCliScript(node, templateCtx, undefined, packRoot);
      case "mcp-call":
        return executeMcpCall(node, templateCtx);
      case "action":
        return executeActionNode(node, flowState, flowId, actionRegistry, sessionKey, user, workflow);
      case "human":
        return executeHumanWait(node, templateCtx);
      case "async-callback": {
        const _asyncCallbackCfg = loadConfig().app.asyncCallback;
        const asyncCallbackDeps: AsyncCallbackExecutorDeps = {
          database: getDatabase(),
          flowId,
          nodeId: node.id,
          workflowId: flowState.workflowId,
          callbackBaseUrl: _asyncCallbackCfg?.callbackBaseUrl ?? "",
          defaultTimeout: _asyncCallbackCfg?.defaultTimeout ?? "1h",
        };
        return executeAsyncCallback(node, templateCtx, asyncCallbackDeps);
      }
      case "done":
        return { status: "succeeded", result: { done: true } };
      case "loop-group":
        return { status: "failed", error: "loop-group must be materialized by controller before execution" };
      case "dynamic-template":
        return { status: "failed", error: "dynamic-template must be materialized by controller before execution" };
      case "goal-evaluator":
        return { status: "failed", error: "goal-evaluator must be handled by controller on-result pipeline" };
      case "llm-orchestrator": {
        const { executeLlmOrchestrator } = await import("./executors/llm-orchestrator.js");
        const priorIterations = flowState.orchestrationState?.[node.id]?.iterations ?? [];
        return executeLlmOrchestrator(node, templateCtx, priorIterations, flowState.orchestrationState?.[node.id]);
      }
      case "subagent":
        return executeSubagentWithEmbeddedFallback(node, templateCtx, flowState, flowId);
      case "bcs-route":
        return executeBcsRoute(node, templateCtx, api, flowState, { workflow });
      case "baas-call":
        return executeBaasCall(node, templateCtx, api, flowState, (msg) => {
          reportNodeProgress(flowId, flowState.workflowId, node.id, "baas-call", flowState.nodeStates[node.id]?.attempts ?? 1, msg);
        });
      case "collaboration":
        return executeCollaborationDelivery(node, templateCtx, flowState, flowId);
      case "subworkflow":
        const swDeps = subworkflowDepsGetter?.();
        if (!swDeps) {
          return { status: "failed", error: `subworkflow node ${node.id}: subworkflow execution is not available in this context` };
        }
        return executeSubworkflow(node, templateCtx, flowState, flowId, swDeps);
      default:
        return { status: "failed", error: `Unknown executor type: ${(node.executor as { type: string }).type}` };
    }
  };
}

async function executeActionNode(
  node: WorkflowNode,
  flowState: FlowState,
  flowId: string,
  actionRegistry: ActionRegistry,
  sessionKey: string,
  user?: WorkflowRuntimeUser,
  workflow?: WorkflowSpec,
): Promise<ExecutorResult> {
  if (node.executor.type !== "action") {
    return { status: "failed", error: "not an action node" };
  }

  const templateExtras = buildActionTemplateExtras(flowState, node.id);
  const context: ActionExecutionContext = {
    flowId,
    workflowId: flowState.workflowId,
    actionId: node.executor.action,
    nodeId: node.id,
    sessionKey,
    executionMode: flowState.executionMode,
    bcsGroupId: flowState.bcsGroupId,
    params: flowState.params,
    input: flowState.input,
    workflowData: flowState.workflowData,
    nodeOutput: templateExtras.nodeOutput,
    actionOutputs: flowState.actionOutputs,
    loop: templateExtras.loop,
    templateAliases: templateExtras.templateAliases,
    user: user ?? {},
    workflow,
  };
  const args = resolveActionArgs(node.executor.args ?? {}, context);
  const result = await actionRegistry.execute(node.executor.action, args, context);
  return { status: "succeeded", result };
}

function templateCtxNodeOutput(flowState: FlowState, currentNodeId?: string): Record<string, Record<string, unknown>> {
  return buildActionTemplateExtras(flowState, currentNodeId).nodeOutput;
}

function isExecutorResult(value: Record<string, unknown>): value is ExecutorResult {
  return value.status === "succeeded" || value.status === "waiting" || value.status === "failed";
}

async function executeApprovalDeliveryAction(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  flowState: FlowState,
  flowId: string,
  delivery: ApprovalDeliveryMode,
  actionRegistry: ActionRegistry,
  sessionKey: string,
  user?: WorkflowRuntimeUser,
  workflow?: WorkflowSpec,
): Promise<ExecutorResult> {
  if (!getLegacyApprovalExecutor(node)) {
    return { status: "failed", error: "not an approval node" };
  }
  if (!delivery.action) {
    return { status: "failed", error: `approval delivery bcs-cli for node ${node.id} requires action` };
  }

  const templateExtras = buildActionTemplateExtras(flowState, node.id);
  const context: ActionExecutionContext = {
    flowId,
    workflowId: flowState.workflowId,
    actionId: delivery.action,
    nodeId: node.id,
    sessionKey,
    executionMode: flowState.executionMode,
    bcsGroupId: flowState.bcsGroupId,
    params: flowState.params,
    input: flowState.input,
    workflowData: flowState.workflowData,
    nodeOutput: templateExtras.nodeOutput,
    actionOutputs: flowState.actionOutputs,
    loop: templateExtras.loop,
    templateAliases: templateExtras.templateAliases,
    user: user ?? {},
    workflow,
  };
  const actionInput = {
    node,
    templateCtx,
    flowState,
  };
  const result = await actionRegistry.execute(delivery.action, actionInput, context);

  if (isExecutorResult(result)) {
    return result;
  }
  return { status: "succeeded", result };
}

async function executeCollaborationDeliveryAction(
  node: WorkflowNode,
  templateCtx: TemplateContext,
  flowState: FlowState,
  flowId: string,
  delivery: CollaborationDeliveryMode,
  actionRegistry: ActionRegistry,
  sessionKey: string,
  user?: WorkflowRuntimeUser,
  workflow?: WorkflowSpec,
): Promise<ExecutorResult> {
  if (node.executor.type !== "collaboration") {
    return { status: "failed", error: "not a collaboration node" };
  }
  if (!delivery.action) {
    return { status: "failed", error: `collaboration delivery bcs-cli for node ${node.id} requires action` };
  }

  const templateExtras = buildActionTemplateExtras(flowState, node.id);
  const context: ActionExecutionContext = {
    flowId,
    workflowId: flowState.workflowId,
    actionId: delivery.action,
    nodeId: node.id,
    sessionKey,
    executionMode: flowState.executionMode,
    bcsGroupId: flowState.bcsGroupId,
    params: flowState.params,
    input: flowState.input,
    workflowData: flowState.workflowData,
    nodeOutput: templateExtras.nodeOutput,
    actionOutputs: flowState.actionOutputs,
    loop: templateExtras.loop,
    templateAliases: templateExtras.templateAliases,
    user: user ?? {},
    workflow,
  };
  const actionInput = {
    node,
    templateCtx,
    flowState,
  };
  const result = await actionRegistry.execute(delivery.action, actionInput, context);

  if (isExecutorResult(result)) {
    return result;
  }
  return { status: "succeeded", result };
}

// ── Build Controller Deps ──

/**
 * Derive the session file path from sessionId + agentId using OpenClaw conventions.
 * Falls back to OPENCLAW_STATE_DIR or ~/.openclaw as the state root.
 * Path: <stateDir>/agents/<agentId>/sessions/<sessionId>.jsonl
 */
function deriveSessionFilePath(sessionId: string, agentId?: string): string {
  const stateDir = process.env.OPENCLAW_STATE_DIR?.trim()
    || process.env.OPENCLAW_HOME?.trim()
    || join(homedir(), ".openclaw");
  const normalizedAgentId = (agentId?.trim() || "main").toLowerCase();
  return join(stateDir, "agents", normalizedAgentId, "sessions", `${sessionId}.jsonl`);
}

function buildDeps(
  api: PluginApi,
  sessionKey: string,
  sessionId: string | undefined,
  boundTaskFlow: ControllerDeps["boundTaskFlow"],
  skillRoot: string,
  deliveryContext?: Record<string, unknown>,
  workflow?: WorkflowSpec,
  resolvedWorkflows?: ResolvedWorkflow[],
  resolvedPacks?: ControllerDeps["resolvedPacks"],
  onProgress?: ControllerDeps["onProgress"],
  abortSignal?: AbortSignal,
  toolCtx?: EmbeddedAgentToolContext,
  chatInjectLabel = formatChatInjectLabel(workflow?.id),
  facadeRegistry: FacadeRegistry = buildFacadeRegistry([], []),
  packRoot?: string,
  packId?: string,
  failedWorkflows?: ControllerDeps["failedWorkflows"],
): ControllerDeps {
  const actionRegistry = createDefaultActionRegistry(resolvedPacks);
  // If deliveryContext has no user, fall back to requesterSenderId from toolCtx
  // (same logic as get_current_user tool) so that flow_runs.user_id is populated.
  const requesterSenderId = (toolCtx as Record<string, unknown> | undefined)?.requesterSenderId;
  const effectiveDeliveryContext =
    requesterSenderId && !deliveryContext?.user
      ? { ...(deliveryContext ?? {}), user: { id: requesterSenderId } }
      : deliveryContext;
  const user = resolveRuntimeUserContext({ deliveryContext: effectiveDeliveryContext, workflowDefaults: workflow?.defaults });
  const resolvedPackRoot = packRoot || (packId ? resolvePackRootFromId(packId) : undefined);
  if (!packRoot && packId) {
    console.info("[taskguard] packRoot not available from resolved pack, falling back to conventional path", {
      packId,
      fallbackPath: resolvedPackRoot,
    });
  }

  // ── Platform Adapter: construct from OpenClaw API ──
  const adapter = createOpenClawAdapter({
    api,
    sessionKey,
    sessionId,
    skillRoot,
    deliveryContext: effectiveDeliveryContext,
    onProgress,
    abortSignal,
    chatInjectFn: (message, idempotencyKey) => injectChatMessage(api, sessionKey, message, idempotencyKey, chatInjectLabel),
    resolveUser: resolveRuntimeUserContext,
    workflowDefaults: workflow?.defaults as Record<string, unknown> | undefined,
  });

  // ── Resolve and set engine name for flow_runs.engine tracking ──
  const _appCfg = loadConfig().app;
  setEngineName(resolveEngineName(adapter.platform, { configEngine: _appCfg.engine }));

  // ── Bridge: adapter + extras → ControllerDeps ──
  // Build the observability emitter: combines Channel notifications with
  // persistent execution step logging (best-effort, never throws).
  const db = getDatabase();
  const stepLogRepo = db && db.dbType !== "noop" ? new ExecutionStepLogRepository(db) : null;
  const stepLogger = stepLogRepo ? new ExecutionStepLogger(stepLogRepo) : undefined;

  // ── Run archive: run log uploader + archive builder ──
  // RunLogUploader is a singleton — create once, reuse across buildDeps calls.
  // Uses the same pattern as all other clawweb API calls: loadDatabaseConfig()
  // to determine mode, then createApiClient() for API mode or RunLogRepository
  // for direct DB mode.
  if (!_runLogUploader) {
    let runLogRepo: IRunLogRepository | null = null;
    const dbConfig = loadDatabaseConfig();
    const isApiMode = dbConfig.type === "api";
    console.log(
      `[taskguard] Run log uploader: buildDeps diag — ` +
      `dbConfig.type=${dbConfig.type} dbConfig.api=${dbConfig.api != null} ` +
      `db=${db != null} db.dbType=${(db as any)?.dbType ?? "null"}`,
    );

    if (isApiMode && dbConfig.api) {
      // API mode: use RunLogApiRepository (same pattern as all other API repos)
      if (_pendingRunLogRepo) {
        // register() callback already created the repo — pick it up
        runLogRepo = _pendingRunLogRepo;
        _pendingRunLogRepo = null;
        console.log("[taskguard] Run log uploader: picked up deferred API repository");
      } else {
        // Create API repo directly (register() callback hasn't run yet)
        const apiClient = createApiClient(dbConfig.api);
        runLogRepo = new RunLogApiRepository(apiClient);
        console.log("[taskguard] Run log uploader: API mode (direct init in buildDeps)");
      }
    } else if (db && db.dbType !== "noop") {
      // Direct DB mode: use RunLogRepository
      runLogRepo = new RunLogRepository(db);
    }

    if (runLogRepo) {
      const uploader = new RunLogUploader(runLogRepo, { maxEntriesPerFlow: 500 });
      _runLogUploader = uploader;
      setRunLogUploader(uploader);
      uploader.start();
      console.log("[taskguard] Run log uploader: created and started");
      // RunArchiveBuilder only works in direct DB mode (not API mode)
      if (!isApiMode && db && db.dbType !== "noop") {
        _runArchiveBuilder = new RunArchiveBuilder(db, runLogRepo);
      }
    } else {
      console.log("[taskguard] Run log uploader: deferred (database not ready yet)");
    }

    // ── Guardian Agent: node failure analysis at retry time ──
    try {
      const { app: appCfg } = loadConfig();
      const guardianCfg: GuardianConfig = {
        enabled: appCfg.guardian?.enabled !== false,
        analysisTimeoutSeconds: appCfg.guardian?.analysisTimeoutSeconds ?? 60,
        maxPromptMultiplier: appCfg.guardian?.maxPromptMultiplier ?? 2,
      };
      if (guardianCfg.enabled && api && sessionKey) {
        const guardianAgent = new GuardianAgent(api as never, {
          sessionKey,
          toolCtx: toolCtx as never,
          abortSignal,
          botId: _activeBotId ?? undefined,
        }, guardianCfg);
        setGuardianAgent(guardianAgent);
        console.log("[taskguard] Guardian agent: enabled");
      } else {
        setGuardianAgent(null);
        console.log("[taskguard] Guardian agent: disabled");
      }
    } catch (guardianErr) {
      console.warn(`[taskguard] Guardian agent init failed: ${guardianErr instanceof Error ? guardianErr.message : guardianErr}`);
      setGuardianAgent(null);
    }
  }
  const eventEmitter = new DynamicWorkflowEventEmitter({
    channel: {
      send: (event) => {
        // Best-effort: use injectChatMessage to push observability events
        // into the OpenClaw channel. Silently swallow errors.
        try {
          const summary = `[observability] ${event.type}: node=${event.nodeId} flow=${event.flowId}`;
          injectChatMessage(api, sessionKey, summary, `observability-${event.type}-${event.flowId}-${event.nodeId}`, " observability").catch(() => {});
        } catch { /* best-effort */ }
      },
    },
    logger: stepLogger,
  });

  const deps = buildControllerDeps(adapter, {
    actionRegistry,
    executeNode: createExecutorDispatch(api, sessionKey, actionRegistry, user, toolCtx, abortSignal, workflow, () => deps.subworkflowDeps, resolvedPackRoot),
    api,
    resolvedWorkflows,
    failedWorkflows,
    resolvedPacks,
    formatWorkflowCommand: (workflowId, command, args = [], options = {}) =>
      formatFacadeWorkflowCommand(facadeRegistry, workflowId, command, args, options),
    flowControl: getFlowControlService() ?? undefined,
    chatInjectLevel: loadConfig().app.chatInject.level,
    eventEmitter,
    // Version management deps (set by init flow)
    packsRoot: _packsRoot ?? undefined,
    clawWebBaseUrl: loadConfig().app.api.clawwebUrl || loadConfig().app.api.baseUrl,
    botId: loadBotId(),
    ownerId: loadOwnerId(),
    signatureKey: loadConfig().app.api.privateKeyB64,
    // Git config — read once at init and cached in deps, not re-read on every command.
    // Previously toVersionDeps() called loadConfig() again per invocation, which could
    // silently fail (catch swallowed errors) and leave gitRemoteUrl/gitUsername/gitToken empty.
    gitRemoteUrl: loadConfig().app.git.remoteUrl || undefined,
    gitUsername: loadConfig().app.git.username || undefined,
    gitToken: process.env.CLAWMIND_GIT_TOKEN || loadConfig().app.git.token || undefined,
    gitEmail: process.env.CLAWMIND_GIT_EMAIL || loadConfig().app.git.email || undefined,
    facadeBindingRepo: (() => {
      // Mirrors the dispatch-entry DB/API detection (index.ts:2062): API mode →
      // FacadeBindingApiRepository, DB mode → FacadeBindingRepository. Absent when
      // no DB is configured — handleDeploy then skips the binding write (warning).
      try {
        const _cfg = loadDatabaseConfig();
        if (_cfg.type === "api" && _cfg.api) {
          return new FacadeBindingApiRepository(createApiClient(_cfg.api));
        }
        if (db && db.dbType !== "noop") {
          return new FacadeBindingRepository(db);
        }
      } catch (err) {
        console.warn(`[versioning] failed to construct facadeBindingRepo: ${err instanceof Error ? err.message : err}`);
      }
      return undefined;
    })(),
  });
  // Set global bot/owner/session IDs for JSONL log entries (used by embedded agent event logging)
  _activeBotId = deps.botId ?? null;
  _activeOwnerId = deps.ownerId ?? null;
  _activeSessionKey = deps.sessionKey ?? null;
  _activeLogDir = deps.workflowLogDir;

  // ── Orphaned flow recovery: fire once on first buildDeps call ──
  if (!_recoveryExecuted) {
    _recoveryExecuted = true;
    const recoveryBotId = loadBotId();
    const recoveryEngine = getEngineName();
    if (recoveryBotId && recoveryEngine && getFlowRunRepository()) {
      console.log(`[taskguard] buildDeps: triggering orphaned flow recovery (botId=${recoveryBotId} engine=${recoveryEngine})`);
      // Fire-and-forget: recovery runs in background, doesn't block deps construction
      recoverOrphanedFlows(deps, recoveryBotId, recoveryEngine).catch((err) => {
        console.error("[taskguard] orphaned flow recovery failed:", err instanceof Error ? err.message : String(err));
      });
    } else {
      console.log(`[taskguard] buildDeps: orphaned flow recovery skipped (botId=${recoveryBotId ?? "NULL"} engine=${recoveryEngine ?? "NULL"} repo=${getFlowRunRepository() ? "OK" : "NULL"})`);
    }
  }

  return deps;
}

/**
 * Execute a `debug-segment` action from the standalone OpenClaw plugin tool.
 *
 * Mirrors the catalog → DB-spec-repo → buildDeps setup of `dispatchWorkflowCommand`,
 * but with two hard overrides so the debug segment is side-effect free:
 *   - `boundTaskFlow` → a no-op adapter (no flow_run records, no resume/finish/fail)
 *   - `chatInject` → a no-op (no progress notifications)
 *
 * The real executor dispatch (`executeNode`) is inherited from `buildDeps`, so nodes
 * genuinely run — only the production persistence/notification layer is silenced.
 */
async function executeDebugSegment(params: {
  api: PluginApi;
  sessionKey: string;
  workflowId: string;
  deliveryContext?: Record<string, unknown>;
  action: ControllerAction;
}): Promise<string> {
  const { api, sessionKey, workflowId, action } = params;
  const sessionId = resolveSessionId(sessionKey);

  const workflowCatalog = loadWorkflowPackCatalog();

  // DB/API spec repo (best-effort) — required so requireWorkflowLookup can find
  // workflows that are only deployed to DB, not present as local pack YAML.
  let _dbSpecRepo: IWorkflowSpecRepository | undefined;
  try {
    const _db = getDatabase();
    const _cfg = loadDatabaseConfig();
    // Prefer the workflowId's pack-local executor resolution via packs root.
    if (_cfg.type === "api" && _cfg.api) {
      _dbSpecRepo = new WorkflowSpecApiRepository(createApiClient(_cfg.api));
    } else if (_db && _db.dbType !== "noop") {
      _dbSpecRepo = new WorkflowSpecRepository(_db);
    }
  } catch (err) {
    console.warn("[taskguard] workflow_debug_segment: failed to init DB spec repo, continuing pack-only", {
      error: truncateForLog(err),
    });
  }

  // We don't resolve a single workflow up front — handleDebugSegment calls
  // requireWorkflowLookup itself. Just give it a populated catalog + DB repo.
  const boundTaskFlow = api.runtime.taskFlow.bindSession({
    sessionKey,
    requesterOrigin: params.deliveryContext,
  });

  const deps = buildDeps(
    api,
    sessionKey,
    sessionId,
    boundTaskFlow as unknown as ControllerDeps["boundTaskFlow"],
    ".",
    params.deliveryContext,
    undefined,
    workflowCatalog.workflows,
    workflowCatalog.packs,
    undefined, // onProgress — debug segment is silent, no progress callbacks
    undefined, // abortSignal
    undefined, // toolCtx — not executing inside an embedded agent
    formatChatInjectLabel(workflowId),
    buildFacadeRegistry(workflowCatalog.packs, []),
  );
  if (_dbSpecRepo) deps.workflowSpecApiRepo = _dbSpecRepo;

  // ── Hard overrides: silence production persistence + notifications ──
  let noOpRevision = 0;
  deps.boundTaskFlow = {
    createManaged: async () => ({}),
    setWaiting: async () => ({ applied: true, flow: {} }),
    resume: async () => ({ applied: true, flow: { revision: noOpRevision } }),
    finish: async () => undefined,
    fail: async () => undefined,
    list: async () => ({ flows: [] }),
    get: async () => null,
    findLatest: async () => null,
    runTask: async () => ({}),
  } as unknown as ControllerDeps["boundTaskFlow"];
  deps.chatInject = (async () => undefined) as ControllerDeps["chatInject"];

  const result = await executeAction(action, deps, params.deliveryContext, { type: "workflow" }, sessionKey);
  return result;
}

/**
 * Best-effort DB spec repo for the slash-interception hooks.
 *
 * Mirrors the catalog-build block of dispatchWorkflowCommand: in API mode we use
 * the API-backed repository, in sqlite mode the local repository. Returns
 * undefined when there is no DB. Never throws — a missing repo just means the
 * probe falls back to pack-only resolution.
 */
function buildHookSpecRepo(): IWorkflowSpecRepository | undefined {
  try {
    const _db = getDatabase();
    const _cfg = loadDatabaseConfig();
    if (_cfg.type === "api" && _cfg.api) {
      return new WorkflowSpecApiRepository(createApiClient(_cfg.api));
    }
    if (_db && _db.dbType !== "noop") {
      return new WorkflowSpecRepository(_db);
    }
  } catch {
    // best-effort — hooks must never throw here
  }
  return undefined;
}

/**
 * Determine whether `workflowId` is a real workflow the caller may run, WITHOUT
 * requiring a precomputed id list. Used by the slash-interception hooks to let
 * `/workflowId` work when the workflow has no facade and lives only in the DB
 * (so it never appears in the local pack catalog).
 *
 * Strategy (mirrors resolveWorkflow in packs/resolver.ts):
 *   1. If present in the local pack catalog → exists.
 *   2. Else query the DB spec repo (findByWorkflowId) → exists if a row is found.
 *
 * Returns false on any error so the hook falls through harmlessly.
 */
export async function probeWorkflowExists(
  workflowId: string,
  packWorkflows: ResolvedWorkflow[],
  specRepo: IWorkflowSpecRepository | undefined,
): Promise<boolean> {
  if (!workflowId) return false;
  if (packWorkflows.some((w) => w.id === workflowId)) return true;
  if (!specRepo) return false;
  try {
    const row = await specRepo.findByWorkflowId(workflowId);
    return !!row;
  } catch {
    return false;
  }
}

// Built-in command verbs that must NEVER be treated as a bare workflowId slash.
// Kept in sync with BUILTIN_COMMAND_VERBS in command-parser.ts (the subset that
// could plausibly appear after a leading "/"); "workflow" itself is handled by
// the matched-command path above, so it's excluded here too.
const BARE_SLASH_BUILTIN_VERBS = new Set([
  "workflow", "help", "run", "inspect", "state", "logs", "runs", "flows", "packs", "pack",
  "cutover-check", "detail", "confirm", "repair", "retry", "submit", "skip", "revise",
  "reject", "reopen", "resume", "debug", "export", "import", "validate", "test", "list",
  "schedule", "webhook", "deploy", "pull", "rollback", "deploys", "history", "status",
  "share", "unshare", "clawmind",
]);

// A bare workflowId must look like an identifier (letters/digits/_/-). Pure
// numerics or anything with spaces/symbols is not a workflowId slash.
const BARE_WORKFLOW_ID_PATTERN = /^[A-Za-z][A-Za-z0-9_\-]*$/;

/**
 * Extract a bare `/<workflowId> [args...]` slash from `text` and, when the id
 * is an unknown command, confirm via the DB probe that it is a real workflow.
 *
 * Returns `{ commandName, raw }` (raw = the args after the slash, so dispatch's
 * parseWorkflowCommandWithFacade rewrites it to `run <workflowId> <args>`) when
 * the slash is a DB-resolvable workflow, otherwise `null` (caller must NOT
 * intercept — let OpenClaw route the message to other skills / the agent).
 */
export async function tryInterceptBareWorkflowId(params: {
  text: string;
  catalogWorkflows: ResolvedWorkflow[];
  specRepo: IWorkflowSpecRepository | undefined;
}): Promise<{ commandName: string; raw: string } | null> {
  // Reuse the same scoping/strip logic as extractWrappedWorkflowSlashCommand:
  // operate on the message body, ignoring fenced code blocks.
  const scanText = stripFencedCodeBlocksForProbe(scopedTextForProbe(params.text));
  const lines = scanText.split("\n");
  if (lines.length === 0) return null;

  // Only the first non-empty line qualifies as a slash command trigger.
  let firstLine = "";
  for (const line of lines) {
    const t = line.trim();
    if (t) { firstLine = t; break; }
  }
  // Match `/verb [rest]` (optional leading [label] prefix, like the matcher).
  const m = firstLine.match(/^(?:\[[^\]]+\]\s+)?\/([^\s\/]+)(?:[ \t]+([\s\S]*))?$/);
  if (!m) return null;
  const verb = m[1].toLowerCase();
  const rest = (m[2] ?? "").trim();
  if (BARE_SLASH_BUILTIN_VERBS.has(verb)) return null;
  if (!BARE_WORKFLOW_ID_PATTERN.test(verb)) return null;

  const exists = await probeWorkflowExists(verb, params.catalogWorkflows, params.specRepo);
  if (!exists) return null;

  // raw must carry the workflowId verb so dispatch's no-facade parser rewrites
  // it to `run <workflowId> <rest>`. parseWorkflowCommandWithFacade strips a
  // leading "/" so either form works; we keep the slash to match how a user
  // would type it.
  return { commandName: verb, raw: rest ? `/${verb} ${rest}` : `/${verb}` };
}

// Local re-implementations of the two scoping helpers from
// wrapped-slash-command.ts (kept private there) so this probe can mirror the
// matched-command path's text normalization without widening that module's API.
function scopedTextForProbe(text: string): string {
  const normalized = text.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
  const marker = "[消息内容]";
  const idx = normalized.lastIndexOf(marker);
  if (idx < 0) return normalized;
  return normalized.slice(idx + marker.length);
}
function stripFencedCodeBlocksForProbe(text: string): string {
  return text.replace(/```[\s\S]*?```/g, "");
}

/**
 * Locate install-clawmind.sh adjacent to the deployed plugin package.
 *
 * Search order:
 * 1. scripts/install-clawmind.sh relative to the package root (walk up from
 *    import.meta.url, same strategy as discoverPacksAdjacentToEntry in
 *    packs/resolver.ts).
 * 2. scripts/install-clawmind.sh in the OPENCLAW_EXTENSION_DIR (production
 *    install path on remote openclaw hosts).
 *
 * Returns the absolute path to the script, or undefined if not found.
 */
function locateInstallScript(): string | undefined {
  // Strategy 1: walk up from this source file's compiled location
  try {
    let dir: string;
    try {
      dir = import.meta.dirname;
    } catch {
      dir = new URL(".", import.meta.url).pathname;
    }
    for (let depth = 0; depth < 10 && dir && dir !== join(dir, ".."); depth++) {
      const candidate = join(dir, "scripts", "install-clawmind.sh");
      if (existsSync(candidate) && statSync(candidate).isFile()) {
        return candidate;
      }
      dir = join(dir, "..");
    }
  } catch {
    // import.meta.url unavailable — fall through to Strategy 2
  }

  // Strategy 2: production install path (from env var)
  const extDir = process.env.OPENCLAW_EXTENSION_DIR;
  if (extDir) {
    const prodPath = join(extDir, "scripts/install-clawmind.sh");
    if (existsSync(prodPath) && statSync(prodPath).isFile()) {
      return prodPath;
    }
  }

  return undefined;
}

/**
 * Execute `clawmind update` — runs install-clawmind.sh to overwrite-install
 * the latest ClawMind plugin package. The script downloads the tgz from OSS,
 * backs up and restores packs/, removes the old install, extracts the new
 * version, and restarts the openclaw gateway.
 *
 * Returns a human-readable result string.
 */
async function handleClawmindUpdate(): Promise<string> {
  const scriptPath = locateInstallScript();
  if (!scriptPath) {
    return [
      "❌ 未找到 install-clawmind.sh 脚本。",
      "",
      "请确认 ClawMind 插件已正确安装，且安装包中包含 scripts/install-clawmind.sh 文件。",
      "如果是通过手动方式安装的，请从源码仓库执行 scripts/install-clawmind.sh。",
    ].join("\n");
  }

  console.info("[taskguard] handleClawmindUpdate: located script", { scriptPath });

  try {
    // Execute the install script via bash, stream stdout/stderr to console
    const { execFile } = await import("node:child_process");
    const result = await new Promise<{ stdout: string; stderr: string; code: number }>(
      (resolve) => {
        execFile(
          "bash",
          [scriptPath],
          {
            timeout: 120_000,   // 2 minute timeout for download + extract + restart
            maxBuffer: 1024 * 1024 * 5,  // 5MB buffer
            env: { ...process.env },
          },
          (err, stdout, stderr) => {
            if (err) {
              resolve({
                stdout: stdout?.toString() ?? "",
                stderr: stderr?.toString() ?? "",
                code: (err as { code?: number }).code ?? 1,
              });
            } else {
              resolve({
                stdout: stdout?.toString() ?? "",
                stderr: stderr?.toString() ?? "",
                code: 0,
              });
            }
          },
        );
      },
    );

    if (result.code === 0) {
      return [
        "✅ ClawMind 更新完成！",
        "",
        result.stdout.trim(),
      ].join("\n");
    } else {
      return [
        "❌ ClawMind 更新失败（脚本退出码 " + result.code + "）",
        "",
        "── stdout ──",
        result.stdout.trim() || "(empty)",
        "",
        "── stderr ──",
        result.stderr.trim() || "(empty)",
      ].join("\n");
    }
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.error("[taskguard] handleClawmindUpdate error", { error: errMsg });
    return [
      "❌ ClawMind 更新执行异常：",
      "",
      errMsg,
    ].join("\n");
  }
}

async function dispatchWorkflowCommand(params: {
  api: PluginApi;
  sessionKey: string;
  raw: string;
  commandName?: string;
  skillName?: string;
  entrypoint: WorkflowCommandEntrypoint;
  workspaceDir?: string;
  deliveryContext?: Record<string, unknown>;
  onProgress?: ControllerDeps["onProgress"];
  abortToolCtx?: EmbeddedAgentToolContext;
  startAsync?: boolean;
  /** debug-segment 专用:从 workflow_engine_dispatch 兄弟 schema 字段透传的上游 context。
   *  仅在解析出的 action.action==="debug-segment" 时 merge 进 action(command 串无法承载
   *  嵌套对象,parser 把 nodeOutput 留空 {},靠此处兜底)。其他 action 忽略。 */
  inlineDebugContext?: {
    nodeOutput?: Record<string, Record<string, unknown>>;
    workflowData?: Record<string, unknown>;
    input?: Record<string, unknown>;
  };
}): Promise<string> {
  // Tool and hook entrypoints converge here: load packs, resolve facade, parse, bind deps, then call controller.
  const {
    api,
    sessionKey,
    raw,
    commandName,
    skillName,
    entrypoint,
    workspaceDir = ".",
    deliveryContext,
    onProgress,
    abortToolCtx,
    startAsync = false,
    inlineDebugContext,
  } = params;
  const sessionId = resolveSessionId(sessionKey);
  console.info("[taskguard] dispatch entry", {
    entrypoint,
    sessionKey,
    commandName,
    skillName,
    rawPreview: raw?.slice(0, 120),
  });
  recordInjectTrace("dispatch_entry", {
    entrypoint,
    session_key: sessionKey,
    command_name: commandName,
    skill_name: skillName,
    raw_preview: raw?.slice(0, 120),
  });
  const activeAbortRun = registerActiveAbortRun({
    sessionKey,
    commandName,
    command: raw,
  });

  try {
    const workflowCatalog = loadWorkflowPackCatalog();
    console.info("[taskguard] pack catalog loaded", {
      packCount: workflowCatalog.packs.length,
      packIds: workflowCatalog.packs.map((p) => p.manifest.id),
      workflowCount: workflowCatalog.workflows.length,
      commandName,
      skillName,
    });
    let _dbBindings: DbFacadeBinding[] = [];
    let _dbSpecRepo: IWorkflowSpecRepository | undefined;
    let _permRepo: IBotWorkflowPermissionRepository | undefined;
    let _notifConfigRepo: INotificationConfigRepository | undefined;
    try {
      const _db = getDatabase();
      const _cfg = loadDatabaseConfig();
      const _isApi = _cfg.type === "api";
      if (_isApi && _cfg.api) {
        // API mode: load facade bindings and workflow specs from clawweb internal API
        const _apiClient = createApiClient(_cfg.api);
        _dbBindings = await loadApiFacadeBindings(_apiClient);
        _dbSpecRepo = new WorkflowSpecApiRepository(_apiClient);
        _permRepo = new BotWorkflowPermissionApiRepository(_apiClient);
        _notifConfigRepo = new NotificationConfigApiRepository(_apiClient);
        console.info("[taskguard] loaded facade bindings from API", { dbBindingsCount: _dbBindings.length });
      } else if (_db && _db.dbType !== "noop") {
        // Direct DB mode: load facade bindings and workflow specs from local database
        _dbBindings = await loadDbFacadeBindings(_db);
        _dbSpecRepo = new WorkflowSpecRepository(_db);
        _notifConfigRepo = new NotificationConfigRepository(_db);
      }
    } catch (err) {
      console.warn("[taskguard] failed to load DB facade bindings, continuing with pack-only facades", {
        error: truncateForLog(err),
      });
    }
    const facadeRegistry = buildFacadeRegistry(workflowCatalog.packs, _dbBindings);
    console.info("[taskguard] facade registry built", {
      commands: facadeRegistry.commands(),
      dbBindingsCount: _dbBindings.length,
    });
    // Register notification config repository for DB-backed notification settings
    if (_notifConfigRepo) {
      setNotificationConfigRepository(_notifConfigRepo);
    }
    const facade = facadeRegistry.resolve(commandName) ?? facadeRegistry.resolve(skillName);
    const commandSurface = facade
      ? ({ type: "facade", command: facade.command } satisfies WorkflowCommandSurface)
      : ({ type: "workflow" } satisfies WorkflowCommandSurface);
    const action = parseWorkflowCommandWithFacade(raw, {
      commandName,
      skillName,
      facade,
    });
    // debug-segment:command 串无法承载 nodeOutput/workflowData/input 嵌套对象,parser
    // 把它们留空({})。由 workflow_engine_dispatch 兄弟 schema 字段透传的 inlineDebugContext
    // 在此 merge 兑现 —— 仅对 debug-segment 生效,其他 action 不碰。
    if (action.action === "debug-segment" && inlineDebugContext) {
      if (inlineDebugContext.nodeOutput && Object.keys(inlineDebugContext.nodeOutput).length > 0) {
        action.nodeOutput = inlineDebugContext.nodeOutput;
      }
      if (inlineDebugContext.workflowData) {
        action.workflowData = inlineDebugContext.workflowData;
      }
      if (inlineDebugContext.input) {
        action.input = inlineDebugContext.input;
      }
    }
    const actionWorkflowId = inferWorkflowIdFromAction(action);
    recordWorkflowCommandDispatchStarted({
      sessionKey,
      entrypoint,
      commandName,
      skillName,
      raw,
      facade: facade?.command,
      workflowId: actionWorkflowId,
    });
    console.info("[taskguard] dispatch step: before injectCommandEchoMessage", { sessionKey, commandName, actionWorkflowId });
    await injectCommandEchoMessage(api, sessionKey, raw, commandName, skillName, actionWorkflowId);
    console.info("[taskguard] dispatch step: after injectCommandEchoMessage", { sessionKey, commandName, actionWorkflowId });
    const debugFlag = action.action === "run" ? action.debug : undefined;
    console.info("[taskguard] dispatch step: before resolveWorkflow", { actionWorkflowId, debugFlag, hasDbSpecRepo: !!_dbSpecRepo });
    const resolvedWorkflow = actionWorkflowId
      ? await resolveWorkflow(actionWorkflowId, _dbSpecRepo, workflowCatalog.workflows, debugFlag)
      : undefined;
    console.info("[taskguard] dispatch step: after resolveWorkflow", {
      actionWorkflowId,
      resolved: !!resolvedWorkflow,
      resolvedId: resolvedWorkflow?.id,
      resolvedSource: resolvedWorkflow?.source?.kind,
    });
    // ── Permission check: DB permission (primary) → YAML allowedBots (fallback) ──
    if (resolvedWorkflow) {
      const botId = loadBotId();
      console.info("[taskguard] dispatch step: permission check", {
        botId,
        ownerId: loadOwnerId(),
        hasPermRepo: !!_permRepo,
        workflowId: resolvedWorkflow.spec.id,
      });
      if (botId) {
        // Step 1: Try DB permission check
        let dbPermissionChecked = false;
        const ownerId = loadOwnerId();
        if (_permRepo && ownerId) {
          try {
            console.info("[taskguard] dispatch step: before checkExecutePermission", { botId, ownerId, workflowId: resolvedWorkflow.spec.id });
            const permResult = await _permRepo.checkExecutePermission(botId, ownerId, resolvedWorkflow.spec.id);
            console.info("[taskguard] dispatch step: after checkExecutePermission", { allowed: permResult.allowed, hasRecords: permResult.hasRecords });
            if (permResult.hasRecords) {
              dbPermissionChecked = true;
              if (!permResult.allowed) {
                const msg = `[taskguard] 工作流 "${resolvedWorkflow.spec.title || resolvedWorkflow.spec.id}" 不允许当前 bot (${botId}) 执行（权限表拒绝）`;
                console.warn("[taskguard] blocked by bot_workflow_permissions", { botId, ownerId, workflowId: resolvedWorkflow.spec.id });
                return msg;
              }
            }
            // hasRecords === false → fall through to allowedBots
          } catch (error) {
            console.warn("[taskguard] DB permission check failed, falling back to allowedBots", { error: truncateForLog(error) });
          }
        }

        // Step 2: Fallback to YAML allowedBots (only if DB check was not conclusive)
        if (!dbPermissionChecked && resolvedWorkflow.spec.allowedBots && resolvedWorkflow.spec.allowedBots.length > 0) {
          if (!resolvedWorkflow.spec.allowedBots.includes(botId)) {
            const msg = `[taskguard] 工作流 "${resolvedWorkflow.spec.title || resolvedWorkflow.spec.id}" 不允许在当前 bot (${botId}) 上执行，允许的 bot: ${resolvedWorkflow.spec.allowedBots.join(", ")}`;
            console.warn("[taskguard] blocked by allowedBots filter", { botId, allowedBots: resolvedWorkflow.spec.allowedBots, workflowId: resolvedWorkflow.spec.id });
            return msg;
          }
        }
      }
    }

    // ── Auto-pull missing local pack from DB (best-effort, run path only) ──
    // A DB-resolved workflow (source.kind="db") runs off the spec alone, but pack-local
    // resources (cli-script scripts, skill SKILL.md, templates) only exist once the pack
    // directory is on disk. When the local pack is absent we materialize it from the DB
    // before running. Reuses the execute-permission gate above — does not re-check edit
    // permission — and is best-effort: on failure we fall back to a DB-spec run rather
    // than aborting. "Local pack missing" is judged against the local pack catalog
    // (resolveWorkflowByIdFromPacks), NOT resolvedWorkflow.source, because resolveWorkflow
    // is DB-first and returns "db" even when a local pack exists.
    if (
      action.action === "run"
      && actionWorkflowId
      && resolvedWorkflow
      && !resolveWorkflowByIdFromPacks(actionWorkflowId, workflowCatalog.workflows)
    ) {
      const _appCfg = loadConfig().app;
      const _clawWebBaseUrl = _appCfg.api.clawwebUrl || _appCfg.api.baseUrl;
      if (_packsRoot && _clawWebBaseUrl) {
        const autoPullDeps = {
          packsRoot: _packsRoot,
          clawWebBaseUrl: _clawWebBaseUrl,
          signatureKey: _appCfg.api.privateKeyB64,
          botId: loadBotId(),
          ownerId: loadOwnerId(),
          resolvedWorkflows: workflowCatalog.workflows,
          resolvedPacks: workflowCatalog.packs,
          gitRemoteUrl: _appCfg.git.remoteUrl || undefined,
          gitUsername: _appCfg.git.username || undefined,
          gitToken: process.env.CLAWMIND_GIT_TOKEN || _appCfg.git.token || undefined,
          gitEmail: process.env.CLAWMIND_GIT_EMAIL || _appCfg.git.email || undefined,
        };
        try {
          console.info("[taskguard] auto-pull: local pack missing, pulling from DB", { workflowId: actionWorkflowId });
          const { handlePull } = await import("./controller/version-commands.js");
          const pullResult = await handlePull(autoPullDeps, actionWorkflowId, { skipPermissionCheck: true });
          console.info("[taskguard] auto-pull result", { workflowId: actionWorkflowId, result: pullResult });
        } catch (err) {
          console.warn("[taskguard] auto-pull failed (non-fatal, continuing with DB spec)", {
            workflowId: actionWorkflowId,
            error: truncateForLog(err),
          });
        }
      }
    }
    const workflowForDeps = resolvedWorkflow?.spec;
    const chatInjectLabel = formatChatInjectLabel(actionWorkflowId);
    console.info("[taskguard] dispatch step: before bindSession", { sessionKey, actionWorkflowId });
    const boundTaskFlow = api.runtime.taskFlow.bindSession({
      sessionKey,
      requesterOrigin: deliveryContext,
    });

    const deps = buildDeps(
      api,
      sessionKey,
      sessionId,
      boundTaskFlow as unknown as ControllerDeps["boundTaskFlow"],
      workspaceDir,
      deliveryContext,
      workflowForDeps,
      workflowCatalog.workflows,
      workflowCatalog.packs,
      onProgress,
      activeAbortRun.controller.signal,
      abortToolCtx,
      chatInjectLabel,
      facadeRegistry,
      resolvedWorkflow?.pack?.root,
      resolvedWorkflow?.pack?.id,
      workflowCatalog.failedWorkflows,
    );
    // In API mode, attach the workflow spec API repo for DB-first resolution inside controller
    if (_dbSpecRepo) {
      deps.workflowSpecApiRepo = _dbSpecRepo;
    }
    // Capture deps for scheduler's launchWorkflow callback and card-web poller
    _latestDeps = deps;
    if (_extensions?.startPollers) {
      _extensions.startPollers(deps);
    } else {
      updatePollerDeps(deps);
    }
    captureCallbackPollerDeps(deps);

    const shouldDetachToolDispatch = shouldAutoDetachToolDispatch({
      entrypoint,
      startAsync,
      action,
      workflow: resolvedWorkflow?.spec,
    });

    if (startAsync || shouldDetachToolDispatch) {
      if (action.action !== "run") {
        throw new Error(startAsync
          ? "workflow_engine_start_async only supports run commands"
          : "workflow_engine_dispatch detached mode only supports run commands");
      }
      const executionMode = resolveExecutionMode(deliveryContext, sessionKey);
      const bcsGroupId = executionMode !== "private"
        ? (deliveryContext?.bcsGroupId as string | undefined) ?? extractGroupIdFromSessionKey(sessionKey)
        : undefined;
      console.info("[taskguard] dispatch step: before detached handleRun", {
        actionWorkflowId: action.workflowId,
        sessionKey,
        startAsync,
        autoDetached: shouldDetachToolDispatch,
      });
      const flowId = await handleRun(deps, {
        workflowId: action.workflowId,
        params: action.params,
        message: action.message,
        files: action.files,
        executionMode,
        bcsGroupId,
        commandSurface,
        debug: action.debug,
        startAsync: true,
      });
      const payload = {
        status: "started",
        workflowId: action.workflowId,
        flowId,
        detached: shouldDetachToolDispatch,
        reason: shouldDetachToolDispatch ? "workflow_engine_dispatch_agent_node_deadlock_guard" : "workflow_engine_start_async",
        message: shouldDetachToolDispatch
          ? `流程已启动并转入后台执行，避免 workflow_engine_dispatch 同步等待 embedded-agent 节点造成死锁 (workflow: ${action.workflowId}, flowId: ${flowId})`
          : `流程已异步启动 (workflow: ${action.workflowId}, flowId: ${flowId})`,
      };
      console.info("[taskguard] dispatch step: after detached handleRun", payload);
      return JSON.stringify(payload, null, 2);
    }

    console.info("[taskguard] dispatch step: before executeAction", { action: action.action, actionWorkflowId, sessionKey });
    const depsBuilders: DispatchDepsBuilders = {
      buildScheduleDeps: getScheduleCommandDeps,
      buildWebhookDeps: getWebhookCommandDeps,
    };
    const result = await dispatchWithTimeout(
      () => executeAction(action, deps, deliveryContext, commandSurface, sessionKey, depsBuilders),
      action.action,
      sessionKey,
    );
    console.info("[taskguard] dispatch step: after executeAction", { result: typeof result === "string" ? result.substring(0, 200) : String(result) });
    return result;
  } finally {
    unregisterActiveAbortRun(activeAbortRun);
  }
}

function formatWorkflowCommandDispatchError(error: unknown): string {
  if (error instanceof Error && error.message.trim()) return error.message;
  const text = String(error).trim();
  return text || "workflow 命令执行失败。";
}

/**
 * Diagnostic trace for chat-inject & dispatch — routed through the JSONL logger
 * (NOT console) because console.* output is not captured in this deployment's
 * log files. event_type="debug_inject_trace" lands in clawmind.log
 * alongside workflow_command_dispatch_started etc.
 */
function recordInjectTrace(
  stage: string,
  fields: Record<string, unknown> = {},
  flowId: string | null = null,
): void {
  void appendWorkflowJsonlLog(
    buildDirectLogRecord({
      flowId,
      eventType: "debug_inject_trace",
      message: `[debug-inject] ${stage}`,
      botId: _activeBotId,
      sessionKey: _activeSessionKey,
      details: { stage, ...fields },
    }),
    { baseDir: _activeLogDir },
  ).catch(() => { /* best-effort log */ });
}

function recordWorkflowCommandDispatchStarted(params: {
  sessionKey: string;
  entrypoint: WorkflowCommandEntrypoint;
  commandName?: string;
  skillName?: string;
  raw: string;
  facade?: string;
  workflowId?: string;
}): void {
  void appendWorkflowJsonlLog(
    buildDirectLogRecord({
      flowId: "command",
      eventType: "workflow_command_dispatch_started",
      message: "workflow command dispatch started",
      botId: _activeBotId,
      sessionKey: _activeSessionKey,
      details: {
        workflow_id: params.workflowId ?? null,
        node_id: null,
        action_id: null,
        attempt: null,
        session_key: params.sessionKey,
        source: params.entrypoint,
        command_name: params.commandName,
        skill_name: params.skillName,
        facade: params.facade ?? null,
        raw_command: params.raw.slice(0, 500),
        error: null,
      },
    }),
    { baseDir: _activeLogDir },
  ).catch(() => { /* best-effort log */ });
}

function recordWrappedSlashIntercept(params: {
  sessionKey: string;
  commandName: string;
  raw: string;
  facade?: string;
  workflowId?: string;
}): void {
  void appendWorkflowJsonlLog(
    buildDirectLogRecord({
      flowId: "command",
      eventType: "wrapped_slash_intercepted",
      message: "wrapped slash command intercepted",
      botId: _activeBotId,
      sessionKey: _activeSessionKey,
      details: {
        workflow_id: params.workflowId ?? null,
        node_id: null,
        action_id: null,
        attempt: null,
        session_key: params.sessionKey,
        source: "before_agent_reply",
        command_name: params.commandName,
        facade: params.facade ?? null,
        raw_command: params.raw.slice(0, 500),
        error: null,
      },
    }),
    { baseDir: _activeLogDir },
  ).catch(() => { /* best-effort log */ });
}

// ── Build BCS approval mapping from all registered workflows ──

function buildBcsApprovalMapping(): Record<string, string> {
  const mapping: Record<string, string> = {};
  let workflows: WorkflowSpec[] = [];
  try {
    workflows = loadWorkflowPackCatalog().workflows.map((workflow) => workflow.spec);
  } catch {
    workflows = [];
  }
  for (const workflow of workflows) {
    for (const node of workflow.nodes) {
      const approvalExecutor = getLegacyApprovalExecutor(node);
      const routeTargets = approvalExecutor?.route?.to
        ?? (node.executor.type === "collaboration" ? node.executor.route?.to : undefined);
      for (const target of routeTargets ?? []) {
        if ((target.type === "name" || target.type === "bot") && typeof target.value === "string" && target.value.trim()) {
          mapping[target.value] = node.id;
        }
      }
      if (node.executor.type === "bcs-route" && node.executor.target) {
        mapping[node.executor.target] = node.id;
      }
    }
  }
  return mapping;
}

function looksLikeLegacyBcsApprovalProtocol(text: string): boolean {
  const stripped = text.replace(/^\[from:[^\]]+\]\s*/, "").trim();
  return stripped.includes("workflow-approval-v1")
    && (
      stripped.includes("approval_result")
      || stripped.includes("approval_error")
    );
}

// ── Scheduler State (module-level for command access) ──

let _scheduler: CronScheduler | null = null;
let _triggerStore: ScheduledTriggerRepository | null = null;
let _schedulerEnabled = false;
/** Latest controller deps captured from each command dispatch, used by scheduler's launchWorkflow. */
let _latestDeps: ControllerDeps | null = null;
/** Current bot ID — set per workflow execution, used for JSONL log entries. */
let _activeBotId: string | null = null;
/** Current owner ID — set per workflow execution, used for JSONL log entries. */
let _activeOwnerId: string | null = null;
/** Current session key — set per workflow execution, used for JSONL log entries. */
let _activeSessionKey: string | null = null;
/** Current workflow log directory — set per workflow execution, used for JSONL log entries. */
let _activeLogDir: string | undefined = undefined;

/** Run archive builder — initialized in buildDeps, used by MCP tool and API server. */
let _runArchiveBuilder: RunArchiveBuilder | null = null;
/** Run log uploader — initialized in buildDeps, uploads structured run logs to DB. */
let _runLogUploader: RunLogUploader | null = null;
/**
 * Pending run log repository for API mode.
 * register() callback may execute before buildDeps() creates _runLogUploader.
 * When that happens, we stash the repo here so buildDeps() can pick it up.
 */
let _pendingRunLogRepo: IRunLogRepository | null = null;

/**
 * Build ScheduleCommandDeps from current scheduler state.
 * Called when a `/workflow schedule` command is dispatched.
 */
function getScheduleCommandDeps(deps: ControllerDeps): ScheduleCommandDeps {
  return {
    triggerStore: _triggerStore,
    schedulerEnabled: _schedulerEnabled,
    schedulerRunning: _scheduler?.isRunning() ?? false,
    fireTrigger: async (trigger) => {
      const baseParams: Record<string, string> = trigger.params_json
        ? JSON.parse(trigger.params_json)
        : {};
      const params: Record<string, string> = {
        ...baseParams,
        triggerSource: "manual",
        triggerId: trigger.trigger_id,
      };
      try {
        const flowId = await handleRun(deps, {
          workflowId: trigger.workflow_id,
          params,
          executionMode: "private",
        });
        return flowId;
      } catch {
        return null;
      }
    },
    workflowExists: (workflowId: string) => {
      if (deps.workflowRegistry) return workflowId in deps.workflowRegistry;
      const fromPacks = deps.resolvedWorkflows ?? [];
      return fromPacks.some((w) => w.id === workflowId);
    },
    packExists: (packId: string) => {
      const packs = deps.resolvedPacks ?? [];
      return packs.some((p) => p.manifest.id === packId);
    },
  };
}

// ── Webhook State (module-level for command access) ──

let _webhookTriggerStore: WebhookTriggerRepository | null = null;
let _webhookEventStore: WebhookEventRepository | null = null;
let _webhookEnabled = false;
let _webhookCleanupTimer: ReturnType<typeof setInterval> | null = null;
let _approvalCardCleanupTimer: ReturnType<typeof setInterval> | null = null;
let _flowTimeoutTimer: ReturnType<typeof setInterval> | null = null;

// ── Versioning state ──
let _packsRoot: string | null = null;

/**
 * Build WebhookCommandDeps from current webhook state.
 * Called when a `/workflow webhook` command is dispatched.
 */
function getWebhookCommandDeps(deps: ControllerDeps): WebhookCommandDeps {
  let apiBaseUrl = "http://127.0.0.1:3210";
  try {
    const { app } = loadConfig();
    if (app.api.enabled) {
      apiBaseUrl = `http://${app.api.host}:${app.api.port}`;
    }
  } catch { /* use default */ }

  return {
    triggerStore: _webhookTriggerStore,
    webhookEnabled: _webhookEnabled,
    workflowExists: (workflowId: string) => {
      if (deps.workflowRegistry) return workflowId in deps.workflowRegistry;
      const fromPacks = deps.resolvedWorkflows ?? [];
      return fromPacks.some((w) => w.id === workflowId);
    },
    packExists: (packId: string) => {
      const packs = deps.resolvedPacks ?? [];
      return packs.some((p) => p.manifest.id === packId);
    },
    apiBaseUrl,
  };
}

// ── Dynamic IAM token provider ──

/**
 * Create an ApiClient config that includes a dynamic iamtoken provider.
 * The provider re-reads the config file on every request, enabling hot-reload
 * of IAM tokens without restarting the process. This is critical for local
 * development where IAM tokens expire and must be renewed in application.yaml.
 *
 * In production, iamtoken is not configured (ACE is bypassed via internal routing),
 * so the provider returns undefined and no Cookie header is sent.
 */
function createApiConfigWithProvider(config: ApiClientConfig): ApiClientConfig {
  return {
    ...config,
    iamtokenProvider: () => {
      try {
        return loadConfig().app.api.iamtoken;
      } catch {
        // Config file may be temporarily unavailable; fall back to static value
        return config.iamtoken;
      }
    },
  };
}

/** Convenience: create an ApiClient with dynamic token refresh.
 *  If corp extensions provide createApiClient, use it; otherwise use community stub. */
function createApiClient(config: ApiClientConfig): ApiClient {
  if (_extensions?.createApiClient) {
    return _extensions.createApiClient(config) as ApiClient;
  }
  return new ApiClient(createApiConfigWithProvider(config));
}

// ── Plugin Entry ──

/**
 * Register the ClawMind plugin with an OpenClaw-compatible PluginApi.
 *
 * This function is the platform-specific entry point for OpenClaw. It is
 * re-exported from `src/platform/openclaw-entry.ts` which calls
 * `definePluginEntry()`. Keeping the registration body here lets the rest of
 * `src/index.ts` continue to host the core dispatch/build helpers without
 * importing `openclaw/plugin-sdk` at runtime.
 */
export function registerTaskguardPlugin(api: PluginApi, extensions?: TaskguardExtensions): void {
  // Store extensions for corp module injection (createApiClient, knowledge adapters, etc.)
  _extensions = extensions;

  // ── Wire up previously-unchecked extension points ──
  // These 5 fields were defined in TaskguardExtensions but never consumed.
  // We now check and invoke them so corp injections actually take effect.

  // 1. createDatabase — if corp provides a database factory, use it instead of community default
  const dbFactory = extensions?.createDatabase
    ? () => extensions.createDatabase!(loadDatabaseConfig() as unknown as Parameters<typeof extensions.createDatabase>[0])
    : () => createDatabase();

  // 2. createNotifier — store for later use by notification dispatch
  if (extensions?.createNotifier) {
    _corpNotifier = extensions.createNotifier;
  }

  // 3. createApprovalProvider — store for later use by approval flow
  if (extensions?.createApprovalProvider) {
    _corpApprovalProvider = extensions.createApprovalProvider;
  }

  // 4. registerExecutors — call immediately so corp executors are registered at plugin load
  if (extensions?.registerExecutors) {
    try {
      extensions.registerExecutors(undefined);
    } catch (err) {
      console.warn("[taskguard] registerExecutions extension failed:", err instanceof Error ? err.message : String(err));
    }
  }

  // 5. registerAuthMethods — store auth methods for callback authentication
  if (extensions?.registerAuthMethods) {
    try {
      _corpAuthMethods = extensions.registerAuthMethods(undefined);
    } catch (err) {
      console.warn("[taskguard] registerAuthMethods extension failed:", err instanceof Error ? err.message : String(err));
    }
  }

  // Initialize database asynchronously (best-effort; falls back to NoOpDatabase on failure)
    dbFactory().then(async (db) => {
      setDatabase(db);

      const dbConfig = loadDatabaseConfig();
      const isApiMode = dbConfig.type === "api";

      // ── CRITICAL: RunLogUploader setup MUST happen FIRST, before any other
      // initialization that could throw. If any code below throws, the .catch()
      // handler silently swallows the error, and the uploader would never be
      // created — causing all run_logs to be silently dropped.
      // ──
      let _runLogApiRepoForBuilder: RunLogApiRepository | null = null;
      try {
        if (isApiMode) {
          const apiConfig = dbConfig.api!;
          const apiClient = createApiClient(apiConfig);
          const runLogApiRepo = new RunLogApiRepository(apiClient);
          _runLogApiRepoForBuilder = runLogApiRepo;
          if (_runLogUploader) {
            // buildDeps() already created uploader with its own repo;
            // register() callback's repo is identical, no need to swap.
            console.log("[taskguard] Run log uploader: already created by buildDeps (API mode)");
          } else {
            _pendingRunLogRepo = runLogApiRepo;
            console.log("[taskguard] Run log uploader: repository deferred (buildDeps not yet called)");
          }
        } else if (db && db.dbType !== "noop") {
          const runLogRepo = new RunLogRepository(db);
          const uploader = new RunLogUploader(runLogRepo, { maxEntriesPerFlow: 500 });
          _runLogUploader = uploader;
          setRunLogUploader(uploader);
          uploader.start();
          console.log("[taskguard] Run log uploader: created from register() callback (direct DB mode)");
          if (!_runArchiveBuilder) {
            _runArchiveBuilder = new RunArchiveBuilder(db, runLogRepo);
          }
        }
        // API mode deferred: if we stashed _pendingRunLogRepo, create uploader now
        if (!_runLogUploader && _pendingRunLogRepo) {
          const uploader = new RunLogUploader(_pendingRunLogRepo, { maxEntriesPerFlow: 500 });
          _pendingRunLogRepo = null;
          _runLogUploader = uploader;
          setRunLogUploader(uploader);
          uploader.start();
          console.log("[taskguard] Run log uploader: created from register() callback (API mode, deferred repo)");
        }
      } catch (uploaderErr) {
        console.error(
          "[taskguard] RunLogUploader init FAILED:",
          uploaderErr instanceof Error ? uploaderErr.message : String(uploaderErr),
        );
      }

      if (isApiMode) {
        // API mode: create ApiClient and API-backed repositories
        const apiConfig = dbConfig.api!;
        if (!apiConfig.privateKeyB64) {
          console.warn("[taskguard] API mode enabled but CLAWMIND_PRIVATE_KEY not set — API writes will fail");
        }
        const apiClient = createApiClient(apiConfig);
        console.log(`[taskguard] API mode: using clawweb at ${apiConfig.baseUrl}`);

        setEventRepository(new FlowEventApiRepository(apiClient));

        // Set up run archive builder for API mode (queries clawweb via API)
        if (!_runArchiveBuilder) {
          _runArchiveBuilder = new RunArchiveApiBuilder(apiClient, _runLogApiRepoForBuilder!) as unknown as RunArchiveBuilder;
          console.log("[taskguard] Run archive builder: API mode initialized");
        }

        let statePersistenceEnabled = true;
        let recordMetrics = true;
        let maxIoSizeKb = 32;
        try {
          const { app } = loadConfig();
          statePersistenceEnabled = app.statePersistence.enabled;
          recordMetrics = app.statePersistence.recordMetrics;
          maxIoSizeKb = app.statePersistence.maxIoSizeKb;
        } catch { /* use defaults */ }

        if (statePersistenceEnabled) {
          setNodeExecutionRepository(new NodeExecutionApiRepository(apiClient, maxIoSizeKb));
          setFlowRunRepository(new FlowRunApiRepository(apiClient));
          setNodeStepTraceRepository(new NodeStepTraceApiRepository(apiClient));
          setHallucinationCheckRepository(new HallucinationCheckApiRepository(apiClient));
        }
        if (recordMetrics) {
          setMetricsRepository(new FlowMetricsApiRepository(apiClient));
        }
        setAlertRepository(new TriggeredAlertApiRepository(apiClient));
      } else if (db.dbType !== "noop") {
        // Direct DB mode: use SQL repositories
        setEventRepository(new FlowEventRepository(db));

        let statePersistenceEnabled = true;
        let recordMetrics = true;
        try {
          const { app } = loadConfig();
          statePersistenceEnabled = app.statePersistence.enabled;
          recordMetrics = app.statePersistence.recordMetrics;
        } catch { /* use defaults */ }

        if (statePersistenceEnabled) {
          setNodeExecutionRepository(new NodeExecutionRepository(db));
          setFlowRunRepository(new FlowRunRepository(db));
          setNodeStepTraceRepository(new NodeStepTraceRepository(db));
          setHallucinationCheckRepository(new HallucinationCheckRepository(db));
        } else {
          console.warn("[taskguard] statePersistence is disabled — flow runs will not be persisted to engine DB");
        }
        if (recordMetrics) {
          setMetricsRepository(new FlowMetricsRepository(db));
        }
        setAlertRepository(new TriggeredAlertRepository(db));
      }

      // ── Load DB-stored config (cm_app_config) and merge with local application.yaml ──
      // This merges DB config sections over local YAML in memory (files are never modified).
      // After this, loadConfig() returns the cached merged config.
      const apiClientForConfig = isApiMode ? createApiClient(dbConfig.api!) : undefined;
      await initConfig(db, apiClientForConfig);

      // Initialize knowledge bases from config
      try {
        const { app } = loadConfig();
        if (app.knowledge.enabled) {
          const bases = [];
          // If corp extensions provide knowledge adapters, use them; otherwise use community stubs
          if (_extensions?.createKnowledgeAdapters) {
            const corpAdapters = _extensions.createKnowledgeAdapters(app);
            if (Array.isArray(corpAdapters)) {
              bases.push(...corpAdapters);
            }
          } else {
            if (app.knowledge.sources.yuque.enabled && app.knowledge.sources.yuque.token) {
              bases.push(new YuQueAdapter({
                apiBaseUrl: `https://${app.knowledge.sources.yuque.domain}`,
                authToken: app.knowledge.sources.yuque.token,
              }, app.knowledge.maxResults));
            }
            if (app.knowledge.sources.agentmind.enabled && app.knowledge.sources.agentmind.token) {
              bases.push(new AgentMindAdapter({
                token: app.knowledge.sources.agentmind.token,
                apiBaseUrl: app.knowledge.sources.agentmind.endpoint,
                instanceName: app.knowledge.sources.agentmind.knowledgeBaseId,
                interfaceName: "search",
                userName: "clawflow",
                userId: "0",
              }, app.knowledge.maxResults));
            }
          }
          if (bases.length > 0) {
            setKnowledgeBases(bases, app.knowledge);
          }
        }
      } catch { /* knowledge init is best-effort */ }

      // Initialize KnowledgeBaseManager for DB-backed GRT KBs (knowledgeBaseId on nodes)
      try {
        if (db) {
          const kbManager = new KnowledgeBaseManager(db);
          setKnowledgeBaseManager(kbManager);
          kbManager.startAutoRefresh();
        }
      } catch { /* KB manager init is best-effort */ }

      // Initialize flow control (concurrency limiting & queueing)
      try {
        const { app } = loadConfig();
        if (app.flowControl.enabled && (db.dbType !== "noop" || isApiMode)) {
          initFlowControl(db, app.flowControl, {
            onWorkflowResume: async (flowId: string, payload: string | null) => {
              const sessionKey = recoverSessionKey(flowId, payload);
              console.log(`[flow-control] WORKFLOW_RESUME_START flowId=${flowId} sessionKey=${sessionKey.slice(0, 12)}...`);
              const boundTaskFlow = api.runtime.taskFlow.bindSession({ sessionKey });
              const workflowCatalog = loadWorkflowPackCatalog();
              const resumeDeps: ControllerDeps = {
                boundTaskFlow: boundTaskFlow as unknown as ControllerDeps["boundTaskFlow"],
                chatInject: async (_msg: string, _key: string) => { /* no-op for dispatcher-initiated resume */ },
                executeNode: async () => ({ status: "succeeded" as const }),
                actionRegistry: createDefaultActionRegistry(workflowCatalog.packs),
                sessionKey,
                skillRoot: ".",
                resolvedWorkflows: workflowCatalog.workflows,
                failedWorkflows: workflowCatalog.failedWorkflows,
                resolvedPacks: workflowCatalog.packs,
                flowControl: getFlowControlService() ?? undefined,
              };
              const resumeResult = await resumeQueuedWorkflow(resumeDeps, flowId);
              console.log(`[flow-control] WORKFLOW_RESUME_END flowId=${flowId} result=${resumeResult}`);

              // resumeQueuedWorkflow returns:
              //   true  — flow resumed, or flow gone/not-waiting (terminal: drop queue entry)
              //   false — transient failure (revision conflict etc) — reenqueue for retry
              if (!resumeResult) {
                throw new Error(`resumeQueuedWorkflow returned false for ${flowId} — transient failure, will re-enqueue`);
              }
            },
            // onNodeResume removed — executor-level flow control removed in simplification
            onExpired: (flowId: string, nodeId: string | null, scopeKey: string, _payload: string | null) => {
              // INVARIANT: Flow control NEVER modifies flow_runs.status.
              // OnExpired only releases resources (slots). The flow remains in
              // its current state. The Controller's orphan recovery or other
              // mechanisms (timeouts, manual intervention) handle the flow state.
              console.warn(
                `[flow-control] QUEUE_EXPIRED flowId=${flowId} node=${nodeId ?? "N/A"} scope=${scopeKey} ` +
                `— releasing slots only (flow state unchanged)`,
              );
              try {
                const fcService = getFlowControlService();
                fcService?.releaseAllForFlow(flowId);
              } catch (err) {
                console.error(`[flow-control] EXPIRED_FLOW_PROCESS_ERROR flowId=${flowId}:`, err);
              }
            },
            findOrphanedWaitingFlows: isApiMode ? (() => {
              // Cache the API client once — avoid creating a new one on every tick.
              const cachedApiClient = createApiClient(dbConfig.api!);
              return async () => {
              // In API mode, query the clawweb internal API for waiting flows,
              // then check which ones have no queue entry (orphaned) or have
              // stale "dispatched" entries (zombie dispatched — the dispatch
              // failed but the entry was never cleaned up).
              try {
                const fcService = getFlowControlService();
                if (!fcService) return [];
                const apiClient = cachedApiClient;
                // H4 fix: Query both "waiting" and "blocked" flows, matching SQLite mode.
                // Blocked flows may need re-enqueueing when their semaphore opens up
                // but the dispatcher missed the notification.
                const [waitingRes, blockedRes, queueItems] = await Promise.all([
                  apiClient.get<Array<{ flow_id: string; workflow_id: string; session_key?: string }>>("/runs?status=waiting&limit=50"),
                  apiClient.get<Array<{ flow_id: string; workflow_id: string; session_key?: string }>>("/runs?status=blocked&limit=50"),
                  fcService.getQueueItems(undefined, 200),
                ]);
                if (!waitingRes.ok || !waitingRes.data) {
                  console.log(`[flow-control] findOrphanedWaitingFlows: /runs?status=waiting request failed ok=${waitingRes.ok} hasData=${!!waitingRes.data}`);
                }
                if (!blockedRes.ok || !blockedRes.data) {
                  console.log(`[flow-control] findOrphanedWaitingFlows: /runs?status=blocked request failed ok=${blockedRes.ok} hasData=${!!blockedRes.data}`);
                }
                // Internal API wraps results in { success, data } envelope;
                // ApiClient.parseResponseBody unwraps on success, so data is the inner array.
                // But if unwrapping fails, it may return the whole envelope.
                const parseRuns = (res: typeof waitingRes): Array<{ flow_id: string; workflow_id: string; session_key?: string }> => {
                  if (!res.ok || !res.data) return [];
                  return Array.isArray(res.data)
                    ? res.data
                    : ((res.data as Record<string, unknown>)?.data as Array<{ flow_id: string; workflow_id: string; session_key?: string }> | undefined) ?? [];
                };
                const waitingRuns = parseRuns(waitingRes);
                const blockedRuns = parseRuns(blockedRes);
                // Deduplicate by flow_id (a flow shouldn't be both waiting & blocked, but be safe)
                const seenFlowIds = new Set<string>();
                const runs: Array<{ flow_id: string; workflow_id: string; session_key?: string }> = [];
                for (const r of [...waitingRuns, ...blockedRuns]) {
                  if (!seenFlowIds.has(r.flow_id)) {
                    seenFlowIds.add(r.flow_id);
                    runs.push(r);
                  }
                }
                console.log(`[flow-control] findOrphanedWaitingFlows: found ${waitingRuns.length} waiting + ${blockedRuns.length} blocked runs, ${queueItems.length} queue items`);
                if (runs.length === 0) return [];

                // DEDUP: Index queue items by flow_id for efficient lookup.
                // Only consider queue items belonging to THIS instance to avoid
                // cross-instance duplicate enqueueing. A flow already queued on
                // another instance should be handled by that instance, not re-enqueued here.
                const instanceId = fcService.getInstanceId();
                const queueByFlowId = new Map<string, typeof queueItems>();
                for (const q of queueItems) {
                  // Skip queue items from other instances — they handle their own orphaned flows
                  if (q.instance_id && q.instance_id !== instanceId) continue;
                  const existing = queueByFlowId.get(q.flow_id);
                  if (existing) existing.push(q);
                  else queueByFlowId.set(q.flow_id, [q]);
                }

                // A "stale dispatched" entry is one that's been in "dispatched" status
                // for more than 5 minutes — this indicates a zombie dispatch that failed
                // to complete (e.g., due to sessionKey mismatch causing resumeQueuedWorkflow
                // to return false, followed by a failed reenqueueOnFailure or crash).
                const STALE_DISPATCHED_THRESHOLD_SECS = 300; // 5 minutes
                const now = Math.floor(Date.now() / 1000);

                const orphaned: Array<{ flowId: string; workflowId: string; payload?: string | null }> = [];

                for (const r of runs) {
                  const qEntries = queueByFlowId.get(r.flow_id);
                  if (!qEntries || qEntries.length === 0) {
                    // No queue entries at all — truly orphaned
                    const payload = r.session_key
                      ? JSON.stringify({ sessionKey: r.session_key })
                      : null;
                    if (!payload) {
                      console.warn(`[flow-control] findOrphanedWaitingFlows: orphaned flow ${r.flow_id} has no session_key, will use fallback sessionKey`);
                    }
                    orphaned.push({ flowId: r.flow_id, workflowId: r.workflow_id || "default", payload });
                    continue;
                  }
                  // Log queue entry details for debugging
                  const statuses = qEntries.map(q => {
                    const age = (q.gmt_modified != null && Number.isFinite(q.gmt_modified))
                      ? (now - q.gmt_modified)
                      : "???";
                    return `${q.status}(age=${age}s)`;
                  });
                  console.log(`[flow-control] findOrphanedWaitingFlows: flow ${r.flow_id} has ${qEntries.length} queue entries: ${statuses.join(", ")}`);
                  // Check if ALL entries for this flow are stale "dispatched" (zombie)
                  // OR if there are both dispatched and queued entries (the queued ones are from
                  // reenqueueOnFailure loops that also fail, creating an infinite loop pattern).
                  // Consider a flow "stuck" if it has any dispatched entry that's old enough,
                  // regardless of whether it also has queued entries.
                  // Use gmt_modified (updated on dispatch) not enqueued_at (set on first enqueue),
                  // because an item queued for 10min then dispatched 30s ago should NOT be stale.
                  // Guard against null/undefined gmt_modified (can happen with API responses).
                  const hasStaleDispatched = qEntries.some(q => {
                    if (q.status !== "dispatched") return false;
                    if (q.gmt_modified == null || !Number.isFinite(q.gmt_modified)) return false;
                    const age = now - q.gmt_modified;
                    return age > STALE_DISPATCHED_THRESHOLD_SECS;
                  });
                  if (hasStaleDispatched) {
                    // Check how long this flow has been in the queue overall (using enqueued_at).
                    // If it's been cycling for more than ZOMBIE_QUEUE_AGE_SECS, it's unrecoverable —
                    // mark it as failed instead of re-enqueueing to break the infinite loop.
                    const ZOMBIE_QUEUE_AGE_SECS = 3600; // 1 hour
                    const oldestEnqueuedAt = qEntries
                      .map(q => q.enqueued_at)
                      .filter((v): v is number => v != null && Number.isFinite(v))
                      .reduce((min, v) => Math.min(min, v), Infinity);
                    const queueAge = (oldestEnqueuedAt !== Infinity) ? (now - oldestEnqueuedAt) : null;
                    const isZombie = queueAge != null && queueAge > ZOMBIE_QUEUE_AGE_SECS;

                    if (isZombie) {
                      // ZOMBIE ESCAPE: This flow has been cycling in the queue for too long.
                      // It will never succeed — release its resources and DO NOT re-enqueue.
                      // INVARIANT: flow control NEVER modifies flow_runs.status.
                      // The Controller's own timeout mechanism or manual intervention
                      // will handle the actual flow state transition.
                      console.warn(
                        `[flow-control] findOrphanedWaitingFlows: ZOMBIE ESCAPE for flow ${r.flow_id} — ` +
                        `queue age ${queueAge}s > ${ZOMBIE_QUEUE_AGE_SECS}s. Releasing resources, not re-enqueueing.`,
                      );
                      // releaseAllForFlow releases slots AND deletes queue entries
                      fcService.releaseAllForFlow(r.flow_id);
                      // Do NOT add to orphaned — don't re-enqueue a zombie
                      // Do NOT call boundTaskFlow.fail() — flow control doesn't modify flow state
                    } else {
                      // Stale dispatched but not yet a zombie — clear and re-enqueue
                      console.log(`[flow-control] findOrphanedWaitingFlows: flow ${r.flow_id} has ${qEntries.length} stale dispatched entries (age > ${STALE_DISPATCHED_THRESHOLD_SECS}s), clearing and re-enqueueing`);
                      try {
                        await fcService.releaseAllForFlow(r.flow_id);
                      } catch (err) {
                        console.error(`[flow-control] findOrphanedWaitingFlows: failed to clean up stale entries for ${r.flow_id}:`, err);
                      }
                      const payload = r.session_key
                        ? JSON.stringify({ sessionKey: r.session_key })
                        : null;
                      if (!payload) {
                        console.warn(`[flow-control] findOrphanedWaitingFlows: stale-dispatched flow ${r.flow_id} has no session_key, will use fallback sessionKey`);
                      }
                      orphaned.push({ flowId: r.flow_id, workflowId: r.workflow_id || "default", payload });
                    }
                  }
                  // Flows with "queued" entries or fresh "dispatched" entries are NOT orphaned
                }

                if (orphaned.length > 0) {
                  console.log(`[flow-control] findOrphanedWaitingFlows: ${orphaned.length} orphaned/stale-dispatched flows: ${orphaned.map(o => o.flowId).join(", ")}`);
                }
                return orphaned;
              } catch (err) {
                console.error("[flow-control] findOrphanedWaitingFlows failed:", err);
                return [];
              }
              };
            })() : async () => {
              // SQLite mode: query local DB for waiting/blocked flows with no queue entry
              try {
                const fcService = getFlowControlService();
                if (!fcService) return [];

                const flowRunRepo = db && db.dbType !== "noop"
                  ? new FlowRunRepository(db)
                  : null;
                if (!flowRunRepo) return [];

                const waitingRuns = await flowRunRepo.findRuns({ status: "waiting", limit: 50 });
                const blockedRuns = await flowRunRepo.findRuns({ status: "blocked", limit: 50 });
                const allRuns = [...waitingRuns, ...blockedRuns];
                if (allRuns.length === 0) return [];

                const queueItems = await fcService.getQueueItems(undefined, 200);
                // DEDUP: Only consider queue items for THIS instance to avoid
                // cross-instance duplicate enqueueing.
                const instanceId = fcService.getInstanceId();
                const myQueueItems = queueItems.filter(q => !q.instance_id || q.instance_id === instanceId);
                const queuedFlowIds = new Set(myQueueItems.map(q => q.flow_id));

                const orphaned: Array<{ flowId: string; workflowId: string; payload?: string | null }> = [];
                for (const run of allRuns) {
                  if (!queuedFlowIds.has(run.flow_id)) {
                    // recoverSessionKey will try the registry DB as fallback
                    const sessionKey = recoverSessionKey(run.flow_id, null);
                    const payload = sessionKey
                      ? JSON.stringify({ sessionKey })
                      : null;
                    if (!payload) {
                      console.warn(`[flow-control] findOrphanedWaitingFlows(sqlite): orphaned flow ${run.flow_id} has no sessionKey, will use fallback`);
                    }
                    orphaned.push({ flowId: run.flow_id, workflowId: run.workflow_id || "default", payload });
                  }
                }

                if (orphaned.length > 0) {
                  console.log(`[flow-control] findOrphanedWaitingFlows(sqlite): ${orphaned.length} orphaned flows: ${orphaned.map(o => o.flowId).join(", ")}`);
                }
                return orphaned;
              } catch (err) {
                console.error("[flow-control] findOrphanedWaitingFlows(sqlite) failed:", err);
                return [];
              }
            },
          }, isApiMode ? createApiClient(dbConfig.api!) : undefined);
        }
      } catch { /* flow control init is best-effort */ }

      // Initialize validation template resolver (DB-backed LLM output validation)
      try {
        if (isApiMode) {
          const apiConfig = dbConfig.api!;
          const apiClient = createApiClient(apiConfig);
          const vtRepo = new ValidationTemplateApiRepository(apiClient);
          setValidationTemplateResolver(async (templateId: string) => {
            const row = await vtRepo.findEnabled(templateId);
            if (!row) return null;
            try {
              const parsed = JSON.parse(row.content);
              if (!parsed.prompt) return null;
              return parsed;
            } catch {
              console.error(`[taskguard] Validation template ${templateId}: invalid JSON content`);
              return null;
            }
          });
          console.info("[taskguard] Validation template resolver initialized (API mode)");
        } else if (db && db.dbType !== "noop") {
          const vtRepo = new ValidationTemplateRepository(db);
          setValidationTemplateResolver(async (templateId: string) => {
            const row = await vtRepo.findEnabled(templateId);
            if (!row) return null;
            try {
              const parsed = JSON.parse(row.content);
              if (!parsed.prompt) return null;
              return parsed;
            } catch {
              console.error(`[taskguard] Validation template ${templateId}: invalid JSON content`);
              return null;
            }
          });
          console.info("[taskguard] Validation template resolver initialized");
        }
      } catch { /* validation template init is best-effort */ }

      // Initialize retry config
      try {
        const { app } = loadConfig();
        if (app.retry.kbSearchEnabled) {
          setRetryConfig(app.retry);
        }
        // Initialize analysis config (needs metrics repo for querying)
        if (app.analysis.enabled) {
          const metricsRepo = isApiMode
            ? new FlowMetricsApiRepository(createApiClient(dbConfig.api!))
            : (db ? new FlowMetricsRepository(db) : null);
          setAnalysisConfig(app.analysis, metricsRepo);
        }
        // Initialize alerting config (DingTalk notifications + alert persistence)
        if (app.alerting.enabled) {
          const alertRepo = isApiMode
            ? new TriggeredAlertApiRepository(createApiClient(dbConfig.api!))
            : (db ? new TriggeredAlertRepository(db) : null);
          setAlertingConfig(app.alerting, alertRepo);
        }

        // Initialize workflow notification dispatcher (enterprise DingTalk single-chat + group)
        // Credentials are read from each workflow's YAML — only the clawwebBaseUrl is needed here
        setWorkflowNotificationConfig(
          app.api.clawwebUrl || app.api.baseUrl,
        );

        // Initialize notification config repository for DB-backed notification settings
        if (isApiMode && dbConfig.api) {
          const apiClient = createApiClient(dbConfig.api);
          setNotificationConfigRepository(new NotificationConfigApiRepository(apiClient));
        } else if (db.dbType !== "noop") {
          setNotificationConfigRepository(new NotificationConfigRepository(db));
        }

        // Initialize HTTP callback notification system (config repository)
        // Note: In API mode, createDatabase() returns NoOpDatabase (dbType="noop"),
        // so we must NOT gate on db.dbType — API mode uses ApiClient, not the local db.
        try {
          // Initialize audit log repository first (must be set before config repo)
          if (isApiMode) {
            setHttpCallbackLogRepository(new HttpCallbackLogApiRepository(createApiClient(dbConfig.api!)));
          } else if (db.dbType !== "noop") {
            setHttpCallbackLogRepository(new HttpCallbackLogRepository(db));
          }
          if (isApiMode) {
            const httpCallbackConfigRepo = new HttpCallbackConfigApiRepository(createApiClient(dbConfig.api!));
            setHttpCallbackRepositories(httpCallbackConfigRepo);
          } else if (db.dbType !== "noop") {
            const httpCallbackConfigRepo = new HttpCallbackConfigRepository(db);
            setHttpCallbackRepositories(httpCallbackConfigRepo);
          }
        } catch (err) {
          console.warn("[taskguard] HTTP callback config repository init failed (non-fatal):", err instanceof Error ? err.message : String(err));
        }

        // Load HTTP callback configs from DB + YAML into dispatcher cache
        (async () => {
          try {
            const catalog = loadWorkflowPackCatalog();
            const yamlSpecs = new Map<string, import("./types.js").WorkflowSpec>();
            for (const wf of catalog.workflows) {
              if (wf.spec?.id) yamlSpecs.set(wf.spec.id, wf.spec);
            }
            await reloadHttpCallbackConfigs(yamlSpecs);
            console.info("[taskguard] HTTP callback configs loaded from DB + YAML");
          } catch (err) {
            console.warn("[taskguard] HTTP callback config reload failed (non-fatal):", err instanceof Error ? err.message : String(err));
          }
        })();

        // Initialize webhook state (before API server so routes can be registered)
        try {
          _webhookEnabled = app.webhook.enabled;
          if (app.webhook.enabled && (isApiMode || db.dbType !== "noop")) {
            if (isApiMode) {
              const apiClient = createApiClient(dbConfig.api!);
              _webhookTriggerStore = new WebhookTriggerApiRepository(apiClient) as unknown as WebhookTriggerRepository;
              _webhookEventStore = new WebhookEventApiRepository(apiClient) as unknown as WebhookEventRepository;
            } else {
              _webhookTriggerStore = new WebhookTriggerRepository(db);
              _webhookEventStore = new WebhookEventRepository(db);
            }
            console.info("[taskguard] Webhook triggers enabled");
          }
        } catch { /* webhook init is best-effort */ }

        // Start query API server if enabled
        if (app.api.enabled) {
          let webhookDeps: import("./api/server.js").WebhookDeps | undefined;
          if (_webhookEnabled && _webhookTriggerStore && _webhookEventStore) {
            const launchWorkflow: import("./webhook/trigger-adapter.js").WorkflowLauncher = async (options) => {
              const deps = _latestDeps;
              if (!deps) {
                console.warn("[webhook] Cannot fire workflow: controller deps not yet available");
                return null;
              }
              try {
                const flowId = await handleRun(deps, {
                  workflowId: options.workflowId,
                  params: options.params,
                  executionMode: options.executionMode as "private",
                  chatInjectLevel: options.chatInjectLevel,
                });
                return flowId;
              } catch {
                return null;
              }
            };
            webhookDeps = {
              config: app.webhook,
              triggerStore: _webhookTriggerStore,
              eventStore: _webhookEventStore,
              launchWorkflow,
            };
          }
          startApiServer(app.api, {
            flowRunRepository: isApiMode ? new FlowRunApiRepository(createApiClient(dbConfig.api!)) : (db ? new FlowRunRepository(db) : null),
            eventRepository: isApiMode ? new FlowEventApiRepository(createApiClient(dbConfig.api!)) : (db ? new FlowEventRepository(db) : null),
            nodeExecutionRepository: isApiMode ? new NodeExecutionApiRepository(createApiClient(dbConfig.api!), app.statePersistence.maxIoSizeKb) : (db ? new NodeExecutionRepository(db) : null),
            metricsRepository: isApiMode ? new FlowMetricsApiRepository(createApiClient(dbConfig.api!)) : (db ? new FlowMetricsRepository(db) : null),
            alertRepository: isApiMode ? new TriggeredAlertApiRepository(createApiClient(dbConfig.api!)) : (db ? new TriggeredAlertRepository(db) : null),
            facadeBindingRepository: isApiMode ? new FacadeBindingApiRepository(createApiClient(dbConfig.api!)) : (db ? new FacadeBindingRepository(db) : null),
            runArchiveBuilder: _runArchiveBuilder,
          }, webhookDeps);
        }
      } catch { /* retry/analysis init is best-effort */ }

      // Initialize scheduler if enabled
      try {
        const { app } = loadConfig();
        _schedulerEnabled = app.scheduler.enabled;
        if (app.scheduler.enabled && (isApiMode || db.dbType !== "noop")) {
          if (isApiMode) {
            const apiClient = createApiClient(dbConfig.api!);
            _triggerStore = new ScheduledTriggerApiRepository(apiClient) as unknown as ScheduledTriggerRepository;
            console.info("[taskguard] Scheduler enabled (API mode)");
          } else {
            _triggerStore = new ScheduledTriggerRepository(db);
          }
          const launchWorkflow: WorkflowLauncher = async (options) => {
            const deps = _latestDeps;
            if (!deps) {
              console.warn("[scheduler] Cannot fire workflow: controller deps not yet available");
              return null;
            }
            try {
              const flowId = await handleRun(deps, {
                workflowId: options.workflowId,
                params: options.params,
                executionMode: options.executionMode,
                chatInjectLevel: options.chatInjectLevel,
              });
              return flowId;
            } catch {
              return null;
            }
          };
          _scheduler = new CronScheduler({
            config: app.scheduler,
            triggerStore: _triggerStore,
            launchWorkflow,
          });
          _scheduler.start().catch((err) => {
            const msg = err instanceof Error ? err.message : String(err);
            console.error(`[taskguard] Scheduler start failed: ${msg}`);
          });
        }
      } catch { /* scheduler init is best-effort */ }

      // Start card-web approval poller (polls approval_cards DB for resolved approvals)
      // In API mode, the poller uses pollResolvedCardsApi() which calls
      // clawweb's internal API; in direct-DB mode, it queries the database directly.
      // Always start — the poller internally decides API vs DB path
      // based on cfg.api.baseUrl, so db.dbType is irrelevant here.
      try {
        if (!_extensions?.startPollers) {
          startCardWebPoller();
        }
      } catch { /* card-web poller init is best-effort */ }

      // Start async-callback timeout poller (scans callback_tokens for expired pending tokens)
      try {
        const callbackConfig = loadConfig().app.asyncCallback;
        if (callbackConfig?.enabled && db) {
          startCallbackTimeoutPoller(db, callbackConfig);
        }
      } catch { /* callback timeout poller init is best-effort */ }

      // Webhook event retention cleanup (repos initialized earlier)
      if (_webhookEnabled && _webhookEventStore) {
        try {
          const { app } = loadConfig();
          const cleanupEvents = async () => {
            if (!_webhookEventStore) return;
            try {
              const deleted = await _webhookEventStore.deleteOlderThan(app.webhook.eventRetentionDays);
              if (deleted > 0) {
                console.info(`[webhook] Cleaned up ${deleted} events older than ${app.webhook.eventRetentionDays} days`);
              }
            } catch { /* best-effort */ }
          };
          cleanupEvents();
          _webhookCleanupTimer = setInterval(cleanupEvents, 24 * 60 * 60 * 1000);
          _webhookCleanupTimer.unref?.(); // don't prevent process exit
        } catch { /* cleanup init is best-effort */ }
      }

      // ── Git init + per-pack repos ──
      try {
        const { defaultWorkspaceWorkflowsRoot } = await import("./packs/resolver.js");
        let packsDir = defaultWorkspaceWorkflowsRoot();
        const botId = loadBotId();
        const ownerId = loadOwnerId();

        // Ensure packs directory exists and resolve to canonical path.
        const { existsSync } = await import("node:fs");
        const { join: pathJoin } = await import("node:path");
        const canonicalPacksDir = pathJoin(homedir(), "openclawExt", "clawmind", "packs");
        if (existsSync(pathJoin(homedir(), "openclawExt", "clawmind")) && packsDir !== canonicalPacksDir) {
          console.log(`[versioning] Resolver returned ${packsDir}, but ~/openclawExt/clawmind exists — using ${canonicalPacksDir}`);
          packsDir = canonicalPacksDir;
        }

        try {
          const fs = await import("node:fs/promises");
          await fs.mkdir(packsDir, { recursive: true });
        } catch { /* best-effort */ }

        // Store packsDir for later use in ControllerDeps
        _packsRoot = packsDir;

        // Inject PACK_ROOT for Python scripts
        process.env.PACK_ROOT = packsDir;
        if (!process.env.WORKFLOW_PACKS_ROOT) {
          process.env.WORKFLOW_PACKS_ROOT = packsDir;
        }

        // Per-pack git repos: each pack directory (packs/{packId}/) is its own
        // git repository, all pushing to the same remote on branch wf/{workflowId}.
        // No single packs-level git init needed — repos are created on-demand
        // by ensureGitRepoForPack() during migration/deploy/pull/rollback.

        const { app: appConfig } = loadConfig();
        const gitConfig = appConfig.git;

        console.log(`[versioning] Git configured (per-pack repo model, remote=${gitConfig?.remoteUrl ? "yes" : "no"})`);

        // ── Startup migration: align all DB workflows to local ──
        // Runs every startup because local may be stale
        // (e.g. web edits in DB not yet synced, or packs dir lost and restored from git).
        try {
          const { handleMigration } = await import("./controller/version-commands.js");
          const workflowCatalog = loadWorkflowPackCatalog();
          const gitToken = process.env.CLAWMIND_GIT_TOKEN ?? gitConfig?.token ?? "";
          const migrationDeps = {
            packsRoot: packsDir,
            clawWebBaseUrl: appConfig.api.clawwebUrl || appConfig.api.baseUrl,
            botId,
            ownerId,
            resolvedWorkflows: workflowCatalog.workflows as any[],
            resolvedPacks: workflowCatalog.packs as any[],
            signatureKey: appConfig.api.privateKeyB64,
            gitRemoteUrl: gitConfig?.remoteUrl,
            gitUsername: gitConfig?.username,
            gitToken,
          };
          console.log("[versioning] Running startup migration (DB→local alignment)...");
          console.log(`[versioning] Migration deps: clawWebBaseUrl=${migrationDeps.clawWebBaseUrl}, botId=${botId}, ownerId=${ownerId}, packsRoot=${packsDir}, localWorkflows=${workflowCatalog.workflows.length}`);
          const migrationResult = await handleMigration(migrationDeps);
          console.log(`[versioning] Migration result: ${migrationResult}`);
          // If migration failed due to API unavailability, schedule a retry
          if (migrationResult.includes("❌") || migrationResult.includes("不可达")) {
            console.warn("[versioning] Migration failed (API may be unavailable), will retry via sync-poll");
          }
        } catch (err) {
          console.warn(`[versioning] Migration failed (non-fatal): ${err instanceof Error ? err.message : err}`);
        }

        // ── One-time startup sync for ClawWeb changes (no periodic polling) ──
        if (gitConfig?.remoteUrl) {
          const { detectClawWebChanges, handleSyncDelete } = await import("./controller/sync-poll.js");

          // Initial sync: run immediately on startup (covers migration retry when
          // ClawWeb API was unavailable during startup but becomes available later)
          const runSyncPoll = async () => {
            try {
              // Reload workflow catalog for accurate local vs DB diff
              const currentCatalog = loadWorkflowPackCatalog();
              const pollDeps = {
                clawWebBaseUrl: appConfig.api.clawwebUrl || appConfig.api.baseUrl,
                botId,
                ownerId,
                resolvedWorkflows: currentCatalog.workflows as any[],
                resolvedPacks: currentCatalog.packs as any[],
                packsRoot: packsDir,
                gitRemoteUrl: gitConfig?.remoteUrl,
                gitUsername: gitConfig?.username,
                gitToken: process.env.CLAWMIND_GIT_TOKEN ?? gitConfig?.token ?? "",
              };

              const changes = await detectClawWebChanges(pollDeps);

              // Handle changed/new workflows (pull from DB — includes git sync for scripts)
              if (changes.changed.length > 0) {
                console.log(`[sync-poll] Detected ${changes.changed.length} changed/new workflows from ClawWeb`);
                const { handlePull } = await import("./controller/version-commands.js");
                for (const wfId of changes.changed) {
                  try {
                    const r = await handlePull(pollDeps, wfId);
                    console.log(`[sync-poll] ${r}`);
                  } catch (err) {
                    console.warn(`[sync-poll] Auto-pull failed for ${wfId}: ${err instanceof Error ? err.message : err}`);
                  }
                }
              }

              // Handle deleted workflows (remove local + git)
              if (changes.deleted.length > 0) {
                console.log(`[sync-poll] Detected ${changes.deleted.length} deleted workflows from ClawWeb`);
                for (const del of changes.deleted) {
                  try {
                    const result = await handleSyncDelete(packsDir, del.workflowId, del.packId, {
                      botId,
                      ownerId,
                    });
                    console.log(`[sync-poll] ${result}`);
                  } catch (err) {
                    console.warn(`[sync-poll] Sync-delete failed for ${del.workflowId}: ${err instanceof Error ? err.message : err}`);
                  }
                }
              }
            } catch (err) {
              console.warn(`[sync-poll] Poll error: ${err instanceof Error ? err.message : err}`);
            }
          };

          // Run once on startup to sync DB→local, then stop.
          // No periodic polling — avoids git queue contention and clobbering local edits.
          // Users can manually `workflow pull` if they edit via ClawWeb while bot is running.
          runSyncPoll();
          console.log(`[versioning] Sync-on-startup: will sync DB→local once, no periodic polling`);
        }
      } catch (err) {
        console.warn(`[versioning] Git init failed (non-fatal): ${err instanceof Error ? err.message : err}`);
      }

      // ── Startup self-check: print run_logs write chain status ──
      try {
        const dbConfig = loadDatabaseConfig();
        const isApiMode = dbConfig.type === "api";
        const uploader = _runLogUploader;
        const hasRepo = uploader ? (uploader as any).repo != null : false;
        const pendingRepo = _pendingRunLogRepo != null;
        const builder = _runArchiveBuilder != null;
        const dbType = (db as any)?.dbType ?? "unknown";

        console.log(
          `[clawmind:startup] run_logs chain: ` +
          `dbType=${dbType} isApiMode=${isApiMode} ` +
          `runLogUploader=${uploader != null} repo=${hasRepo} ` +
          `pendingRepo=${pendingRepo} runArchiveBuilder=${builder}`,
        );

        if (isApiMode && uploader && !hasRepo) {
          console.warn(
            "[clawmind:startup] run_logs WARNING: API mode but RunLogUploader has no repository! " +
            "Check: did register() callback execute before buildDeps()?",
          );
        }
        if (isApiMode && !uploader) {
          console.warn(
            "[clawmind:startup] run_logs WARNING: API mode but RunLogUploader is null! " +
            "buildDeps() has not been called yet — uploader will be created on first command.",
          );
        }
        if (!isApiMode && dbType !== "noop" && uploader && !hasRepo) {
          console.warn(
            "[clawmind:startup] run_logs WARNING: Direct DB mode but RunLogUploader has no repository! " +
            "Check: RunLogRepository(db) should have been passed to RunLogUploader constructor.",
          );
        }
      } catch (checkErr) {
        console.warn(`[clawmind:startup] run_logs self-check failed: ${checkErr instanceof Error ? checkErr.message : checkErr}`);
      }
    }).catch((err) => {
      console.error(
        "[taskguard] register() DB init FAILED:",
        err instanceof Error ? err.message : String(err),
        "— RunLogUploader and other DB-dependent features will NOT be available.",
      );
    });

    // Cleanup expired approval cards every hour
    const APPROVAL_CARD_CLEANUP_INTERVAL = 60 * 60 * 1000;
    const APPROVAL_CARD_MAX_AGE = 24 * 60 * 60 * 1000; // 24 hours
    _approvalCardCleanupTimer = setInterval(() => {
      try {
        const cleaned = cleanupExpiredApprovalCards(APPROVAL_CARD_MAX_AGE);
        if (cleaned > 0) {
          console.info(`[taskguard] Cleaned up ${cleaned} expired approval cards`);
        }
      } catch { /* best-effort */ }
    }, APPROVAL_CARD_CLEANUP_INTERVAL);
    _approvalCardCleanupTimer.unref?.(); // don't prevent process exit

    // Flow-timeout watchdog: reap flows stuck in "running" past the timeout and
    // mark them failed. Independent of flowControl (which is disabled) — this is
    // the only safety net that stops abandoned/zombie flows from sitting in
    // "running" forever. Tunable via application.yaml (execution.flowTimeoutMinutes /
    // execution.flowReapIntervalSecs) with env overrides (CLAWMIND_FLOW_TIMEOUT_MINUTES /
    // CLAWMIND_FLOW_REAP_INTERVAL_SECS). Set flowTimeoutMinutes=0 to disable.
    //
    // NOTE: _flowTimeoutMs is read dynamically on each sweep via loadConfig()
    // instead of being cached at startup. This ensures DB cm_app_config overrides
    // (loaded asynchronously by initConfig) are picked up correctly — at startup
    // the DB config may not be ready yet, causing loadConfig() to fall back to
    // local application.yaml with a stale value.
    const FLOW_REAP_INTERVAL_MS = loadConfig().app.execution.flowReapIntervalSecs * 1000;
    if (loadConfig().app.execution.flowTimeoutMinutes > 0) {
      console.info(
        `[taskguard] flow-timeout watchdog: timeout=${loadConfig().app.execution.flowTimeoutMinutes}min ` +
        `interval=${loadConfig().app.execution.flowReapIntervalSecs}s`,
      );
      // DB-first spec resolver for the watchdog, mirroring runtime `resolveWorkflow`.
      // Lets per-workflow `flowTimeoutMinutes` take effect for workflows deployed to
      // DB that have no local pack YAML (the previous catalog-only lookup missed them).
      const _watchdogDb = getDatabase();
      const _watchdogCfg = loadDatabaseConfig();
      const _watchdogIsApi = _watchdogCfg.type === "api";
      const _watchdogSpecRepo: IWorkflowSpecRepository | undefined = _watchdogIsApi && _watchdogCfg.api
        ? new WorkflowSpecApiRepository(createApiClient(_watchdogCfg.api))
        : (_watchdogDb && _watchdogDb.dbType !== "noop" ? new WorkflowSpecRepository(_watchdogDb) : undefined);
      _flowTimeoutTimer = setInterval(() => {
        // Resolve the TaskFlow instance once per sweep — both the state reader
        // and the fail() caller share this reference.
        const taskFlow = api.runtime?.taskFlow as Record<string, unknown> | undefined;

        // Callback 1: read FlowState from TaskFlow for richer failure diagnostics.
        const getStateForFlow = async (flowId: string): Promise<FlowState | undefined> => {
          try {
            if (!taskFlow) return undefined;
            const getAnyOwner = taskFlow.getAnyOwner;
            let flow: Record<string, unknown> | null = null;
            if (typeof getAnyOwner === "function") {
              flow = await (getAnyOwner as (id: string) => Promise<Record<string, unknown> | null>).call(taskFlow, flowId);
            } else {
              const listAll = taskFlow.listAll;
              if (typeof listAll !== "function") return undefined;
              const flows = await (listAll as () => Promise<Array<Record<string, unknown>> | { flows: Array<Record<string, unknown>> }>).call(taskFlow);
              const flowList = Array.isArray(flows) ? flows : flows?.flows ?? [];
              flow = flowList.find((f) => (f.flowId ?? f.flow_id) === flowId) ?? null;
            }
            if (!flow) return undefined;
            const stateJson = flow.stateJson;
            if (typeof stateJson !== "string") return undefined;
            return JSON.parse(stateJson) as FlowState;
          } catch {
            return undefined;
          }
        };

        // Callback 2: fail the flow in TaskFlow to prevent the executeLoop from
        // overwriting the "failed" status with "succeeded" after abort.
        const failFlowInTaskFlow = async (flowId: string, stateJson: string, blockedSummary: string): Promise<boolean> => {
          if (!taskFlow || typeof taskFlow.fail !== "function") return false;
          try {
            // Read the current revision from TaskFlow so fail() can CAS.
            const getAnyOwner = taskFlow.getAnyOwner;
            let flow: Record<string, unknown> | null = null;
            if (typeof getAnyOwner === "function") {
              flow = await (getAnyOwner as (id: string) => Promise<Record<string, unknown> | null>).call(taskFlow, flowId);
            }
            const revision = (flow as Record<string, unknown> | null)?.revision as number | undefined;
            await (taskFlow.fail as (params: Record<string, unknown>) => Promise<unknown>).call(taskFlow, {
              flowId,
              expectedRevision: revision,
              stateJson,
              blockedSummary,
            });
            return true;
          } catch {
            // Revision conflict or already terminal — executeLoop may have already
            // transitioned the flow. Return false to signal that the CAS didn't apply.
            return false;
          }
        };

        // Read timeout dynamically each sweep — loadConfig() may return a stale
        // value at startup (before initConfig loads DB cm_app_config), but after
        // initConfig completes the cached config reflects DB overrides.
        const _flowTimeoutMs = loadConfig().app.execution.flowTimeoutMinutes * 60 * 1000;
        if (_flowTimeoutMs <= 0) return; // watchdog disabled

        void reapStaleRunningFlows(_flowTimeoutMs, 100, getStateForFlow, failFlowInTaskFlow, async (workflowId) => {
          // Resolve per-workflow flowTimeoutMinutes, DB-first (mirrors runtime
          // resolveWorkflow) with local pack YAML as fallback. Returns undefined
          // to fall back to the global timeoutMs when the workflow has no
          // per-workflow override or can't be resolved.
          try {
            // 1. Check local pack catalog
            const catalog = loadWorkflowPackCatalog();
            const wf = await resolveWorkflow(workflowId, _watchdogSpecRepo, catalog.workflows);
            return wf?.spec?.flowTimeoutMinutes;
          } catch {
            return undefined;
          }
        })
          .then((n) => {
            if (n > 0) console.warn(`[taskguard] flow-timeout watchdog reaped ${n} stuck flow(s) → failed`);
          })
          .catch((e) => { console.error(`[taskguard] flow-timeout watchdog error: ${e?.message ?? e}`); });
      }, FLOW_REAP_INTERVAL_MS);
      _flowTimeoutTimer.unref?.(); // don't prevent process exit
    } else {
      console.info("[taskguard] flow-timeout watchdog: disabled (execution.flowTimeoutMinutes=0) — flows may run to completion");
    }

    // ── Orphaned flow recovery is deferred to buildDeps() which runs when
    // the first session is created. At that point all ControllerDeps are
    // available (executeNode, actionRegistry, abortSignal, chatInject, etc.).
    // See buildDeps() return statement for the recovery trigger.
    // ──

    // Graceful shutdown: stop scheduler and clean up webhook on process signals
    const shutdownScheduler = async () => {
      if (_scheduler?.isRunning()) {
        console.info("[taskguard] Shutting down scheduler...");
        await _scheduler.stop();
      }
    };
    const shutdownWebhook = () => {
      _webhookTriggerStore = null;
      _webhookEventStore = null;
      _webhookEnabled = false;
      if (_webhookCleanupTimer) {
        clearInterval(_webhookCleanupTimer);
        _webhookCleanupTimer = null;
      }
      if (_approvalCardCleanupTimer) {
        clearInterval(_approvalCardCleanupTimer);
        _approvalCardCleanupTimer = null;
      }
      if (_flowTimeoutTimer) {
        clearInterval(_flowTimeoutTimer);
        _flowTimeoutTimer = null;
      }
    };
    process.on("SIGINT", async () => { await _runLogUploader?.flushAll?.(); await shutdownScheduler(); shutdownWebhook(); stopFlowControl(); });
    process.on("SIGTERM", async () => { await _runLogUploader?.flushAll?.(); await shutdownScheduler(); shutdownWebhook(); stopFlowControl(); });

    (api as RegisterHookCapableApi).registerHook("command:stop", (event) => {
      const abortedSync = abortActiveRunsForSession(event.sessionKey);
      const abortedAsync = abortAsyncExecutionsForSession(event.sessionKey);
      const aborted = abortedSync + abortedAsync;
      if (aborted > 0) {
        console.log("[taskguard] command:stop aborted active runs", {
          sessionKey: event.sessionKey,
          sync: abortedSync,
          async: abortedAsync,
        });
      }
    }, {
      name: "clawmind-command-stop",
      description: "Abort active clawmind runs when /stop is issued",
    });

    // Tool path: OpenClaw slash skill forwards raw text here, then dispatchWorkflowCommand owns facade normalization.
    api.registerTool((toolCtx) => ({
      name: "workflow_engine_dispatch",
      label: "Workflow Engine Dispatch",
      description: "通用工作流引擎调度工具。可用命令: run, list, detail, validate, test, debug, state, logs, runs, packs, pack, deploy, pull, rollback, deploys, status, share, unshare, confirm, revise, reject, retry, skip, submit, resume, reopen, schedule, webhook, cutover-check, repair, export, import, help",
      parameters: COMMAND_PARAMETERS,
      async execute(_id, params, signalOrOnUpdate, onUpdateArg) {
        const raw = (params as { command: string }).command;
        const commandName = optionalStringParam(params, "commandName");
        const skillName = optionalStringParam(params, "skillName");
        const sessionKey = toolCtx.sessionKey;
        if (!sessionKey?.trim()) {
          throw new Error("workflow_engine_dispatch requires a sessionKey in tool context");
        }
        // Guard: block "run" commands from inside embedded-agent sessions.
        // When a workflow node's LLM calls workflow_engine_dispatch with a "run"
        // command, it starts a nested workflow that the user never asked for —
        // the LLM reconstructs input (losing/reordering data) and creates
        // unintended child flows. We allow non-run commands (state, logs, etc.)
        // for observability, but block workflow creation from within a node.
        if (sessionKey.includes(":embedded:") && /^\s*run\b/i.test(raw)) {
          throw new Error(
            "workflow_engine_dispatch: 不允许在工作流节点内部通过 run 命令启动子工作流。" +
            "请直接完成当前节点的分析任务，不要调用 workflow_engine_dispatch run。",
          );
        }
        // debug-segment 专用:读兄弟 schema 字段透传上游 context(见 COMMAND_PARAMETERS 说明)。
        const inlineDebugContext = (params as {
          nodeOutput?: Record<string, Record<string, unknown>>;
          workflowData?: Record<string, unknown>;
          input?: Record<string, unknown>;
        });
        const deliveryCtx = toolCtx.deliveryContext as Record<string, unknown> | undefined;
        const onUpdate =
          typeof onUpdateArg === "function"
            ? onUpdateArg
            : typeof signalOrOnUpdate === "function"
              ? signalOrOnUpdate
              : undefined;
        const result = await dispatchWorkflowCommand({
          api,
          sessionKey,
          raw,
          commandName,
          skillName,
          entrypoint: "workflow_engine_dispatch",
          workspaceDir: toolCtx.workspaceDir ?? ".",
          deliveryContext: deliveryCtx,
          onProgress: onUpdate
            ? (text, details) => {
                onUpdate({
                  content: [{ type: "text", text }],
                  details: { status: "running", ...details },
                });
              }
            : undefined,
          abortToolCtx: toolCtx as EmbeddedAgentToolContext,
          ...(inlineDebugContext.nodeOutput || inlineDebugContext.workflowData || inlineDebugContext.input
            ? {
                inlineDebugContext: {
                  nodeOutput: inlineDebugContext.nodeOutput,
                  workflowData: inlineDebugContext.workflowData,
                  input: inlineDebugContext.input,
                },
              }
            : {}),
        });
        return {
          content: [{ type: "text", text: result }],
          details: {},
        };
      },
    }), { name: "workflow_engine_dispatch" });

    // ── workflow_debug_segment tool ──
    // Standalone tool (NOT a workflow_engine_dispatch subcommand): execute a
    // workflow segment starting at `fromNode`, with upstream outputs provided
    // by the caller. Registered here on the OpenClaw plugin path so this works
    // in plugin deployments where only the `workflow_engine_dispatch` tool is
    // whitelisted — the same tool is also registered by registerWorkflowTools
    // on the MCP-server / Hermes-SSE path.
    //
    // Debug execution is side-effect free: we bind the real boundTaskFlow so
    // the executor dispatch works, then OVERRIDE boundTaskFlow + chatInject
    // with no-ops so nothing is persisted to the production TaskFlow and no
    // chat notifications fire.
    api.registerTool((toolCtx) => ({
      name: "workflow_debug_segment",
      label: "Workflow Debug Segment",
      description:
        "调试工作流片段：从指定节点开始执行，上游输出由模型提供。"
        + "用于跳过已验证的上游节点、单独调试某个节点、或跑一段工作流。"
        + "调试执行不写入生产 TaskFlow、不发进度通知，结果仅返回给调用方。",
      parameters: DEBUG_SEGMENT_PARAMETERS,
      async execute(_id, params) {
        const sessionKey = toolCtx.sessionKey;
        if (!sessionKey?.trim()) {
          throw new Error("workflow_debug_segment requires a sessionKey in tool context");
        }
        const p = params as {
          workflowId: string;
          fromNode: string;
          toNode?: string;
          nodeOutput?: Record<string, Record<string, unknown>>;
          workflowData?: Record<string, unknown>;
          input?: Record<string, unknown>;
        };
        const deliveryCtx = toolCtx.deliveryContext as Record<string, unknown> | undefined;

        const action: ControllerAction = {
          action: "debug-segment",
          workflowId: p.workflowId,
          fromNode: p.fromNode,
          toNode: p.toNode,
          nodeOutput: p.nodeOutput ?? {},
          workflowData: p.workflowData,
          input: p.input,
        };

        const result = await executeDebugSegment({
          api,
          sessionKey,
          workflowId: p.workflowId,
          deliveryContext: deliveryCtx,
          action,
        });
        return { content: [{ type: "text", text: result }], details: {} };
      },
    }), { name: "workflow_debug_segment" });

    // ── get_current_user tool ──
    // Returns the current user's identity and channel context.
    // Available to embedded-agent nodes and external skills.
    api.registerTool((toolCtx) => ({
      name: "get_current_user",
      label: "Get Current User",
      description:
        "获取当前工作流执行上下文中的用户身份和渠道信息。" +
        "支持钉钉单聊/群聊、Web自有Bot/他人Bot、BCS群聊等场景。" +
        "返回 senderId、senderName、channel、chatType 等结构化信息。",
      parameters: {
        type: "object",
        properties: {},
        required: [],
      },
      async execute() {
        const sessionKey = (toolCtx.sessionKey as string) ?? "";
        const deliveryCtx = toolCtx.deliveryContext as Record<string, unknown> | undefined;
        const messages = (toolCtx as Record<string, unknown>).messages as unknown[] | undefined;

        // ownerId: try deliveryContext.ownerId, then env CREDENTIALS_OWNER_ID
        const ownerId = String(
          deliveryCtx?.ownerId ??
          process.env.CREDENTIALS_OWNER_ID ??
          "",
        ) || undefined;

        // OpenClaw's tool context doesn't expose the messages array, but it does
        // provide requesterSenderId — the trusted sender id from inbound context.
        // Fold it into the delivery context so resolveUserIdentity's fallback
        // path can surface it when channel detection can't determine the sender.
        const mergedDeliveryCtx =
          toolCtx.requesterSenderId && !deliveryCtx?.user
            ? { ...(deliveryCtx ?? {}), user: { id: toolCtx.requesterSenderId } }
            : deliveryCtx;

        const identity = resolveUserIdentity({
          messages,
          sessionKey,
          ownerId,
          deliveryContext: mergedDeliveryCtx,
          env: process.env as Record<string, string | undefined>,
        });

        return {
          content: [{ type: "text", text: JSON.stringify(identity, null, 2) }],
          details: {},
        };
      },
    }), { name: "get_current_user" });
    // R1: Dedicated workflow_choice tool — more specific than workflow_engine_dispatch,
    // structures the choice action so Agent doesn't need to format command strings.
    api.registerTool((toolCtx) => ({
      name: "workflow_choice",
      label: "Workflow Choice",
      description: "当用户在等待选择方案时，用此工具提交选择。仅当工作流处于人工等待状态时可用。例如用户说\"走快速\"，调用此工具 action=confirm, choice=fast。",
      parameters: CHOICE_PARAMETERS,
      async execute(_id, params, signalOrOnUpdate, onUpdateArg) {
        const { action, choice, note } = params as { action: string; choice?: string; note?: string };
        const sessionKey = toolCtx.sessionKey;
        if (!sessionKey?.trim()) {
          throw new Error("workflow_choice requires a sessionKey in tool context");
        }

        // Translate structured choice to the canonical command format
        let raw: string;
        if (action === "reject") {
          raw = note ? `reject ${note}` : "reject";
        } else {
          raw = choice ? `confirm choice: ${choice}${note ? ` 备注: ${note}` : ""}` : "confirm";
        }

        const deliveryCtx = toolCtx.deliveryContext as Record<string, unknown> | undefined;
        const onUpdate =
          typeof onUpdateArg === "function"
            ? onUpdateArg
            : typeof signalOrOnUpdate === "function"
              ? signalOrOnUpdate
              : undefined;

        const result = await dispatchWorkflowCommand({
          api,
          sessionKey,
          raw,
          commandName: undefined,
          skillName: undefined,
          entrypoint: "workflow_engine_dispatch",
          workspaceDir: toolCtx.workspaceDir ?? ".",
          deliveryContext: deliveryCtx,
          onProgress: onUpdate
            ? (text, details) => {
                onUpdate({
                  content: [{ type: "text", text }],
                  details: { status: "running", ...details },
                });
              }
            : undefined,
          abortToolCtx: toolCtx as EmbeddedAgentToolContext,
        });
        return { content: [{ type: "text", text: result }], details: {} };
      },
    }), { name: "workflow_choice" });

    // ── workflow_submit_phase_result ──
    // Dev-workflow phase callback: BOT calls this tool to submit phase execution
    // results (status, gitOps, artifacts) back to clawweb for persistence and display.
    api.registerTool((_toolCtx) => ({
      name: "workflow_submit_phase_result",
      label: "Submit Phase Result",
      description:
        "将研发工作流阶段的执行结果（代码变更、产物、状态）上报到 clawweb。"
        + "BOT 执行完研发工作流阶段后调用此工具，将 gitOps 和 artifacts 上报到 clawweb 前端可展示。",
      parameters: {
        type: "object",
        properties: {
          workflowId:    { type: "string", description: "研发工作流 ID" },
          phaseId:       { type: "string", description: "阶段 ID" },
          status:        { type: "string", enum: ["success", "failed", "timeout"], description: "阶段执行状态" },
          resultSummary: { type: "string", description: "阶段执行结果摘要" },
          documentUrl:   { type: "string", description: "主要产出文档 URL" },
          documentTitle: { type: "string", description: "主要产出文档标题" },
          error:         { type: "string", description: "错误信息" },
          baasRunId:     { type: "string", description: "BaaS 运行 ID" },
          gitOps: {
            type: "array",
            items: {
              type: "object",
              properties: {
                operation:     { type: "string", enum: ["clone", "pull", "checkout", "commit", "push"] },
                repoUrl:       { type: "string" },
                branch:        { type: "string" },
                commitSha:     { type: "string" },
                commitMessage: { type: "string" },
                remoteBranch:  { type: "string" },
                summary:       { type: "string" },
                result:        { type: "string", enum: ["success", "failed", "timeout"] },
                errorMessage:  { type: "string" },
                executedBy:    { type: "string" },
              },
              required: ["operation", "repoUrl", "branch", "result"],
            },
          },
          artifacts: {
            type: "array",
            items: {
              type: "object",
              properties: {
                artifactType: { type: "string" },
                title:        { type: "string" },
                content:      { type: "string" },
                contentUrl:   { type: "string" },
                format:       { type: "string", enum: ["markdown", "yaml", "json", "html"] },
                source:       { type: "string", enum: ["bot", "human", "imported"] },
                authoredBy:   { type: "string" },
              },
              required: ["artifactType", "title"],
            },
          },
        },
        required: ["workflowId", "phaseId", "status"],
      },
      async execute(_id, params) {
        const action: ControllerAction = {
          action: "dev-workflow-callback",
          params: params as unknown as DevWorkflowCallbackParams,
        };
        // Build minimal ControllerDeps — dev-workflow-callback only needs
        // the handler itself which creates its own ApiClient from env vars.
        // We pass an empty-ish deps since the handler ignores most fields.
        const deps = {
          actionRegistry: null as unknown as import("./actions/types.js").ActionRegistry,
          boundTaskFlow: null as unknown as ControllerDeps["boundTaskFlow"],
          chatInject: () => Promise.resolve(),
          executeNode: null as unknown as ControllerDeps["executeNode"],
          sessionKey: "dev-workflow-callback",
          skillRoot: "",
        } as ControllerDeps;
        const result = await (_extensions?.handleCallback
          ? _extensions.handleCallback(deps, params as unknown as DevWorkflowCallbackParams)
          : handleDevWorkflowCallback(deps, params as unknown as DevWorkflowCallbackParams));
        return { content: [{ type: "text", text: result }], details: {} };
      },
    }), { name: "workflow_submit_phase_result" });

    // ── clawmind_update tool ──
    // Executes install-clawmind.sh to overwrite-install the latest ClawMind
    // plugin package. Triggered when the user says "clawmind update".
    // The script downloads the tgz from OSS, backs up & restores packs/,
    // removes the old install, extracts the new version, and restarts the
    // openclaw gateway.
    api.registerTool((_toolCtx) => ({
      name: "clawmind_update",
      label: "ClawMind Update",
      description:
        "更新 ClawMind 插件到最新版本。执行 install-clawmind.sh 脚本，自动下载最新安装包、备份并恢复 packs 目录、重启 openclaw gateway。"
        + "当用户说\"clawmind update\"或\"更新 clawmind\"时调用此工具。",
      parameters: {
        type: "object",
        properties: {},
        required: [],
      },
      async execute() {
        const result = await handleClawmindUpdate();
        return { content: [{ type: "text", text: result }], details: {} };
      },
    }), { name: "clawmind_update" });

    // ── generate_run_archive tool ──
    api.registerTool((toolCtx) => ({
      name: "generate_run_archive",
      label: "Generate Run Archive",
      description: `为指定的工作流实例生成完整的运行档案。输入 flowId，聚合 flow_runs、node_executions、flow_events、node_step_traces、execution_step_log、run_logs、aw_langfuse_traces、aw_langfuse_observation 等所有相关数据，输出结构化的运行档案 JSON，包含失败汇总和根因提示。适用场景：分析工作流失败原因、为 Agent 自动修 BUG 提供上下文。`,
      parameters: {
        type: "object" as const,
        properties: {
          flowId: {
            type: "string",
            description: "工作流实例 ID (flowId)",
          },
        },
        required: ["flowId"],
      },
      async execute(_id, params) {
        const flowId = (params as { flowId?: string }).flowId;
        if (!flowId?.trim()) {
          return {
            content: [{ type: "text" as const, text: "错误: 缺少 flowId 参数" }],
            details: {},
          };
        }
        if (!_runArchiveBuilder) {
          return {
            content: [{ type: "text" as const, text: "错误: 运行档案构建器未初始化（数据库未配置）" }],
            details: {},
          };
        }
        try {
          const archive = await _runArchiveBuilder.buildArchive(flowId);
          const summary = formatArchiveSummary(archive);
          return {
            content: [{ type: "text" as const, text: summary }],
            details: { archive },
          };
        } catch (error) {
          const msg = error instanceof Error ? error.message : String(error);
          return {
            content: [{ type: "text" as const, text: `档案构建失败: ${msg}` }],
            details: {},
          };
        }
      },
    }), { name: "generate_run_archive" });

    //
    // Two sources of compression config:
    //   1. Per-session: registered by executeEmbeddedAgent via registerSessionCompressionConfig
    //   2. Global fallback: from application.yaml sessionCompression defaults
    //
    // When no per-session entry is found (e.g., subagent sessions, main agent),
    // the global config is used so compression applies to ALL sessions.

    // ── IMPORTANT: Hook availability ──
    // The `before_agent_start` hook is a legacy/deprecated hook in OpenClaw and
    // is NOT reliably routed to plugins via the plugin SDK. It may fire in the
    // runtime but the plugin handler is NOT invoked. The `before_prompt_build`
    // hook IS reliably delivered and includes messages/token data, making it
    // the primary compression hook. We keep `before_agent_start` registered as
    // a fallback but the real compression work happens in `before_prompt_build`.

    // 1. before_agent_start: diagnostic fallback (legacy hook — may not fire for plugins)
    //    If the runtime DOES deliver this hook, we use it to store the token
    //    estimate for before_prompt_build. It also attempts file compression as
    //    a bonus, but the primary compression path is before_prompt_build.
    api.on("before_agent_start", async (event, ctx) => {
      const sessionKey = (ctx as Record<string, unknown>).sessionKey as string | undefined;
      if (!sessionKey) {
        console.log(
          `[session-watch-compressor] before_agent_start: no sessionKey in context, skipping. ` +
          `contextKeys=${Object.keys(ctx).join(",")}`,
        );
        return;
      }

      const entry = getSessionCompressionEntry(sessionKey);
      if (!entry) {
        console.log(
          `[session-watch-compressor] before_agent_start: no per-session entry for sessionKey=${sessionKey}, skipping. ` +
          `(This is expected for non-embedded-agent sessions — use before_prompt_build instead.)`,
        );
        return;
      }

      const { sessionFile, config, onCompress, debounce } = entry;
      if (config.promptBuildEnabled === false) return;

      const sc = config.sessionCompression ?? {};
      const minTokensToCompact = sc.minTokensToCompact ?? 10_000;
      const maxSessionTokens = sc.maxSessionTokens ?? 50_000;

      // ── Threshold check: estimate tokens from session file ──
      // See session-watch-compressor.ts for rationale: event.prompt/messages
      // miss systemPrompt (~11K tokens) and tool definitions (~8K tokens),
      // leading to severe underestimation. Session file size is the right metric,
      // but must account for JSON overhead (~88% of file is non-message content).
      const FILE_SIZE_TOKEN_DIVISOR_BAS = 12;
      const barEvent = event as unknown as { prompt?: string; messages?: unknown[] };
      let estimatedTokens = 0;
      let estimationSource = "none";

      // 1. Session file size — primary estimation source for compression threshold
      if (sessionFile) {
        try {
          const info = await fsStat(sessionFile);
          if (info.size > 0) {
            estimatedTokens = Math.ceil(info.size / FILE_SIZE_TOKEN_DIVISOR_BAS);
            estimationSource = "session-file";
          } else {
            console.log(
              `[session-watch-compressor] before_agent_start: sessionFile exists but size=0, ` +
              `session may have just started`,
            );
          }
        } catch (err) {
          console.log(
            `[session-watch-compressor] before_agent_start: stat failed for sessionFile=${sessionFile}: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }

      // 2. Messages-based estimation
      const messages = barEvent.messages;
      if (estimatedTokens <= 0 && messages && Array.isArray(messages)) {
        const usage = estimateTokenUsageFromMessages(messages);
        estimatedTokens = usage?.totalTokens ?? 0;
        if (estimatedTokens > 0) estimationSource = "messages";
      }

      // 3. Prompt text fallback (first LLM call: messages=[] but prompt has content)
      if (estimatedTokens <= 0 && barEvent.prompt && typeof barEvent.prompt === "string") {
        estimatedTokens = estimateTextTokens(barEvent.prompt);
        if (estimatedTokens > 0) estimationSource = "prompt";
      }

      console.log(
        `${compressionLogTag(entry)} before_agent_start: sessionKey=${sessionKey}, ` +
        `estimatedTokens=${estimatedTokens}, minTokensToCompact=${minTokensToCompact}, ` +
        `hasSessionFile=${!!sessionFile}, source=${estimationSource}`,
      );

      // Store the token estimate for before_prompt_build to read.
      entry.lastEstimatedTokens = estimatedTokens;

      // Context is within budget — skip compression.
      if (estimatedTokens < minTokensToCompact) return;

      if (!sessionFile) return;

      try {
        const result = await maybeCompactSessionFileSafe(sessionFile, {
          maxSessionTokens,
          minTokensToCompact: 0, // always try since we already know context exceeds threshold
          recencyWindow: sc.recencyWindow ?? 6,
          toolPrepassEnabled: sc.toolPrepassEnabled ?? true,
          toolResultMaxChars: sc.toolResultMaxChars ?? 5000,
          deduplicateReads: sc.deduplicateReads ?? true,
          readDedupTtlMs: sc.readDedupTtlMs ?? 300_000,
        });

        if (result.kind === "compressed") {
          const now = Date.now();
          debounce.lastCompressionTime = now;
          debounce.lastCompressionStats = result.stats;
          debounce.lastSidecarPath = result.sidecarPath;
          console.log(
            `${compressionLogTag(entry)} before_agent_start: compressed session file (sidecar), ` +
            `${result.stats.inputTokens}→${result.stats.outputTokens} tokens, ` +
            `sidecar=${result.sidecarPath}`,
          );
          onCompress?.({ ...result.stats, phase: "beforeAgentStart" });
        } else if (result.kind === "skipped") {
          const reason = result.reason;
          const inTok = result.inputTokens;
          const inMsg = result.inputMessages;
          console.log(
            `${compressionLogTag(entry)} before_agent_start: compression skipped (reason=${reason}, ` +
            `inputTokens=${inTok ?? "n/a"}, inputMessages=${inMsg ?? "n/a"})`,
          );
        } else if (result.kind === "error") {
          console.warn(`${compressionLogTag(entry)} before_agent_start compression error: ${result.error}`);
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`${compressionLogTag(entry)} before_agent_start compression failed: ${msg}`);
      }
    });

    // 2. before_prompt_build: PRIMARY compression hook — does file I/O + notice injection
    //
    // This hook fires before every LLM call in the agent loop. It:
    //   a) Estimates tokens from the event messages
    //   b) If over threshold, compresses the session file (debounced)
    //   c) Injects a compaction notice via prependContext if messages were evicted
    //
    // This is the reliable compression path because `before_prompt_build` is
    // properly routed to plugins by the OpenClaw runtime.
    //
    // Per-session entries (from executeEmbeddedAgent) take priority.
    // If no per-session entry exists, we fall back to the global application
    // config (application.yaml sessionCompression) so that subagent sessions
    // and the main agent session also benefit from compression.
    api.on("before_prompt_build", async (event, ctx) => {
      const sessionKey = (ctx as Record<string, unknown>).sessionKey as string | undefined;
      const sessionId = (ctx as Record<string, unknown>).sessionId as string | undefined;
      const agentId = (ctx as Record<string, unknown>).agentId as string | undefined;
      const bpbEvent = event as unknown as { prompt?: string; messages?: unknown[]; estimatedTokens?: number };
      const msgCount = bpbEvent.messages && Array.isArray(bpbEvent.messages) ? bpbEvent.messages.length : 0;

      // Log hook firing for debugging compression lifecycle
      console.log(
        `[session-watch-compressor] before_prompt_build FIRED: ` +
        `sessionKey=${sessionKey ?? "(none)"}, sessionId=${sessionId ?? "(none)"}, agentId=${agentId ?? "(none)"}, ` +
        `estimatedTokens=${bpbEvent.estimatedTokens ?? "n/a"}, msgCount=${msgCount}, ` +
        `contextKeys=${Object.keys(ctx).join(",")}`,
      );

      if (!sessionKey) {
        console.log(
          `[session-watch-compressor] before_prompt_build: no sessionKey in context, skipping.`,
        );
        return;
      }

      // before_prompt_build compresses ONLY embedded-agent sessions, which have a
      // registered per-session entry. main/sub (non-embedded-agent) sessions have
      // no entry here, and this hook leaves their active session JSONL untouched —
      // the earlier global-fallback compression path is intentionally disabled so
      // before_prompt_build can never rewrite or sidecar the main/sub session file.
      // (See docs/specs/session-rewrite-fix.md §3.2.4, §5.)
      const entry = getSessionCompressionEntry(sessionKey);
      if (!entry) {
        console.log(
          `[session-watch-compressor] before_prompt_build: no per-session entry for sessionKey=${sessionKey} ` +
          `(non-embedded-agent / main-sub session) — before_prompt_build compression is scoped to embedded-agent sessions only, skipping.`,
        );
        return;
      }
      console.log(
        `[session-watch-compressor] before_prompt_build: found per-session entry for sessionKey=${sessionKey}, ` +
        `sessionFile=${entry.sessionFile}`,
      );

      const { sessionFile, config, debounce } = entry;
      if (config.promptBuildEnabled === false) return;

      const sc = config.sessionCompression ?? {};
      const minTokensToCompact = sc.minTokensToCompact ?? 10_000;
      const maxSessionTokens = sc.maxSessionTokens ?? 50_000;
      const cooldownMs = config.compressionCooldownMs ?? 30_000;
      const injectNotice = config.injectCompactionNotice ?? true;
      const onCompress = config.onCompress;

      // ── Threshold check: estimate tokens from session file ──
      // See session-watch-compressor.ts for rationale: event.prompt/messages
      // miss systemPrompt (~11K tokens) and tool definitions (~8K tokens),
      // leading to severe underestimation. Session file size is the right metric.
      // bpbEvent already extracted at top of handler
      let estimatedTokens = 0;
      let estimationSource = "none";

      // 1. Session file size — primary estimation source for compression threshold
      // JSONL session files contain ~12% actual message content and ~88% JSON overhead
      // (metadata: id, parentId, timestamp; non-message entries: session, model_change, etc.).
      // Using /4 (raw text ratio) overestimates by ~8x. Using /12 better matches
      // actual message token counts observed in production (~31 bytes per token).
      const FILE_SIZE_TOKEN_DIVISOR = 12;
      if (sessionFile) {
        try {
          const info = await fsStat(sessionFile);
          if (info.size > 0) {
            estimatedTokens = Math.ceil(info.size / FILE_SIZE_TOKEN_DIVISOR);
            estimationSource = "session-file";
          } else {
            // File exists but is empty — embedded-agent may have just created/truncated it.
            // Use the estimate from before_agent_start if available.
            console.log(
              `[session-watch-compressor] before_prompt_build: sessionFile exists but size=0, ` +
              `trying entry.lastEstimatedTokens=${entry.lastEstimatedTokens ?? "none"}`,
            );
          }
        } catch (err) {
          console.log(
            `[session-watch-compressor] before_prompt_build: stat failed for sessionFile=${sessionFile}: ${err instanceof Error ? err.message : String(err)}`,
          );
        }
      }

      // 2. before_agent_start cached estimate (reliable when file is empty at prompt-build time)
      if (estimatedTokens <= 0 && entry.lastEstimatedTokens && entry.lastEstimatedTokens > 0) {
        estimatedTokens = entry.lastEstimatedTokens;
        estimationSource = "cached-agent-start";
      }

      // 3. Runtime-provided estimate (only if file-size and cached estimates failed)
      if (estimatedTokens <= 0 && bpbEvent.estimatedTokens && bpbEvent.estimatedTokens > 0) {
        estimatedTokens = bpbEvent.estimatedTokens;
        estimationSource = "runtime";
      }

      // 4. Messages-based estimation
      if (estimatedTokens <= 0 && bpbEvent.messages && Array.isArray(bpbEvent.messages)) {
        const usage = estimateTokenUsageFromMessages(bpbEvent.messages as unknown[]);
        estimatedTokens = usage?.totalTokens ?? 0;
        if (estimatedTokens > 0) estimationSource = "messages";
      }

      // 5. Prompt text fallback (first LLM call: messages=[] but prompt has content)
      if (estimatedTokens <= 0 && bpbEvent.prompt && typeof bpbEvent.prompt === "string") {
        estimatedTokens = estimateTextTokens(bpbEvent.prompt);
        if (estimatedTokens > 0) estimationSource = "prompt";
      }

      const needsCompression = estimatedTokens >= minTokensToCompact;
      console.log(
        `${compressionLogTag(entry)} before_prompt_build: sessionKey=${sessionKey}, ` +
        `estimatedTokens=${estimatedTokens}, minTokensToCompact=${minTokensToCompact}, ` +
        `maxSessionTokens=${maxSessionTokens}, ` +
        `needsCompression=${needsCompression}, hasSessionFile=${!!sessionFile}, ` +
        `source=per-session, estimationSource=${estimationSource}`,
      );

      // Store token estimate for next before_prompt_build call (cooldown reuse)
      entry.lastEstimatedTokens = estimatedTokens;

      // Context within budget — skip compression, but still inject notice if we
      // recently compressed (the file change takes effect on the next agent turn).
      if (!needsCompression) {
        console.log(
          `${compressionLogTag(entry)} before_prompt_build: SKIP (under budget), ` +
          `estimatedTokens=${estimatedTokens} < minTokensToCompact=${minTokensToCompact}`,
        );
        config.onHookEvent?.({
          hook: "before_prompt_build",
          action: "skip",
          detail: `under budget: ${estimatedTokens} < ${minTokensToCompact}`,
          data: { estimatedTokens, minTokensToCompact, maxSessionTokens, estimationSource },
        });
        if (
          injectNotice
          && debounce.lastCompressionStats
          && debounce.lastCompressionStats.messagesEvicted > 0
        ) {
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
        return;
      }

      // ── Debounce: avoid redundant file I/O on rapid successive LLM calls ──
      const now = Date.now();
      const timeSinceLastCompression = now - debounce.lastCompressionTime;
      const shouldCompressFile = sessionFile && timeSinceLastCompression >= cooldownMs;

      console.log(
        `${compressionLogTag(entry)} before_prompt_build: DEBOUNCE CHECK, ` +
        `shouldCompressFile=${shouldCompressFile}, ` +
        `timeSinceLastCompress=${timeSinceLastCompression}ms, cooldownMs=${cooldownMs}ms, ` +
        `hasSessionFile=${!!sessionFile}, lastCompressStats=${debounce.lastCompressionStats ? "present" : "none"}`,
      );

      // If within cooldown, inject cached notice but skip file compression
      if (!shouldCompressFile) {
        if (
          injectNotice
          && debounce.lastCompressionStats
          && debounce.lastCompressionStats.messagesEvicted > 0
        ) {
          console.log(
            `[session-watch-compressor] before_prompt_build: cooldown skip, injecting cached prependContext, ` +
            `timeSinceLastCompress=${timeSinceLastCompression}ms < cooldownMs=${cooldownMs}ms`,
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

        // Within cooldown, no prior compression stats: inject generic budget notice
        if (injectNotice && estimatedTokens >= maxSessionTokens) {
          console.log(
            `[session-watch-compressor] before_prompt_build: COOLDOWN + OVER BUDGET, injecting generic budget notice, ` +
            `estimatedTokens=${estimatedTokens} >= maxSessionTokens=${maxSessionTokens}`,
          );
          return {
            prependContext: [
              `[上下文压缩通知]`,
              `当前上下文约 ${estimatedTokens} tokens，已超过预算 ${maxSessionTokens} tokens。`,
              `会话历史中较旧的冗长工具输出将在下次加载时被截断或移除。`,
              `如需查看被压缩的细节，请参考节点执行日志。`,
            ].join("\n"),
          };
        }
        console.log(
          `[session-watch-compressor] before_prompt_build: COOLDOWN SKIP (no prior stats, within budget), returning empty`,
        );
        return;
      }

      // ── File compression: over threshold + outside cooldown ──
      // Only embedded-agent sessions reach this point (main/sub sessions
      // early-return above). Embedded-agent entries use in-place compression,
      // which rewrites the session file (unchanged behavior, see
      // docs/specs/session-rewrite-fix.md §5).
      console.log(
        `${compressionLogTag(entry)} before_prompt_build: COMPRESSING session file, ` +
        `sessionFile=${sessionFile}, estimatedTokens=${estimatedTokens}, maxSessionTokens=${maxSessionTokens}, ` +
        `mode=inplace`,
      );
      const compactConfig = {
        maxSessionTokens,
        minTokensToCompact: 0, // always try since we already know context exceeds threshold
        recencyWindow: sc.recencyWindow ?? 6,
        toolPrepassEnabled: sc.toolPrepassEnabled ?? true,
        toolResultMaxChars: sc.toolResultMaxChars ?? 5000,
        deduplicateReads: sc.deduplicateReads ?? true,
        readDedupTtlMs: sc.readDedupTtlMs ?? 300_000,
      };
      try {
        // Use the SAFE sidecar variant — compressed output goes to
        // <file>.compressed.jsonl, original JSONL is never modified.
        // This preserves the full tool_call/tool_result history so that
        // extractNodeStepTrace can read the complete session after the
        // node completes. See docs/openspec/step-trace-compression-fix/.
        const compactOutcome = await maybeCompactSessionFileSafe(sessionFile, compactConfig);

        if (compactOutcome.kind === "compressed") {
          debounce.lastCompressionTime = now;
          debounce.lastCompressionStats = compactOutcome.stats;
          debounce.lastSidecarPath = compactOutcome.sidecarPath;
          console.log(
            `${compressionLogTag(entry)} before_prompt_build: compressed session file (sidecar), ` +
            `${compactOutcome.stats.inputTokens}→${compactOutcome.stats.outputTokens} tokens, ` +
            `messagesEvicted=${compactOutcome.stats.messagesEvicted}, ` +
            `toolsCompressed=${compactOutcome.stats.toolResultsCompressed}, ` +
            `sidecar=${compactOutcome.sidecarPath}`,
          );
          config.onHookEvent?.({
            hook: "before_prompt_build",
            action: "compressed",
            detail: `${compactOutcome.stats.inputTokens}→${compactOutcome.stats.outputTokens} tokens, evicted=${compactOutcome.stats.messagesEvicted}`,
            data: { ...compactOutcome.stats },
          });
          onCompress?.({ ...compactOutcome.stats, phase: "beforePromptBuild" });
        } else if (compactOutcome.kind === "skipped") {
          console.log(
            `${compressionLogTag(entry)} before_prompt_build: compression skipped (reason=${compactOutcome.reason}, ` +
            `inputTokens=${compactOutcome.inputTokens ?? "n/a"}, inputMessages=${compactOutcome.inputMessages ?? "n/a"})`,
          );
        } else if (compactOutcome.kind === "error") {
          console.warn(
            `${compressionLogTag(entry)} before_prompt_build: compression error (${compactOutcome.error})`,
          );
        }

        // Inject compaction notice if messages were evicted
        if (injectNotice && compactOutcome.kind === "compressed" && compactOutcome.stats.messagesEvicted > 0) {
          const stats = compactOutcome.stats;
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

        // Context exceeds budget but file compression didn't help: inject generic notice
        if (injectNotice && estimatedTokens >= maxSessionTokens) {
          return {
            prependContext: [
              `[上下文压缩通知]`,
              `当前上下文约 ${estimatedTokens} tokens，已超过预算 ${maxSessionTokens} tokens。`,
              `会话历史中较旧的冗长工具输出将在下次加载时被截断或移除。`,
              `如需查看被压缩的细节，请参考节点执行日志。`,
            ].join("\n"),
          };
        }
      } catch (error) {
        const msg = error instanceof Error ? error.message : String(error);
        console.warn(`[session-watch-compressor] before_prompt_build compression failed: ${msg}`);
      }

      return;
    });

    // 2b. llm_output: real-time API token feedback for session compression
    //
    // Each LLM call inside an agent loop fires llm_output with the actual token
    // usage (input + cacheRead). This is the ONLY reliable source of actual
    // prompt size because:
    //   - Session-file-based estimates capture only ~10% of the prompt
    //     (they exclude system prompt, tool definitions, skill content).
    //   - This hook is the ONLY caller of updateSessionActualTokenEstimate().
    //
    // By feeding actual tokens into the compression registry HERE, the
    // subsequent tool_result_persist hook (which fires each turn) can make
    // accurate budgetRatio decisions and trigger compression mid-loop.
    api.on("llm_output", (event, ctx) => {
      const loCtx = ctx as unknown as { sessionKey?: string };
      const sessionKey = loCtx.sessionKey;
      if (!sessionKey) return;

      const loEvent = event as unknown as {
        usage?: { input?: number; cacheRead?: number };
        provider?: string;
        model?: string;
      };
      const usage = loEvent.usage;
      if (!usage) return;

      const rawInput = usage.input ?? 0;
      const cacheRead = usage.cacheRead ?? 0;
      const effectiveTotal = rawInput + cacheRead;
      if (effectiveTotal <= 0) return;

      // For compression decisions, use rawInput (fresh tokens sent to model).
      // cacheRead tokens are served from the provider's prefix cache and do NOT
      // occupy extra context window — they represent previously cached prefix,
      // not additional prompt content. Using the sum (rawInput + cacheRead)
      // would inflate budgetRatio and trigger unnecessary compression.
      // We still report the combined value for observability.
      const tokensForCompression = rawInput > 0 ? rawInput : effectiveTotal;

      // Record call history for cacheRead diagnostics
      const entry0 = getSessionCompressionEntry(sessionKey);
      let sessionFileTokens = 0;
      if (entry0?.sessionFile) {
        try {
          const info = statSync(entry0.sessionFile);
          sessionFileTokens = Math.ceil(info.size / 12);
        } catch { /* ignore */ }
      }
      if (entry0) {
        const callIndex = entry0.llmCallHistory.length + 1;
        const record = { callIndex, input: rawInput, cacheRead, effective: effectiveTotal, sessionFileTokens, timestamp: Date.now() };
        entry0.llmCallHistory.push(record);
        if (entry0.llmCallHistory.length > 20) entry0.llmCallHistory.shift();

        // Diagnostic: analyze cacheRead pattern to determine if it occupies context window
        // If cacheRead occupies context: input+cacheRead ≈ constant (context window size)
        // If cacheRead is external cache: input grows with session, cacheRead stays constant
        const history = entry0.llmCallHistory;
        if (history.length >= 2) {
          const prev = history[history.length - 2];
          const curr = record;
          const inputDelta = curr.input - prev.input;
          const cacheReadDelta = curr.cacheRead - prev.cacheRead;
          const effectiveDelta = curr.effective - prev.effective;
          const sessionDelta = curr.sessionFileTokens - prev.sessionFileTokens;
          console.log(
            `${compressionLogTag(entry0)} llm_output CACHE_DIAG: call#${callIndex}, ` +
            `inputΔ=${inputDelta > 0 ? "+" : ""}${inputDelta}, ` +
            `cacheReadΔ=${cacheReadDelta > 0 ? "+" : ""}${cacheReadDelta}, ` +
            `effectiveΔ=${effectiveDelta > 0 ? "+" : ""}${effectiveDelta}, ` +
            `sessionFileΔ=${sessionDelta > 0 ? "+" : ""}${sessionDelta} tokens, ` +
            `pattern=${
              Math.abs(cacheReadDelta) < 100 && inputDelta > 0
                ? "INPUT_GROWS_CACHE_STABLE→cacheRead=external_cache"
                : cacheReadDelta < -100 && inputDelta > 100
                  ? "INPUT_UP_CACHE_DOWN→cacheRead=occupies_window"
                  : Math.abs(effectiveDelta) < 500
                    ? "EFFECTIVE_STABLE→cacheRead=occupies_window"
                    : "INCONCLUSIVE"
            }`,
          );
        }
      }

      console.log(
        `${compressionLogTag(entry0)} llm_output: sessionKey=${sessionKey}, ` +
        `input=${rawInput}, cacheRead=${cacheRead}, effective=${effectiveTotal}, ` +
        `tokensForCompression=${tokensForCompression}, sessionFileTokens=${sessionFileTokens}`,
      );

      updateSessionActualTokenEstimate(sessionKey, tokensForCompression);

      // Trigger async session file compression immediately when over budget.
      // This ensures compression begins as soon as we KNOW the prompt is too
      // large, rather than waiting for the next tool_result_persist. The
      // compressed file will be used by the NEXT LLM call in the agent loop.
      const entry = getSessionCompressionEntry(sessionKey);
      if (!entry) return;

      const sc = entry.config.sessionCompression ?? {};
      const minTokensToCompact = sc.minTokensToCompact ?? 30000;
      const maxSessionTokens = sc.maxSessionTokens ?? 50000;
      const contextBudget = entry.config.contextTokenBudget ?? maxSessionTokens;
      const budgetRatio = contextBudget > 0 ? tokensForCompression / contextBudget : 0;

      // Only compress if over the compaction threshold AND budget ratio
      // exceeds 50% (avoid compressing tiny sessions that happen to exceed
      // minTokensToCompact but are still well within budget).
      const shouldCompress = tokensForCompression >= minTokensToCompact && budgetRatio >= 0.5;
      if (!shouldCompress) return;

      const sessionFile = entry.sessionFile;
      if (!sessionFile) return;

      // Fire-and-forget: compress the session file for the next LLM call.
      // Debounce: skip if compressed within the last 30 seconds.
      const now = Date.now();
      const lastCompressMs = entry.debounce?.lastCompressionTime ?? 0;
      if (now - lastCompressMs < 30_000) return;

      console.log(
        `${compressionLogTag(entry)} llm_output: triggering async session compression, ` +
        `tokensForCompression=${tokensForCompression}, budgetRatio=${(budgetRatio * 100).toFixed(1)}%, ` +
        `input=${rawInput}, cacheRead=${cacheRead}, sessionFile=${sessionFile}`,
      );
      maybeCompactSessionFileSafe(sessionFile, {
        maxSessionTokens,
        minTokensToCompact,
        recencyWindow: sc.recencyWindow ?? 6,
        toolPrepassEnabled: sc.toolPrepassEnabled ?? true,
        toolResultMaxChars: entry.config.toolResultMaxChars ?? 5000,
        deduplicateReads: sc.deduplicateReads ?? true,
        readDedupTtlMs: sc.readDedupTtlMs ?? 300_000,
        insertCompactionNotice: sc.insertCompactionNotice ?? true,
        // Skip file-size heuristic: actual API tokens already confirm over-budget.
        // The session file itself may look small because most tokens come from
        // system prompt / tool definitions / skill content, not the JSONL file.
        skipSizeHeuristic: true,
        // Pass actual API token counts so maybeCompactSessionFileSafe can calculate
        // a tighter effective budget that accounts for non-session overhead.
        // Without this, the compressor sees session tokens < maxSessionTokens
        // and skips ("under-budget"), even though the total prompt is way over.
        actualPromptTokens: tokensForCompression,
        contextTokenBudget: contextBudget,
        modelContextWindow: entry.config.modelContextWindow ?? contextBudget,
      }).then((result) => {
        if (result.kind === "compressed") {
          const stats = result.stats;
          console.log(
            `${compressionLogTag(entry)} llm_output: async compression completed (sidecar), ` +
            `${stats.inputTokens}→${stats.outputTokens} tokens, ` +
            `ratio=${stats.compressionRatio.toFixed(2)}, evicted=${stats.messagesEvicted}, ` +
            `sidecar=${result.sidecarPath}`,
          );
          if (entry.debounce) {
            entry.debounce.lastCompressionTime = Date.now();
            entry.debounce.lastSidecarPath = result.sidecarPath;
          }
          entry.config.onHookEvent?.({
            hook: "llm_output",
            action: "compressed",
            detail: `${stats.inputTokens}→${stats.outputTokens} tokens, evicted=${stats.messagesEvicted}`,
            data: { ...stats, tokensForCompression, rawInput, cacheRead },
          });
        } else if (result.kind === "skipped") {
          const contextBudget = entry.config.contextTokenBudget ?? maxSessionTokens;
          const modelWindow = entry.config.modelContextWindow ?? contextBudget;
          console.log(
            `${compressionLogTag(entry)} llm_output: compression skipped (reason=${result.reason}), ` +
            `sessionTokens=${result.inputTokens ?? "n/a"}, ` +
            `contextBudget=${contextBudget}, modelWindow=${modelWindow}, ` +
            `tokensForCompression=${tokensForCompression}`,
          );
          entry.config.onHookEvent?.({
            hook: "llm_output",
            action: "skip",
            detail: `compression skipped: ${result.reason}`,
            data: {
              reason: result.reason,
              sessionTokens: result.inputTokens,
              contextBudget,
              modelWindow,
              tokensForCompression,
              rawInput,
              cacheRead,
            },
          });
        } else {
          console.log(
            `${compressionLogTag(entry)} llm_output: compression error: ${result.error}`,
          );
        }
      }).catch((err) => {
        console.error(
          `${compressionLogTag(entry)} llm_output: async compression error:`,
          err instanceof Error ? err.message : err,
        );
      });
    });

    // 3. tool_result_persist: truncate tool outputs + mid-turn session compression
    //
    // This is a per-turn hook that fires after each tool call during multi-turn
    // embedded-agent conversations.  combined with the llm_output hook above,
    // it has access to actual API token counts and can make accurate compression
    // decisions mid-loop.
    //
    // Strategy:
    //  a) Budget-aware dynamic truncation — reduce toolResultMaxChars as the
    //     session approaches maxSessionTokens, keeping per-message contribution
    //     small when the budget is tight.
    //  b) After each tool result, check the session file size and update
    //     entry.lastEstimatedTokens so subsequent hooks see an up-to-date estimate.
    //  c) If the file exceeds minTokensToCompact, trigger async session file
    //     compression (fire-and-forget).  This doesn't change the in-memory
    //     context for the CURRENT run, but prepares a compressed file for the
    //     NEXT runEmbeddedPiAgent call (e.g. JSON repair retry).
    //  d) Log budget warnings so operators can see how close to the limit.
    api.on("tool_result_persist", (event, ctx) => {
      // The PluginApi type inference doesn't carry the proper overload types
      // for api.on(); it resolves to the before_agent_reply signature. Cast
      // to access tool_result_persist-specific fields.
      const trpEvent = event as unknown as { toolName?: string; toolCallId?: string; message: unknown; isSynthetic?: boolean };
      const trpCtx = ctx as unknown as { agentId?: string; sessionKey?: string; toolName?: string; toolCallId?: string };

      const sessionKey = trpCtx.sessionKey;
      if (!sessionKey) return;
      const entry = getSessionCompressionEntry(sessionKey);
      if (!entry) return;

      const { config } = entry;
      const enabled = config.toolPersistEnabled ?? true;
      if (!enabled) return;

      const sc = config.sessionCompression ?? {};
      const maxSessionTokens = sc.maxSessionTokens ?? 50000;
      const minTokensToCompact = sc.minTokensToCompact ?? 30000;

      // ── Budget-aware dynamic truncation ──
      // As the session approaches the context token budget, reduce the
      // per-result truncation threshold to keep each message's contribution
      // small. Uses the ACTUAL API-reported token count (lastActualInputTokens)
      // when available, because the session-file-based estimate
      // (lastEstimatedTokens) captures only ~10% of the prompt — it excludes
      // system prompt, tool definitions, and skill content.
      const baseMaxChars = config.toolResultMaxChars ?? 5000;
      const actualInputTokens = entry.lastActualInputTokens;
      const estimatedTokens = entry.lastEstimatedTokens ?? 0;
      // Use actual API-reported tokens if available; otherwise fall back
      // to the session-file-based estimate (which severely underestimates).
      const effectiveInputTokens = actualInputTokens ?? estimatedTokens;
      // The budget denominator should be the model's context window, not
      // the session compression budget. Falls back to maxSessionTokens.
      const contextBudget = config.contextTokenBudget ?? maxSessionTokens;
      const budgetRatio = contextBudget > 0 ? effectiveInputTokens / contextBudget : 0;

      let effectiveMaxChars = baseMaxChars;
      if (budgetRatio >= 0.9) {
        // 90%+ of context — minimal content allowed
        effectiveMaxChars = Math.min(baseMaxChars, 500);
      } else if (budgetRatio >= 0.75) {
        // 75-90% of context — aggressive truncation
        effectiveMaxChars = Math.min(baseMaxChars, 1500);
      } else if (budgetRatio >= 0.5) {
        // 50-75% of context — moderate truncation
        effectiveMaxChars = Math.min(baseMaxChars, 3000);
      } else if (budgetRatio >= 0.1) {
        // 10-50% of context — light truncation
        // With 200K context, this activates at ~20K tokens — covers most
        // multi-turn data-preprocessing runs (23K-160K effective input).
        effectiveMaxChars = Math.min(baseMaxChars, 4000);
      }

      const toolName = trpEvent.toolName ?? trpCtx.toolName;
      const message = trpEvent.message as Record<string, unknown> | null;
      if (!message || typeof message !== "object") return;
      if (trpEvent.isSynthetic) return;

      // ── 无损 sidecar 双写(评测 transcript,默认启用) ──
      // 在下面任何截断之前捕获原始的、未截断的消息。
      // 对每条非合成结果(含错误)都触发,让 sidecar 成为完整镜像。
      // 无配置门控;只要有 sessionFile 就写。绝不阻塞或抛错。
      if (entry.sessionFile) {
        const sidecarToolCallId =
          trpEvent.toolCallId ??
          trpCtx.toolCallId ??
          (message.tool_use_id as string | undefined);
        sidecarWriter.append(entry.sessionFile, {
          ts: Date.now(),
          toolCallId: sidecarToolCallId,
          toolName,
          isError: message.is_error as boolean | undefined,
          message,
        });
      }

      const isError = message.is_error as boolean | undefined;
      if (isError) return;

      const content = message.content as string | Array<Record<string, unknown>> | undefined;
      if (!content) return;

      // ── Truncate tool result content ──
      let truncatedMessage: Record<string, unknown> | null = null;

      // Handle string content
      if (typeof content === "string") {
        if (content.length <= effectiveMaxChars) {
          // Content fits within budget — no truncation needed, but still
          // proceed to session file size check below.
        } else {
          const boundary = findTruncationBoundary(content, effectiveMaxChars);
          const toolLabel = toolName ? `工具 ${toolName} 输出` : "工具输出";
          const truncated = content.slice(0, boundary) +
            `\n\n[... ${toolLabel}已截断，原始长度 ${content.length.toLocaleString()} 字符，保留前 ${boundary.toLocaleString()} 字符 ...]`;
          console.log(
            `${compressionLogTag(entry)} tool_result_persist: truncated tool=${toolName ?? "unknown"}, ` +
            `${content.length}→${truncated.length} chars, ` +
            `effectiveMaxChars=${effectiveMaxChars} (budgetRatio=${(budgetRatio * 100).toFixed(0)}%)`,
          );
          truncatedMessage = { ...message, content: truncated };
        }
      }

      // Handle array content blocks
      if (Array.isArray(content)) {
        let totalChars = 0;
        for (const block of content) {
          if (typeof block === "object" && block !== null && block.type === "text" && typeof block.text === "string") {
            totalChars += block.text.length;
          }
        }
        if (totalChars > effectiveMaxChars * 2) {
          let remaining = effectiveMaxChars;
          const truncated = content.map((block) => {
            if (typeof block === "object" && block !== null && block.type === "text" && typeof block.text === "string") {
              if (remaining <= 0) {
                return { ...block, text: `[... 输出已省略，超出压缩预算 ...]` };
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

          console.log(
            `${compressionLogTag(entry)} tool_result_persist: truncated array content, ` +
            `tool=${toolName ?? "unknown"}, totalTextChars=${totalChars}, ` +
            `effectiveMaxChars=${effectiveMaxChars} (budgetRatio=${(budgetRatio * 100).toFixed(0)}%)`,
          );
          truncatedMessage = { ...message, content: truncated };
        }
      }

      // ── Mid-turn session file size check + async compression ──
      // This is the only per-turn hook, so we update the session file estimate
      // here and trigger async compression if over budget. Note: we use the
      // session-file-based estimate for COMPRESSION threshold decisions (it
      // measures the session file content), and the API-reported actual tokens
      // for BUDGET RATIO decisions (it reflects the full prompt).
      const sessionFile = entry.sessionFile;
      if (sessionFile) {
        // Fire-and-forget: check file size, update estimate, maybe compress.
        // We don't await because tool_result_persist is synchronous and the
        // result must be returned immediately.  Compression is for the NEXT
        // runEmbeddedPiAgent call, not the current one.
        (async () => {
          try {
            const info = await fsStat(sessionFile);
            const FILE_SIZE_TOKEN_DIVISOR_TRP = 12;
            const newFileEstimate = info.size > 0 ? Math.ceil(info.size / FILE_SIZE_TOKEN_DIVISOR_TRP) : estimatedTokens;

            // Update the session-file-based estimate for compression threshold checks.
            // Do NOT overwrite lastActualInputTokens — that comes from API responses.
            entry.lastEstimatedTokens = newFileEstimate;

            // Budget warning: use ACTUAL tokens (from API) vs context budget.
            // Only warn if actual data is available; otherwise use the file estimate
            // as a lower bound (will undercount but better than nothing).
            const warnTokens = entry.lastActualInputTokens ?? newFileEstimate;
            const warnBudget = contextBudget;
            const warnRatio = warnBudget > 0 ? warnTokens / warnBudget : 0;
            if (warnRatio >= 0.5) {
              console.warn(
                `${compressionLogTag(entry)} tool_result_persist: ⚠️ session cost warning, ` +
                `actualTokens=${entry.lastActualInputTokens ?? "n/a"}, fileEstimate=${newFileEstimate}, ` +
                `budget=${warnBudget} (${(warnRatio * 100).toFixed(0)}%), ` +
                `sessionFile=${sessionFile}`,
              );
            }

            // Trigger async file compression when EITHER the file estimate OR the
            // actual API token count exceeds the threshold. The file-estimate
            // path is the traditional check; the actual-token path ensures we
            // compress even when the session file is small but the LLM prompt
            // is large (system prompt + tool defs + skills add 10-50x overhead
            // over the raw session file).
            const shouldCompressByFile = newFileEstimate >= minTokensToCompact;
            const shouldCompressByActual = entry.lastActualInputTokens != null
              && entry.lastActualInputTokens >= minTokensToCompact;
            if (shouldCompressByFile || shouldCompressByActual) {
              const trigger = shouldCompressByActual ? "actual-tokens" : "file-estimate";
              console.log(
                `${compressionLogTag(entry)} tool_result_persist: triggering async session file compression, ` +
                `trigger=${trigger}, fileEstimate=${newFileEstimate}, ` +
                `actualTokens=${entry.lastActualInputTokens ?? "n/a"}, ` +
                `minTokensToCompact=${minTokensToCompact}, ` +
                `sessionFile=${sessionFile}`,
              );
              const compressResult = await maybeCompactSessionFileSafe(sessionFile, {
                maxSessionTokens,
                minTokensToCompact,
                recencyWindow: sc.recencyWindow ?? 6,
                toolPrepassEnabled: sc.toolPrepassEnabled ?? true,
                toolResultMaxChars: config.toolResultMaxChars ?? 5000,
                deduplicateReads: sc.deduplicateReads ?? true,
                readDedupTtlMs: sc.readDedupTtlMs ?? 300_000,
                insertCompactionNotice: sc.insertCompactionNotice ?? true,
              });

              if (compressResult.kind === "compressed") {
                const stats = compressResult.stats;
                console.log(
                  `${compressionLogTag(entry)} tool_result_persist: async compression completed, ` +
                  `${stats.inputTokens}→${stats.outputTokens} tokens, ` +
                  `ratio=${stats.compressionRatio.toFixed(2)}, evicted=${stats.messagesEvicted}`,
                );
                // Update estimate after compression
                entry.lastEstimatedTokens = stats.outputTokens;
                // Notify via the onCompress callback if available
                entry.onCompress?.({ ...stats, phase: "toolResultPersist" });
              } else if (compressResult.kind === "skipped") {
                const reason = compressResult.reason;
                const inTok = (compressResult as { inputTokens?: number }).inputTokens;
                console.log(
                  `${compressionLogTag(entry)} tool_result_persist: async compression skipped, ` +
                  `reason=${reason}, inputTokens=${inTok ?? "n/a"}`,
                );
              }
            }
          } catch (err) {
            // Don't block the tool_result_persist handler on errors
            console.log(
              `${compressionLogTag(entry)} tool_result_persist: async size check failed, ` +
              `error=${err instanceof Error ? err.message : String(err)}`,
            );
          }
        })();
      }

      // Return truncated message (or undefined if no truncation needed)
      if (truncatedMessage) {
        return { message: truncatedMessage as any };
      }
      return;
    });

    // ── End session compression hooks ──

    // Dedup set for hint injection: tracks which (session, waitingNode, workflow)
    // combos have already received a hint, to avoid flooding the transcript.
    // Bounded: entries are only added when isExactMatch misses but a waiting flow
    // exists; the set is pruned when it exceeds 200 entries.
    const nlHintSentSet = new Set<string>();

    // Hook path: BCS callbacks are handled first; wrapped slash fallback then reuses the same dispatchWorkflowCommand path.
    api.on("before_agent_reply", async (event, ctx) => {
      const body = event.cleanedBody;
      console.log("[taskguard] before_agent_reply FIRED", {
        hasBody: !!body,
        bodyLength: typeof body === "string" ? body.length : 0,
        bodyPreview: typeof body === "string" ? body.substring(0, 150) : `(type: ${typeof body})`,
        sessionKey: ctx.sessionKey?.substring(0, 60),
      });
      if (!body) return { handled: false };

      const classification = classifyBcsCollaborationMessage(body);
      if (classification.kind === "invalid") {
        return { handled: true, reply: { text: "BCS 协作回包格式无效，已忽略" } };
      }
      if (classification.kind === "valid") {
        const structuredMessage = classification.message;
        const sessionKey = ctx.sessionKey;
        if (!sessionKey) return { handled: false };

        try {
          const boundTaskFlow = api.runtime.taskFlow.bindSession({ sessionKey });
          const workflowCatalog = loadWorkflowPackCatalog();
          const sessionId = resolveSessionId(sessionKey);

          // 审批人身份校验：collaboration_result 时检查发送人是否在审批人列表中
          if (structuredMessage.messageType === "collaboration_result" && structuredMessage.flowId) {
            try {
              const flow = await (boundTaskFlow as unknown as ControllerDeps["boundTaskFlow"]).get(structuredMessage.flowId);
              if (flow) {
                const state = parseRawFlowState(flow) as FlowState;
                const _hookDb = getDatabase();
                const _hookCfg = loadDatabaseConfig();
                let _hookSpecRepo: IWorkflowSpecRepository | undefined;
                if (_hookCfg.type === "api" && _hookCfg.api) {
                  _hookSpecRepo = new WorkflowSpecApiRepository(createApiClient(_hookCfg.api));
                } else if (_hookDb && _hookDb.dbType !== "noop") {
                  _hookSpecRepo = new WorkflowSpecRepository(_hookDb);
                }
                const resolvedWorkflow = await resolveWorkflow(state.workflowId, _hookSpecRepo, workflowCatalog.workflows);
                const workflow = resolvedWorkflow?.spec ?? state.workflowSnapshot;
                if (workflow) {
                  const nodeId = structuredMessage.nodeId;
                  if (nodeId) {
                    const node = workflow.nodes.find((n) => n.id === nodeId);
                    if (node) {
                      const senderId = extractSenderIdFromMessage(body);
                      if (senderId) {
                        const validation = validateApprover({ node, senderId });
                        if (!validation.valid) {
                          return { handled: true, reply: { text: (validation as { valid: false; reason: string }).reason } };
                        }
                      }
                    }
                  }
                }
              }
            } catch {
              // 校验失败不影响主流程，继续处理
            }
          }

          const deps = buildDeps(api, sessionKey, sessionId, boundTaskFlow as unknown as ControllerDeps["boundTaskFlow"], ".", undefined, undefined, workflowCatalog.workflows, workflowCatalog.packs, undefined, undefined, undefined, undefined, buildFacadeRegistry([], []), undefined, undefined, workflowCatalog.failedWorkflows);
          await handleBcsCollaborationMessage(deps, structuredMessage);
          const label = structuredMessage.messageType === "collaboration_result" ? "协作结果" : "协作错误";
          return {
            handled: true,
            reply: {
              text: `已收到 BCS ${label}：${structuredMessage.nodeId ?? structuredMessage.taskId ?? "unknown"}`,
            },
          };
        } catch {
          return { handled: false };
        }
      }

      if (looksLikeLegacyBcsApprovalProtocol(body)) {
        return { handled: true, reply: { text: "旧版 BCS 审批结构化回包已不再支持，请使用 workflow-collaboration-v1 协作回包。" } };
      }

      const match = body.match(/^\[from:(.*?)\]([\s\S]*)/);
      if (match) {
        const fromBot = match[1];
        const replyText = match[2];

        const approvalMapping = buildBcsApprovalMapping();
        const nodeId = approvalMapping[fromBot];
        if (nodeId) {
          const sessionKey = ctx.sessionKey;
          if (!sessionKey) return { handled: false };

          try {
            const boundTaskFlow = api.runtime.taskFlow.bindSession({ sessionKey });
            const flow = await (boundTaskFlow as unknown as ControllerDeps["boundTaskFlow"]).findLatest();
            if (!flow) return { handled: false };

            const state = parseRawFlowState(flow) as FlowState;
            if (state.executionMode !== "bcs-group" && state.executionMode !== "dingtalk-group") return { handled: false };
            const workflowCatalog = loadWorkflowPackCatalog();
            const _hookDb = getDatabase();
            const _hookCfg = loadDatabaseConfig();
            let _hookSpecRepo: IWorkflowSpecRepository | undefined;
            if (_hookCfg.type === "api" && _hookCfg.api) {
              _hookSpecRepo = new WorkflowSpecApiRepository(createApiClient(_hookCfg.api));
            } else if (_hookDb && _hookDb.dbType !== "noop") {
              _hookSpecRepo = new WorkflowSpecRepository(_hookDb);
            }
            const resolvedWorkflow = await resolveWorkflow(state.workflowId, _hookSpecRepo, workflowCatalog.workflows);
            const workflow = resolvedWorkflow?.spec ?? state.workflowSnapshot;
            if (!workflow) return { handled: false };
            const node = workflow.nodes.find((item) => item.id === nodeId);
            if (!node) return { handled: false };

            const approved = !replyText.includes("驳回") && !replyText.includes("不通过");
            const sessionId = resolveSessionId(sessionKey);
            const deps = buildDeps(api, sessionKey, sessionId, boundTaskFlow as unknown as ControllerDeps["boundTaskFlow"], ".", undefined, workflow, workflowCatalog.workflows, workflowCatalog.packs, undefined, undefined, undefined, undefined, buildFacadeRegistry([], []), resolvedWorkflow?.pack?.root, resolvedWorkflow?.pack?.id, workflowCatalog.failedWorkflows);
            const reviewTime = new Date().toISOString();
            const approvalResult = decorateApprovalCallbackResult({
              workflow,
              state,
              node,
              fromBot,
              approved,
              reviewTime,
              note: replyText.trim(),
            });

            await handleBcsCallback(deps, readFlowId(flow), nodeId, approvalResult);

            return { handled: true, reply: { text: `已收到 ${fromBot} 的协作结果：${approved ? "通过" : "驳回"}` } };
          } catch {
            return { handled: false };
          }
        }
      }

      let facadeRegistry: FacadeRegistry = buildFacadeRegistry([]);
      let _dbBindings: DbFacadeBinding[] = [];
      let _hookWorkflowIds: string[] = [];
      try {
        const workflowCatalog = loadWorkflowPackCatalog();
        _hookWorkflowIds = workflowCatalog.workflows.map((w: any) => w.id).filter(Boolean);
        console.log("[taskguard] pack catalog loaded", { packCount: workflowCatalog.packs.length, packIds: workflowCatalog.packs.map((p: any) => p.manifest?.id), workflowCount: workflowCatalog.workflows.length });
        const _db = getDatabase();
        const _cfg = loadDatabaseConfig();
        const _isApi = _cfg.type === "api";
        if (_isApi && _cfg.api) {
          _dbBindings = await loadApiFacadeBindings(createApiClient(_cfg.api));
        } else if (_db && _db.dbType !== "noop") {
          _dbBindings = await loadDbFacadeBindings(_db);
        }
        facadeRegistry = buildFacadeRegistry(workflowCatalog.packs, _dbBindings);
        console.log("[taskguard] facade registry built", { commands: facadeRegistry.commands(), dbBindings: _dbBindings.length });
      } catch (err) {
        console.log("[taskguard] CAUGHT ERROR in facade registry build, falling back to DB-only facades", {
          errorMessage: (err as any)?.message,
          errorName: (err as any)?.name,
          errorStack: (err as any)?.stack?.substring(0, 800),
        });
        // Pack facade conflict should not block DB bindings — fall back to DB-only registry
        if (_dbBindings.length > 0) {
          try {
            facadeRegistry = buildFacadeRegistry([], _dbBindings);
            console.log("[taskguard] fallback to DB-only facade registry", { commands: facadeRegistry.commands(), dbBindings: _dbBindings.length });
          } catch (fallbackErr) {
            console.log("[taskguard] DB-only fallback also failed", { errorMessage: (fallbackErr as any)?.message });
          }
        }
      }
      const specRepo = buildHookSpecRepo();
      const allowedCommands = ["workflow", ...facadeRegistry.commands(), ..._hookWorkflowIds];
      console.log("[taskguard] facade interception debug", { allowedCommands, bodyPreview: typeof body === "string" ? body.substring(0, 200) : "(no body)" });
      const wrapped = extractWrappedWorkflowSlashCommand(body, { allowedCommands });
      console.log("[taskguard] extractWrappedWorkflowSlashCommand result", { kind: wrapped.kind, commandName: (wrapped as any).commandName, raw: (wrapped as any).raw?.substring(0, 100) });
      if (wrapped.kind === "ambiguous") {
        return {
          handled: true,
          reason: "clawmind-wrapped-slash-command",
          reply: { text: wrapped.message },
        };
      }
      if (wrapped.kind === "command") {
        if (!ctx.sessionKey) {
          return {
            handled: true,
            reason: "clawmind-wrapped-slash-command",
            reply: { text: "无法执行 workflow 命令：当前 OpenClaw hook 上下文缺少 sessionKey。" },
          };
        }

        const facade = facadeRegistry.resolve(wrapped.commandName);
        recordWrappedSlashIntercept({
          sessionKey: ctx.sessionKey,
          commandName: wrapped.commandName,
          raw: wrapped.raw,
          facade: facade?.command,
          workflowId: facade?.defaultWorkflow,
        });

        try {
          const result = await dispatchWorkflowCommand({
            api,
            sessionKey: ctx.sessionKey,
            raw: wrapped.raw,
            commandName: wrapped.commandName,
            skillName: wrapped.skillName,
            entrypoint: "before_agent_reply",
            workspaceDir: (ctx as { workspaceDir?: string }).workspaceDir ?? ".",
          });

          return {
            handled: true,
            reason: "clawmind-wrapped-slash-command",
            reply: { text: result },
          };
        } catch (err) {
          return {
            handled: true,
            reason: "clawmind-wrapped-slash-command",
            reply: { text: formatWorkflowCommandDispatchError(err) },
          };
        }
      }

      // ── No-facade workflowId slash: `/riskreview ...` without a facade ──
      // The matched-command path above only fires for entries in
      // allowedCommands ("workflow" + facade commands + local-pack workflowIds).
      // A workflow that has no facade and lives only in the DB never reaches
      // allowedCommands, so `/workflowId` falls through here. Probe the DB to
      // confirm it is a real workflow before intercepting; if it isn't,
      // return handled:false so OpenClaw routes the message normally (other
      // skills / the agent) instead of swallowing it.
      if (ctx.sessionKey) {
        const probed = await tryInterceptBareWorkflowId({
          text: body,
          catalogWorkflows: loadWorkflowPackCatalog().workflows,
          specRepo,
        });
        if (probed) {
          try {
            const result = await dispatchWorkflowCommand({
              api,
              sessionKey: ctx.sessionKey,
              raw: probed.raw,
              commandName: probed.commandName,
              skillName: probed.commandName,
              entrypoint: "before_agent_reply",
              workspaceDir: (ctx as { workspaceDir?: string }).workspaceDir ?? ".",
            });
            return {
              handled: true,
              reason: "clawmind-bare-workflow-slash",
              reply: { text: result },
            };
          } catch (err) {
            return {
              handled: true,
              reason: "clawmind-bare-workflow-slash",
              reply: { text: formatWorkflowCommandDispatchError(err) },
            };
          }
        }
      }

      // ── L1-First: intent detection before falling through to Agent (L0) ──
      // Controlled by nlInteraction config (configs/application.yaml).
      // When nlInteraction.enabled is false, L1 detection and hint injection
      // are completely skipped — all messages go through the Agent.
      const nlConfig = loadConfig().app.nlInteraction;

      if (ctx.sessionKey && nlConfig.enabled) {
        try {
          const boundTaskFlow = api.runtime.taskFlow.bindSession({ sessionKey: ctx.sessionKey });
          const flow = await (boundTaskFlow as unknown as ControllerDeps["boundTaskFlow"]).findLatest();
          let flowId: string | undefined;
          let intent: DetectedIntent | null = null;
          let waitingNodeId: string | undefined;

          if (flow) {
            const state = parseRawFlowState(flow) as FlowState;
            // Only attempt L1 when a flow is actively waiting at a human gate
            waitingNodeId = Object.keys(state.nodeStates).find(
              (nId) => state.nodeStates[nId]?.status === "waiting",
            );
            if (waitingNodeId) {
              const waitingInfo: WaitingFlowInfo = {
                waitingNodeId,
                state,
              };

              // isExactMatch check — only when exactMatch is enabled.
              // When disabled, skip deterministic matching entirely and
              // rely on the hint or Agent for all intent resolution.
              if (nlConfig.exactMatch) {
                intent = detectHumanGateIntent(body, waitingInfo);
              }

              if (intent) {
                flowId = readFlowId(flow);
              } else if (nlConfig.hintEnabled) {
                // No exact match → inject hint via chatInject so the Agent
                // can recognize the workflow waiting state even if the
                // original chatInject notification has scrolled out of context.
                // OpenClaw's before_agent_reply hook only reads {handled, reply},
                // so we use chatInject to write the hint into the session transcript.
                // The Agent will see it as part of the conversation history.
                const hintKey = `${ctx.sessionKey}:${waitingNodeId}:${state.workflowId}`;
                if (!nlHintSentSet.has(hintKey)) {
                  const hint = renderWaitingHint(state, waitingNodeId);
                  if (hint) {
                    nlHintSentSet.add(hintKey);
                    // Prune if set grows too large (upper bound for long-running sessions)
                    if (nlHintSentSet.size > 200) {
                      const oldest = nlHintSentSet.values().next().value;
                      if (oldest) nlHintSentSet.delete(oldest);
                    }
                    console.log("[taskguard] L1 hint injected via chatInject", { waitingNodeId, workflowId: state.workflowId });
                    const idempotencyKey = `nl-hint:${hintKey}`;
                    await injectChatMessage(api, ctx.sessionKey, hint, idempotencyKey).catch(() => {
                      // chatInject failure MUST NOT block the message — fall through to Agent
                    });
                  }
                }
              }
            }
          }

          // API fallback: when findLatest() returns null (in-memory Map is empty after
          // gateway restart in API mode), query clawweb for waiting flows and use
          // isExactMatch keyword matching — we don't have full FlowState so
          // detectHumanGateIntent won't work, but ENUM_SYNONYM_MAP + REJECT/CONFIRM
          // keywords cover the common Chinese expressions for approve/reject.
          if ((!flow || !intent) && nlConfig.exactMatch) {
            const dbConfig = loadDatabaseConfig();
            if (dbConfig.type === "api" && dbConfig.api) {
              try {
                const apiClient = createApiClient(dbConfig.api);
                const runRepo = new FlowRunApiRepository(apiClient);
                // Query both waiting and blocked flows (blocked = human-wait that hit flow-control)
                // NOTE: FindFlowRunsOptions does not support sessionKey filtering,
                // so we may pick up flows from other sessions. The L1 dispatch will
                // use the correct sessionKey regardless.
                const [waitingRuns, blockedRuns] = await Promise.all([
                  runRepo.findRuns({ status: "waiting", limit: 1 }),
                  runRepo.findRuns({ status: "blocked", limit: 1 }),
                ]);
                const runs = [...waitingRuns, ...blockedRuns];
                if (runs.length > 0) {
                  flowId = runs[0].flow_id;
                  const text = body.trim().toLowerCase();
                  console.log("[taskguard] L1 API fallback: found waiting/blocked run", { flowId, textPreview: text.substring(0, 80) });

                  // isExactMatch keyword matching (no FlowState available)
                  if (isExactMatch(text, REJECT_KEYWORDS)) {
                    intent = { command: "reject" };
                  } else {
                    let matchedChoice: string | undefined;
                    for (const [choiceValue, synonyms] of Object.entries(ENUM_SYNONYM_MAP)) {
                      if (isExactMatch(text, synonyms)) {
                        matchedChoice = choiceValue;
                        break;
                      }
                    }
                    if (matchedChoice) {
                      intent = { command: `confirm choice: ${matchedChoice}`, matchedValue: matchedChoice };
                    } else if (isExactMatch(text, CONFIRM_KEYWORDS)) {
                      intent = { command: "confirm" };
                    }
                  }

                  if (intent) {
                    console.log("[taskguard] L1 API fallback intent detected", { command: intent.command, matchedValue: intent.matchedValue, flowId });
                  }
                }
              } catch (err) {
                console.log("[taskguard] L1 API fallback query failed", { error: (err as any)?.message });
              }
            }
          }

          if (intent && flowId) {
            console.log("[taskguard] L1 intent detected", { command: intent.command, matchedValue: intent.matchedValue, source: flow ? "local" : "api-fallback" });
            // For choice-based intents, use workflow_choice; for confirm/reject, use the standard command
            let dispatchRaw: string;
            if (intent.matchedValue) {
              // Use "confirm choice: X" syntax — parseCommand extracts the choice value
              // and passes it as note, so parseHumanInput can match it against inputSchema.enum.
              dispatchRaw = `confirm choice: ${intent.matchedValue} --flow-id ${flowId}`;
            } else if (intent.command === "reject") {
              dispatchRaw = `reject --flow-id ${flowId}`;
            } else {
              dispatchRaw = `confirm --flow-id ${flowId}`;
            }

            try {
              const result = await dispatchWorkflowCommand({
                api,
                sessionKey: ctx.sessionKey,
                raw: dispatchRaw,
                commandName: "workflow",
                entrypoint: "before_agent_reply",
                workspaceDir: (ctx as { workspaceDir?: string }).workspaceDir ?? ".",
              });
              return { handled: true, reason: "clawmind-l1-intent", reply: { text: result } };
            } catch (err) {
              console.log("[taskguard] L1 dispatch failed, falling through to Agent", { error: (err as any)?.message });
            }
          }
        } catch (err) {
          // L1 detection failure MUST NOT block the message — fall through to Agent
          console.log("[taskguard] L1 intent detection error, falling through to Agent", { error: (err as any)?.message });
        }
      }

      return { handled: false };
    });

    // ── before_agent_run: intercept /workflow commands from CLI agent execution ──
    api.on("before_agent_run", async (event, ctx) => {
      const prompt = (event as { prompt?: string }).prompt ?? "";
      if (!prompt) return;

      console.log("[taskguard] before_agent_run FIRED", {
        promptPreview: prompt.substring(0, 150),
        promptFull: prompt.substring(0, 2000),
        promptLen: prompt.length,
        agentId: (ctx as { agentId?: string }).agentId,
        sessionKey: ctx.sessionKey?.substring(0, 60),
      });

      // Only intercept when the first non-empty line starts with /workflow or a facade command.
      // This avoids blocking judge agents and other CLI runs whose prompts contain
      // workflow references in later lines.
      //
      // Strip the OpenClaw gateway timestamp prefix injected by injectTimestamp()
      // (format: "[Tue 2026-06-30 16:54 GMT+8] /command ...") before firstLine detection.
      // The prefix otherwise causes firstLine to start with "[" instead of "/", making
      // slash commands from `openclaw agent --message` SKIP the intercept. Webchat is
      // unaffected (it routes through before_agent_reply, which has no such guard).
      const TIMESTAMP_PREFIX_RE = /^\[\w{3} \d{4}-\d{2}-\d{2} \d{2}:\d{2} [A-Z]+[^\]]*\]\s*/;
      const strippedPrompt = prompt.replace(TIMESTAMP_PREFIX_RE, "");
      const firstLine = strippedPrompt.split("\n").map((l) => l.trim()).find((l) => l.length > 0) ?? "";
      if (!firstLine.startsWith("/")) {
        console.log("[taskguard] before_agent_run SKIP: first line does not start with /", {
          firstLine: firstLine.substring(0, 100),
          firstLineHex: Buffer.from(firstLine.substring(0, 200)).toString("hex"),
          originalPromptTop: prompt.substring(0, 150),
        });
        return;
      }

      try {
        let facadeRegistry = buildFacadeRegistry([], []);
        let _dbBindings: any[] = [];
        let _hookWorkflowIds: string[] = [];
        try {
          const workflowCatalog = loadWorkflowPackCatalog();
          _hookWorkflowIds = workflowCatalog.workflows.map((w: any) => w.id).filter(Boolean);
          const _db = getDatabase();
          const _cfg = loadDatabaseConfig();
          const _isApi = _cfg.type === "api";
          if (_isApi && _cfg.api) {
            _dbBindings = await loadApiFacadeBindings(createApiClient(_cfg.api));
          } else if (_db && _db.dbType !== "noop") {
            _dbBindings = await loadDbFacadeBindings(_db);
          }
          facadeRegistry = buildFacadeRegistry(workflowCatalog.packs, _dbBindings);
        } catch (err) {
          console.log("[taskguard] before_agent_run facade registry build failed", { error: (err as any)?.message });
        }

        const specRepo = buildHookSpecRepo();
        const allowedCommands = ["workflow", ...facadeRegistry.commands(), ..._hookWorkflowIds];
        const wrapped = extractWrappedWorkflowSlashCommand(prompt, { allowedCommands });
        console.log("[taskguard] before_agent_run extractWrappedWorkflowSlashCommand result", {
          kind: wrapped.kind,
          commandName: (wrapped as any).commandName,
          raw: (wrapped as any).raw?.substring(0, 100),
        });

        if (wrapped.kind === "command") {
          if (!ctx.sessionKey) {
            console.log("[taskguard] before_agent_run: missing sessionKey, skipping");
            return;
          }

          recordWrappedSlashIntercept({
            sessionKey: ctx.sessionKey,
            commandName: (wrapped as any).commandName,
            raw: (wrapped as any).raw,
            facade: facadeRegistry.resolve((wrapped as any).commandName)?.command,
            workflowId: facadeRegistry.resolve((wrapped as any).commandName)?.defaultWorkflow,
          });

          try {
            const result = await dispatchWorkflowCommand({
              api,
              sessionKey: ctx.sessionKey,
              raw: (wrapped as any).raw,
              commandName: (wrapped as any).commandName,
              skillName: (wrapped as any).skillName,
              entrypoint: "before_agent_run",
              workspaceDir: (ctx as { workspaceDir?: string }).workspaceDir ?? ".",
            });
            return { outcome: "block", reason: result };
          } catch (err) {
            console.log("[taskguard] before_agent_run dispatch failed", { error: (err as any)?.message });
            return { outcome: "block", reason: formatWorkflowCommandDispatchError(err) };
          }
        }

        // ── No-facade workflowId slash: `/riskreview ...` without a facade ──
        // Same logic as before_agent_reply: when no known command matched, check
        // whether the first-line slash is a DB-resolvable workflow and, if so,
        // intercept as `run <workflowId>`. Otherwise leave the prompt untouched.
        if (ctx.sessionKey) {
          const probed = await tryInterceptBareWorkflowId({
            text: strippedPrompt,
            catalogWorkflows: loadWorkflowPackCatalog().workflows,
            specRepo,
          });
          if (probed) {
            try {
              const result = await dispatchWorkflowCommand({
                api,
                sessionKey: ctx.sessionKey,
                raw: probed.raw,
                commandName: probed.commandName,
                skillName: probed.commandName,
                entrypoint: "before_agent_run",
                workspaceDir: (ctx as { workspaceDir?: string }).workspaceDir ?? ".",
              });
              return { outcome: "block", reason: result };
            } catch (err) {
              console.log("[taskguard] before_agent_run bare-workflow dispatch failed", { error: (err as any)?.message });
              return { outcome: "block", reason: formatWorkflowCommandDispatchError(err) };
            }
          }
        }
      } catch (err) {
        console.log("[taskguard] before_agent_run error, falling through", { error: (err as any)?.message });
      }
    });
}
