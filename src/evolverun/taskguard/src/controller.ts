import { existsSync, readFileSync } from "node:fs";
import * as fs from "node:fs/promises";
import { homedir } from "node:os";
import { join, resolve } from "node:path";
import yaml from "yaml";
import { resolveSessionId } from "./session-resolver.js";
import { collectCredentialsAndSession } from "./credentials-collector.js";
import type {
  FlowIdentity,
  FlowInput,
  FlowState,
  HumanInputSchema,
  WaitState,
  NodeState,
  NodeExecutionReport,
  TestCaseReport,
  TestReport,
  WorkflowNode,
  WorkflowCommandSurface,
  HumanCommandHints,
  HumanGateActions,
  HumanGateConfirmAction,
  HumanGateReviseAction,
  HumanWaitSpec,
  HookActionSpec,
  WorkflowSpec,
  ExecutorResult,
  AuditLogEntry,
  ExecutionMode,
  FlowEvent,
  ActionState,
  NodeRetryFailureReason,
  WorkflowPin,
  LoopGroupExecutor,
  LoopGroupRuntimeState,
  DynamicTemplateExecutor,
  DynamicTemplateRuntimeState,
  ExecutionWarning,
} from "./types.js";
import type { IDatabase } from "./db/types.js";
import type { ActionExecutionContext, ActionRegistry } from "./actions/types.js";
import {
  ChatInjectMessageType,
  type ChatInjectOptions,
} from "./platform/types.js";
import { renderStructuredWaitPrompt, type WaitPromptChoice } from "./input/wait-prompt-template.js";
import { applySaveAs, resolveTemplateValue } from "./actions/template.js";
import {
  getReadyNodes,
  findSkippableNodesFixedPoint,
  evaluateOnResult,
  isWorkflowComplete,
  computePhaseAndStatus,
  buildActionTemplateExtras,
  buildTemplateContext,
  resolveTemplate,
  buildNodeOutputContext,
  buildScopedNodeOutputContext,
  type TemplateContext,
} from "./runner.js";
import { resolveUserIdentity } from "./runtime/user-identity.js";
import { runHookActions, type HookRunOutcome } from "./actions/hook-runner.js";
import { executeBcsApprovalBatch, type BcsApprovalBatchApi } from "./executors/bcs-approval-batch.js";
import {
  formatValidationIssues,
  validateWorkflowResources,
  validateWorkflowSemantics,
  WorkflowValidationError,
  normalizeWorkflowSpec,
} from "./validation/workflow.js";
import { quickScanSpecFile } from "./validation/quick-scan.js";
import {
  WORKFLOW_COLLABORATION_PROTOCOL_VERSION,
  type BcsCollaborationMessage,
  type BcsCollaborationResultMessage,
} from "./bcs-collaboration-protocol.js";
import { decorateApprovalCallbackResult } from "./approval-actors.js";
import { evaluateApprovalPolicy } from "./approver-validator.js";
import {
  readCurrentStep,
  parseRawFlowState,
  parseWaitJson,
  readFlowId,
} from "./flow-record.js";
import { appendWorkflowJsonlLog, buildDirectLogRecord, buildWorkflowLogRecord, formatLocalIsoWithOffset } from "./workflow-log.js";
import { renderWorkflowDetail } from "./workflow-detail.js";
import type { FailedWorkflow, ResolvedWorkflow, ResolvedWorkflowPack } from "./packs/types.js";
import {
  listWorkflowIdsFromPacks,
  resolveWorkflow,
  resolveWorkflowByIdFromPacks,
  workflowRegistryFromResolved,
  WorkflowPackResolverError,
} from "./packs/resolver.js";
import { findFailedWorkflow } from "./controller/version-commands.js";
import { WorkflowSpecRepository } from "./db/repositories/workflow-spec-repository.js";
import {
  formatOutputContractIssues,
  validateOutputContractResult,
} from "./output-contract.js";
import {
  createSqliteGlobalFlowStore,
  type GlobalFlowStore,
  type RawTaskFlowRecord,
} from "./flow-store.js";
import { formatTokenUsage, recomputeWorkflowUsage } from "./token-usage.js";
import { formatWarningsAsErrorText } from "./warnings.js";
import { loadOwnerId } from "./credentials.js";
import { normalizeFlowInput } from "./input/normalize.js";
import { extractNodeStepTraceFromContent } from "./runner/step-trace.js";
import { HumanInputValidationError, isHumanInputValidationError, parseHumanInput } from "./input/human.js";
import {
  pickPublicWorkflowOutputs,
  resolveWorkflowOutputs,
} from "./workflow-outputs.js";
import {
  getLegacyApprovalExecutor,
  getLegacyExecutorType,
} from "./legacy-runtime.js";
import { RequiredSkillNotFoundError, resolveRequiredSkill } from "./skill-resolver.js";
import { buildChildFlowState, type SubworkflowCompletionResult } from "./executors/subworkflow.js";
import {
  buildWorkflowStartedMessage,
  buildNodeStartedMessage,
  buildNodeSucceededNotification,
  buildNodeSkippedMessage,
  buildNodeRetryMessage,
  buildNodeFailedMessage,
  buildFlowCompletedMessage,
  buildNodeIndexMap,
  findDownstreamNodes,
  captureDisplayInput,
  captureWorkflowInputPreview,
} from "./controller-notifications.js";

// ── Verbose chatInject helpers ──
// These thin wrappers compute node index from module-level workflow spec maps
// and delegate to the builder functions in controller-notifications.ts.

/** Resolve node index (1-based) from module-level workflow spec maps. */
function resolveNodeIndex(flowId: string, nodeId: string): number {
  const spec = _workflowSpecByFlowId.get(flowId) ?? _currentWorkflowSpec;
  if (!spec?.nodes) return 0;
  const idx = spec.nodes.findIndex((n) => n.id === nodeId);
  return idx >= 0 ? idx + 1 : 0;
}

/**
 * Send a verbose chatInject message — display-only, never throws, never blocks.
 *
 * Routes through {@link enqueueInject} (NOT a bare `void chatInject()`) so these
 * UI notifications obey the same back-pressure as agent-loop injects:
 *   - serialized per flow (idempotencyKey prefix = flowId) → at most ONE inject
 *     subprocess per flow in flight, preserving transcript order,
 *   - the global concurrency cap ({@link MAX_ACTIVE_LANES}) bounds simultaneous
 *     `chat.inject` subprocesses across ALL flows, and
 *   - on lane overflow the OLDEST droppable task is discarded. Progress-only
 *     notifications are droppable; business display results are retained.
 *
 * The previous bare fire-and-forget spawned an unbounded number of `chat.inject`
 * subprocesses (verbose-* fires for every node transition); under concurrent
 * flows this drove the gateway to abnormal exits (exit_code=null) and stalled
 * the whole session. See inject-queue.ts for the full rationale.
 */
function verboseChatInject(
  chatInject: ControllerDeps["chatInject"] | undefined,
  message: string,
  idempotencyKey: string,
  droppable = true,
): void {
  if (!chatInject || !message) return;
  // Gating moved to each caller via `shouldInjectForFlow(flowId, <event>)` so
  // each event uses its own minLevel (started/succeeded/failed=simple,
  // skipped=full, retry=never). This helper is now enqueue-only.
  // Lane key = flowId (idempotencyKey starts with `${flowId}:…`). Serializing per
  // flow keeps one flow's verbose flood from crowding out another's, while the
  // global cap still bounds cross-flow concurrency. Falls back to the full key.
  const colon = idempotencyKey.indexOf(":");
  const laneKey = colon > 0 ? idempotencyKey.slice(0, colon) : idempotencyKey;
  // Pass coalesce info so the queue can merge multiple droppable progress
  // messages within a 2-second window into a single chat.inject subprocess,
  // reducing UI latency under burst-heavy multi-node flows.
  enqueueInject(
    laneKey,
    () => chatInject(message, idempotencyKey),
    droppable
      ? { droppable, coalesceMessage: message, coalesceSender: chatInject, coalesceIdempotencyKey: idempotencyKey }
      : { droppable },
  );
}

/** Send verbose node succeeded notification via chatInject. */
function notifyNodeSucceeded(
  chatInject: ControllerDeps["chatInject"] | undefined,
  level: InjectLevel,
  flowId: string,
  node: WorkflowNode,
  result: ExecutorResult,
  durationMs: number,
  outputContractResult: "pass" | "fail" | "none",
): void {
  if (!chatInject || !shouldInjectForFlow(flowId, "node-succeeded")) return;
  const nodeIndex = resolveNodeIndex(flowId, node.id);
  const notification = buildNodeSucceededNotification({
    node,
    nodeIndex,
    result: result.result as Record<string, unknown> | undefined,
    durationMs,
    outputContractResult,
    level,
  });
  verboseChatInject(
    chatInject,
    notification.message,
    `${flowId}:${node.id}:verbose-succeeded`,
    notification.droppable,
  );
}

/** Send verbose node skipped notification via chatInject. */
function notifyNodeSkipped(
  chatInject: ControllerDeps["chatInject"] | undefined,
  level: InjectLevel,
  flowId: string,
  node: WorkflowNode,
  skipReason: string,
  triggerRuleActual?: string,
): void {
  if (!chatInject || !shouldInjectForFlow(flowId, "node-skipped")) return;
  const nodeIndex = resolveNodeIndex(flowId, node.id);
  const msg = buildNodeSkippedMessage({
    node,
    nodeIndex,
    skipReason,
    triggerRuleActual,
    level,
  });
  verboseChatInject(chatInject, msg, `${flowId}:${node.id}:verbose-skipped`);
}

/**
 * Detach an async-run progress notification from the engine's critical path.
 *
 * The underlying WS chat.inject round trip is P50≈5s (P90≈9s). Awaiting it on
 * the async execution loop between every node transition adds ~49s of pure
 * UI-display latency to a multi-node flow — the engine blocks on display, not
 * on work. This is the same problem {@link enqueueInject} already solves for
 * embedded-agent loop events (see src/inject-queue.ts): it returns immediately,
 * serializes per sessionKey (order preserved, at most one subprocess per
 * session), and bounds global concurrency.
 *
 * Injection is display-only — nothing downstream reads its result, and the
 * reliable wrapper already swallows failures — so detaching is correctness-safe.
 * `droppable` (default true) lets the queue discard this under back-pressure;
 * pass false for messages a user should not miss.
 */
function fireProgressChatInject(
  deps: ControllerDeps,
  message: string,
  idempotencyKey: string,
  droppable = true,
): void {
  // Lane key = flowId (the ":"-delimited prefix of the idempotency key), mirroring
  // verboseChatInject and the embedded-agent injects, so ALL of a flow's injects
  // share ONE serial lane and preserve transcript order. Falling back to
  // sessionKey only when the key has no ":" (shouldn't happen for node injects).
  const colon = idempotencyKey.indexOf(":");
  const laneKey = colon > 0 ? idempotencyKey.slice(0, colon) : (deps.sessionKey ?? idempotencyKey);
  // Pass coalesce info so the queue can merge multiple progress notifications
  // within a 2-second window into a single chat.inject subprocess.
  enqueueInject(
    laneKey,
    () => deps.chatInject(message, idempotencyKey),
    droppable
      ? { droppable, coalesceMessage: message, coalesceSender: deps.chatInject, coalesceIdempotencyKey: idempotencyKey }
      : { droppable },
  );
}

import { MAX_SUBWORKFLOW_DEPTH, type SubworkflowExecutor, type InjectedNodeRecord } from "./types.js";
import { materializeLoopIteration } from "./loop-group.js";
import { materializeBody } from "./materializer.js";
import { resolveEffectiveContextPolicy } from "./execution-context.js";
import { enqueueInject, setDropNotifier } from "./inject-queue.js";
import {
  setFlowInjectLevel,
  clearFlowInjectLevel,
  resolveInjectLevelForFlow,
  shouldInjectForFlow,
  hasFlowInjectLevel,
} from "./inject-gating.js";
import type { InjectLevel } from "./inject-level.js";

const CONTROLLER_ID = "clawmind";
const MAX_FLOW_EVENTS = 200;
const ACTIVE_FLOW_STATUSES = new Set(["queued", "running", "waiting", "blocked", "lost", "failed"]);
const RECOVERABLE_APPROVAL_STATUSES = new Set(["failed", "rejected", "blocked", "waiting"]);
const RETRYABLE_NODE_STATUSES = new Set(["failed", "blocked", "waiting"]);
const NON_RETRYABLE_ACTIVE_NODE_STATUSES = new Set(["running", "postActionsRunning"]);
const SKIPPABLE_COMMAND_NODE_STATUSES = new Set(["waiting", "failed", "blocked"]);
const IMPORTABLE_FLOW_STATUSES = new Set(["queued", "running", "waiting", "blocked", "lost", "failed", "finished", "succeeded"]);
const IMPORTABLE_NODE_STATUSES = new Set(["pending", "running", "postActionsRunning", "waiting", "succeeded", "failed", "blocked", "skipped"]);
let flowEventSeq = 0;

// ── Helpers ──

function now(): number {
  return Date.now();
}

function isoNow(): string {
  return new Date().toISOString();
}

function appendAuditLog(
  state: FlowState,
  node: string,
  action: string,
  detail: string,
): void {
  state.auditLog.push({ time: isoNow(), node, action, detail });
}

function recordNodeUsage(state: FlowState, nodeId: string, result: ExecutorResult): void {
  if (!result.usage && !result.warnings) return;
  state.nodeStates[nodeId] = {
    ...state.nodeStates[nodeId],
    ...(result.usage ? { usage: result.usage } : {}),
    ...(result.warnings && result.warnings.length > 0 ? { warnings: result.warnings } : {}),
  };
  if (result.usage) {
    state.usage = recomputeWorkflowUsage(state.nodeStates);
  }
}

let _eventRepository: import("./db/repositories/types.js").IFlowEventRepository | null = null;

export function setEventRepository(repo: import("./db/repositories/types.js").IFlowEventRepository | null): void {
  _eventRepository = repo;
}

let _db: import("./db/types.js").IDatabase | null = null;

export function setDatabase(db: import("./db/types.js").IDatabase | null): void {
  _db = db;
}

export function getDatabase(): import("./db/types.js").IDatabase | null {
  return _db;
}

// ── State persistence hooks ──

import type { IFlowRunRepository, INodeExecutionRepository, IFlowEventRepository, IFlowMetricsRepository, ITriggeredAlertRepository, INodeStepTraceRepository, IHallucinationCheckRepository, IRunLogRepository, RunLogInsert } from "./db/repositories/types.js";
import { recordFailure, setEnqueueRunLog, withRetry } from "./fire-and-forget/index.js";
import type {
  NodeLifecycleEvent,
  NodeLifecyclePayload,
} from "./controller-hooks/types.js";
import { MetricsRecorder } from "./controller-hooks/metrics-recorder.js";
import { AlertRecorder } from "./controller-hooks/alert-recorder.js";
import { NodeExecutionTracker } from "./controller-hooks/node-execution-tracker.js";
import type { KnowledgeBase, CacheEntry } from "./knowledge/types.js";
import type { KnowledgeBaseManager } from "./knowledge/manager.js";
import type { KnowledgeConfig, RetryConfig } from "./config/types.js";
import { loadConfig } from "./config/loader.js";
import { prepareKnowledgeContext } from "./knowledge/injector.js";
import { createSearchCache } from "./knowledge/search.js";
import { ErrorContextStore } from "./retry/error-context-store.js";
import { RetryTracker, AutoRetryTracker } from "./retry/retry-tracker.js";
import { handleNodeFailure } from "./retry/intelligent-retry.js";
import { WorkflowAnalyzer } from "./analysis/workflow-analyzer.js";
import { ThresholdChecker } from "./analysis/threshold-checker.js";
import type { AnalysisConfig, AlertingConfig } from "./config/types.js";
import type { HealthReport } from "./analysis/types.js";
import { AlertDispatcher } from "./alerts/alert-dispatcher.js";
import type { NodeFailureEvent } from "./alerts/alert-dispatcher.js";
import { WorkflowNotificationDispatcher, rowToDingtalkConfig } from "./alerts/workflow-notification-dispatcher.js";
import { HttpCallbackDispatcher, mergeCallbackConfigs, yamlNotificationToConfig, rowToHttpCallbackConfig } from "./alerts/http-callback-dispatcher.js";
import type { HttpCallbackConfig, IHttpCallbackConfigRepository } from "./alerts/http-callback-types.js";
import { validateNodeOutput, shouldValidateNode } from "./validation.js";
import type { ValidationTemplateContent } from "./validation.js";
import { applyRepair, summarizeRepair } from "./guardian/repair-strategies.js";
import type { GuardianRepair, GuardianAnalysisParams } from "./guardian/types.js";

// ── User identity caching for template context ──
let _cachedUserIdentity: Record<string, unknown> | null = null;
let _cachedSessionKey: string | null = null;

function resolveUserIdentityForContext(deps: ControllerDeps, deliveryContext?: Record<string, unknown>): Record<string, unknown> {
  // Cache by sessionKey — identity doesn't change within a session
  if (_cachedSessionKey === deps.sessionKey && _cachedUserIdentity) {
    return _cachedUserIdentity;
  }
  const identity = resolveUserIdentity({
    messages: deps.messages,
    sessionKey: deps.sessionKey ?? "",
    ownerId: deps.user?.id,
    deliveryContext,
    env: process.env as Record<string, string | undefined>,
  });
  _cachedUserIdentity = { ...identity };
  _cachedSessionKey = deps.sessionKey ?? null;
  return _cachedUserIdentity;
}

let _metricsRecorder: MetricsRecorder | null = null;
let _alertRecorder: AlertRecorder | null = null;
let _nodeExecutionTracker: NodeExecutionTracker | null = null;
let _nodeExecutionRepository: INodeExecutionRepository | null = null;
let _nodeStepTraceRepo: INodeStepTraceRepository | null = null;
let _hallucinationCheckRepo: IHallucinationCheckRepository | null = null;
let _flowRunRepository: IFlowRunRepository | null = null;
/** Pending updateCompletion promises per flowId, used to ensure HTTP callbacks
 *  wait for the DB write to commit before querying flow_runs for ext_info. */
const _pendingCompletionPromises = new Map<string, Promise<unknown>>();
let _engineName: string | null = null;
let _knowledgeBases: KnowledgeBase[] = [];
let _knowledgeCache: Map<string, CacheEntry> | null = null;
let _knowledgeConfig: KnowledgeConfig | null = null;
let _knowledgeBaseManager: KnowledgeBaseManager | null = null;

/** Validation template resolver: returns template content for a given templateId, or null. */
let _validationTemplateResolver: ((templateId: string) => Promise<ValidationTemplateContent | null>) | null = null;
let _retryConfig: RetryConfig | null = null;
let _errorContextStore = new ErrorContextStore();
let _retryTracker = new RetryTracker();
let _autoRetryTracker = new AutoRetryTracker();
/** Current bot ID — set per executeLoop, used by module-scope JSONL log calls. */
let _botId: string | null = null;
/** Current owner ID — set per executeLoop, used by module-scope JSONL log calls. */
let _ownerId: string | null = null;
/** Current session key — set per executeLoop, used by module-scope JSONL log calls. */
let _sessionKey: string | null = null;
let _analyzer: WorkflowAnalyzer | null = null;
let _thresholdChecker: ThresholdChecker | null = null;
let _lastHealthReport: HealthReport | null = null;
let _alertDispatcher: AlertDispatcher | null = null;
let _workflowNotificationDispatcher: WorkflowNotificationDispatcher | null = null;
let _notificationConfigRepo: import("./db/repositories/types.js").INotificationConfigRepository | null = null;
let _httpCallbackDispatcher: HttpCallbackDispatcher | null = null;
let _httpCallbackConfigRepo: IHttpCallbackConfigRepository | null = null;
let _httpCallbackLogRepo: import("./alerts/http-callback-types.js").IHttpCallbackLogRepository | null = null;
let _currentWorkflowSpec: WorkflowSpec | null = null;

/**
 * Per-flow chatInject reference for sending notifications from module-scope
 * functions (completeFlowRun, emitNodeEvent) to teclaw.
 *
 * CONCURRENCY NOTE: This is keyed by flowId on purpose. A process can run
 * multiple flows concurrently (async mode); the previous module-level SINGLETON
 * was overwritten by each new flow's launch, so a flow completing later would
 * dispatch its completion/node-failed notifications through WHATEVER flow most
 * recently launched — i.e. another session's chatInject. That leaked another
 * flow's execution messages into the wrong session. Keying per flowId routes
 * each flow's module-scope notifications back to its own session-bound inject.
 *
 * Mirrors {@link _workflowSpecByFlowId} (same concurrency isolation rationale).
 */
const _chatInjectByFlowId = new Map<string, ChatInjectFn>();

/**
 * Per-flow buffer of messages that failed to deliver via chatInject after all
 * retries. These messages are appended to the flow-completion notification so
 * the user can see what was missed during execution, rather than the messages
 * silently disappearing.
 *
 * Each entry is a trimmed preview (max 300 chars) to keep the completion
 * message manageable. The full message is already persisted in the JSONL log
 * via {@link makeReliableChatInject}'s error path.
 *
 * Mirrors {@link _chatInjectByFlowId} for the same concurrency isolation.
 */
const _failedInjectMessagesByFlowId = new Map<string, string[]>();

/** Maximum number of failed-message previews to retain per flow. */
const MAX_FAILED_INJECT_PREVIEWS = 20;

/**
 * Bind a chatInject function for module-scope notification delivery, scoped to
 * the given flowId. Called once per workflow launch (launchAsyncExecution /
 * executeLoop). Pass `null` to clear bindings for that flowId.
 */
export function setGlobalChatInject(fn: ChatInjectFn | null, flowId: string): void {
  if (fn) {
    _chatInjectByFlowId.set(flowId, fn);
    // Register a drop notifier so that when inject-queue discards droppable
    // tasks under back-pressure, a warning message is injected into the chat
    // stream to inform the user that progress messages were skipped.
    setDropNotifier((summary: string) => {
      // Use the most recently bound chatInject to deliver the drop notice.
      // Best-effort: if this fails, the drop is already logged to console.
      const notifyFn = _chatInjectByFlowId.get(flowId) ?? fn;
      void notifyFn(summary, `${flowId}:inject-queue:dropped`).catch((err) => recordFailure("setGlobalChatInject.dropNotifier", flowId, undefined, err, "warn"));
    });
  } else {
    _chatInjectByFlowId.delete(flowId);
    setDropNotifier(null);
  }
  console.log(`[controller] setGlobalChatInject: ${fn ? "enabled" : "disabled"} flowId=${flowId}`);
}

/** Resolve the chatInject bound to a flowId (module-scope notification routing). */
function resolveChatInjectForFlow(flowId: string): ChatInjectFn | undefined {
  return _chatInjectByFlowId.get(flowId);
}

/**
 * Per-flow workflow spec registry for concurrent flow isolation.
 * In async mode, multiple flows can execute concurrently, so a single
 * _currentWorkflowSpec is insufficient. This Map allows emitNodeEvent
 * and completeFlowRun to look up the correct spec by flowId.
 */
const _workflowSpecByFlowId = new Map<string, WorkflowSpec>();

// ── M2: Async Execution Engine ──

/** Handle for a background async workflow execution. */
export type AsyncExecutionHandle = {
  flowId: string;
  sessionKey: string;
  promise: Promise<void>;
  abortController: AbortController;
};

/**
 * Active async executions, keyed by `${sessionKey}:${flowId}`.
 * Used to:
 * 1. Skip executeLoop in confirm/resume/reject handlers when async engine is already running
 * 2. Abort async executions on /stop
 * 3. Prevent duplicate launches for the same flow
 */
const activeAsyncExecutions = new Map<string, AsyncExecutionHandle>();

function asyncExecutionKey(sessionKey: string, flowId: string): string {
  return `${sessionKey}:${flowId}`;
}

/** Check whether an async execution is active for a given flow. */
export function isAsyncExecutionActive(flowId: string): boolean {
  for (const handle of activeAsyncExecutions.values()) {
    if (handle.flowId === flowId) return true;
  }
  return false;
}

/** Test-only snapshot for verifying async execution cleanup across registries. */
export function __getAsyncExecutionResourceStateForTest(flowId: string): {
  active: boolean;
  workflowSpec: boolean;
  chatInject: boolean;
  verbosity: boolean;
} {
  return {
    active: isAsyncExecutionActive(flowId),
    workflowSpec: _workflowSpecByFlowId.has(flowId),
    chatInject: _chatInjectByFlowId.has(flowId),
    verbosity: hasFlowInjectLevel(flowId),
  };
}

/**
 * Create a reliable version of chatInject with retry logic.
 *
 * Retries up to 3 times with 1s, 2s, 3s exponential delays.
 * On final failure, logs a warning (JSONL log fallback) and returns
 * without throwing — ensuring the workflow continues even if
 * notifications are temporarily unavailable.
 */
export function makeReliableChatInject(
  originalChatInject: ChatInjectFn,
  flowId: string,
): ChatInjectFn {
  const MAX_RETRIES = 3;
  const BASE_DELAY_MS = 1000;

  return async (
    message: string,
    idempotencyKey: string,
    options?: ChatInjectOptions,
  ): Promise<void> => {
    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      try {
        await originalChatInject(message, idempotencyKey, options);
        return; // success
      } catch (err) {
        if (attempt < MAX_RETRIES) {
          const delayMs = BASE_DELAY_MS * (attempt + 1);
          console.warn(
            `[reliableChatInject] attempt ${attempt + 1}/${MAX_RETRIES + 1} failed for flow ${flowId}, ` +
            `retrying in ${delayMs}ms: ${err instanceof Error ? err.message : String(err)}`,
          );
          await new Promise((resolve) => setTimeout(resolve, delayMs));
        } else {
          // Final failure — log and continue (don't throw)
          const errMsg = err instanceof Error ? err.message : String(err);
          console.error(
            `[reliableChatInject] all ${MAX_RETRIES + 1} attempts failed for flow ${flowId}, ` +
            `idempotencyKey=${idempotencyKey}, giving up: ${errMsg}`,
          );
          enqueueRunLog({
            flow_id: flowId,
            level: "error",
            source: "notification",
            message: `chatInject failed after ${MAX_RETRIES + 1} attempts: ${errMsg}`,
            timestamp: Date.now(),
          });
          void appendWorkflowJsonlLog(
            buildDirectLogRecord({
              flowId,
              eventType: "chatinject_failed",
              message: "chatInject delivery failed after retries",
              botId: _botId,
              sessionKey: _sessionKey,
              rawError: err,
              details: { idempotencyKey, messagePreview: message.slice(0, 200) },
            }),
          ).catch((err) => recordFailure("enqueueDirectRunLog.chatinject_failed", flowId, undefined, err, "warn")); // best-effort log

          // Buffer the failed message so it can be backfilled into the
          // flow-completion notification. This ensures the user eventually
          // sees what was missed, rather than the message silently
          // disappearing.
          let previews = _failedInjectMessagesByFlowId.get(flowId);
          if (!previews) {
            previews = [];
            _failedInjectMessagesByFlowId.set(flowId, previews);
          }
          if (previews.length < MAX_FAILED_INJECT_PREVIEWS) {
            previews.push(message.slice(0, 300));
          }
        }
      }
    }
  };
}

/** Abort all async executions for a given session. Returns count aborted. */
export function abortAsyncExecutionsForSession(sessionKey: string): number {
  let aborted = 0;
  for (const [key, handle] of activeAsyncExecutions) {
    if (handle.sessionKey === sessionKey) {
      handle.abortController.abort();
      activeAsyncExecutions.delete(key);
      aborted += 1;
    }
  }
  return aborted;
}

/**
 * Abort a SINGLE async execution by flowId (not the whole session).
 *
 * Unlike {@link abortAsyncExecutionsForSession}, this targets one specific
 * running flow — used by the flow-timeout watchdog so reaping a stuck flow
 * does not kill sibling flows sharing the same session. Returns true if an
 * active execution was found and aborted.
 *
 * The abort propagates to {@link runEmbeddedPiAgent} via the per-flow
 * AbortController wired in {@link launchAsyncExecution}, causing the current
 * LLM/tool call to reject with AbortError → node failed → executeLoop finalizes.
 */
export function abortAsyncExecutionForFlow(flowId: string): boolean {
  for (const [key, handle] of activeAsyncExecutions) {
    if (handle.flowId === flowId) {
      if (!handle.abortController.signal.aborted) {
        handle.abortController.abort(new Error(`flow ${flowId} aborted (timeout watchdog)`));
      }
      activeAsyncExecutions.delete(key);
      console.log(`[async-exec] aborted async execution for flow ${flowId} (timeout watchdog)`);
      return true;
    }
  }
  return false;
}

/**
 * Test-only seam: register a fake async execution handle so tests can verify
 * that {@link abortAsyncExecutionForFlow} / {@link reapStaleRunningFlows}
 * actually abort the running execution. Returns a cleanup function.
 */
export function __registerAsyncExecutionForTest(
  flowId: string,
  sessionKey: string,
  controller: AbortController,
): () => void {
  const key = asyncExecutionKey(sessionKey, flowId);
  const handle: AsyncExecutionHandle = { flowId, sessionKey, promise: Promise.resolve(), abortController: controller };
  activeAsyncExecutions.set(key, handle);
  return () => { activeAsyncExecutions.delete(key); };
}

/**
 * Launch async execution of a workflow in the background.
 * Returns immediately with an AsyncExecutionHandle.
 * The workflow runs to completion (or error) while the tool call returns.
 *
 * @param options.runStartHooks - Whether to run flow start hooks. Default: false
 *   (true for initial run via startWorkflowAfterPreflight, false for resume via confirm/resume/etc.)
 */
/** Max number of times launchAsyncExecution will re-drive a flow after a
 *  conflict-while-running (revision CAS) before giving up and leaving it for
 *  the watchdog. Bounds livelock under a persistently conflicting writer. */
const MAX_CONFLICT_REDRIVES = 2;

function launchAsyncExecution(
  deps: ControllerDeps,
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
  revision: number,
  options?: { runStartHooks?: boolean; debug?: boolean; _conflictRedrivesLeft?: number },
): AsyncExecutionHandle {
  const abortController = new AbortController();
  const key = asyncExecutionKey(deps.sessionKey, flowId);

  // Wire the per-flow AbortController into the executor path. Previously this
  // controller was created but its signal never reached `reliableDeps.abortSignal`
  // (the stale caller-provided session signal kept flowing), so /stop and the
  // timeout watchdog could not abort a running embedded-agent in async mode.
  // If the caller already provided a signal, cascade its abort into ours so
  // session-level stops still propagate.
  const upstreamSignal = deps.abortSignal;
  if (upstreamSignal) {
    if (upstreamSignal.aborted) {
      // Already aborted upstream — abort immediately.
      abortController.abort(upstreamSignal.reason ?? new Error("upstream signal already aborted"));
    } else {
      upstreamSignal.addEventListener(
        "abort",
        () => abortController.abort(upstreamSignal.reason ?? new Error("upstream signal aborted")),
        { once: true },
      );
    }
  }

  // Set global chatInject so module-scope functions (completeFlowRun, emitNodeEvent)
  // can send notifications to teclaw when flow completes or nodes fail.
  setGlobalChatInject(deps.chatInject, flowId);
  setFlowInjectLevel(deps.chatInjectLevel ?? "full", flowId);
  _botId = deps.botId ?? null;
  _ownerId = deps.ownerId ?? null;
  _sessionKey = deps.sessionKey ?? null;

  // Wrap chatInject with retry logic for async mode so that
  // transient chatInject failures don't silently lose notifications.
  // Depend on OUR per-flow signal so aborting the handle actually reaches
  // runEmbeddedPiAgent via deps.abortSignal → embeddedOptions.abortSignal.
  const reliableDeps = { ...deps, abortSignal: abortController.signal, chatInject: makeReliableChatInject(deps.chatInject, flowId) };

  // If the execution exits via a conflict-while-running, the reject handler
  // re-reads the latest flow state and stashes a re-drive request here. The
  // actual re-launch happens in .finally() — AFTER activeAsyncExecutions is
  // cleared — so the single-driver guard (isAsyncExecutionActive) does not
  // skip the fresh launch. See the conflict branch below + .finally() at end.
  let pendingRedrive: { state: FlowState; revision: number; retriesLeft: number } | null = null;

  const promise = executeWorkflowForTest({
    deps: reliableDeps,
    workflow,
    state,
    flowId,
    revision,
    runStartHooks: options?.runStartHooks ?? false,
    debug: options?.debug,
  }).then(
    () => {
      // Normal completion — executeLoop already sent completion notifications
      console.log(`[async-exec] flow ${flowId} completed normally`);
    },
    async (err: unknown) => {
      if (isFlowStateConflict(err)) {
        // Another writer owns the latest revision. Check the actual flow status
        // in TaskFlow to decide whether to safely exit or retry.
        // If the flow is already in a terminal/waiting state, another writer
        // has taken responsibility and we can exit cleanly.
        // If the flow is still "running", our process is the only one responsible
        // for transitioning it — we must retry execution, not silently exit.
        console.warn(`[async-exec] flow ${flowId} stopped after state conflict — checking actual flow status`);
        try {
          const actualFlow = await deps.boundTaskFlow.get(flowId);
          const actualStatus = actualFlow?.status;
          if (actualStatus && actualStatus !== "running") {
            // Flow has been transitioned by another writer (e.g., to "waiting"
            // or "failed"). Safe to exit — the other writer owns it now.
            console.log(`[async-exec] flow ${flowId} is already "${actualStatus}" in TaskFlow — exiting cleanly`);
          } else if (actualFlow && actualStatus === "running") {
            // Flow is still "running" but we lost the revision — the execution
            // loop threw a CAS mid-transition and no other writer took over.
            // Re-read the latest state and schedule a fresh async execution
            // with the new revision (mirrors setWaitingWithRevisionRetry /
            // failWithRevisionRetry). Without this the flow is left as a
            // `running` zombie and downstream nodes (e.g. a dynamic-template
            // node waiting to be materialized by
            // prepareDynamicTemplatesForExecution) never run. Bounded by
            // _conflictRedrivesLeft to prevent livelock under a persistently
            // conflicting writer. The re-launch runs in .finally() so the
            // active-execution registry is cleared first (single-driver guard).
            const retriesLeft = (options?._conflictRedrivesLeft ?? MAX_CONFLICT_REDRIVES) - 1;
            const latestRevisionRaw = (actualFlow as Record<string, unknown>).revision;
            const latestRevision = typeof latestRevisionRaw === "number"
              ? latestRevisionRaw
              : Number(latestRevisionRaw ?? revision);
            if (retriesLeft >= 0) {
              const freshState = safeParseFlowState(actualFlow as Record<string, unknown>) ?? state;
              pendingRedrive = { state: freshState, revision: latestRevision, retriesLeft };
              console.warn(
                `[async-exec] flow ${flowId} conflict-while-running — ` +
                `will re-drive with revision ${latestRevision} after exit (redrives left: ${retriesLeft})`,
              );
              deps.flowControl?.releaseAllForFlow(flowId);
              return;
            }
            console.error(
              `[async-exec] flow ${flowId} still "running" after state conflict, ` +
              `redrive exhausted — manual retry via /workflow retry`,
            );
            enqueueRunLog({
              flow_id: flowId,
              level: "error",
              source: "workflow",
              message: `State conflict redrive exhausted — manual retry required`,
              timestamp: Date.now(),
            });
          } else {
            // Flow unreadable or in an unexpected state — cannot safely re-drive.
            console.error(
              `[async-exec] flow ${flowId} is still "running" after state conflict — ` +
              `no other writer has transitioned it and re-drive is not possible ` +
              `(flow status: ${String(actualStatus)}). Manual retry via /workflow retry.`,
            );
          }
        } catch (inspectErr) {
          console.error(`[async-exec] flow ${flowId} failed to inspect status after conflict:`, inspectErr);
        }
        deps.flowControl?.releaseAllForFlow(flowId);
        return;
      }

      // Crash path: persist the terminal state before any local/user-visible closeout.
      console.error(`[async-exec] flow ${flowId} crashed:`, err);
      const errorMsg = err instanceof Error ? err.message : String(err);
      const errorStack = err instanceof Error ? (err.stack ?? "").split("\n").slice(0, 3).join(" | ") : "";
      enqueueRunLog({
        flow_id: flowId,
        level: "error",
        source: "workflow",
        message: `Flow crashed: ${errorMsg}${errorStack ? ` | stack: ${errorStack}` : ""}`,
        timestamp: Date.now(),
      });

      // Record the crash in JSONL with full stack trace for debugging.
      try {
        appendWorkflowJsonlLog(buildDirectLogRecord({
          flowId,
          eventType: "flow_crashed",
          message: `Workflow crashed: ${errorMsg}`,
          botId: _botId,
          sessionKey: _sessionKey,
          rawError: err,
        }));
      } catch { /* JSONL write is best-effort */ }

      // 1. Mark the flow as failed in TaskFlow. A revision conflict means a
      // newer writer owns the flow, so this worker may only clean up.
      try {
        const failResult = await deps.boundTaskFlow.fail({
          flowId,
          expectedRevision: revision,
          resultJson: JSON.stringify({ error: errorMsg, crashed: true }),
        });
        assertFlowStateUpdateApplied(failResult as { applied?: boolean });
      } catch (failErr) {
        if (isFlowStateConflict(failErr)) {
          console.warn(`[async-exec] flow ${flowId} crash persistence lost a state conflict`);
          deps.flowControl?.releaseAllForFlow(flowId);
          return;
        }
        const errMsg = failErr instanceof Error ? failErr.message : String(failErr);
        console.error(`[async-exec] failed to mark flow ${flowId} as failed:`, failErr);
        enqueueRunLog({
          flow_id: flowId,
          level: "error",
          source: "engine",
          message: `boundTaskFlow.fail() failed in crash handler: ${errMsg}`,
          timestamp: Date.now(),
        });
      }

      // 2. Try to notify the user via chatInject
      try {
        await deps.chatInject(
          `[taskguard] ❌ 工作流异常终止 (flowId: ${flowId})\n错误: ${errorMsg}\n请使用 /workflow inspect 查看详情。`,
          `${flowId}:flow:error:crash`,
        );
      } catch (chatErr) {
        console.error(`[async-exec] failed to send crash notification for flow ${flowId}:`, chatErr);
      }

      // 3. Release flow control slots
      deps.flowControl?.releaseAllForFlow(flowId);

      // 4. Update flow run status + dispatch notifications
      completeFlowRun(flowId, "failed", state.currentPhase ?? "running", JSON.stringify({ error: errorMsg, crashed: true }), computeDurationMs(state), state);
    },
  ).finally(() => {
    activeAsyncExecutions.delete(key);
    // Clean up per-flow registries (defensive — completeFlowRun should
    // have already deleted them, but in crash paths it may not have run).
    // ChatInject/verbosity must go too: a leaked binding would keep routing
    // this session's inject to a flowId that no longer exists.
    _workflowSpecByFlowId.delete(flowId);
    _chatInjectByFlowId.delete(flowId);
    clearFlowInjectLevel(flowId);

    // If the conflict-while-running branch scheduled a re-drive, launch it now
    // that the active-execution registry is cleared (so the fresh launch is
    // not skipped by the single-driver guard). Re-register the workflow spec
    // the new execution needs — launchAsyncExecution re-sets global
    // chatInject/verbosity itself, but not _workflowSpecByFlowId.
    if (pendingRedrive) {
      const redrive = pendingRedrive;
      pendingRedrive = null;
      _workflowSpecByFlowId.set(flowId, workflow);
      launchAsyncExecution(deps, workflow, redrive.state, flowId, redrive.revision, {
        _conflictRedrivesLeft: redrive.retriesLeft,
      });
    }
  });

  const handle: AsyncExecutionHandle = {
    flowId,
    sessionKey: deps.sessionKey,
    promise,
    abortController,
  };

  activeAsyncExecutions.set(key, handle);
  console.log(`[async-exec] launched async execution for flow ${flowId} (session: ${deps.sessionKey})`);

  return handle;
}

/** Test-only seam for observing async crash cleanup behavior. */
export function __launchAsyncExecutionForTest(
  deps: ControllerDeps,
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
  revision: number,
  options?: { runStartHooks?: boolean; debug?: boolean },
): AsyncExecutionHandle {
  // Production launches register this mapping in startWorkflowAfterPreflight.
  // Mirror that precondition so cleanup tests exercise every per-flow registry.
  _workflowSpecByFlowId.set(flowId, workflow);
  return launchAsyncExecution(deps, workflow, state, flowId, revision, options);
}

/**
 * Async-aware replacement for direct `await executeLoop(...)` calls.
 *
 * In async mode (app.execution.asyncRun = true):
 * - If an async execution is already active for this flowId, skip executeLoop
 *   (the async engine will pick up the state change automatically).
 * - If no async execution is active (flow was waiting and session reconnected),
 *   launch a new async execution.
 *
 * In sync mode (default), calls executeLoop directly — no behavior change.
 */
async function asyncAwareExecuteLoop(
  deps: ControllerDeps,
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
  revision: number,
): Promise<ExecuteLoopOutcome> {
  const { execution } = loadConfig().app;

  if (execution.asyncRun) {
    if (isAsyncExecutionActive(flowId)) {
      // The async engine is still registered as active. However, it may be
      // in the process of exiting (e.g. it already returned { status: "waiting" }
      // and the .finally() cleanup hasn't run yet). If we skip executeLoop now,
      // the state change we just wrote to TaskFlow will NOT be observed by
      // the exiting engine (it uses an in-memory copy of state, never
      // re-reads from TaskFlow), and the flow will stall indefinitely.
      //
      // Fix: wait for the in-flight engine to fully exit, then launch a new
      // one that picks up the latest TaskFlow state. We poll
      // isAsyncExecutionActive with a short sleep, up to 2 seconds.
      const MAX_WAIT_MS = 2000;
      const POLL_MS = 50;
      const waited = await waitForAsyncEngineExit(flowId, MAX_WAIT_MS, POLL_MS);

      if (waited) {
        // Engine has exited — safe to launch a fresh async execution that
        // will re-read state from TaskFlow (via the state we pass in).
        console.log(`[async-exec] in-flight engine exited after ${waited}ms — launching fresh execution for flow ${flowId}`);
        launchAsyncExecution(deps, workflow, state, flowId, revision);
        return { status: "running", message: "异步引擎已启动（等待前序引擎退出后）" };
      }

      // Engine is genuinely still running (long-running node like
      // embedded-agent). It WILL observe the state change on its next
      // loop iteration because executeLoop reads from the in-memory state
      // which we just mutated. This is the original fast-path skip.
      console.log(`[async-exec] skipping executeLoop for flow ${flowId} — async engine genuinely active (waited ${MAX_WAIT_MS}ms)`);
      return { status: "running", message: "异步引擎正在执行" };
    }

    // No async engine running — this is a resume from waiting state.
    // Launch a new async execution to continue in the background.
    console.log(`[async-exec] no active engine for flow ${flowId} — launching async execution for resume`);
    launchAsyncExecution(deps, workflow, state, flowId, revision);
    return { status: "running", message: "异步引擎已启动" };
  }

  // Sync mode: await executeLoop directly (original behavior)
  return executeLoop(deps, workflow, state, flowId, revision);
}

/**
 * Wait for an in-flight async engine to fully exit (its .finally() cleanup
 * to run and remove it from activeAsyncExecutions).
 *
 * This solves the race condition where a callback writes to TaskFlow and
 * calls asyncAwareExecuteLoop while the previous engine is still in the
 * .then()/.finally() microtask chain — not yet removed from the registry.
 *
 * Returns the number of ms waited if the engine exited, or 0 if it is
 * still active after maxWaitMs.
 */
async function waitForAsyncEngineExit(flowId: string, maxWaitMs: number, pollMs: number): Promise<number> {
  const start = Date.now();
  while (isAsyncExecutionActive(flowId)) {
    const elapsed = Date.now() - start;
    if (elapsed >= maxWaitMs) return 0;
    await sleep(Math.min(pollMs, maxWaitMs - elapsed));
  }
  return Date.now() - start;
}

export function setMetricsRepository(repo: IFlowMetricsRepository | null): void {
  _metricsRecorder = repo ? new MetricsRecorder(repo) : null;
}

export function setAlertRepository(repo: ITriggeredAlertRepository | null): void {
  _alertRecorder = repo ? new AlertRecorder(repo) : null;
}

export function setNodeExecutionRepository(repo: INodeExecutionRepository | null, maxIoSizeKb?: number): void {
  _nodeExecutionRepository = repo;
  _nodeExecutionTracker = repo ? new NodeExecutionTracker(repo, maxIoSizeKb) : null;
  console.log(`[controller] setNodeExecutionRepository: tracker=${_nodeExecutionTracker ? "initialized" : "NULL (node_executions will NOT be persisted)"} repoType=${repo?.constructor?.name ?? "null"} maxIoSizeKb=${maxIoSizeKb ?? 10}`);
}

export function getFlowRunRepository(): IFlowRunRepository | null {
  return _flowRunRepository;
}

export function setFlowRunRepository(repo: IFlowRunRepository | null): void {
  _flowRunRepository = repo;
  // If httpCallbackConfigRepo was set before flowRunRepo became available,
  // the dispatcher was created with flowRunRepo=null. Re-initialize it now
  // so that buildExtInfo can query flow_runs for enrichment data.
  if (repo && _httpCallbackConfigRepo && _httpCallbackDispatcher) {
    _httpCallbackDispatcher = new HttpCallbackDispatcher({
      flowRunRepo: repo,
      nodeExecRepo: _nodeExecutionRepository,
      logRepo: _httpCallbackLogRepo,
      configs: _httpCallbackDispatcher["configCache"] ?? new Map(),
    });
    console.log(`[controller] setFlowRunRepository: httpCallback dispatcher re-initialized with flowRunRepo (logRepo=${_httpCallbackLogRepo ? "yes" : "no"} nodeExecRepo=${_nodeExecutionRepository ? "yes" : "no"})`);
  }
}

// ── Run log uploader (for run archive) ──
let _runLogUploader: {
  enqueue(entry: RunLogInsert): void;
  start(): void;
  shutdown(): void;
  flushAll(): Promise<void>;
} | null = null;
let _runLogUploaderWarningEmitted = false;

export function setRunLogUploader(uploader: typeof _runLogUploader): void {
  _runLogUploader = uploader;
  if (uploader) {
    _runLogUploaderWarningEmitted = false;
    console.log("[controller] RunLogUploader set: uploader is now available");
    // 同步设置 fire-and-forget 模块的 enqueueRunLog 引用
    setEnqueueRunLog((entry) => {
      enqueueRunLog({
        flow_id: entry.flow_id,
        node_id: entry.node_id ?? null,
        level: entry.level,
        source: entry.source,
        message: entry.message,
        timestamp: entry.timestamp,
      });
    });
  } else {
    setEnqueueRunLog(null!);
  }
}

/**
 * 入队一条结构化日志到 RunLogUploader 的内存队列。
 * 同步操作，不阻塞，不抛异常。上报过程完全不影响工作流执行。
 */
export function enqueueRunLog(entry: RunLogInsert): void {
  if (!_runLogUploader) {
    if (!_runLogUploaderWarningEmitted) {
      _runLogUploaderWarningEmitted = true;
      console.warn(
        `[controller] enqueueRunLog called but _runLogUploader is NULL! ` +
        `flow_id=${entry.flow_id} source=${entry.source} level=${entry.level}. ` +
        `All run_logs will be silently dropped until setRunLogUploader() is called.`,
      );
    }
    return;
  }
  _runLogUploader?.enqueue(entry);
}

/** Set the engine name (openclaw/claudecode/teclaw/hermes/cli) for flow_runs.engine tracking. */
export function setEngineName(engine: string | null): void {
  _engineName = engine;
}

/** Get the current engine name. */
export function getEngineName(): string | null {
  return _engineName;
}

// ── Guardian Agent (node failure analysis at retry time) ──
let _guardianAgent: { analyze(params: GuardianAnalysisParams): Promise<GuardianRepair> } | null = null;

export function setGuardianAgent(agent: { analyze(params: GuardianAnalysisParams): Promise<GuardianRepair> } | null): void {
  _guardianAgent = agent;
}

export function setNodeStepTraceRepository(repo: INodeStepTraceRepository | null): void {
  _nodeStepTraceRepo = repo;
}

/** Get the current node step trace repository (used for progress step insertion during execution). */
export function getNodeStepTraceRepository(): INodeStepTraceRepository | null {
  return _nodeStepTraceRepo;
}

export function setHallucinationCheckRepository(repo: IHallucinationCheckRepository | null): void {
  _hallucinationCheckRepo = repo;
}

/**
 * Derive the embedded session key for an embedded-agent node.
 * Format: `${parentSessionKey}:embedded:${nodeId}:${flowId}`
 * This key is used as the session_id in Langfuse traces, enabling
 * clawweb to query Langfuse for a specific node's traces.
 */
function deriveEmbeddedSessionKey(parentSessionKey: string | undefined, nodeId: string, flowId: string, executorType?: string): string | undefined {
  if (!parentSessionKey) return undefined;
  // Only embedded-agent nodes have a real embedded session in Langfuse.
  // Other executor types (done, cli-script, bcs-route, etc.) should not
  // get a derived key — they don't produce Langfuse traces.
  if (executorType && executorType !== "embedded-agent") return undefined;
  return `${parentSessionKey}:embedded:${nodeId}:${flowId}`;
}

/**
 * Persist node step traces from an embedded-agent session JSONL file.
 *
 * Called after an embedded-agent node completes execution.
 * Extracts structured step data (tool_call / tool_result / assistant_text)
 * and batch-inserts into the node_step_traces table, then runs
 * rule-based hallucination detection and persists check results.
 * Best-effort: failures are logged but never block the workflow.
 */
export function persistNodeStepTrace(
  sessionFile: string | undefined,
  flowId: string,
  nodeId: string,
  attempt: number,
  skillName: string | null,
  embeddedSessionKey?: string,
): void {
  if (!_nodeStepTraceRepo) {
    console.warn(`[controller] persistNodeStepTrace: SKIP — _nodeStepTraceRepo is null (flowId=${flowId} nodeId=${nodeId})`);
    return;
  }
  if (!sessionFile) {
    console.warn(`[controller] persistNodeStepTrace: SKIP — sessionFile is undefined (flowId=${flowId} nodeId=${nodeId})`);
    return;
  }

  // ── Fully synchronous extraction ──────────────────────────────────────
  // Previously this used import("./runner/step-trace.js").then(...) which
  // created a Promise microtask chain.  In the OpenClaw embedded-agent
  // runtime (especially when livenessState="abandoned"), the microtask
  // chain could fail to execute before the process yields — the .then()
  // callback simply never ran, and 0 step traces were persisted.
  //
  // step-trace.ts has NO circular dependency with controller.ts (it only
  // imports `node:fs`), so we can safely use a static import and call
  // extractNodeStepTraceFromContent synchronously.  This guarantees the
  // extraction and DB insert scheduling happen in the same synchronous
  // tick as emitNodeEvent.
  const MAX_JSONL_FILE_SIZE = 10 * 1024 * 1024; // keep in sync with step-trace.ts
  let jsonlContent: string | null = null;
  let jsonlFileSize = 0;
  let jsonlLineCount = 0;
  try {
    const buffer = readFileSync(sessionFile);
    jsonlFileSize = buffer.length;
    if (buffer.length > MAX_JSONL_FILE_SIZE) {
      const tail = buffer.slice(-MAX_JSONL_FILE_SIZE).toString("utf8");
      const nl = tail.indexOf("\n");
      jsonlContent = nl >= 0 ? tail.slice(nl + 1) : tail;
    } else {
      jsonlContent = buffer.toString("utf8");
    }
    if (jsonlContent) {
      jsonlLineCount = jsonlContent.split(/\r?\n/).filter((l) => l.trim()).length;
    }
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(
      `[controller] persistNodeStepTrace: readFileSync failed — ${msg} ` +
      `(flowId=${flowId} nodeId=${nodeId} sessionFile=${sessionFile})`,
    );
  }

  console.log(
    `[controller] persistNodeStepTrace: snapshot ` +
    `sessionFile=${sessionFile} fileSize=${jsonlFileSize} ` +
    `contentLen=${jsonlContent?.length ?? 0} lineCount=${jsonlLineCount} ` +
    `flowId=${flowId} nodeId=${nodeId} attempt=${attempt}`,
  );

  if (!jsonlContent) {
    console.warn(
      `[controller] persistNodeStepTrace: SKIP — no JSONL content snapshot ` +
      `(flowId=${flowId} nodeId=${nodeId} sessionFile=${sessionFile})`,
    );
    return;
  }

  // SYNCHRONOUS call — no microtask, no Promise chain
  const trace = extractNodeStepTraceFromContent(jsonlContent, sessionFile, nodeId, flowId);
  if (!trace || trace.steps.length === 0) {
    // Dump first 5 non-empty lines for diagnosis
    const sampleLines = jsonlContent.split(/\r?\n/).filter((l) => l.trim()).slice(0, 5);
    console.warn(
      `[controller] persistNodeStepTrace: SKIP — no steps extracted ` +
      `lineCount=${jsonlLineCount} stepsFound=${trace?.steps.length ?? 0} ` +
      `flowId=${flowId} nodeId=${nodeId} sessionFile=${sessionFile}\n` +
      `  First 5 lines:\n` +
      sampleLines.map((l, i) => `    [${i}] ${l.slice(0, 200)}`).join("\n"),
    );
    return;
  }
  console.log(
    `[controller] persistNodeStepTrace: extracted ` +
    `steps=${trace.steps.length} toolCalls=${trace.toolCallCount} ` +
    `toolErrors=${trace.toolErrorCount} ` +
    `flowId=${flowId} nodeId=${nodeId}`,
  );
  const inserts = trace.steps.map((step) => ({
    flowId,
    nodeId,
    attempt,
    stepSeq: step.seq,
    stepType: step.type,
    skillName,
    toolName: step.toolName ?? null,
    toolUseId: step.toolUseId ?? null,
    toolInputJson: step.toolInput ? JSON.stringify(step.toolInput) : null,
    toolOutputText: step.toolOutput ?? null,
    isError: step.isError ? 1 : 0,
    textContent: step.text ?? null,
    sessionKey: embeddedSessionKey ?? null,
  }));

  // DB write is inherently async — fire-and-forget is fine here
  const expectedCount = inserts.length;
  void _nodeStepTraceRepo!
    .insertBatch(inserts)
    .then((count) => {
      console.log(`[controller] persistNodeStepTrace: inserted ${count} steps (flowId=${flowId} nodeId=${nodeId})`);
      // ── P1: Error observability — partial insert ──
      // If insertBatch returned fewer rows than expected (chunked API mode
      // may partially fail), persist an error progress record so the
      // failure is visible in node_step_traces (not just logs).
      if (count < expectedCount) {
        const detail = `insertBatch partial: ${count}/${expectedCount} steps inserted`;
        console.warn(
          `[controller] persistNodeStepTrace: ${detail} (flowId=${flowId} nodeId=${nodeId})`,
        );
        void persistStepTraceErrorProgress(flowId, nodeId, attempt, skillName, embeddedSessionKey, detail);
      }
    })
    .catch((e) => {
      const msg = e instanceof Error ? e.message : String(e);
      console.warn(`[controller] persistNodeStepTrace: INSERT FAILED — ${msg} (flowId=${flowId} nodeId=${nodeId})`);
      // ── P1: Error observability — total failure ──
      // Persist an error progress record so the failure is discoverable
      // in node_step_traces instead of being silently swallowed.
      void persistStepTraceErrorProgress(
        flowId, nodeId, attempt, skillName, embeddedSessionKey,
        `insertBatch failed: ${msg.substring(0, 500)}`,
      );
    });

  // Run hallucination detection (best-effort, does not block)
  if (_hallucinationCheckRepo) {
    void runHallucinationChecks(trace.steps, flowId, nodeId, attempt);
  }
}

/**
 * Persist an error progress record when insertBatch fails or partially fails.
 *
 * Without this, batch insert failures are silently swallowed (void...catch)
 * and invisible in node_step_traces. The error progress record uses a
 * negative step_seq and step_type "progress" with isError=1 so it's
 * discoverable via the standard query APIs but doesn't collide with
 * real step data.
 */
function persistStepTraceErrorProgress(
  flowId: string,
  nodeId: string,
  attempt: number,
  skillName: string | null,
  embeddedSessionKey: string | undefined,
  errorMessage: string,
): void {
  if (!_nodeStepTraceRepo) return;
  void _nodeStepTraceRepo
    .insert({
      flowId,
      nodeId,
      attempt,
      stepSeq: -1,
      stepType: "progress",
      skillName,
      textContent: `[step-trace-error] ${errorMessage}`,
      isError: 1,
      sessionKey: embeddedSessionKey ?? null,
    })
    .catch(() => {
      // The error progress record itself failed — nothing more we can do
    });
}

/**
 * Run hallucination checks against extracted step data and persist results.
 * Best-effort: errors are logged but never throw.
 */
async function runHallucinationChecks(
  steps: Array<{ seq: number; type: string; toolName?: string; toolUseId?: string; toolInput?: Record<string, unknown>; toolOutput?: string; isError?: boolean; text?: string }>,
  flowId: string,
  nodeId: string,
  attempt: number,
): Promise<void> {
  try {
    const { checkHallucination } = await import("./analysis/hallucination-checker.js");
    // Convert StepRecord[] to minimal NodeStepTraceRow-like shape
    const rows = steps.map((s) => ({
      id: 0,
      flow_id: flowId,
      node_id: nodeId,
      attempt,
      step_seq: s.seq,
      step_type: s.type,
      skill_name: null,
      tool_name: s.toolName ?? null,
      tool_use_id: s.toolUseId ?? null,
      tool_input_json: s.toolInput ? JSON.stringify(s.toolInput) : null,
      tool_output_text: s.toolOutput ?? null,
      is_error: s.isError ? 1 : 0,
      text_content: s.text ?? null,
      session_key: null,
      trace_id: null,
      observation_id: null,
      model: null,
      latency_ms: null,
      prompt_tokens: null,
      completion_tokens: null,
      gmt_create: 0,
    }));
    const result = checkHallucination(rows, flowId, nodeId, attempt);

    const checkInserts = result.checks.map((c) => ({
      flowId,
      nodeId,
      attempt,
      checkType: c.checkType,
      severity: c.severity,
      passed: c.passed ? 1 : 0,
      description: c.description,
      evidence: c.evidence,
      riskScore: result.riskScore,
      riskLevel: result.riskLevel,
    }));

    if (!_hallucinationCheckRepo) return;
    const count = await _hallucinationCheckRepo.insertChecks(checkInserts);
    console.log(`[controller] hallucinationCheck: flowId=${flowId} nodeId=${nodeId} riskLevel=${result.riskLevel} riskScore=${result.riskScore} checks=${count}`);
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e);
    console.warn(`[controller] hallucinationCheck failed: flowId=${flowId} nodeId=${nodeId} error=${msg}`);
  }
}

export function setKnowledgeBases(bases: KnowledgeBase[], config: KnowledgeConfig): void {
  _knowledgeBases = bases;
  _knowledgeConfig = config;
  _knowledgeCache = bases.length > 0 && config.enabled ? createSearchCache() : null;
}

export function setKnowledgeBaseManager(manager: KnowledgeBaseManager): void {
  _knowledgeBaseManager = manager;
}

/** Set the resolver that fetches validation template content by templateId. */
export function setValidationTemplateResolver(
  resolver: ((templateId: string) => Promise<ValidationTemplateContent | null>) | null,
): void {
  _validationTemplateResolver = resolver;
}

export function setRetryConfig(config: RetryConfig): void {
  _retryConfig = config;
  _errorContextStore = new ErrorContextStore(config.errorContextMaxEntries);
}

export function setAnalysisConfig(
  config: AnalysisConfig,
  metricsRepo: IFlowMetricsRepository | null,
): void {
  if (config.enabled && metricsRepo) {
    _analyzer = new WorkflowAnalyzer(metricsRepo);
    _thresholdChecker = new ThresholdChecker(config);
  } else {
    _analyzer = null;
    _thresholdChecker = config.enabled ? new ThresholdChecker(config) : null;
  }
}

/** Get the most recent health report (for API queries). */
export function getLastHealthReport(): HealthReport | null {
  return _lastHealthReport;
}

/** Configure alerting (DingTalk notifications + alert persistence). */
export function setAlertingConfig(config: AlertingConfig, alertRepo: ITriggeredAlertRepository | null): void {
  _alertDispatcher = new AlertDispatcher(alertRepo, config);
}

/** Initialize the workflow notification dispatcher for enterprise DingTalk notifications.
 *
 *  Called during plugin startup after config is loaded.
 *  Credentials (robotCode/appSecret) are read from each workflow's YAML — no global config needed. */
export function setWorkflowNotificationConfig(
  clawwebBaseUrl: string,
): void {
  const baseUrl = clawwebBaseUrl || "http://localhost:3001";
  _workflowNotificationDispatcher = new WorkflowNotificationDispatcher(baseUrl);
  console.log(`[controller] setWorkflowNotificationConfig: dispatcher=enabled clawwebBaseUrl=${baseUrl}`);
}

/** Set the notification config repository for reading DB-backed notification settings. */
export function setNotificationConfigRepository(
  repo: import("./db/repositories/types.js").INotificationConfigRepository | null,
): void {
  _notificationConfigRepo = repo;
  console.log(`[controller] setNotificationConfigRepository: ${repo ? "enabled" : "disabled"}`);
}

/** Set the HTTP callback config repository, then (re)initialize the dispatcher. */
export function setHttpCallbackRepositories(
  configRepo: IHttpCallbackConfigRepository | null,
): void {
  _httpCallbackConfigRepo = configRepo;
  if (configRepo) {
    _httpCallbackDispatcher = new HttpCallbackDispatcher({
      flowRunRepo: _flowRunRepository,
      nodeExecRepo: _nodeExecutionRepository,
      logRepo: _httpCallbackLogRepo,
      configs: new Map(),
    });
    console.log(`[controller] setHttpCallbackRepositories: dispatcher=enabled configRepo=yes flowRunRepo=${_flowRunRepository ? "yes" : "no(pending)"} logRepo=${_httpCallbackLogRepo ? "yes" : "no"}`);
  } else {
    _httpCallbackDispatcher = null;
    console.log(`[controller] setHttpCallbackRepositories: dispatcher=disabled (configRepo=no)`);
  }
}

/** Set the HTTP callback log repository. Must be called before setHttpCallbackRepositories. */
export function setHttpCallbackLogRepository(
  logRepo: import("./alerts/http-callback-types.js").IHttpCallbackLogRepository | null,
): void {
  _httpCallbackLogRepo = logRepo;
  console.log(`[controller] setHttpCallbackLogRepository: logRepo=${logRepo ? "yes" : "no"}`);
}

/**
 * Load HTTP callback configs from DB into the dispatcher's config cache.
 * Called at startup and after config changes via clawweb UI.
 * Falls back to YAML-declared configs for workflows without DB overrides.
 */
export async function reloadHttpCallbackConfigs(
  yamlSpecs?: Map<string, WorkflowSpec>,
): Promise<void> {
  if (!_httpCallbackConfigRepo || !_httpCallbackDispatcher) {
    console.warn(`[controller] reloadHttpCallbackConfigs: skipped (configRepo=${!!_httpCallbackConfigRepo} dispatcher=${!!_httpCallbackDispatcher})`);
    return;
  }

  const dbConfigs = new Map<string, HttpCallbackConfig[]>();
  try {
    // Load all DB configs — query all known workflow IDs from specs
    const yamlMap = new Map<string, HttpCallbackConfig[]>();
    if (yamlSpecs) {
      for (const [workflowId, spec] of yamlSpecs) {
        const yamlCallbacks = spec.notifications?.httpCallbacks;
        if (yamlCallbacks && yamlCallbacks.length > 0) {
          yamlMap.set(workflowId, yamlCallbacks.map((n) => yamlNotificationToConfig(n, workflowId)));
        }
      }
    }

    // Load DB configs for all workflows that have YAML callbacks or known workflow IDs
    const workflowIds = new Set([...(yamlMap.keys())]);
    // Also load DB-only workflow IDs (configs created via clawweb UI with no YAML declaration)
    try {
      const dbWorkflowIds = await _httpCallbackConfigRepo.findAllWorkflowIds();
      for (const wid of dbWorkflowIds) workflowIds.add(wid);
    } catch {
      // Best-effort — continue with YAML-only IDs if this fails
    }
    for (const workflowId of workflowIds) {
      try {
        const rows = await _httpCallbackConfigRepo.findByWorkflowId(workflowId);
        if (rows.length > 0) {
          dbConfigs.set(workflowId, rows.map(rowToHttpCallbackConfig));
        }
      } catch {
        // Best-effort per-workflow load
      }
    }

    const merged = mergeCallbackConfigs(yamlMap, dbConfigs);
    _httpCallbackDispatcher.updateConfigs(merged);

    const totalConfigs = [...merged.values()].reduce((sum, configs) => sum + configs.length, 0);
    console.log(`[controller] reloadHttpCallbackConfigs: loaded ${totalConfigs} configs across ${merged.size} workflows`);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[controller] reloadHttpCallbackConfigs failed: ${msg}`);
  }
}

/**
 * Hot-load HTTP callback configs for a single workflowId.
 *
 * Called from startWorkflowAfterPreflight to ensure the dispatcher's
 * configCache is populated for the workflow being launched, even if
 * reloadHttpCallbackConfigs hasn't seen this workflowId yet (e.g.
 * configs created via clawweb UI after engine startup).
 *
 * Best-effort: failures are logged but never throw.
 */
export async function ensureHttpCallbackConfigForWorkflow(workflowId: string): Promise<void> {
  if (!_httpCallbackConfigRepo || !_httpCallbackDispatcher) {
    console.warn(`[controller] ensureHttpCallbackConfigForWorkflow: skipped for workflow=${workflowId} (configRepo=${!!_httpCallbackConfigRepo} dispatcher=${!!_httpCallbackDispatcher})`);
    return;
  }

  // If configCache already has this workflowId, skip
  if (_httpCallbackDispatcher.hasConfigForWorkflow(workflowId)) {
    console.log(`[controller] ensureHttpCallbackConfigForWorkflow: already cached for workflow=${workflowId}`);
    return;
  }

  try {
    const rows = await _httpCallbackConfigRepo.findByWorkflowId(workflowId);
    if (rows.length > 0) {
      const configs = rows.map(rowToHttpCallbackConfig);
      _httpCallbackDispatcher.addConfigsForWorkflow(workflowId, configs);
      console.log(`[controller] ensureHttpCallbackConfigForWorkflow: loaded ${configs.length} config(s) for workflow=${workflowId} (first config: id=${configs[0]?.id} name=${configs[0]?.name} enabled=${configs[0]?.enabled} notifyOn=[${configs[0]?.notifyOn.join(",") ?? ""}])`);
    } else {
      console.warn(`[controller] ensureHttpCallbackConfigForWorkflow: NO configs found in DB for workflow=${workflowId} (rows=0)`);
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[controller] ensureHttpCallbackConfigForWorkflow failed for workflow=${workflowId}: ${msg}`);
  }
}

/**
 * Resolve notification config from clawweb DB (via repository).
 * Returns undefined if repo is not set, query fails, or no config exists.
 * Fire-and-forget: never throws, never blocks workflow execution.
 */
async function resolveDbNotificationConfig(workflowId: string): Promise<import("./types.js").DingTalkNotificationConfig | undefined> {
  if (!_notificationConfigRepo) return undefined;
  try {
    const row = await _notificationConfigRepo.findByWorkflowId(workflowId);
    if (!row) return undefined;
    return rowToDingtalkConfig(row);
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[controller] resolveDbNotificationConfig failed for ${workflowId}: ${msg}`);
    return undefined;
  }
}

/** Run post-workflow analysis. Best-effort: errors are logged, never thrown. */
async function runPostWorkflowAnalysis(
  workflowId: string,
  flowId: string,
  startTime: number,
): Promise<void> {
  if (!_analyzer || !_thresholdChecker) return;
  try {
    const result = await _analyzer.analyze(workflowId, flowId, startTime, Math.floor(Date.now() / 1000));
    const report = _thresholdChecker.check(result);
    _lastHealthReport = report;

    // Dispatch alerts (DB persistence + DingTalk) for threshold breaches
    if (report.hasBreaches && _alertDispatcher) {
      void _alertDispatcher.dispatchBreaches(report).catch((err) => recordFailure("postWorkflowHealthCheck.dispatchBreaches", flowId, undefined, err, "warn"));
    }
  } catch (error) {
    const msg = error instanceof Error ? error.message : String(error);
    console.warn(`[analysis] Post-workflow analysis failed: ${msg}`);
  }
}

/**
 * Emit a node lifecycle event to all registered persistence hooks.
 * Hooks are called synchronously but perform async DB writes internally.
 */
function emitNodeEvent(event: NodeLifecycleEvent, payload: NodeLifecyclePayload): void {
  _metricsRecorder?.onEvent(event, payload);
  _alertRecorder?.onEvent(event, payload);
  if (event === "node_succeeded" || event === "node_failed") {
    console.log(`[controller] emitNodeEvent(${event}) flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} executorType=${payload.executorType} tracker=${_nodeExecutionTracker ? "yes" : "NO"} flowRunRepo=${_flowRunRepository ? "yes" : "NO"}`);
    const nodeMsg = event === "node_failed"
      ? `Node failed: ${payload.nodeId}, executor=${payload.executorType}, attempt=${payload.attempt}, error=${(payload.error ?? "unknown").slice(0, 200)}`
      : `Node succeeded: ${payload.nodeId}, executor=${payload.executorType}, attempt=${payload.attempt}`;
    enqueueRunLog({
      flow_id: payload.flowId,
      node_id: payload.nodeId,
      level: event === "node_failed" ? "error" : "info",
      source: "node",
      message: nodeMsg,
      timestamp: Date.now(),
    });
  }
  void (_nodeExecutionTracker?.onEvent(event, payload) as unknown as Promise<void> | undefined)?.catch((err) => {
    const errMsg = err instanceof Error ? err.message : String(err);
    console.warn(`[controller] emitNodeEvent.tracker failed: flowId=${payload.flowId} nodeId=${payload.nodeId} event=${event} error=${errMsg}`);
    enqueueRunLog({
      flow_id: payload.flowId,
      node_id: payload.nodeId,
      level: "warn",
      source: "fire-and-forget",
      message: `emitNodeEvent.tracker failed: ${errMsg}`,
      timestamp: Date.now(),
    });
    recordFailure("emitNodeEvent.tracker", payload.flowId, payload.nodeId, err, "warn");
  });

  // Persist step traces for embedded-agent nodes after they complete.
  // Previously this was called from embedded-agent.ts finally block, but the
  // executor context may be abandoned by the OpenClaw runtime (livenessState=
  // "abandoned"), preventing the finally block from completing. Moving the
  // call here ensures it runs in the Controller's event loop, which is never
  // abandoned — this is the same proven path used for flow_runs/node_executions.
  // Persist step traces only on terminal events (node_succeeded or
  // node_failed with willRetry=false). Retriable failures share the
  // same sessionFile which accumulates across attempts — calling
  // persistNodeStepTrace on each would re-insert earlier steps.
  const isTerminal = event === "node_succeeded"
    || (event === "node_failed"
      && payload.systemContext
      && (payload.systemContext as Record<string, unknown>).willRetry === false);
  if (isTerminal && payload.executorType === "embedded-agent") {
    console.log(
      `[controller] emitNodeEvent: calling persistNodeStepTrace ` +
      `sessionFile=${payload.sessionFile ?? "UNDEFINED"} ` +
      `embeddedSessionKey=${payload.embeddedSessionKey ?? "none"} ` +
      `flowId=${payload.flowId} nodeId=${payload.nodeId} attempt=${payload.attempt} ` +
      `event=${event}`,
    );
    persistNodeStepTrace(
      payload.sessionFile,
      payload.flowId,
      payload.nodeId,
      payload.attempt,
      payload.skillName ?? null,
      payload.embeddedSessionKey,
    );
  }

  // Increment flow_runs succeeded/failed counts
  if (_flowRunRepository) {
    if (event === "node_succeeded" || event === "node_skipped") {
      void withRetry(
        () => _flowRunRepository!.incrementNodeCount(payload.flowId, "succeeded_count"),
        { callerId: "incrementNodeCount(succeeded)", flowId: payload.flowId, nodeId: payload.nodeId },
        (e) => {
          const errMsg = e instanceof Error ? e.message : String(e);
          console.error("[taskguard] incrementNodeCount succeeded failed after retries:", errMsg);
          enqueueRunLog({
            flow_id: payload.flowId,
            level: "error",
            source: "engine",
            message: `incrementNodeCount succeeded_count failed: ${errMsg}`,
            timestamp: Date.now(),
          });
          recordFailure("incrementNodeCount.succeeded", payload.flowId, payload.nodeId, e, "error");
        },
      );
    } else if (event === "node_failed" && payload.attempt <= 1) {
      // Only count first-attempt failures to avoid double-counting retries
      void withRetry(
        () => _flowRunRepository!.incrementNodeCount(payload.flowId, "failed_count"),
        { callerId: "incrementNodeCount(failed)", flowId: payload.flowId, nodeId: payload.nodeId },
        (e) => {
          const errMsg = e instanceof Error ? e.message : String(e);
          console.error("[taskguard] incrementNodeCount failed failed after retries:", errMsg);
          enqueueRunLog({
            flow_id: payload.flowId,
            level: "error",
            source: "engine",
            message: `incrementNodeCount failed_count failed: ${errMsg}`,
            timestamp: Date.now(),
          });
          recordFailure("incrementNodeCount.failed", payload.flowId, payload.nodeId, e, "error");
        },
      );
    }
  }

  // Dispatch DingTalk + DB alert for final node failures
  if (event === "node_failed" && _alertDispatcher) {
    const failureEvent: NodeFailureEvent = {
      nodeId: payload.nodeId,
      flowId: payload.flowId,
      workflowId: payload.workflowId,
      error: payload.error ?? "Unknown error",
      attempt: payload.attempt,
    };
    void _alertDispatcher.dispatchNodeFailure(failureEvent).catch((err) => recordFailure("emitNodeEvent.dispatchNodeFailure", payload.flowId, payload.nodeId, err, "warn"));
  }

  // (New) Workflow-level notification — single-chat + enterprise group messages
  // Credentials and targets are read from the workflow YAML notifications config
  // Look up spec by flowId for concurrent flow isolation, fall back to legacy singleton
  const workflowSpecForNotification = _workflowSpecByFlowId.get(payload.flowId) ?? _currentWorkflowSpec;
  if (event === "node_failed") {
    console.log(`[controller] notification-check: dispatcher=${_workflowNotificationDispatcher ? "yes" : "NO"} workflowSpec=${workflowSpecForNotification ? workflowSpecForNotification.id : "NULL"} flowId=${payload.flowId}`);
  }
  if (event === "node_failed" && _workflowNotificationDispatcher && workflowSpecForNotification) {
    console.log(`[controller] dispatching node_failure notification for workflow=${workflowSpecForNotification.id} node=${payload.nodeId}`);
    enqueueRunLog({
      flow_id: payload.flowId,
      node_id: payload.nodeId,
      level: "info",
      source: "notification",
      message: `Dispatching node_failure notification for workflow=${workflowSpecForNotification.id} node=${payload.nodeId}`,
      timestamp: Date.now(),
    });
    void (async () => {
      const dbNotifConfig = await resolveDbNotificationConfig(payload.workflowId);
      _workflowNotificationDispatcher!.dispatchNodeFailure(workflowSpecForNotification!, {
        workflowTitle: workflowSpecForNotification.title,
        workflowId: payload.workflowId,
        flowId: payload.flowId,
        nodeId: payload.nodeId,
        nodeTitle: payload.nodeTitle,
        error: payload.error ?? "Unknown error",
        attempt: payload.attempt,
      }, dbNotifConfig).catch((err) => recordFailure("emitNodeEvent.workflowNotification", payload.flowId, payload.nodeId, err, "warn"));
    })();
  }

  // ── chatInject notification to teclaw for node failure (MCP standalone mode) ──
  // Gated by the flow's inject level: perf suppresses per-node failure
  // notifications; the flow-completion bookend (buildFlowCompletedMessage) still
  // reports the failure at the end. Route through THIS flow's bound inject
  // flow's notification never lands in the wrong session.
  const flowChatInject = event === "node_failed" ? resolveChatInjectForFlow(payload.flowId) : undefined;
  const flowVerbosity = event === "node_failed" ? resolveInjectLevelForFlow(payload.flowId) : "full";
  if (event === "node_failed" && flowChatInject && shouldInjectForFlow(payload.flowId, "node-failed")) {
    const nodeSpec = workflowSpecForNotification?.nodes.find((n) => n.id === payload.nodeId);
    const retryStatus = payload.systemContext as { retryAttempt?: number; maxRetries?: number } | undefined;
    const downstreamNodes = workflowSpecForNotification
      ? findDownstreamNodes(workflowSpecForNotification.nodes, payload.nodeId)
      : [];
    const failedMsg = buildNodeFailedMessage({
      node: nodeSpec ?? { id: payload.nodeId, executor: { type: payload.executorType ?? "unknown" } } as WorkflowNode,
      nodeIndex: resolveNodeIndex(payload.flowId, payload.nodeId),
      executorType: payload.executorType ?? "unknown",
      error: payload.error ?? "Unknown error",
      resolvedInput: undefined,
      retryStatus: retryStatus?.retryAttempt != null ? { attempt: retryStatus.retryAttempt, maxAttempts: retryStatus.maxRetries ?? 1 } : undefined,
      downstreamNodes,
      level: flowVerbosity,
    });
    console.log(`[controller] chatInject node_failed: flowId=${payload.flowId} nodeId=${payload.nodeId} flowChatInject=yes verbosity=${flowVerbosity}`);
    void flowChatInject(failedMsg, `${payload.flowId}:node:${payload.nodeId}:failed`)
      .then(() => { console.log(`[controller] chatInject node_failed delivered: flowId=${payload.flowId} nodeId=${payload.nodeId}`); })
      .catch((e: unknown) => { console.error(`[controller] chatInject node_failed FAILED: flowId=${payload.flowId} nodeId=${payload.nodeId} error=${e instanceof Error ? e.message : String(e)}`); });
  } else if (event === "node_failed") {
    console.log(`[controller] chatInject node_failed SKIPPED: flowId=${payload.flowId} nodeId=${payload.nodeId} flowChatInject=${flowChatInject ? "yes" : "NO"}`);
  }

  // ── HTTP callback notification for node lifecycle events ──
  if (_httpCallbackDispatcher) {
    console.log(`[controller] httpCallback dispatchNodeEvent: flowId=${payload.flowId} workflowId=${payload.workflowId} node=${payload.nodeId} event=${event}`);
    enqueueRunLog({
      flow_id: payload.flowId,
      node_id: payload.nodeId,
      level: "info",
      source: "notification",
      message: `HTTP callback dispatched: event=${event}, node=${payload.nodeId}`,
      timestamp: Date.now(),
    });
    void _httpCallbackDispatcher.dispatchNodeEvent(event, payload).catch((err) => {
      console.warn(`[controller] httpCallback dispatchNodeEvent failed: flowId=${payload.flowId} event=${event} error=${err instanceof Error ? err.message : String(err)}`);
    });
  } else if (event === "node_failed" || event === "node_succeeded" || event === "node_skipped") {
    console.warn(`[controller] httpCallback dispatchNodeEvent SKIPPED: _httpCallbackDispatcher is null for flowId=${payload.flowId} event=${event} node=${payload.nodeId}`);
  }
}

/**
 * Report mid-execution progress for a node (e.g., BaaS runId after submit).
 * Updates the progress_message in node_executions and emits a node_progress event.
 * Safe to call from executors at any time during execution.
 */
export function reportNodeProgress(
  flowId: string,
  workflowId: string,
  nodeId: string,
  executorType: string,
  attempt: number,
  progressMessage: string,
): void {
  emitNodeEvent("node_progress", {
    flowId,
    workflowId,
    nodeId,
    executorType,
    attempt,
    progressMessage,
  });
}

/** Best-effort sync of flow_runs.current_phase and status to the engine DB. */
function syncFlowRunPhase(flowId: string, currentPhase: string, status?: string): void {
  if (!_flowRunRepository) { console.warn(`[taskguard] syncFlowRunPhase: _flowRunRepository is null, skipping flowId=${flowId}`); return; }
  console.log(`[taskguard] syncFlowRunPhase flowId=${flowId} currentPhase=${currentPhase} status=${status ?? "(none)"}`);
  void withRetry(
    () => _flowRunRepository!.updateCurrentPhase(flowId, currentPhase),
    { callerId: "updateCurrentPhase", flowId },
    (e) => {
      const errMsg = e instanceof Error ? e.message : String(e);
      console.error("[taskguard] updateCurrentPhase failed after retries:", errMsg);
      enqueueRunLog({
        flow_id: flowId,
        level: "error",
        source: "engine",
        message: `syncFlowRunPhase updateCurrentPhase failed: ${errMsg}`,
        timestamp: Date.now(),
      });
      recordFailure("syncFlowRunPhase.updateCurrentPhase", flowId, undefined, e, "error");
    },
  ).then(() => { console.log(`[taskguard] updateCurrentPhase OK flowId=${flowId}`); }).catch(() => { /* handled in onExhausted */ });
  if (status) {
    void withRetry(
      () => _flowRunRepository!.updateStatus(flowId, status),
      { callerId: "updateStatus", flowId },
      (e) => {
        const errMsg = e instanceof Error ? e.message : String(e);
        console.error("[taskguard] updateStatus failed after retries:", errMsg);
        enqueueRunLog({
          flow_id: flowId,
          level: "error",
          source: "engine",
          message: `syncFlowRunPhase updateStatus failed: ${errMsg}`,
          timestamp: Date.now(),
        });
        recordFailure("syncFlowRunPhase.updateStatus", flowId, undefined, e, "error");
      },
    ).then(() => { console.log(`[taskguard] updateStatus OK flowId=${flowId} status=${status}`); }).catch(() => { /* handled in onExhausted */ });
  }
}

/** Summarize node states for logging, e.g. "succeeded=3, failed=1, waiting=2, pending=5" */
function summarizeNodeStates(state: FlowState | undefined): string {
  if (!state?.nodeStates) return "no-state";
  const counts: Record<string, number> = {};
  for (const ns of Object.values(state.nodeStates)) {
    const s = ns.status ?? "unknown";
    counts[s] = (counts[s] || 0) + 1;
  }
  return Object.entries(counts).map(([s, c]) => `${s}=${c}`).join(", ") || "empty";
}

/** List failed node IDs with their errors for logging. */
function listFailedNodes(state: FlowState | undefined): string {
  if (!state?.nodeStates) return "";
  const failed = Object.entries(state.nodeStates)
    .filter(([, ns]) => ns.status === "failed")
    .map(([id, ns]) => `${id}(${(ns.error ?? "unknown").slice(0, 80)})`)
    .join("; ");
  return failed || "";
}

/** Persist node result to flow_runs.
 *
 *  FUNDAMENTAL FIX (result_json race condition):
 *  This function NO LONGER writes to flow_runs.result_json.
 *
 *  Previously, it fired fire-and-forget updateResultJson() calls that raced
 *  with completeFlowRun()'s final structured write, causing YAML outputs to
 *  be intermittently overwritten by stale node-level { nodeId, output } data.
 *
 *  Node outputs are already persisted in node_executions.output_json via
 *  emitNodeEvent(). The flow_runs.result_json column is now written ONLY by
 *  completeFlowRun(), which is the sole authoritative writer.
 *
 *  This function is kept as a no-op placeholder to minimize call-site changes.
 *  It can be removed entirely once call sites are cleaned up.
 */
function persistNodeResult(_flowId: string, _nodeId: string, _result: Record<string, unknown> | undefined): void {
  // Intentional no-op — see comment above.
  // Node outputs are persisted via emitNodeEvent() to node_executions.output_json.
  // flow_runs.result_json is written exclusively by completeFlowRun().
}

/**
 * Persist the current flow state to TaskFlow (best-effort).
 * Used by the orchestrator pipeline to save state between iterations.
 */
async function persistStateToFlow(deps: ControllerDeps, state: FlowState, flowId: string): Promise<void> {
  try {
    // Use the setWaiting method with a no-op waiting config to persist state
    // This is the standard way to save flow state in the controller
    await deps.boundTaskFlow.setWaiting({
      flowId,
      nodeId: "__orchestrator-persist__",
      prompt: "",
      stateJson: JSON.stringify(state),
    });
  } catch (err) {
    console.warn(`[controller] persistStateToFlow failed for flowId=${flowId}:`, err instanceof Error ? err.message : err);
  }
}

/** Build a structured failure result_json from flow state.
 *  Includes workflowId, phase, all failed nodes with their IDs, titles,
 *  executor types, error messages, attempt counts, and duration.
 *  This provides rich debugging context directly in flow_runs.result_json
 *  without needing to join node_executions.
 */
function buildFailedResultJson(
  flowId: string,
  currentPhase: string,
  resultJson: string | null | undefined,
  totalDurationMs: number | null | undefined,
  state: FlowState | undefined,
): string {
  const workflowSpec = _workflowSpecByFlowId.get(flowId) ?? _currentWorkflowSpec;
  const failedNodes: Array<{
    nodeId: string;
    nodeTitle?: string;
    executorType?: string;
    error: string;
    attempt: number;
    /** Full executor result from the last failed attempt, including
     *  EmbeddedAgentResult.meta (teclawDiagnostic, etc.), payloads,
     *  and messagingToolSentTexts. Null when not available. */
    result?: Record<string, unknown> | null;
    /** Warnings from the failed execution. */
    warnings?: ExecutionWarning[] | null;
    /** Duration of the last failed attempt in ms. */
    durationMs?: number | null;
  }> = [];

  if (state?.nodeStates) {
    for (const [nodeId, ns] of Object.entries(state.nodeStates)) {
      if (ns.status === "failed") {
        const nodeSpec = workflowSpec?.nodes.find((n) => n.id === nodeId);
        const duration = ns.completedAt && ns.startedAt
          ? ns.completedAt - ns.startedAt
          : null;
        failedNodes.push({
          nodeId,
          nodeTitle: nodeSpec?.title,
          executorType: nodeSpec?.executor?.type,
          error: ns.error ?? "Unknown error",
          attempt: ns.attempts ?? 1,
          // Include the full result object for detailed diagnostics.
          // This carries teclawDiagnostic meta, payloads, etc.
          result: ns.result ?? null,
          warnings: ns.warnings ?? null,
          durationMs: duration,
        });
      }
    }
  }

  return JSON.stringify({
    status: "failed",
    flowId,
    workflowId: state?.workflowId,
    phase: currentPhase,
    durationMs: totalDurationMs ?? null,
    failedNodes,
    // Preserve original resultJson as a message if it's a simple string
    message: resultJson ?? undefined,
    nodeSummary: summarizeNodeStates(state),
    // Include resolved workflow outputs if available (even on failure —
    // some outputs may have been resolved before the failure occurred).
    outputs: extractWorkflowOutputs(state),
  });
}

/** Extract resolved workflow outputs from FlowState.workflowData.outputs.
 *  Returns undefined when outputs are absent or not a record. */
function extractWorkflowOutputs(state: FlowState | undefined): Record<string, unknown> | undefined {
  if (!state?.workflowData?.outputs) return undefined;
  if (!isRecord(state.workflowData.outputs)) return undefined;
  const outputs = state.workflowData.outputs as Record<string, unknown>;
  if (Object.keys(outputs).length === 0) return undefined;
  return outputs;
}

/** Find the last succeeded node's ID and result from flow state.
 *  Returns undefined when no succeeded node is found. */
function findLastSucceededNodeOutput(state: FlowState | undefined): { nodeId: string; result: Record<string, unknown> } | undefined {
  if (!state?.nodeStates) return undefined;
  let lastSucceeded: { nodeId: string; completedAt: number; result: Record<string, unknown> } | undefined;
  for (const [nodeId, ns] of Object.entries(state.nodeStates)) {
    if (ns.status === "succeeded" && ns.result && ns.completedAt != null) {
      if (!lastSucceeded || ns.completedAt > lastSucceeded.completedAt) {
        lastSucceeded = { nodeId, completedAt: ns.completedAt, result: ns.result as Record<string, unknown> };
      }
    }
  }
  return lastSucceeded ? { nodeId: lastSucceeded.nodeId, result: lastSucceeded.result } : undefined;
}

/** Build a structured success result_json from flow state.
 *  Includes the last succeeded node's output AND resolved workflow outputs
 *  so that flow_runs.result_json is self-contained for consumers. */
function buildSuccessResultJson(
  flowId: string,
  currentPhase: string,
  totalDurationMs: number | null | undefined,
  state: FlowState | undefined,
): string {
  const outputs = extractWorkflowOutputs(state);
  const lastNode = findLastSucceededNodeOutput(state);

  const result: Record<string, unknown> = {
    status: "succeeded",
    flowId,
    workflowId: state?.workflowId,
    phase: currentPhase,
    durationMs: totalDurationMs ?? null,
    nodeSummary: summarizeNodeStates(state),
  };

  // Include last succeeded node's output in a dedicated field
  if (lastNode) {
    result.lastNodeOutput = { nodeId: lastNode.nodeId, ...lastNode.result };
  }

  // Include resolved YAML outputs section values
  if (outputs) {
    result.outputs = outputs;
  }

  return JSON.stringify(result);
}

/** Best-effort completion of a flow_runs row. */
function completeFlowRun(flowId: string, status: string, currentPhase: string, resultJson?: string | null, totalDurationMs?: number | null, state?: FlowState, explicitWorkflowId?: string): void {
  const nodeSummary = summarizeNodeStates(state);
  const failedNodes = status === "failed" ? listFailedNodes(state) : "";
  console.log(`[controller] ══ FLOW_STATE_CHANGE ══ flowId=${flowId} status=${status} phase=${currentPhase} duration=${totalDurationMs ?? "n/a"}ms nodes=[${nodeSummary}]${failedNodes ? ` failed_nodes=[${failedNodes}]` : ""}`);
  enqueueRunLog({
    flow_id: flowId,
    level: status === "failed" ? "error" : "info",
    source: "workflow",
    message: `Flow completed: status=${status}, phase=${currentPhase}, duration=${totalDurationMs ?? "n/a"}ms, nodes=[${nodeSummary}]${failedNodes ? `, failed_nodes=[${failedNodes}]` : ""}`,
    timestamp: Date.now(),
  });

  // ── DB persistence (depends on _flowRunRepository) ──
  // This section is gated on _flowRunRepository, but notifications below are NOT.
  if (_flowRunRepository) {
    // On failure, build structured resultJson with detailed failure info
    // (failed node IDs, titles, executor types, errors, attempts) so that
    // flow_runs.result_json is self-contained for debugging without joins.
    // On success, build structured resultJson with resolved workflow outputs
    // (from YAML `outputs:` definitions) AND the last succeeded node's output.
    const effectiveResultJson = status === "failed" && state
      ? buildFailedResultJson(flowId, currentPhase, resultJson, totalDurationMs, state)
      : status === "failed"
        ? resultJson ?? null   // failed but no state — use caller-provided resultJson
        : status === "succeeded"
          ? buildSuccessResultJson(flowId, currentPhase, totalDurationMs, state)
          : undefined;            // waiting/blocked — preserve existing result_json

    // On failure, also ensure input_json is populated from state.input so
    // the flow_runs row is self-contained for debugging even if the initial
    // fire-and-forget INSERT failed or hasn't committed yet.
    const inputJson = state?.input ? JSON.stringify({
      message: state.input.message ?? null,
      params: state.input.params ?? {},
      digest: state.input.digest ?? null,
      fileCount: state.input.files?.length ?? 0,
    }) : undefined;

    // Compute succeeded/failed counts from state for final reconciliation.
    // This ensures the counts are correct even if incrementNodeCount calls
    // were missed (e.g., _flowRunRepository was null during execution).
    let succeededCount: number | undefined;
    let failedCount: number | undefined;
    if (state?.nodeStates) {
      let sCount = 0;
      let fCount = 0;
      for (const ns of Object.values(state.nodeStates)) {
        if (ns.status === "succeeded" || ns.status === "skipped") sCount++;
        else if (ns.status === "failed") fCount++;
      }
      succeededCount = sCount;
      failedCount = fCount;
    }

    // FUNDAMENTAL FIX: completeFlowRun is the SOLE writer of flow_runs.result_json.
    // persistNodeResult no longer writes to result_json (it's a no-op), so there
    // is no fire-and-forget race to guard against. The completion write below
    // is the only write path for the final structured result_json.
    const completionPromise = _flowRunRepository.updateCompletion(flowId, {
      status,
      currentPhase,
      resultJson: effectiveResultJson,
      inputJson,
      // Guard: clamp values to MySQL BIGINT signed max (9,223,372,036,854,775,807)
      // to prevent out-of-range errors even after the BIGINT migration.
      // The practical risk is totalDurationMs exceeding INT range (2,147,483,647)
      // for zombie flows that ran for >24.8 days.
      totalDurationMs: totalDurationMs != null
        ? Math.min(totalDurationMs, Number.MAX_SAFE_INTEGER)
        : null,
      // totalTokenUsage: not computed here — will be populated by clawweb
      // from aggregated node_execution token_usage_json when accuracy is ensured.
      succeededCount,
      failedCount,
      completedAt: Math.min(Math.floor(Date.now() / 1000), Number.MAX_SAFE_INTEGER),
    }).catch((e) => {
      const errMsg = e instanceof Error ? e.message : String(e);
      console.error("[taskguard] updateCompletion failed:", errMsg);
      enqueueRunLog({
        flow_id: flowId,
        level: "error",
        source: "engine",
        message: `flowRunRepo.updateCompletion failed: ${errMsg}`,
        timestamp: Date.now(),
      });
    });

    // Reconcile stale "running" node_executions when a flow reaches terminal state.
    // Any node still marked as "running" in node_executions after the flow has
    // completed (succeeded/failed) is a race condition or orphaned execution.
    // Mark them as "skipped" (on success) or "failed" (on failure) so that
    // succeeded_count/failed_count remain accurate.
    // NOTE: Fire-and-forget with retries — completeFlowRun is synchronous so we
    // cannot await. Failures are logged via recordFailure.
    const isTerminalStatus = status === "succeeded" || status === "failed" || status === "cancelled";
    if (isTerminalStatus && _nodeExecutionRepository) {
      void withRetry(
        () => _nodeExecutionRepository!.reconcileStaleRunning(flowId, status),
        { callerId: "reconcileStaleRunning", flowId, maxRetries: 3, baseDelayMs: 500 },
        (e) => {
          const errMsg = e instanceof Error ? e.message : String(e);
          console.error("[taskguard] reconcileStaleRunning failed after retries:", errMsg);
          enqueueRunLog({
            flow_id: flowId,
            level: "error",
            source: "engine",
            message: `reconcileStaleRunning failed: ${errMsg}`,
            timestamp: Date.now(),
          });
          recordFailure("reconcileStaleRunning", flowId, undefined, e, "error");
        },
      ).catch(() => { /* handled in onExhausted */ });
    }

    // Store the completion promise so that the HTTP callback below can await it
    // before querying flow_runs, ensuring the callback sees the final status.
    // Only store for terminal statuses — "waiting"/"blocked" don't trigger callbacks
    // and would leak entries in the Map if stored.
    if (isTerminalStatus) {
      _pendingCompletionPromises.set(flowId, completionPromise);
    }
  } else {
    console.warn(`[taskguard] completeFlowRun: _flowRunRepository is null — DB persist skipped for flowId=${flowId}`);
  }

  // (New) Aggregated workflow failure notification via enterprise DingTalk
  // Credentials and targets are read from the workflow YAML notifications config
  // Look up spec by flowId for concurrent flow isolation, fall back to legacy singleton
  const workflowSpecForCompletion = _workflowSpecByFlowId.get(flowId) ?? _currentWorkflowSpec;
  const workflowIdForCompletion = state?.workflowId
    ?? explicitWorkflowId
    ?? workflowSpecForCompletion?.id
    ?? "";
  if (status === "failed") {
    console.log(`[controller] completeFlowRun-notification-check: dispatcher=${_workflowNotificationDispatcher ? "yes" : "NO"} workflowSpec=${workflowSpecForCompletion ? workflowSpecForCompletion.id : "NULL"} state=${state ? "yes" : "NO"} flowId=${flowId}`);
  }
  if (status === "failed" && _workflowNotificationDispatcher && workflowSpecForCompletion && state) {
    const failedNodes = Object.entries(state.nodeStates)
      .filter(([, ns]) => ns.status === "failed")
      .map(([nodeId, ns]) => {
        const nodeSpec = workflowSpecForCompletion!.nodes.find((n) => n.id === nodeId);
        return {
          nodeId,
          nodeTitle: nodeSpec?.title,
          error: ns.error ?? "Unknown error",
          attempt: ns.attempts ?? 1,
        };
      });

    if (failedNodes.length > 0) {
      enqueueRunLog({
        flow_id: flowId,
        level: "info",
        source: "notification",
        message: `Dispatching workflow_failure notification: failedNodes=[${failedNodes.map((n) => n.nodeId).join(",")}]`,
        timestamp: Date.now(),
      });
      void (async () => {
        const dbNotifConfig = await resolveDbNotificationConfig(state!.workflowId);
        _workflowNotificationDispatcher!.dispatchWorkflowFailure(workflowSpecForCompletion!, {
          workflowTitle: workflowSpecForCompletion.title,
          workflowId: state!.workflowId,
          flowId,
          failedNodes,
        }, dbNotifConfig).catch((err) => recordFailure("completeFlowRun.dispatchWorkflowFailure", flowId, undefined, err, "warn"));
      })();
    }
  }

  // ── HTTP callback notification for workflow completion ──
  // Wait for the updateCompletion DB write to settle before dispatching the
  // callback, so that buildExtInfo() sees the final flow_runs.status instead
  // of a stale value (e.g. "running" or a previous "failed" from a prior run).
  if (status === "succeeded" || status === "failed" || status === "cancelled") {
    const workflowId = state?.workflowId ?? explicitWorkflowId ?? workflowSpecForCompletion?.id ?? "";
    if (_httpCallbackDispatcher) {
      if (workflowIdForCompletion) {
        console.log(`[controller] httpCallback dispatchWorkflowEvent: flowId=${flowId} workflowId=${workflowIdForCompletion} status=${status}`);
        const completionPromise = _pendingCompletionPromises.get(flowId);
        const dispatch = () => {
          _pendingCompletionPromises.delete(flowId);
          void _httpCallbackDispatcher!.dispatchWorkflowEvent(workflowIdForCompletion, flowId, status).catch((err) => {
            console.warn(`[controller] httpCallback dispatchWorkflowEvent failed: flowId=${flowId} status=${status} error=${err instanceof Error ? err.message : String(err)}`);
          });
        };
        if (completionPromise) {
          void completionPromise.then(dispatch, dispatch);
        } else {
          dispatch();
        }
      } else {
        console.warn(`[controller] httpCallback dispatchWorkflowEvent SKIPPED: workflowId is empty for flowId=${flowId} status=${status} (state=${state ? "yes" : "NO"} explicitWorkflowId=${explicitWorkflowId ?? "undefined"} specId=${workflowSpecForCompletion?.id ?? "null"})`);
      }
    } else {
      console.warn(`[controller] httpCallback dispatchWorkflowEvent SKIPPED: _httpCallbackDispatcher is null for flowId=${flowId} status=${status}`);
    }
  }

  // ── chatInject notification to teclaw (MCP standalone mode) ──
  // When running as MCP server for teclaw, flow completion/failure must be
  // sent via chatInject (WS chat.inject or MCP notification) so teclaw
  // learns about the outcome. In OpenClaw plugin mode, the per-flow inject
  // is also bound but chatInject goes to OpenClaw's chat stream — harmless.
  // Route through THIS flow's bound inject (resolved by flowId) so a concurrent
  // flow's completion never lands in the wrong session.
  const flowChatInject = resolveChatInjectForFlow(flowId);
  const flowVerbosity = resolveInjectLevelForFlow(flowId);
  if (flowChatInject && (status === "failed" || status === "succeeded" || status === "cancelled")) {
    const workflowTitle = workflowSpecForCompletion?.title ?? state?.workflowId ?? flowId;
    // Build the first failed node detail for the enhanced message
    const firstFailedNode = status === "failed" && state ? (() => {
      const failedEntry = Object.entries(state.nodeStates).find(([, ns]) => ns.status === "failed");
      if (!failedEntry) return undefined;
      const [fNodeId, fNs] = failedEntry;
      return {
        nodeId: fNodeId,
        error: fNs.error ?? "Unknown error",
        input: fNs.result as Record<string, unknown> | undefined,
        retryStatus: fNs.attempts != null ? { attempt: fNs.attempts, maxAttempts: fNs.retry?.maxAttempts ?? 1 } : undefined,
      };
    })() : undefined;

    const completionMsg = buildFlowCompletedMessage({
      flowId,
      workflowTitle,
      workflowId: workflowIdForCompletion,
      status: status as "succeeded" | "failed" | "cancelled",
      currentPhase,
      totalDurationMs,
      nodeStates: state?.nodeStates ?? {},
      nodes: workflowSpecForCompletion?.nodes ?? [],
      failedNode: firstFailedNode,
      level: flowVerbosity,
      workflowOutputs: isRecord(state?.workflowData?.outputs) ? state!.workflowData.outputs as Record<string, unknown> : undefined,
      workflowOutputsSpec: workflowSpecForCompletion?.outputs,
    });

    // ── Backfill failed inject messages ──
    // If any chat.inject calls failed after all retries during this flow's
    // execution, append their previews to the completion message so the user
    // can see what was missed rather than messages silently disappearing.
    const failedPreviews = _failedInjectMessagesByFlowId.get(flowId);
    const finalMsg = failedPreviews && failedPreviews.length > 0
      ? completionMsg + "\n\n" + [
          "━━━━━━━━━━━━━━━━━━━━━━━━━",
          `⚠️ 以下 ${failedPreviews.length} 条消息在执行过程中因网络/网关问题未能送达，回填如下：`,
          "━━━━━━━━━━━━━━━━━━━━━━━━━",
          ...failedPreviews.map((p, i) => `[${i + 1}] ${p}`),
        ].join("\n")
      : completionMsg;

    const completionOptions: ChatInjectOptions | undefined =
      status === "succeeded" || status === "failed"
        ? {
            messageType: status === "failed"
              ? ChatInjectMessageType.Error
              : ChatInjectMessageType.Info,
            flowId,
            workflowId: workflowIdForCompletion,
            metadata: {
              _clawmind: true,
              schemaVersion: 1,
              workflowFinished: true,
              workflowStatus: status,
            },
          }
        : undefined;

    console.log(`[controller] chatInject flow_completion: status=${status} flowId=${flowId} flowChatInject=yes verbosity=${flowVerbosity} msg_len=${finalMsg.length} failed_injects=${failedPreviews?.length ?? 0}`);
    void flowChatInject(finalMsg, `${flowId}:flow:${status}`, completionOptions)
      .then(() => { console.log(`[controller] chatInject flow_completion delivered: flowId=${flowId} status=${status}`); })
      .catch((e: unknown) => { console.error(`[controller] chatInject flow_completion FAILED: flowId=${flowId} status=${status} error=${e instanceof Error ? e.message : String(e)}`); });
  } else if (status === "failed" || status === "succeeded" || status === "cancelled") {
    console.log(`[controller] chatInject flow_completion SKIPPED: flowId=${flowId} status=${status} flowChatInject=${flowChatInject ? "yes" : "NO"}`);
  }

  // Clean up per-flow registries to prevent memory leaks and stale bindings
  // (a leaked chatInject binding would keep dispatching to the wrong session).
  _workflowSpecByFlowId.delete(flowId);
  _chatInjectByFlowId.delete(flowId);
  clearFlowInjectLevel(flowId);
  _failedInjectMessagesByFlowId.delete(flowId);
}

/**
 * Recover orphaned flows after engine restart.
 *
 * Scans flow_runs for flows in "running" status that belong to the current
 * bot + plugin instance (origin_bot_id + engine). For each such flow, reads
 * the TaskFlow state and resumes execution via asyncAwareExecuteLoop.
 *
 * Design principles:
 * - No extra `engine_started` tracking field needed — recovery path is the
 *   same whether setTimeout(fn, 0) never fired or the process crashed mid-execution.
 * - Revision CAS on boundTaskFlow.resume()/fail() ensures only one process
 *   drives a given flow; duplicate recovery attempts silently skip.
 * - workflowSnapshot from FlowState is used to load the spec, avoiding
 *   incompatibilities with newer pack versions.
 *
 * @param deps - Full ControllerDeps object. Must include boundTaskFlow, executeNode,
 *               actionRegistry, and abortSignal for executeLoop to work.
 * @param botId - Current bot ID for origin filtering
 * @param engine - Current engine name for origin filtering
 * @param concurrency - Max number of flows to recover concurrently (default 3)
 * @param maxFlows - Max number of flows to scan (default 50)
 */
export async function recoverOrphanedFlows(
  deps: ControllerDeps,
  botId: string,
  engine: string,
  concurrency = 3,
  maxFlows = 50,
): Promise<void> {
  if (!_flowRunRepository) {
    console.warn("[controller] recoverOrphanedFlows: _flowRunRepository is null — skipping recovery");
    return;
  }

  const runningFlows = await _flowRunRepository.findRunningByOrigin(botId, engine, maxFlows);
  if (runningFlows.length === 0) {
    console.log(`[controller] recoverOrphanedFlows: no orphaned flows found (botId=${botId}, engine=${engine})`);
    return;
  }

  console.log(`[controller] recoverOrphanedFlows: found ${runningFlows.length} orphaned flow(s) (botId=${botId}, engine=${engine})`);

  enqueueRunLog({
    flow_id: "__engine__",
    level: "info",
    source: "engine",
    message: `recover_orphaned_flows:start botId=${botId} engine=${engine} count=${runningFlows.length}`,
    timestamp: Date.now(),
  });

  let successCount = 0;
  let failCount = 0;
  let skipCount = 0;

  const recoverOne = async (flowId: string): Promise<void> => {
    try {
      console.log(`[controller] recoverOrphanedFlows: recovering flow ${flowId}`);

      const flow = await deps.boundTaskFlow.get(flowId);
      if (!flow) {
        console.warn(`[controller] recoverOrphanedFlows: flow ${flowId} not found in TaskFlow — skipping`);
        enqueueRunLog({
          flow_id: flowId, level: "warn", source: "engine",
          message: "recover_orphaned_flow:skip flow not found in TaskFlow",
          timestamp: Date.now(),
        });
        skipCount++;
        return;
      }

      const revision = flow.revision as number;
      const state = parseFlowState(flow);
      const status = flow.status as string;

      // Only recover flows that are in a resumable state.
      // "running" in flow_runs may lag behind TaskFlow if the engine
      // crashed mid-update; skip flows that have already progressed
      // past "running" or entered a terminal state in TaskFlow.
      if (status !== "running" && status !== "waiting" && status !== "blocked") {
        console.log(`[controller] recoverOrphanedFlows: flow ${flowId} TaskFlow status="${status}" — not resumable, skipping`);
        skipCount++;
        return;
      }

      // Load workflow spec from state snapshot for version compatibility.
      // deps is full ControllerDeps so loadWorkflowForState has all needed fields.
      const workflow = await loadWorkflowForState(deps, state);

      // Set up per-flow chatInject if available
      if (deps.chatInject) {
        setGlobalChatInject(deps.chatInject, flowId);
      }
      setFlowInjectLevel("full", flowId);

      appendAuditLog(state, "__recovery__", "engine-recovery", `引擎重启后自动恢复 flow (status=${status}, revision=${revision})`);

      let effectiveRevision = revision;

      // If TaskFlow status is not already "running", CAS via resume().
      if (status !== "running") {
        const resumeResult = await deps.boundTaskFlow.resume({
          flowId,
          expectedRevision: revision,
          status: "running",
          currentStep: state.activeNodes[0] ?? "recovery",
          stateJson: JSON.stringify(state),
        });

        if (!resumeResult.applied) {
          console.warn(`[controller] recoverOrphanedFlows: flow ${flowId} revision conflict on resume — skipping (likely recovered by another instance)`);
          enqueueRunLog({
            flow_id: flowId, level: "warn", source: "engine",
            message: "recover_orphaned_flow:conflict revision conflict on resume — another instance may have recovered this flow",
            timestamp: Date.now(),
          });
          skipCount++;
          return;
        }

        // Use the new revision from resume result if available, otherwise re-read
        effectiveRevision = (resumeResult.flow?.revision as number)
          ?? ((await deps.boundTaskFlow.get(flowId))?.revision as number)
          ?? revision;
      }

      // Sync flow_runs status
      syncFlowRunPhase(flowId, state.currentPhase, "running");

      // Launch execution via async-aware path
      await asyncAwareExecuteLoop(deps, workflow, state, flowId, effectiveRevision);

      console.log(`[controller] recoverOrphanedFlows: flow ${flowId} recovered successfully`);
      enqueueRunLog({
        flow_id: flowId, level: "info", source: "engine",
        message: `recover_orphaned_flow:ok revision=${effectiveRevision}`,
        timestamp: Date.now(),
      });
      successCount++;
    } catch (error) {
      const errMsg = error instanceof Error ? error.message : String(error);
      console.error(`[controller] recoverOrphanedFlows: flow ${flowId} failed: ${errMsg}`);

      enqueueRunLog({
        flow_id: flowId, level: "error", source: "engine",
        message: `recover_orphaned_flow:fail error=${errMsg}`,
        timestamp: Date.now(),
      });

      // Mark the flow as failed so it doesn't remain orphaned
      try {
        completeFlowRun(flowId, "failed", "recovery");
      } catch (completeErr) {
        const completeMsg = completeErr instanceof Error ? completeErr.message : String(completeErr);
        console.warn(`[controller] recoverOrphanedFlows: completeFlowRun threw: ${completeMsg}`);
      }

      failCount++;
    }
  };

  // Use fixed-size batches for proper concurrency control.
  // This avoids the Promise.race anti-pattern where all flows could
  // end up running simultaneously.
  const runBatches = async (): Promise<void> => {
    for (let i = 0; i < runningFlows.length; i += concurrency) {
      const batch = runningFlows.slice(i, i + concurrency);
      await Promise.all(batch.map((row) => recoverOne(row.flow_id)));
    }
  };

  await runBatches();

  console.log(`[controller] recoverOrphanedFlows: done — success=${successCount} failed=${failCount} skipped=${skipCount} total=${runningFlows.length}`);
  enqueueRunLog({
    flow_id: "__engine__",
    level: "info",
    source: "engine",
    message: `recover_orphaned_flows:done botId=${botId} engine=${engine} success=${successCount} failed=${failCount} skipped=${skipCount}`,
    timestamp: Date.now(),
  });
}

/**
 * Flow-timeout watchdog.
 *
 * Marks flows stuck in "running" longer than `timeoutMs` as **failed**. This is
 * the safety net for flows whose in-memory executor was abandoned (e.g. agent
 * session went zombie, engine restarted, chat.inject storm) and would otherwise
 * sit in "running" forever — the flow-control zombie-escape does NOT cover this
 * when `flowControl.enabled=false`.
 *
 * DB-level: it does not need the in-memory FlowState, so it can reap flows the
 * current process never owned. Only status="running" is targeted; flows parked
 * as "waiting"/"blocked" (e.g. awaiting human approval) are left alone. It also
 * aborts the in-memory async execution (if one is active in this process) so the
 * running LLM/tool call rejects with AbortError and the executor finalizes. If
 * the flow was abandoned in another process it can only flip the DB status.
 *
 * @returns number of flows reaped.
 */
export async function reapStaleRunningFlows(
  timeoutMs: number,
  limit = 100,
  getStateForFlow?: (flowId: string) => Promise<FlowState | undefined>,
  /**
   *  Fail the flow in TaskFlow (status + revision update).
   *  When provided, reapStaleRunningFlows will call boundTaskFlow.fail() BEFORE
   *  completeFlowRun(), so that the in-memory executeLoop encounters a revision
   *  conflict on its next boundTaskFlow.resume()/fail() call and exits without
   *  overwriting the "failed" status with "succeeded". This prevents the race
   *  condition where the watchdog marks the flow failed in flow_runs but the
   *  executeLoop (unaware of the watchdog's action) later marks it succeeded.
   */
  failFlowInTaskFlow?: (flowId: string, stateJson: string, blockedSummary: string) => Promise<boolean>,
  /**
   *  Resolve per-workflow timeout in minutes. When provided, each flow is
   *  checked against its own workflow's `flowTimeoutMinutes` instead of the
   *  global default. Returns undefined if the workflow has no per-workflow
   *  override (fall back to the global timeoutMs). May be async (e.g. a DB
   *  lookup); awaited per stale row.
   */
  getWorkflowTimeoutMinutes?:
    | ((workflowId: string) => number | undefined | Promise<number | undefined>),
): Promise<number> {
  if (!_flowRunRepository) return 0;
  const globalTimeoutSecs = Math.max(1, Math.floor(timeoutMs / 1000));

  // Use the shortest possible timeout as the cutoff for the DB query.
  // This ensures we don't miss any flows that might have a shorter per-workflow
  // timeout. Per-workflow filtering happens in the row loop below.
  let shortestTimeoutSecs = globalTimeoutSecs;
  // The cutoff is conservatively computed from the global timeout. If a
  // per-workflow timeout is shorter, those flows will be caught by this cutoff
  // and then precisely filtered below. If a per-workflow timeout is longer,
  // those flows will be caught by the cutoff but skipped in the row loop
  // (ranForSecs < perWorkflowTimeoutSecs).
  const cutoff = Math.floor(Date.now() / 1000) - shortestTimeoutSecs;

  let rows: Awaited<ReturnType<IFlowRunRepository["findStaleRunning"]>>;
  try {
    rows = await _flowRunRepository.findStaleRunning(cutoff, limit);
  } catch (e) {
    console.error(`[controller] reapStaleRunningFlows: findStaleRunning threw: ${(e as Error)?.message ?? e}`);
    return 0;
  }
  if (rows.length === 0) return 0;

  let reapedCount = 0;
  const nowSecs = Math.floor(Date.now() / 1000);
  for (const row of rows) {
    const ranForSecs = Math.max(0, nowSecs - (row.started_at ?? nowSecs));

    // Resolve per-workflow timeout: per-workflow YAML > global config.
    let effectiveTimeoutSecs = globalTimeoutSecs;
    if (getWorkflowTimeoutMinutes) {
      const perWorkflowMinutes = await getWorkflowTimeoutMinutes(row.workflow_id);
      if (perWorkflowMinutes !== undefined) {
        if (perWorkflowMinutes === 0) {
          // flowTimeoutMinutes=0 in workflow YAML disables the watchdog for this workflow.
          continue;
        }
        effectiveTimeoutSecs = Math.max(1, perWorkflowMinutes * 60);
      }
    }

    // Skip flows that haven't exceeded their effective timeout.
    if (ranForSecs < effectiveTimeoutSecs) continue;

    console.warn(
      `[controller] FLOW_TIMEOUT flowId=${row.flow_id} workflow=${row.workflow_id} ` +
      `ranFor=${ranForSecs}s > timeout=${effectiveTimeoutSecs}s — aborting execution and marking failed`,
    );
    enqueueRunLog({
      flow_id: row.flow_id,
      level: "error",
      source: "workflow",
      message: `Flow timed out: ranFor=${ranForSecs}s > timeout=${effectiveTimeoutSecs}s`,
      timestamp: Date.now(),
    });

    // 1. Abort the in-memory async execution so the running LLM/tool call
    //    throws AbortError → node failed → executeLoop finalizes. If the flow
    //    was abandoned (no handle in this process, e.g. after a restart) this
    //    is a no-op and the completeFlowRun below still reaps the DB row.
    const abortedInMemory = abortAsyncExecutionForFlow(row.flow_id);
    if (!abortedInMemory) {
      console.log(`[controller] FLOW_TIMEOUT flowId=${row.flow_id} — no active in-process execution; only DB status reaped`);
    }

    // 2. Try to obtain FlowState for richer failure diagnostics.
    // When state is available, completeFlowRun can build structured result_json
    // (failed node IDs, titles, errors) and compute accurate succeeded/failed counts.
    let flowState: FlowState | undefined;
    if (getStateForFlow) {
      try {
        flowState = await getStateForFlow(row.flow_id);
      } catch (e) {
        console.warn(`[controller] reapStaleRunningFlows: getStateForFlow threw for ${row.flow_id}: ${(e as Error)?.message ?? e}`);
      }
    }

    // 3. Persist the "failed" terminal status.
    const reason = JSON.stringify({
      error: `工作流执行超时（运行 ${ranForSecs}s，超过 ${effectiveTimeoutSecs}s 上限），已自动终止并置为失败`,
      reason: "flow_timeout",
      ranForSecs,
      timeoutSecs: effectiveTimeoutSecs,
      abortedInMemory,
    });

    // 3a. Fail the flow in TaskFlow so that the in-memory executeLoop
    //     encounters a revision conflict on its next boundTaskFlow.resume()
    //     or boundTaskFlow.fail() call and exits without overwriting the
    //     "failed" status with "succeeded". This is the critical fix for the
    //     race condition where the watchdog marks the flow failed but the
    //     executeLoop (still running in-memory after abort) later marks it
    //     succeeded because all remaining nodes complete normally.
    if (failFlowInTaskFlow && flowState) {
      try {
        const failed = await failFlowInTaskFlow(
          row.flow_id,
          JSON.stringify(flowState),
          `工作流执行超时（运行 ${ranForSecs}s，超过 ${effectiveTimeoutSecs}s 上限），已自动终止并置为失败`,
        );
        if (failed) {
          console.log(`[controller] FLOW_TIMEOUT flowId=${row.flow_id} — TaskFlow status updated to failed`);
        } else {
          console.warn(`[controller] FLOW_TIMEOUT flowId=${row.flow_id} — TaskFlow fail() returned false (revision conflict or already terminal)`);
        }
      } catch (e) {
        // Revision conflict means another writer already owns the flow.
        // This is expected when the executeLoop already transitioned the flow.
        const msg = e instanceof Error ? e.message : String(e);
        console.warn(`[controller] FLOW_TIMEOUT flowId=${row.flow_id} — TaskFlow fail() threw: ${msg}`);
      }
    }

    // 3b. Update flow_runs table with the failed status.
    // Pass FlowState when available so completeFlowRun can build structured
    // failure info. Pass row.workflow_id explicitly so HTTP callback can
    // dispatch workflow_failed even without a FlowState object.
    completeFlowRun(row.flow_id, "failed", row.current_phase ?? "timeout", reason, ranForSecs * 1000, flowState, row.workflow_id);
    reapedCount += 1;
  }
  return reapedCount;
}

/** Compute total flow duration in ms from the first event to now. */
function computeDurationMs(state: FlowState): number | null {
  const start = firstFlowEventTime(state);
  if (!start || start <= 0) return null;
  // now() returns Date.now() (milliseconds), start is also milliseconds.
  // The difference is already in ms — do NOT multiply by 1000.
  return now() - start;
}

function appendFlowEvent(
  state: FlowState,
  event: Omit<FlowEvent, "id" | "time" | "data" | "error"> & {
    data?: Record<string, unknown>;
    error?: string | null;
  },
  options: { log?: boolean; rawError?: unknown } = {},
): FlowEvent {
  state.flowEvents ??= [];
  flowEventSeq += 1;
  const record: FlowEvent = {
    id: `evt_${flowEventSeq}`,
    time: now(),
    data: {},
    error: null,
    ...event,
  };
  state.flowEvents.push(record);
  if (options.log !== false) {
    void appendWorkflowJsonlLog(buildWorkflowLogRecord({ event: record, sessionKey: _sessionKey, botId: _botId, ownerId: _ownerId, rawError: options.rawError })).catch(() => { /* best-effort log */ });
  }
  // Dual-write to database (best-effort, non-blocking)
  if (_eventRepository) {
    void _eventRepository.insert({
      id: record.id,
      time: record.time,
      type: record.type,
      flowId: record.flowId,
      workflowId: record.workflowId,
      nodeId: record.nodeId ?? null,
      actionId: record.actionId ?? null,
      attempt: record.attempt ?? null,
      data: record.data,
      error: record.error ?? null,
    }).catch(() => { /* best-effort, ignore */ });
  }
  if (state.flowEvents.length > MAX_FLOW_EVENTS) {
    state.flowEvents.splice(0, state.flowEvents.length - MAX_FLOW_EVENTS);
  }
  return record;
}

export const appendFlowEventForTest = appendFlowEvent;

function summarizeRecord(value: Record<string, unknown> | undefined): Record<string, unknown> {
  if (!value) return { hasResult: false };
  const keys = Object.keys(value);
  return {
    hasResult: true,
    resultKeys: keys.slice(0, 20),
    resultKeyCount: keys.length,
  };
}

export const summarizeFlowEventRecordForTest = summarizeRecord;

function truncateDebugText(value: string, max = 2_000): string {
  return value.length > max ? `${value.slice(0, max)}...` : value;
}

function sanitizeFailedResultDebug(value: Record<string, unknown> | undefined): Record<string, unknown> | undefined {
  if (!value) return undefined;
  const debug: Record<string, unknown> = {};
  for (const key of ["parseFailureRawOutput", "repairParseFailureRawOutput", "repairRunError"]) {
    const item = value[key];
    if (typeof item === "string") {
      debug[key] = truncateDebugText(item);
    }
  }
  return Object.keys(debug).length > 0 ? debug : undefined;
}

function formatNodeRetryCommandHint(deps: Pick<ControllerDeps, "formatWorkflowCommand">, workflowId: string, nodeId: string): string {
  return [
    "可复制重试命令：",
    formatWorkflowCommand(deps, workflowId, "retry", ["--node", nodeId]),
  ].join("\n");
}

function summarizeBcsApprovalResult(value: Record<string, unknown>): Record<string, unknown> {
  const summary: Record<string, unknown> = {
    ...summarizeRecord(value),
  };
  for (const key of ["approved", "action", "reviewerRole", "taskId", "batchId", "approvalType"]) {
    if (value[key] !== undefined) summary[key] = value[key];
  }
  return summary;
}

function collectApprovalResults(state: FlowState, nodeId: string): Array<{ senderId: string; approved: boolean }> {
  const results: Array<{ senderId: string; approved: boolean }> = [];
  const nodeState = state.nodeStates[nodeId];
  if (!nodeState) return results;

  // Check existing result
  const existingResult = nodeState.result as Record<string, unknown> | undefined;
  if (existingResult) {
    const senderId = (existingResult.reviewerId as string) ?? (existingResult.reviewerBot as string) ?? "unknown";
    const approved = existingResult.approved === true;
    results.push({ senderId, approved });
  }

  // Check audit log for previous partial approvals
  const auditEntries = (state as Record<string, unknown>).auditLog as Array<Record<string, unknown>> | undefined;
  if (auditEntries) {
    for (const entry of auditEntries) {
      if (entry.nodeId === nodeId && entry.event === "partial-approval" && entry.data) {
        const data = entry.data as Record<string, unknown>;
        const partialResults = data.approvedResults as Array<{ senderId: string; approved: boolean }> | undefined;
        if (partialResults) {
          for (const r of partialResults) {
            if (!results.some((existing) => existing.senderId === r.senderId)) {
              results.push(r);
            }
          }
        }
      }
    }
  }

  return results;
}

function ensureFlowStateDefaults(state: FlowState): FlowState {
  state.workflowData ??= {};
  state.actionOutputs ??= {};
  state.flowHooks ??= {};
  state.loopGroups = isRecord(state.loopGroups) ? state.loopGroups as FlowState["loopGroups"] : {};
  state.dynamicTemplates = isRecord(state.dynamicTemplates) ? state.dynamicTemplates as FlowState["dynamicTemplates"] : {};
  state.runtimeNodeMeta = isRecord(state.runtimeNodeMeta) ? state.runtimeNodeMeta as FlowState["runtimeNodeMeta"] : {};
  state.injectedNodes ??= [];
  state.orchestrationState = isRecord(state.orchestrationState) ? state.orchestrationState as FlowState["orchestrationState"] : {};
  state.llmEvaluations = isRecord(state.llmEvaluations) ? state.llmEvaluations as FlowState["llmEvaluations"] : {};
  return state;
}

function parseFlowState(flow: Record<string, unknown>): FlowState {
  return ensureFlowStateDefaults(parseRawFlowState(flow) as FlowState);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function normalizeFlowListResult(
  value: { flows?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>,
): Array<Record<string, unknown>> {
  if (Array.isArray(value)) return value;
  return Array.isArray(value.flows) ? value.flows : [];
}

function safeParseFlowState(flow: Record<string, unknown>): FlowState | null {
  try {
    return parseFlowState(flow);
  } catch (err) {
    const flowId = typeof flow.flowId === "string" ? flow.flowId : "unknown";
    console.warn(`[controller] safeParseFlowState failed for flowId=${flowId}:`, (err as Error)?.message ?? err);
    enqueueRunLog({
      flow_id: flowId,
      level: "error",
      source: "engine",
      message: `parseFlowState failed: ${(err as Error)?.message ?? String(err)}`,
      timestamp: Date.now(),
    });
    return null;
  }
}

function readFlowRecordStatus(flow: Record<string, unknown>): string {
  const status = flow.status ?? flow.flow_status;
  return typeof status === "string" && status.length > 0 ? status : "-";
}

function readFlowRecordRevision(flow: Record<string, unknown>): string {
  const revision = flow.revision ?? flow.rev;
  return revision === undefined || revision === null ? "-" : String(revision);
}

function readFlowRecordTime(flow: Record<string, unknown>): string {
  const time = flow.updatedAt ?? flow.gmt_modified ?? flow.endedAt ?? flow.ended_at ?? flow.createdAt ?? flow.gmt_create;
  return formatLocalDateTime(time);
}

function readOptionalFlowId(flow: Record<string, unknown>): string {
  try {
    return readFlowId(flow);
  } catch {
    return "-";
  }
}

function markdownCell(value: unknown): string {
  return String(value ?? "-").replace(/\|/g, "\\|").replace(/\n/g, " ");
}

function formatLocalDateTime(value: unknown): string {
  const date = typeof value === "number"
    ? new Date(value)
    : typeof value === "string"
      ? new Date(value)
      : null;
  if (!date || Number.isNaN(date.getTime())) return "-";
  const parts = new Intl.DateTimeFormat("zh-CN", {
    timeZone: "Asia/Shanghai",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).formatToParts(date);
  const pick = (type: string) => parts.find((part) => part.type === type)?.value ?? "00";
  return `${pick("year")}-${pick("month")}-${pick("day")} ${pick("hour")}:${pick("minute")}:${pick("second")}`;
}

function formatDuration(ms: number): string {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  if (totalSeconds < 60) return `${totalSeconds}s`;
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  if (minutes < 60) return `${minutes}m${seconds.toString().padStart(2, "0")}s`;
  const hours = Math.floor(minutes / 60);
  const restMinutes = minutes % 60;
  return `${hours}h${restMinutes.toString().padStart(2, "0")}m`;
}

function nodeDurationLabel(nodeState: NodeState | undefined, nowMs = now()): string {
  if (!nodeState || typeof nodeState.startedAt !== "number") return "";
  const end = typeof nodeState.completedAt === "number" ? nodeState.completedAt : nowMs;
  const duration = formatDuration(end - nodeState.startedAt);
  if (nodeState.status === "waiting") return ` — 已等待 ${duration}`;
  if (nodeState.status === "running" || nodeState.status === "postActionsRunning" || nodeState.status === "blocked") {
    return ` — 已运行 ${duration}`;
  }
  if (typeof nodeState.completedAt === "number") return ` — 耗时 ${duration}`;
  return "";
}

function firstFlowEventTime(state: FlowState): number | undefined {
  return state.flowEvents?.find((event) => event.type === "workflow_started")?.time
    ?? state.flowEvents?.[0]?.time;
}

function lastFlowEventTime(state: FlowState): number | undefined {
  return state.flowEvents?.[state.flowEvents.length - 1]?.time;
}

async function findActiveFlowForIdentity(
  deps: ControllerDeps,
  workflowId: string,
  identity: FlowIdentity,
): Promise<Record<string, unknown> | null> {
  const flows = await findActiveFlowsForIdentity(deps, workflowId, identity);
  return flows[0] ?? null;
}

async function findActiveFlowsForIdentity(
  deps: ControllerDeps,
  workflowId: string,
  identity: FlowIdentity,
): Promise<Array<Record<string, unknown>>> {
  const flows = normalizeFlowListResult(await deps.boundTaskFlow.list());
  const matched: Array<Record<string, unknown>> = [];
  for (const flow of flows) {
    const status = typeof flow.status === "string" ? flow.status : "";
    if (!ACTIVE_FLOW_STATUSES.has(status)) continue;

    let state: FlowState;
    try {
      state = parseFlowState(flow);
    } catch (err) {
      const flowId = typeof flow.flowId === "string" ? flow.flowId : "unknown";
      console.warn(`[controller] reapStaleRunningFlows: parseFlowState failed for flowId=${flowId}:`, (err as Error)?.message ?? err);
      enqueueRunLog({
        flow_id: flowId,
        level: "error",
        source: "engine",
        message: `parseFlowState failed in reapStaleRunningFlows: ${(err as Error)?.message ?? String(err)}`,
        timestamp: Date.now(),
      });
      continue;
    }

    // A soft-cleaned (hidden) failed flow must not block new runs. `/workflow runs
    // cleanup` marks flows hidden precisely so reruns can proceed; without this guard
    // cleanup only hides them from the runs list while the duplicate guard keeps
    // rejecting same-identity reruns — so cleanup appears to do nothing.
    if (flowIsHidden(state)) continue;

    const stateIdentityKey = state.identity?.key;
    if (state.workflowId === workflowId && stateIdentityKey === identity.key) {
      matched.push(flow);
    }
  }
  return matched;
}

function formatDuplicateWorkflowError(
  flow: Record<string, unknown>,
  workflowId: string,
  identity: FlowIdentity,
): string {
  const status = typeof flow.status === "string" ? flow.status : "未知";
  return [
    "已有同 identity 的未清理流程，未启动新流程。",
    `workflow：${workflowId}`,
    `identity：${identity.label} (${identity.key})`,
    `flowId：${readFlowId(flow)}`,
    `currentStep：${readCurrentStep(flow) ?? "未知"}`,
    `status：${status}`,
    `如确认要重跑，请先清理旧流程或使用对应 workflow 的 reopen/cleanup 命令。`,
  ].join("\n");
}

async function assertNoDuplicateActiveFlow(
  deps: ControllerDeps,
  workflowId: string,
  identity: FlowIdentity,
): Promise<string | undefined> {
  if (identity.duplicatePolicy === "allow") return undefined;

  const activeFlow = await findActiveFlowForIdentity(deps, workflowId, identity);
  if (!activeFlow) return undefined;
  if (identity.duplicatePolicy === "reuse-active") return readFlowId(activeFlow);

  throw new Error(formatDuplicateWorkflowError(activeFlow, workflowId, identity));
}

function snapshotHookStatuses(
  hooks: WorkflowNode["onSuccess"],
  states: Record<string, { status?: string }>,
): Map<string, string | undefined> {
  return new Map((hooks ?? []).map((hook) => [hook.id, states[hook.id]?.status]));
}

async function runHookActionsWithEvents(params: {
  hooks: WorkflowNode["onSuccess"];
  states: Record<string, ActionState>;
  registry: ActionRegistry;
  context: ActionExecutionContext;
  state: FlowState;
  flowId: string;
  scope: "workflow" | "node";
  lifecycle: "onStart" | "onFinish" | "onSuccess" | "validation";
  nodeId?: string;
}): Promise<HookRunOutcome> {
  for (const hook of params.hooks ?? []) {
    if (params.states[hook.id]?.status === "succeeded") continue;

    appendFlowEvent(params.state, {
      type: "action_started",
      flowId: params.flowId,
      workflowId: params.state.workflowId,
      nodeId: params.nodeId,
      actionId: hook.id,
      data: {
        action: hook.action,
        required: hook.required === true,
        scope: params.scope,
        lifecycle: params.lifecycle,
        nodeId: params.nodeId,
      },
    });

    const outcome = await runHookActions({
      hooks: [hook],
      states: params.states,
      registry: params.registry,
      context: params.context,
    });
    if (outcome.status === "blocked") return outcome;
  }

  return { status: "succeeded" };
}

function appendSucceededHookEvents(
  state: FlowState,
  flowId: string,
  hooks: WorkflowNode["onSuccess"],
  states: Record<string, { status?: string }>,
  before: Map<string, string | undefined>,
  nodeId?: string,
): void {
  for (const hook of hooks ?? []) {
    if (before.get(hook.id) === "succeeded") continue;
    if (states[hook.id]?.status !== "succeeded") continue;
    appendFlowEvent(state, {
      type: "action_succeeded",
      flowId,
      workflowId: state.workflowId,
      nodeId,
      actionId: hook.id,
      data: { action: hook.action },
    });
  }
}

function normalizeNodeRetry(node: WorkflowNode): Required<NonNullable<WorkflowNode["retry"]>> {
  return {
    maxAttempts: node.retry?.maxAttempts ?? 1,
    backoffMs: node.retry?.backoffMs ?? 0,
    on: node.retry?.on ?? ["executor-failed"],
  };
}

function nodeRetryHandles(node: WorkflowNode, reason: NodeRetryFailureReason): boolean {
  return normalizeNodeRetry(node).on.includes(reason);
}

function clearNodeValidationState(state: FlowState, nodeId: string): void {
  if (state.nodeValidationStates?.[nodeId]) {
    state.nodeValidationStates[nodeId] = {};
  }
}

async function runNodeValidationActions(params: {
  node: WorkflowNode;
  state: FlowState;
  flowId: string;
  registry: ActionRegistry;
  context: ActionExecutionContext;
}): Promise<HookRunOutcome> {
  if (!params.node.validation?.actions?.length) {
    return { status: "succeeded" };
  }
  params.state.nodeValidationStates ??= {};
  params.state.nodeValidationStates[params.node.id] ??= {};
  return runHookActionsWithEvents({
    hooks: params.node.validation.actions,
    states: params.state.nodeValidationStates[params.node.id],
    registry: params.registry,
    context: params.context,
    state: params.state,
    flowId: params.flowId,
    scope: "node",
    lifecycle: "validation",
    nodeId: params.node.id,
  });
}

async function sleep(ms: number): Promise<void> {
  if (ms <= 0) return;
  await new Promise((resolve) => setTimeout(resolve, ms));
}

function initNodeState(node: WorkflowNode, executorType: string, previousState?: NodeState): NodeState {
  const retry = normalizeNodeRetry(node);
  const nodeState: NodeState = {
    status: "running",
    phase: node.phase,
    executor: executorType,
    startedAt: now(),
    attempts: 0,
    retry,
    progressMessageIds: [],
  };
  if (previousState?.manualRetries !== undefined) {
    nodeState.manualRetries = previousState.manualRetries;
  }
  return nodeState;
}

async function executeNodeWithRetry(
  deps: ControllerDeps,
  node: WorkflowNode,
  templateCtx: TemplateContext,
  state: FlowState,
  flowId: string,
  workflow?: WorkflowSpec,
): Promise<ExecutorResult> {
  const retry = normalizeNodeRetry(node);
  const onFailure = node.validation?.onFailure ?? "block-node";
  let lastError = "";
  let lastRawError: unknown = undefined;
  let lastFailedResult: ExecutorResult | undefined;
  let lastFailureReason: NodeRetryFailureReason = "executor-failed";
  let guardianRepair: GuardianRepair | null = null;

  for (let attempt = 1; attempt <= retry.maxAttempts; attempt += 1) {
    lastFailureReason = "executor-failed";
    // Use guardian-repaired node if available, otherwise original node
    const effectiveNode = guardianRepair ? applyRepair(node, guardianRepair) : node;
    const executionTemplateCtx: TemplateContext = {
      ...templateCtx,
      run: {
        flowId,
        workflowId: state.workflowId,
      },
    };
    console.log(`[controller] NODE_EXECUTING flowId=${flowId} node=${node.id} executor=${node.executor.type} attempt=${attempt}/${retry.maxAttempts} title="${node.title ?? ""}"`);
    enqueueRunLog({
      flow_id: flowId,
      node_id: node.id,
      level: "info",
      source: "node",
      message: `Node executing: ${node.id} (${node.title ?? ""}), executor=${node.executor.type}, attempt=${attempt}/${retry.maxAttempts}`,
      timestamp: Date.now(),
    });
    const nodeState = state.nodeStates[node.id];
    const nodeStartTime = Date.now();
    state.nodeStates[node.id] = {
      ...nodeState,
      status: "running",
      startedAt: nodeState?.startedAt ?? now(),
      attempts: attempt,
      retry,
      error: null,
    };
    appendFlowEvent(state, {
      type: "node_started",
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      attempt,
      data: { executor: node.executor.type },
    });

    // Emit state persistence hook: node_started
    const initialProgressMessage = node.progressMessage
      ? resolveTemplate(node.progressMessage, executionTemplateCtx)
      : undefined;
    // Resolve prompt template for embedded-agent/subagent/collaboration nodes so the
    // actual (variable-substituted) prompt is available at node start time.
    let resolvedPromptForStart: string | undefined;
    const executorType = node.executor.type;
    if (executorType === "embedded-agent" || executorType === "subagent" || executorType === "collaboration") {
      const promptTemplate = (node.executor as Record<string, unknown>).prompt as string | undefined
        ?? (node.executor as Record<string, unknown>).message as string | undefined;
      if (promptTemplate) {
        const full = resolveTemplate(promptTemplate, executionTemplateCtx);
        resolvedPromptForStart = full.length > 4000
          ? full.substring(0, 3989) + "... [TRUNCATED]"
          : full;
      }
    }
    const nodeInputSummary = JSON.stringify({
      params: executionTemplateCtx.params ?? {},
      nodeOutputKeys: executionTemplateCtx.nodeOutput ? Object.keys(executionTemplateCtx.nodeOutput) : [],
    });
    emitNodeEvent("node_started", {
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      executorType: node.executor.type,
      attempt,
      nodeTitle: node.title ?? null,
      progressMessage: initialProgressMessage,
      inputJson: nodeInputSummary,
      sessionKey: deps.sessionKey,
      sessionId: deps.sessionId,
      embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, node.id, flowId, node.executor.type),
      resolvedPrompt: resolvedPromptForStart,
      systemContext: {
        triggerRule: node.triggerRule ?? node.join ?? "all_success",
        phase: node.phase ?? undefined,
        retry: retry ? { maxAttempts: retry.maxAttempts, on: retry.on } : undefined,
        knowledgeBaseId: node.knowledgeBaseId ?? undefined,
      },
    });

    try {
      // Knowledge injection: augment template context with KB results
      let ctx = executionTemplateCtx;

      // Priority: knowledgeBaseId (DB-backed GRT KB) > legacy knowledge flag
      if (node.knowledgeBaseId && _knowledgeBaseManager) {
        const kbAdapter = await _knowledgeBaseManager.getById(node.knowledgeBaseId);
        if (kbAdapter && _knowledgeConfig?.enabled) {
          const queryText = node.knowledgeQuery ?? JSON.stringify(executionTemplateCtx);
          const kc = await prepareKnowledgeContext(queryText, _knowledgeConfig, [kbAdapter], _knowledgeCache ?? undefined);
          if (kc.formattedText) {
            ctx = { ...executionTemplateCtx, knowledgeContext: kc.formattedText };
          }
        }
      } else if (node.knowledge && _knowledgeBases.length > 0 && _knowledgeConfig?.enabled) {
        const queryText = node.knowledgeQuery ?? JSON.stringify(executionTemplateCtx);
        const kc = await prepareKnowledgeContext(queryText, _knowledgeConfig, _knowledgeBases, _knowledgeCache ?? undefined);
        if (kc.formattedText) {
          ctx = { ...executionTemplateCtx, knowledgeContext: kc.formattedText };
        }
      }

      // Display-only node-start notifications use the exact context passed to
      // the executor and remain isolated from the execution critical path.
      try {
        const level = deps.chatInjectLevel ?? "full";
        if (shouldInjectForFlow(flowId, "node-started")) {
          const nodeIndex = resolveNodeIndex(flowId, node.id);
          const displayInput = captureDisplayInput(node, ctx);
          const isAgentNode = node.executor.type === "embedded-agent"
            || node.executor.type === "subagent";
          const workflowInput = isAgentNode
            ? captureWorkflowInputPreview(ctx)
            : undefined;
          const startedMsg = buildNodeStartedMessage({
            node,
            nodeIndex,
            executorType: node.executor.type,
            triggerRule: node.triggerRule ?? node.join,
            workflowInput,
            resolvedInput: Object.keys(displayInput).length > 0 ? displayInput : undefined,
            level,
          });
          verboseChatInject(deps.chatInject, startedMsg, `${flowId}:${node.id}:verbose-started`);
        }
      } catch (error) {
        const errorType = typeof error;
        try {
          console.warn(
            `[controller] node-start notification failed: flowId=${flowId} nodeId=${node.id} errorType=${errorType}`,
          );
        } catch {
          // Observability failures must not reach executor execution.
        }
      }

      const result = await deps.executeNode(effectiveNode, ctx, state, flowId);
      const durationMs = Date.now() - nodeStartTime;

      if (result.status === "waiting") {
        // ── Node waiting ── do NOT emit node_succeeded; waiting is not terminal
        emitNodeEvent("node_duration_ms", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, durationMs });
        return result;
      }

      if (result.status !== "failed") {
        // ── Output contract validation (always run when configured) ──
        if (result.status === "succeeded" && node.outputContract) {
          const contractIssues = validateOutputContractResult(node.outputContract, result.result, node.id);
          if (contractIssues.length > 0) {
            lastFailedResult = result;
            lastFailureReason = "output-contract-failed";
            lastError = formatOutputContractIssues(node.title, contractIssues);
          }
        }

        // ── Node-level validation (post-outputContract, pre-succeeded) ──
        // Skip validation for block-node mode; Task 3 handles it in handleNodeResult.
        if (
          result.status === "succeeded" &&
          lastFailureReason !== "output-contract-failed" &&
          node.validation &&
          onFailure !== "block-node"
        ) {
          // Make the current node's output available to validation actions via
          // {{nodeOutput}} / {{nodeOutput.<nodeId>}} before building the context.
          state.nodeStates[node.id] = {
            ...state.nodeStates[node.id],
            result: result.result,
          };
          const validationContext = buildActionContext(
            deps,
            state,
            workflow ?? { id: state.workflowId, title: state.workflowId, nodes: [node] },
            flowId,
            node.id,
          );
          const validationOutcome = await runNodeValidationActions({
            node,
            state,
            flowId,
            registry: deps.actionRegistry,
            context: validationContext,
          });

          if (validationOutcome.status === "blocked") {
            if (onFailure === "fail-node") {
              lastFailedResult = result;
              lastFailureReason = "validation-failed";
              lastError = `${node.title ?? node.id} 校验失败: ${validationOutcome.hookId} — ${validationOutcome.error}`;
              clearNodeValidationState(state, node.id);
            } else if (onFailure === "ignore") {
              appendAuditLog(
                state,
                node.id,
                "validation-ignored",
                `${node.title ?? node.id} 校验失败但已忽略: ${validationOutcome.hookId}`,
              );
            }
          }
        }

        // ── Node succeeded ──
        // Emit node_succeeded only when no retryable failure reason was recorded.
        if (
          result.status === "succeeded" &&
          lastFailureReason !== "output-contract-failed" &&
          lastFailureReason !== "validation-failed"
        ) {
          recordNodeUsage(state, node.id, result);
          const outputContractValidated = node.outputContract ? true : undefined;
          const contractValidatedLog = node.outputContract ? "contractValidated=true" : "contractValidated=false/no-contract";
          const warningsErrorText = formatWarningsAsErrorText(result.warnings);
          if (result.warnings && result.warnings.length > 0) {
            console.warn(`[controller] NODE_SUCCEEDED_WITH_WARNINGS flowId=${flowId} node=${node.id} executor=${node.executor.type} attempt=${attempt} duration=${durationMs}ms warnings=${result.warnings.length} summary=${warningsErrorText?.slice(0, 200)}`);
          } else {
            console.log(`[controller] NODE_SUCCEEDED flowId=${flowId} node=${node.id} executor=${node.executor.type} attempt=${attempt} duration=${durationMs}ms ${contractValidatedLog}`);
          }
          emitNodeEvent("node_duration_ms", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, durationMs });
          emitNodeEvent("node_token_usage_total", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, usage: result.usage });
          emitNodeEvent("node_succeeded", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, durationMs, usage: result.usage, inputJson: nodeInputSummary, outputJson: result.result ? JSON.stringify(result.result) : null, sessionKey: deps.sessionKey, sessionId: deps.sessionId, embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, node.id, flowId, node.executor.type), resolvedPrompt: result.resolvedPrompt, sessionFile: result.sessionFile, skillName: result.skillName ?? (node.executor as { skillName?: string }).skillName?.trim() ?? null, systemContext: { outputContractValidated, outputContractIssues: 0, warnings: result.warnings, warningsErrorText } });
          notifyNodeSucceeded(deps.chatInject, deps.chatInjectLevel ?? "full", flowId, node, result, durationMs, node.outputContract ? "pass" : "none");
          return result;
        }
      } else {
        lastFailedResult = result;
        lastFailureReason = "executor-failed";
        lastError = result.error ?? "执行失败";
        lastRawError = result.rawError;
      }

      if (result.status !== "failed" && lastFailureReason !== "output-contract-failed" && lastFailureReason !== "validation-failed") {
        return result;
      }
    } catch (err) {
      lastFailedResult = undefined;
      lastFailureReason = "executor-failed";
      lastError = err instanceof Error ? `${err.name}: ${err.message}` : String(err);
      lastRawError = err;
    }

    const durationMs = Date.now() - nodeStartTime;

    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      status: "failed",
      completedAt: now(),
      error: lastError,
      // Preserve the full result from the last failed execution attempt.
      // This includes EmbeddedAgentResult.meta (with teclawDiagnostic),
      // payloads, messagingToolSentTexts, etc. — enabling buildFailedResultJson
      // to propagate detailed failure context into flow_runs.result_json.
      ...(lastFailedResult?.result ? { result: lastFailedResult.result } : {}),
    };

    if (attempt < retry.maxAttempts && nodeRetryHandles(node, lastFailureReason)) {
      // ── Node will retry ──
      console.log(`[controller] NODE_RETRY flowId=${flowId} node=${node.id} executor=${node.executor.type} attempt=${attempt}/${retry.maxAttempts} reason=${lastFailureReason} error=${(lastError ?? "").slice(0, 80)} duration=${durationMs}ms`);

      // Async progress: notify node retry
      if (loadConfig().app.execution.asyncRun && shouldInjectForFlow(flowId, "parallel-progress")) {
        fireProgressChatInject(
          deps,
          `🔄 ${node.title ?? node.id} 第 ${attempt} 次执行失败，准备重试 (${attempt}/${retry.maxAttempts})`,
          `${flowId}:${node.id}:retry`,
        );
      }

      // Verbose chatInject: node retry notification (minLevel=never — never
      // injected in any current level; guard kept for explicitness and future
      // level expansion).
      {
        const level = deps.chatInjectLevel ?? "full";
        if (shouldInjectForFlow(flowId, "node-retry")) {
          const retryMsg = buildNodeRetryMessage({
            node,
            nodeIndex: resolveNodeIndex(flowId, node.id),
            attempt,
            maxAttempts: retry.maxAttempts,
            lastError,
            retrySpec: retry,
            level,
          });
          verboseChatInject(deps.chatInject, retryMsg, `${flowId}:${node.id}:verbose-retry-${attempt}`);
        }
      }

      // ── Node will retry ──
      emitNodeEvent("node_failed", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, durationMs, error: lastError, inputJson: nodeInputSummary, outputJson: lastFailedResult?.result ? JSON.stringify(lastFailedResult.result) : null, sessionKey: deps.sessionKey, sessionId: deps.sessionId, embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, node.id, flowId, node.executor.type), resolvedPrompt: lastFailedResult?.resolvedPrompt, sessionFile: lastFailedResult?.sessionFile, skillName: lastFailedResult?.skillName ?? (node.executor as { skillName?: string }).skillName?.trim() ?? null, systemContext: { failureReason: lastFailureReason, willRetry: true, retryAttempt: attempt, maxRetries: retry.maxAttempts, retryOn: retry.on } });
      emitNodeEvent("node_retry", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, durationMs, error: lastError, inputJson: nodeInputSummary, outputJson: lastFailedResult?.result ? JSON.stringify(lastFailedResult.result) : null, sessionKey: deps.sessionKey, sessionId: deps.sessionId, systemContext: { failureReason: lastFailureReason, retryAttempt: attempt, maxRetries: retry.maxAttempts, retryOn: retry.on, backoffMs: retry.backoffMs } });
      appendAuditLog(state, node.id, "retry", `第 ${attempt} 次执行失败，准备重试: ${lastError}`);

      // Intelligent retry: capture error context and search KB for hints
      if (_retryConfig?.kbSearchEnabled) {
        try {
          const retryDirective = await handleNodeFailure(
            flowId, state.workflowId, node.id, attempt, lastError,
            _errorContextStore.getLastQuery(flowId),
            _errorContextStore, _retryTracker, _autoRetryTracker,
            _retryConfig, _knowledgeBases, _knowledgeConfig, _knowledgeCache ?? undefined,
          );
          if (retryDirective.errorRecoveryContext) {
            templateCtx = { ...templateCtx, errorRecoveryContext: retryDirective.errorRecoveryContext };
          }
        } catch { /* intelligent retry is best-effort */ }
      }

      // Guardian Agent: analyze failure and generate repair strategy for next retry
      if (_guardianAgent && attempt < retry.maxAttempts) {
        try {
          enqueueRunLog({ flow_id: flowId, node_id: node.id, level: "info", source: "guardian",
            message: `守护 Agent 开始分析节点 ${node.id} 第 ${attempt} 次失败`, timestamp: Date.now() });
          const analysisParams: GuardianAnalysisParams = {
            nodeId: node.id,
            executorType: node.executor.type,
            error: lastError,
            resolvedPrompt: lastFailedResult?.resolvedPrompt,
            inputJson: nodeInputSummary,
            outputJson: lastFailedResult?.result ? JSON.stringify(lastFailedResult.result).slice(0, 1000) : undefined,
            attempt,
            maxAttempts: retry.maxAttempts,
          };
          guardianRepair = await _guardianAgent.analyze(analysisParams);
          enqueueRunLog({ flow_id: flowId, node_id: node.id, level: "info", source: "guardian",
            message: `守护 Agent 分析完成: failureReason=${guardianRepair.failureReason}, repairAction=${guardianRepair.action}, reasoning=${guardianRepair.reasoning.slice(0, 200)}`,
            timestamp: Date.now() });
          if (guardianRepair.action !== "retry-as-is" && guardianRepair.action !== "skip-retry") {
            enqueueRunLog({ flow_id: flowId, node_id: node.id, level: "warn", source: "guardian",
              message: `应用修复策略 ${guardianRepair.action}: ${summarizeRepair(guardianRepair)}`,
              timestamp: Date.now() });
          }
          if (guardianRepair.action === "skip-retry") {
            enqueueRunLog({ flow_id: flowId, node_id: node.id, level: "error", source: "guardian",
              message: `守护 Agent 判断不值得重试: ${guardianRepair.reasoning.slice(0, 200)}, 终止重试`,
              timestamp: Date.now() });
            // Emit final failure and break
            emitNodeEvent("node_failed", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, durationMs, error: lastError, inputJson: nodeInputSummary, outputJson: lastFailedResult?.result ? JSON.stringify(lastFailedResult.result) : null, sessionKey: deps.sessionKey, sessionId: deps.sessionId, embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, node.id, flowId, node.executor.type), resolvedPrompt: lastFailedResult?.resolvedPrompt, sessionFile: lastFailedResult?.sessionFile, skillName: lastFailedResult?.skillName ?? (node.executor as { skillName?: string }).skillName?.trim() ?? null, systemContext: { failureReason: lastFailureReason, willRetry: false, retryAttempt: attempt, maxRetries: retry.maxAttempts, guardianSkip: true } });
            return { status: "failed", error: lastError, rawError: lastRawError, resolvedPrompt: lastFailedResult?.resolvedPrompt, sessionFile: lastFailedResult?.sessionFile, skillName: lastFailedResult?.skillName ?? (node.executor as { skillName?: string }).skillName?.trim() ?? null };
          }
        } catch (guardianErr) {
          const gmsg = guardianErr instanceof Error ? guardianErr.message : String(guardianErr);
          enqueueRunLog({ flow_id: flowId, node_id: node.id, level: "warn", source: "guardian",
            message: `守护 Agent 降级: ${gmsg}, 原样重试`, timestamp: Date.now() });
          guardianRepair = null;
        }
      }

      await sleep(retry.backoffMs);
    } else {
      // ── Node failed (retries exhausted) ──
      console.log(`[controller] NODE_FAILED_FINAL flowId=${flowId} node=${node.id} executor=${node.executor.type} attempt=${attempt}/${retry.maxAttempts} reason=${lastFailureReason} error=${(lastError ?? "").slice(0, 100)} duration=${durationMs}ms`);
      emitNodeEvent("node_failed", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, durationMs, error: lastError, inputJson: nodeInputSummary, outputJson: lastFailedResult?.result ? JSON.stringify(lastFailedResult.result) : null, sessionKey: deps.sessionKey, sessionId: deps.sessionId, embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, node.id, flowId, node.executor.type), resolvedPrompt: lastFailedResult?.resolvedPrompt, sessionFile: lastFailedResult?.sessionFile, skillName: lastFailedResult?.skillName ?? (node.executor as { skillName?: string }).skillName?.trim() ?? null, systemContext: { failureReason: lastFailureReason, willRetry: false, retryAttempt: attempt, maxRetries: retry.maxAttempts } });
      emitNodeEvent("node_duration_ms", { flowId, workflowId: state.workflowId, nodeId: node.id, executorType: node.executor.type, attempt, durationMs });

      // ── BUG-FIX: Abort ghost agent process ──
      // When a node fails permanently (retries exhausted), the underlying
      // embedded-agent process may still be running asynchronously (e.g. LLM
      // timeout resolved after controller gave up). Signal abort to terminate
      // the agent run and suppress further ghost events.
      if (deps.abortSignal && !deps.abortSignal.aborted && node.executor.type === "embedded-agent") {
        console.log(`[controller] ABORT_GHOST_AGENT flowId=${flowId} node=${node.id} — aborting embedded-agent after final failure`);
        // Note: we do NOT call abortActiveRunsForSession here because that
        // would abort ALL active runs for the session, including other nodes.
        // Instead, the ghost event guard in index.ts (embeddedAgentOptions)
        // suppresses stale callbacks by checking nodeStates status.
        // The abortSignal is the session-level signal; if only this node
        // failed (not the entire workflow), we must not abort other nodes.
      }

      break;
    }
  }

  if (
    lastFailureReason === "validation-failed" &&
    onFailure === "fail-node" &&
    lastFailedResult
  ) {
    // Validation failed and retries are exhausted / not configured for validation-failed.
    // Preserve the original result payload for diagnostics, but report the node as failed.
    const failedResult: ExecutorResult = {
      status: "failed",
      error: lastError,
      result: lastFailedResult.result,
    };
    if (lastRawError != null) {
      failedResult.rawError = lastRawError;
    }
    return failedResult;
  }

  const finalResult = lastFailedResult ?? { status: "failed" as const, error: lastError };
  if (lastRawError != null && finalResult.status === "failed") {
    finalResult.rawError = lastRawError;
  }
  return finalResult;
}

function buildActionContext(
  deps: ControllerDeps,
  state: FlowState,
  workflow: WorkflowSpec,
  flowId: string,
  nodeId?: string,
): ActionExecutionContext {
  const templateExtras = buildActionTemplateExtras(state, nodeId);
  const nodeOutput = templateExtras.nodeOutput;
  if (nodeId && state.nodeStates[nodeId]?.result) {
    nodeOutput[nodeId] = state.nodeStates[nodeId].result as Record<string, unknown>;
    const meta = state.runtimeNodeMeta?.[nodeId];
    if (meta) {
      nodeOutput[meta.bodyNodeId] = state.nodeStates[nodeId].result as Record<string, unknown>;
    }
  }

  return {
    flowId,
    workflowId: workflow.id,
    nodeId,
    sessionKey: deps.sessionKey,
    executionMode: state.executionMode,
    bcsGroupId: state.bcsGroupId,
    params: state.params,
    input: state.input,
    workflowData: state.workflowData,
    nodeOutput,
    actionOutputs: state.actionOutputs,
    loop: templateExtras.loop,
    templateAliases: templateExtras.templateAliases,
    workflow,
    user: {
      id: workflow.defaults?.user?.id ?? deps.user?.id,
      name: workflow.defaults?.user?.name ?? deps.user?.name,
    },
  };
}

function buildHookWaitState(state: FlowState, flowId: string, summary: string): WaitState {
  return {
    kind: "platform-workflow",
    workflowId: state.workflowId,
    params: state.params,
    activeNodes: state.activeNodes,
    waitingFor: "hook-retry",
    hint: summary,
    userAction: `/workflow resume ${flowId} <revision>`,
  };
}

async function blockOnHookFailure(
  deps: ControllerDeps,
  flowId: string,
  revision: number,
  state: FlowState,
  summary: string,
): Promise<number> {
  const waitState = buildHookWaitState(state, flowId, summary);
  return blockFlow(deps, flowId, revision, state, waitState, summary, state.activeNodes[0] ?? "hook");
}

async function blockFlow(
  deps: ControllerDeps,
  flowId: string,
  revision: number,
  state: FlowState,
  waitState: WaitState,
  summary: string,
  currentStep: string,
): Promise<number> {
  console.log(`[controller] FLOW_BLOCKED flowId=${flowId} node=${currentStep} waitingFor=${waitState.waitingFor} summary=${summary.slice(0, 80)}`);
  enqueueRunLog({
    flow_id: flowId,
    node_id: currentStep === "hook" ? null : currentStep,
    level: "info",
    source: "workflow",
    message: `Flow blocked: node=${currentStep}, waitingFor=${waitState.waitingFor}, summary=${summary.slice(0, 80)}`,
    timestamp: Date.now(),
  });
  appendFlowEvent(state, {
    type: "workflow_blocked",
    flowId,
    workflowId: state.workflowId,
    nodeId: currentStep === "hook" ? undefined : currentStep,
    data: { summary },
  });
  // BUG-25 fix: Wrap boundTaskFlow.fail() to prevent unhandled exceptions from
  // leaving the flow stuck. In API mode, network/auth failures can cause this.
  let newRevision = revision;
  try {
    const failResult = await deps.boundTaskFlow.fail({
      flowId,
      expectedRevision: revision,
      status: "blocked",
      currentStep,
      stateJson: JSON.stringify(state),
      waitJson: JSON.stringify(waitState),
      blockedSummary: summary,
    });

    const failRecord = failResult as { applied?: boolean; flow?: { revision?: unknown } };
    if (failRecord.applied === false) {
      throw new Error("状态更新冲突，请重试");
    }
    newRevision = typeof failRecord.flow?.revision === "number" ? failRecord.flow.revision : revision;
  } catch (failErr) {
    if (failErr instanceof Error && failErr.message === "状态更新冲突，请重试") throw failErr;
    const errMsg = failErr instanceof Error ? failErr.message : String(failErr);
    console.error(`[controller] blockFlow: boundTaskFlow.fail() threw for flowId=${flowId}:`, failErr);
    enqueueRunLog({
      flow_id: flowId,
      level: "error",
      source: "engine",
      message: `boundTaskFlow.fail() failed in blockFlow: ${errMsg}`,
      timestamp: Date.now(),
    });
  }

  syncFlowRunPhase(flowId, state.currentPhase, "blocked");

  return newRevision;
}

async function runFlowStartHooks(
  params: {
    deps: ControllerDeps;
    workflow: WorkflowSpec;
    state: FlowState;
    flowId: string;
    revision: number;
  },
): Promise<{ revision: number; blocked: boolean }> {
  const hooks = params.workflow.workflow?.onStart;
  if (!hooks || hooks.length === 0) return { revision: params.revision, blocked: false };

  params.state.flowHooks.onStart ??= {};
  const before = snapshotHookStatuses(hooks, params.state.flowHooks.onStart);
  const outcome: HookRunOutcome = await runHookActionsWithEvents({
    hooks,
    states: params.state.flowHooks.onStart,
    registry: params.deps.actionRegistry,
    context: buildActionContext(params.deps, params.state, params.workflow, params.flowId),
    state: params.state,
    flowId: params.flowId,
    scope: "workflow",
    lifecycle: "onStart",
  });

  if (outcome.status === "blocked") {
    appendFlowEvent(params.state, {
      type: "action_failed",
      flowId: params.flowId,
      workflowId: params.state.workflowId,
      actionId: outcome.hookId,
      data: { action: outcome.action },
      error: outcome.error,
    });
    appendAuditLog(
      params.state,
      "-",
      "hook-blocked",
      `${outcome.action}/${outcome.hookId}: ${outcome.error}`,
    );
    const newRevision = await blockOnHookFailure(
      params.deps,
      params.flowId,
      params.revision,
      params.state,
      `启动 hook 失败: ${outcome.hookId} — ${outcome.error}`,
    );
    return { revision: newRevision, blocked: true };
  }

  appendSucceededHookEvents(params.state, params.flowId, hooks, params.state.flowHooks.onStart, before);
  return { revision: params.revision, blocked: false };
}

async function runFlowFinishHooks(
  params: {
    deps: ControllerDeps;
    workflow: WorkflowSpec;
    state: FlowState;
    flowId: string;
    revision: number;
  },
): Promise<{ revision: number; blocked: boolean }> {
  const hooks = params.workflow.workflow?.onFinish;
  if (!hooks || hooks.length === 0) return { revision: params.revision, blocked: false };

  params.state.flowHooks.onFinish ??= {};
  const before = snapshotHookStatuses(hooks, params.state.flowHooks.onFinish);
  const outcome: HookRunOutcome = await runHookActionsWithEvents({
    hooks,
    states: params.state.flowHooks.onFinish,
    registry: params.deps.actionRegistry,
    context: buildActionContext(params.deps, params.state, params.workflow, params.flowId),
    state: params.state,
    flowId: params.flowId,
    scope: "workflow",
    lifecycle: "onFinish",
  });

  if (outcome.status === "blocked") {
    appendFlowEvent(params.state, {
      type: "action_failed",
      flowId: params.flowId,
      workflowId: params.state.workflowId,
      actionId: outcome.hookId,
      data: { action: outcome.action },
      error: outcome.error,
    });
    appendAuditLog(
      params.state,
      "-",
      "hook-blocked",
      `${outcome.action}/${outcome.hookId}: ${outcome.error}`,
    );
    const newRevision = await blockOnHookFailure(
      params.deps,
      params.flowId,
      params.revision,
      params.state,
      `完成 hook 失败: ${outcome.hookId} — ${outcome.error}`,
    );
    return { revision: newRevision, blocked: true };
  }

  appendSucceededHookEvents(params.state, params.flowId, hooks, params.state.flowHooks.onFinish, before);
  return { revision: params.revision, blocked: false };
}

async function runNodeSuccessHooks(
  params: {
    deps: ControllerDeps;
    workflow: WorkflowSpec;
    state: FlowState;
    flowId: string;
    revision: number;
    node: WorkflowNode;
    /** When true, send a chat notification if the hook blocks the flow.
     *  Defaults to false so command/callback handlers can keep their own
     *  response messages without duplicating the notification. */
    notifyOnBlock?: boolean;
  },
): Promise<{ revision: number; blocked: boolean }> {
  const nodeState = params.state.nodeStates[params.node.id];
  if (!nodeState) return { revision: params.revision, blocked: false };
  if (!params.node.onSuccess || params.node.onSuccess.length === 0) {
    if (nodeState.status !== "succeeded") {
      params.state.nodeStates[params.node.id] = {
        ...nodeState,
        status: "succeeded",
        postActions: nodeState.postActions ?? {},
        completedAt: nodeState.completedAt ?? now(),
        error: null,
      };
      appendAuditLog(params.state, params.node.id, "succeeded", `${params.node.title} 完成`);
    }

    applyPhaseAndStatus(params.workflow, params.state);
    return { revision: params.revision, blocked: false };
  }

  params.state.nodeStates[params.node.id] = {
    ...nodeState,
    status: "postActionsRunning",
    postActions: nodeState.postActions ?? {},
  };
  applyPhaseAndStatus(params.workflow, params.state);

  const before = snapshotHookStatuses(params.node.onSuccess, params.state.nodeStates[params.node.id].postActions);
  const outcome: HookRunOutcome = await runHookActionsWithEvents({
    hooks: params.node.onSuccess,
    states: params.state.nodeStates[params.node.id].postActions,
    registry: params.deps.actionRegistry,
    context: buildActionContext(params.deps, params.state, params.workflow, params.flowId, params.node.id),
    state: params.state,
    flowId: params.flowId,
    scope: "node",
    lifecycle: "onSuccess",
    nodeId: params.node.id,
  });

  if (outcome.status === "blocked") {
    appendFlowEvent(params.state, {
      type: "action_failed",
      flowId: params.flowId,
      workflowId: params.state.workflowId,
      nodeId: params.node.id,
      actionId: outcome.hookId,
      data: { action: outcome.action },
      error: outcome.error,
    });
    params.state.nodeStates[params.node.id] = {
      ...params.state.nodeStates[params.node.id],
      status: "blocked",
      error: `${outcome.action}/${outcome.hookId}: ${outcome.error}`,
    };
    applyPhaseAndStatus(params.workflow, params.state);
    appendAuditLog(
      params.state,
      params.node.id,
      "hook-blocked",
      `${params.node.title} 后置动作失败: ${outcome.hookId} — ${outcome.error}`,
    );

    const newRevision = await blockOnHookFailure(
      params.deps,
      params.flowId,
      params.revision,
      params.state,
      `${params.node.title} 后置动作失败: ${outcome.hookId} — ${outcome.error}`,
    );

    // Notify the user session: the node's post-action failed and the flow is blocked.
    // Without this, blocked onSuccess hooks are only visible in ClawWeb.
    if (params.notifyOnBlock ?? false) {
      const blockedMessage = [
        `节点 **${params.node.title}** 的后置动作 **${outcome.hookId}** 执行失败，流程已阻塞。`,
        `失败原因：${outcome.error}`,
        "",
        "后续操作：",
        `- 重新执行该上游节点并重置下游：\`/workflow retry --node ${params.node.id} --flowId ${params.flowId}\``,
        `- 仅重新尝试后置动作：\`/workflow inspect ${params.flowId}\` 查看最新 revision 后，执行 \`/workflow resume ${params.flowId} <revision>\``,
      ].join("\n");
      await params.deps.chatInject(blockedMessage, `${params.flowId}:${params.node.id}:hook-blocked`);
    }

    return { revision: newRevision, blocked: true };
  }

  params.state.nodeStates[params.node.id] = {
    ...params.state.nodeStates[params.node.id],
    status: "succeeded",
    completedAt: now(),
    error: null,
  };

  applyPhaseAndStatus(params.workflow, params.state);
  appendSucceededHookEvents(
    params.state,
    params.flowId,
    params.node.onSuccess,
    params.state.nodeStates[params.node.id].postActions ?? {},
    before,
    params.node.id,
  );
  appendAuditLog(params.state, params.node.id, "succeeded", `${params.node.title} 完成`);
  return { revision: params.revision, blocked: false };
}

export async function executeWorkflowForTest(
  params: ExecuteWorkflowForTestParams,
): Promise<void> {
  const state = ensureFlowStateDefaults(params.state);
  state.flowHooks ??= {};

  if (!params.deps.subworkflowDeps && (params.deps.resolvedWorkflows || params.deps.workflowSpecApiRepo || (params.deps.db && params.deps.db.dbType !== "noop"))) {
    params.deps.subworkflowDeps = buildSubworkflowDepsForExecution(
      params.deps,
      state,
      params.flowId,
      params.debug,
    );
  }

  if (params.runStartHooks) {
    const outcome = await runFlowStartHooks({
      deps: params.deps,
      workflow: params.workflow,
      state,
      flowId: params.flowId,
      revision: params.revision,
    });
    if (outcome.blocked) return;
  }

  await executeLoop(
    params.deps,
    params.workflow,
    state,
    params.flowId,
    params.revision,
  );
}

// ── Chat Inject ──

type ChatInjectFn = (
  message: string,
  idempotencyKey: string,
  options?: ChatInjectOptions,
) => Promise<void>;

// ── Executor Dispatch (Phase 1: stubs, real executors in Phase 2/3) ──

type ExecutorDispatchFn = (
  node: WorkflowNode,
  templateCtx: TemplateContext,
  flowState: FlowState,
  flowId: string,
) => Promise<ExecutorResult>;

// ── Controller ──

export type ControllerDeps = {
  actionRegistry: ActionRegistry;
  boundTaskFlow: {
    createManaged: (params: Record<string, unknown>) => Promise<Record<string, unknown>>;
    setWaiting: (params: Record<string, unknown>) => Promise<{ applied: boolean; flow: Record<string, unknown> }>;
    resume: (params: Record<string, unknown>) => Promise<{ applied: boolean; flow: Record<string, unknown> }>;
    finish: (params: Record<string, unknown>) => Promise<unknown>;
    fail: (params: Record<string, unknown>) => Promise<unknown>;
    list: () => Promise<{ flows: Array<Record<string, unknown>> } | Array<Record<string, unknown>>>;
    get: (token: string) => Promise<Record<string, unknown> | null>;
    findLatest: () => Promise<Record<string, unknown> | null>;
    runTask: (params: Record<string, unknown>) => Promise<Record<string, unknown>>;
  };
  chatInject: ChatInjectFn;
  /** Unified chatInject level: perf | simple | full. Default: "full". */
  chatInjectLevel?: import("./inject-level.js").InjectLevel;
  executeNode: ExecutorDispatchFn;
  api?: unknown;
  sessionKey: string;
  sessionId?: string;
  skillRoot: string;
  user?: { id?: string; name?: string };
  messages?: unknown[];
  onProgress?: (text: string, details?: Record<string, unknown>) => void;
  abortSignal?: AbortSignal;
  workflowRegistry?: Record<string, WorkflowSpec>;
  resolvedWorkflows?: ResolvedWorkflow[];
  failedWorkflows?: import("./packs/types.js").FailedWorkflow[];
  resolvedPacks?: ResolvedWorkflowPack[];
  workflowLogDir?: string;
  db?: IDatabase;
  workflowSpecApiRepo?: import("./db/repositories/types.js").IWorkflowSpecRepository;
  formatWorkflowCommand?: (
    workflowId: string,
    command: string,
    args?: string[],
    options?: { surface?: WorkflowCommandSurface },
  ) => string;
  subworkflowDeps?: import("./executors/subworkflow.js").SubworkflowDeps;
  flowControl?: import("./flow-control/service.js").FlowControlService;
  /** Transport mode for this session — "stdio" (MCP) or "http-sse" (Hermes). Used for logging and conditional logic. */
  transportMode?: "stdio" | "http-sse";
  /** Dynamic workflow observability emitter — dual-writes events to Channel + ExecutionStepLogger. */
  eventEmitter?: import("./observability/emitter.js").DynamicWorkflowEventEmitter;
  /** Packs root directory for git versioning operations. */
  packsRoot?: string;
  /** ClawWeb API base URL. */
  clawWebBaseUrl?: string;
  /** Bot ID for versioning (per-bot branch). */
  botId?: string;
  /** Owner ID for versioning (per-bot branch). */
  ownerId?: string;
  /** Ed25519 private key (base64) for signing internal API requests (deploy_history). */
  signatureKey?: string;
  /** Git remote URL for per-pack repos. All packs push to the same remote. */
  gitRemoteUrl?: string;
  /** Git username for credential-cache. */
  gitUsername?: string;
  /** Git token/password for credential-cache. */
  gitToken?: string;
  /** Git commit author email. Corporate git servers require valid company email. */
  gitEmail?: string;
  /** Facade binding repository — written by handleDeploy (facade_bindings table). API or DB mode. */
  facadeBindingRepo?: import("./db/repositories/types.js").IFacadeBindingRepository;
};

type WorkflowLookup = {
  workflow: WorkflowSpec;
  resolved?: ResolvedWorkflow;
};

type ValidationIssue = {
  code: string;
  title: string;
  detail?: string;
  suggestion?: string;
};

function issue(code: string, title: string, detail?: string, suggestion?: string): ValidationIssue {
  return {
    code,
    title,
    ...(detail ? { detail } : {}),
    ...(suggestion ? { suggestion } : {}),
  };
}

function renderIssueList(issues: ValidationIssue[]): string[] {
  if (issues.length === 0) return [];
  const lines = ["", "**问题详情**"];
  issues.forEach((item, index) => {
    lines.push(`${index + 1}. ${item.title}`);
    if (item.detail) lines.push(`   - 细节：${item.detail}`);
    if (item.suggestion) lines.push(`   - 建议：${item.suggestion}`);
  });
  return lines;
}

function hasIssue(issues: ValidationIssue[], code: string): boolean {
  return issues.some((item) => item.code === code);
}

function listAvailableWorkflowIds(deps: ControllerDeps): string[] {
  if (deps.workflowRegistry) return Object.keys(deps.workflowRegistry).sort();
  return listWorkflowIdsFromPacks(deps.resolvedWorkflows ?? []);
}

function workflowRegistryFromDeps(deps: ControllerDeps): Record<string, WorkflowSpec> {
  if (deps.workflowRegistry) return deps.workflowRegistry;
  return workflowRegistryFromResolved(deps.resolvedWorkflows ?? []);
}

async function findWorkflowLookup(deps: ControllerDeps, workflowId: string, debug?: boolean): Promise<WorkflowLookup | undefined> {
  // When --debug is set, local Pack YAML takes priority over DB
  if (!debug) {
    // DB/API-first: check spec repo before in-memory registry (DB/API overrides Pack YAML)
    if (deps.db && deps.db.dbType !== "noop") {
      try {
        const specRepo = new WorkflowSpecRepository(deps.db);
        const resolved = await resolveWorkflow(workflowId, specRepo, deps.resolvedWorkflows ?? []);
        if (resolved && resolved.source.kind === "db") {
          console.info("[taskguard] findWorkflowLookup: resolved from DB", { workflowId });
          return { workflow: resolved.spec, resolved };
        }
      } catch (err) {
        // DB lookup failed, fall through to registry/packs
        console.warn("[taskguard] findWorkflowLookup: DB spec resolution failed", { workflowId, error: err instanceof Error ? err.message : String(err) });
      }
    }
    // API mode: check if there's an API-based spec repo available
    if (deps.workflowSpecApiRepo) {
      try {
        console.info("[taskguard] findWorkflowLookup: resolving from API spec repo", { workflowId });
        const resolved = await resolveWorkflow(workflowId, deps.workflowSpecApiRepo, deps.resolvedWorkflows ?? []);
        if (resolved && resolved.source.kind === "db") {
          console.info("[taskguard] findWorkflowLookup: resolved from API", { workflowId });
          return { workflow: resolved.spec, resolved };
        }
        console.info("[taskguard] findWorkflowLookup: API returned non-db source, falling through", { workflowId, sourceKind: resolved?.source?.kind });
      } catch (err) {
        // API lookup failed, fall through to registry/packs
        console.warn("[taskguard] findWorkflowLookup: API spec resolution failed", { workflowId, error: err instanceof Error ? err.message : String(err) });
      }
    }
  }
  // Fallback: in-memory registry (loaded from Pack YAML at startup)
  if (deps.workflowRegistry?.[workflowId]) {
    return { workflow: deps.workflowRegistry[workflowId] };
  }
  // Fallback: resolve from Pack files on disk
  const resolved = resolveWorkflowByIdFromPacks(workflowId, deps.resolvedWorkflows ?? []);
  return resolved ? { workflow: resolved.spec, resolved } : undefined;
}

async function requireWorkflowLookup(deps: ControllerDeps, workflowId: string, debug?: boolean): Promise<WorkflowLookup> {
  const lookup = await findWorkflowLookup(deps, workflowId, debug);
  if (lookup) return lookup;
  const available = listAvailableWorkflowIds(deps).join(", ");
  throw new WorkflowPackResolverError(
    `Workflow "${workflowId}" pack 未安装或未被发现。Available: ${available || "none"}`,
  );
}

function buildWorkflowPin(resolved: ResolvedWorkflow | undefined): WorkflowPin | undefined {
  if (!resolved) return undefined;
  const isDb = resolved.source.kind === "db";
  // DB mode: pack.root is empty but pack.id may be available from row.pack_id.
  // Keep packId so packRoot can be resolved at template-build time via WORKFLOW_PACKS_ROOT.
  const packId = resolved.pack.id || undefined;
  return {
    workflowId: resolved.spec.id,
    workflowVersion: resolved.spec.version,
    workflowDigest: resolved.digest,
    packId,
    packVersion: isDb ? undefined : resolved.pack.version || undefined,
    packDigest: isDb ? undefined : resolved.pack.digest || undefined,
    packRoot: resolved.pack.root || undefined,
    source: resolved.source.kind,
    capturedAt: new Date().toISOString(),
  };
}

function workflowPinMatches(current: WorkflowPin, expected: WorkflowPin): boolean {
  return current.workflowId === expected.workflowId
    && current.workflowVersion === expected.workflowVersion
    && current.workflowDigest === expected.workflowDigest
    && current.packId === expected.packId
    && current.packVersion === expected.packVersion
    && current.packDigest === expected.packDigest
    && current.source === expected.source;
}

function workflowSnapshotMatches(current: WorkflowSpec, expected: WorkflowSpec): boolean {
  return JSON.stringify(current) === JSON.stringify(expected);
}

function findPackById(deps: ControllerDeps, packId: string): ResolvedWorkflowPack | undefined {
  return (deps.resolvedPacks ?? []).find((pack) => pack.manifest.id === packId);
}

function workflowsForPack(deps: Pick<ControllerDeps, "resolvedWorkflows">, packId: string): ResolvedWorkflow[] {
  return (deps.resolvedWorkflows ?? []).filter((workflow) => workflow.pack.id === packId);
}

function openclawHome(): string {
  return process.env.OPENCLAW_HOME?.trim() || join(homedir(), ".openclaw");
}

function safePathSegment(value: string): string {
  return value.replace(/[^A-Za-z0-9_.-]+/g, "_").slice(0, 80) || "workflow";
}

function createFlowArtifactDir(workflowId: string): string {
  return join(
    openclawHome(),
    "workspace",
    "workflow-artifacts",
    safePathSegment(workflowId),
    `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
  );
}

function workflowDigestForLaunch(workflow: WorkflowSpec, resolved?: ResolvedWorkflow): string {
  return resolved?.digest ?? `${workflow.id}@${workflow.version}`;
}

function requiredParamsForWorkflow(workflow: WorkflowSpec): string[] {
  return workflow.input?.requiredParams ?? workflow.requiredParams ?? [];
}

function assertRequiredParams(workflow: WorkflowSpec, workflowId: string, params: Record<string, string>, command: "run"): void {
  const missing = requiredParamsForWorkflow(workflow).filter((p) => !params[p]);
  if (missing.length === 0) return;
  throw new Error(`缺少必填参数: ${missing.join(", ")}。用法: /workflow ${command} ${workflowId} ${missing.map((p) => `--${p} <value>`).join(" ")}`);
}

function buildInputTemplateContext(
  input: FlowInput,
  params: Record<string, string>,
): TemplateContext {
  return {
    skillRoot: "",
    nodeOutput: {},
    params,
    input,
    workflowData: {},
    actionOutputs: {},
    flowHooks: {},
  };
}

function resolveFlowIdentity(
  workflow: WorkflowSpec,
  input: FlowInput,
  params: Record<string, string>,
): FlowIdentity {
  const context = buildInputTemplateContext(input, params);
  const spec = workflow.identity;
  const key = spec?.key
    ? resolveTemplate(spec.key, context).trim()
    : "";
  if (!key) {
    throw new Error(`Workflow ${workflow.id} identity.key 为空或未声明，请检查 workflow YAML 的 identity.key`);
  }
  const label = spec?.label
    ? resolveTemplate(spec.label, context).trim()
    : key;
  return {
    key,
    label: label || key,
    duplicatePolicy: spec?.duplicatePolicy ?? "reject-active",
  };
}

function fallbackFlowInput(state: FlowState): FlowInput {
  return state.input ?? {
    params: state.params,
    files: [],
    digest: "legacy",
    digestShort: "legacy",
  };
}

function executableSkillRoots(): string[] {
  const root = openclawHome();
  return [
    join(root, "workspace", "skills"),
    join(root, "skills"),
  ];
}

async function skillExistsInExecutableRoots(skillName: string): Promise<boolean> {
  const [workspaceSkillsDir, homeSkillsDir] = executableSkillRoots();
  try {
    await resolveRequiredSkill(skillName, { workspaceSkillsDir, homeSkillsDir });
    return true;
  } catch (error) {
    if (error instanceof RequiredSkillNotFoundError) {
      return false;
    }
    throw error;
  }
}

type ExecuteWorkflowForTestParams = {
  deps: ControllerDeps;
  workflow: WorkflowSpec;
  state: FlowState;
  flowId: string;
  revision: number;
  runStartHooks?: boolean;
  /** When true, subworkflow resolution skips DB/API and uses local Pack YAML. */
  debug?: boolean;
};

type WorkflowPreflightResult = {
  workflowData: Record<string, unknown>;
  actionOutputs: Record<string, Record<string, unknown>>;
};

function isEmptyPreflightResult(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") return value.trim().length === 0;
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value as Record<string, unknown>).length === 0;
  return false;
}

function preflightComparable(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

async function appendPreflightLog(
  deps: ControllerDeps,
  workflowId: string,
  actionId: string,
  data: Record<string, unknown>,
  error?: string,
): Promise<void> {
  flowEventSeq += 1;
  const event: FlowEvent = {
    id: `evt_${flowEventSeq}`,
    time: now(),
    type: "workflow_preflight",
    flowId: "preflight",
    workflowId,
    actionId,
    data,
    error: error ?? null,
  };
  await appendWorkflowJsonlLog(
    buildWorkflowLogRecord({ event, sessionKey: deps.sessionKey, botId: deps.botId, ownerId: deps.ownerId }),
    { baseDir: deps.workflowLogDir },
  );
}

async function runWorkflowPreflight(params: {
  deps: ControllerDeps;
  workflow: WorkflowSpec;
  workflowId: string;
  params: Record<string, string>;
  input: FlowInput;
  executionMode: ExecutionMode;
  bcsGroupId?: string;
}): Promise<WorkflowPreflightResult> {
  const preflight = params.workflow.workflow?.preflight ?? [];
  const workflowData: Record<string, unknown> = {};
  const actionOutputs: Record<string, Record<string, unknown>> = {};
  if (preflight.length === 0) return { workflowData, actionOutputs };

  const context: ActionExecutionContext = {
    flowId: "preflight",
    workflowId: params.workflowId,
    sessionKey: params.deps.sessionKey,
    executionMode: params.executionMode,
    bcsGroupId: params.bcsGroupId,
    params: params.params,
    input: params.input,
    workflowData,
    nodeOutput: {},
    actionOutputs,
    workflow: params.workflow,
    user: {
      id: params.workflow.defaults?.user?.id ?? params.deps.user?.id,
      name: params.workflow.defaults?.user?.name ?? params.deps.user?.name,
    },
  };
  const states: Record<string, ActionState> = {};

  for (const [index, item] of preflight.entries()) {
    const hookId = item.id?.trim() || `preflight-${index + 1}`;
    const hook: HookActionSpec = {
      id: hookId,
      action: item.action,
      required: item.required ?? true,
      args: item.args,
      retry: item.retry,
      saveAs: item.saveAs,
    };
    await appendPreflightLog(params.deps, params.workflowId, hookId, {
      status: "started",
      action: hook.action,
      required: hook.required === true,
    });

    const outcome = await runHookActions({
      hooks: [hook],
      states,
      registry: params.deps.actionRegistry,
      context,
    });
    const state = states[hookId];
    if (outcome.status === "blocked") {
      await appendPreflightLog(params.deps, params.workflowId, hookId, {
        status: "failed",
        action: hook.action,
      }, outcome.error);
      throw new Error(`preflight ${hookId} 失败: ${outcome.error}`);
    }
    if (state?.status === "failed") {
      await appendPreflightLog(params.deps, params.workflowId, hookId, {
        status: "skipped",
        action: hook.action,
        required: false,
      }, state.error ?? undefined);
      continue;
    }

    const result = state?.result ?? {};
    await appendPreflightLog(params.deps, params.workflowId, hookId, {
      status: "succeeded",
      action: hook.action,
      ...summarizeRecord(result),
    });
    const emptyTarget = item.abortIf?.empty === true
      ? result
      : typeof item.abortIf?.empty === "string"
        ? resolveTemplateValue(item.abortIf.empty, context, result)
        : undefined;
    if (item.abortIf?.empty !== undefined && isEmptyPreflightResult(emptyTarget)) {
      const message = typeof item.abortIf.message === "string" && item.abortIf.message.trim()
        ? String(resolveTemplateValue(item.abortIf.message, context, result))
        : `preflight ${hookId} 结果为空，未启动流程。`;
      throw new Error(message);
    }
    if (item.abortIf?.in) {
      const actual = resolveTemplateValue(item.abortIf.in.value, context, result);
      const actualComparable = preflightComparable(actual);
      const candidates = item.abortIf.in.list.map(preflightComparable);
      if (candidates.includes(actualComparable)) {
        const message = typeof item.abortIf.in.message === "string" && item.abortIf.in.message.trim()
          ? String(resolveTemplateValue(item.abortIf.in.message, context, result))
          : `preflight ${hookId} 命中阻断条件，未启动流程。`;
        throw new Error(message);
      }
    }
  }

  return { workflowData, actionOutputs };
}

async function startWorkflowAfterPreflight(
  deps: ControllerDeps,
  workflow: WorkflowSpec,
  workflowId: string,
  params: Record<string, string>,
  input: FlowInput,
  identity: FlowIdentity,
  executionMode: ExecutionMode,
  bcsGroupId?: string,
  resolvedWorkflow?: ResolvedWorkflow,
  preflightResult?: WorkflowPreflightResult,
  commandSurface: WorkflowCommandSurface = { type: "workflow" },
  startAsync = false,
  debug = false,
  campaignId?: string,
): Promise<string> {
  const workflowPin = buildWorkflowPin(resolvedWorkflow);

  // Load config: inline `config` on spec takes precedence;
  // then params.configPath (user override from CLI, e.g. --configPath)
  // then workflow.configPath (YAML default)
  let configData: Record<string, unknown> = {};
  const effectiveConfigPath = params.configPath || workflow.configPath;
  if (workflow.config && typeof workflow.config === "object") {
    configData = workflow.config as Record<string, unknown>;
  } else if (effectiveConfigPath) {
    // DB-sourced workflows have empty pack.root — fall back to workspace workflows directory
    const packRoot = resolvedWorkflow?.pack?.root
      || (resolvedWorkflow?.pack?.id
        ? join(homedir(), ".openclaw", "workspace", "workflows", resolvedWorkflow.pack.id)
        : "");
    const configAbsPath = effectiveConfigPath.startsWith("/") || effectiveConfigPath.startsWith("\\")
      ? effectiveConfigPath
      : (packRoot ? join(packRoot, effectiveConfigPath) : "");
    if (!configAbsPath) {
      throw new Error(`configPath "${effectiveConfigPath}" cannot be resolved: no pack root available`);
    }
    if (existsSync(configAbsPath)) {
      try {
        const raw = readFileSync(configAbsPath, "utf-8");
        if (configAbsPath.endsWith(".json")) {
          configData = JSON.parse(raw);
        } else {
          // YAML support — lazy require to avoid import cost for JSON-only packs
          const { load: yamlLoad } = await import("js-yaml") as typeof import("js-yaml");
          configData = yamlLoad(raw) as Record<string, unknown> ?? {};
        }
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        throw new Error(`Failed to load config from ${effectiveConfigPath}: ${msg}`);
      }
    } else {
      throw new Error(`configPath "${effectiveConfigPath}" does not exist in pack root`);
    }
  }

  // Inject resolved user identity into params so templates can use {{userId}}/{{userName}}`);
  const resolvedUserId = deps.user?.id ?? workflow.defaults?.user?.id;
  const resolvedUserName = deps.user?.name ?? workflow.defaults?.user?.name;
  const paramsWithUser: Record<string, string> = { ...params };
  if (resolvedUserId) paramsWithUser.userId = resolvedUserId;
  if (resolvedUserName) paramsWithUser.userName = resolvedUserName;

  // Inject session context for identity extraction (channel detection)
  if (deps.sessionKey) paramsWithUser.sessionKey = deps.sessionKey;
  const ownerId = loadOwnerId();
  if (ownerId) paramsWithUser.ownerId = ownerId;

  const initialState: FlowState = {
    workflowId,
    workflowVersion: workflow.version,
    params: paramsWithUser,
    commandSurface,
    input,
    identity,
    executionMode,
    bcsGroupId,
    businessStatus: "INIT",
    currentPhase: workflow.nodes[0]?.phase ?? "P1",
    activeNodes: [],
    nodeStates: {},
    workflowData: { ...(preflightResult?.workflowData ?? {}), ...(Object.keys(configData).length > 0 ? { config: configData } : {}) },
    actionOutputs: { ...(preflightResult?.actionOutputs ?? {}) },
    flowHooks: {},
    auditLog: [],
    ...(workflowPin ? { workflowPin, workflowSnapshot: structuredClone(workflow) } : {}),
  };

  // Collect credentials and session info for human intervention support
  const collected = collectCredentialsAndSession(deps.sessionKey, deps.sessionId);
  initialState.originCredentials = collected.credentialsJson;
  initialState.originSessionKey = collected.originSessionKey;
  initialState.originSessionId = collected.originSessionId;
  initialState.originBotId = collected.originBotId;

  for (const node of workflow.nodes) {
    initialState.nodeStates[node.id] = {
      status: "pending",
      phase: node.phase,
      executor: node.executor.type,
    };
  }

  const paramsDesc = Object.entries(params).map(([k, v]) => `${k}=${v}`).join(", ");
  const goalSuffix = [paramsDesc, identity.label].filter(Boolean).join(", ");
  appendAuditLog(initialState, "-", "flow-created", `${workflow.title} 流程创建 (${goalSuffix})`);
  const startedEvent = appendFlowEvent(initialState, {
    type: "workflow_started",
    flowId: "pending",
    workflowId,
    data: {
      executionMode,
      params,
      identity,
      inputDigest: input.digest,
      inputDigestShort: input.digestShort,
      fileCount: input.files.length,
      hasMessage: Boolean(input.message),
    },
  }, { log: false });

  const flow = await deps.boundTaskFlow.createManaged({
    controllerId: CONTROLLER_ID,
    workflowId,
    goal: `${workflow.title}: ${goalSuffix || identity.key}`,
    status: "running",
    currentStep: workflow.nodes[0]?.id ?? "start",
    stateJson: JSON.stringify(initialState),
  });

  const flowId = readFlowId(flow as Record<string, unknown>);
  const revision = (flow as Record<string, unknown>).revision as number | undefined;
  startedEvent.flowId = flowId;

  // Campaign: associate flow with campaign (re-associate with real flowId if campaignId was provided)
  if (campaignId) {
    const { onFlowStart } = await import("./campaign/campaign-hooks.js");
    // The pre-check in handleRun used "pending" as flowId — now we have the real one.
    // onFlowStart will associateFlow which inserts a row with the real flowId.
    // The "pending" row from handleRun's pre-check was a quota check only (no DB write
    // since associateFlow was not called — only checkQuota was).
    // Actually onFlowStart calls both checkQuota AND associateFlow. To avoid double-association,
    // we should only call associateFlow here. Let's call onFlowStart which does both —
    // the first call in handleRun only did checkQuota (because flowId was "pending").
    // Wait — onFlowStart always calls associateFlow. So we'd get a duplicate.
    // Fix: call associateFlow directly here, skip the handleRun pre-check's associate.
    // Actually the handleRun pre-check DID call onFlowStart which called associateFlow with "pending".
    // That created a row with flowId="pending". Now we need to update it to the real flowId.
    // Simplest: just associateFlow here with the real flowId. The "pending" row will be orphaned
    // but uk_campaign_flows_flow prevents duplicate flow_id — "pending" != real flowId so both exist.
    // To keep it clean: delete the "pending" row first, then associate with real flowId.
    try {
      const { getCampaignRepository } = await import("./campaign/campaign-hooks.js");
      const campaignRepo = getCampaignRepository();
      if (campaignRepo) {
        // The "pending" association from handleRun pre-check may have been created.
        // Since checkQuota doesn't call associateFlow (it only reads), there's no "pending" row.
        // So we can safely associateFlow here with the real flowId.
        await campaignRepo.associateFlow({ campaignId, flowId, workflowId });
      }
    } catch (err) {
      console.warn("[campaign] associateFlow with real flowId failed:", err);
    }
  }

  // Register workflow spec per flowId for concurrent flow isolation.
  // This enables emitNodeEvent and completeFlowRun to look up the correct
  // spec when multiple flows execute concurrently (async mode).
  _workflowSpecByFlowId.set(flowId, workflow);
  void appendWorkflowJsonlLog(buildWorkflowLogRecord({ event: startedEvent, sessionKey: deps.sessionKey, botId: deps.botId, ownerId: deps.ownerId }), { baseDir: deps.workflowLogDir }).catch(() => { /* best-effort log */ });

  // Run log: workflow started
  enqueueRunLog({
    flow_id: flowId,
    level: "info",
    source: "workflow",
    message: `Workflow started: ${workflowId} (${workflow.title || workflowId})`,
    timestamp: Date.now(),
  });

  // Persist flow run to engine DB immediately on start
  if (_flowRunRepository) {
    const triggeredBy = commandSurface.type === "facade" ? `facade:${commandSurface.command}` : commandSurface.type;
    // Fallback chain for userId: deps.user → workflow.defaults.user → originBotId 冒号后的工号
    const userIdFromBotId = collected.originBotId?.includes(":")
      ? collected.originBotId.split(":").pop()!.trim() || undefined
      : undefined;
    const userId = deps.user?.id ?? workflow.defaults?.user?.id ?? userIdFromBotId;
    const userName = deps.user?.name ?? workflow.defaults?.user?.name;
    // Build the full command text for display (e.g. "/marketing-dispatch 4000009")
    const commandText = commandSurface.type === "facade"
      ? `/${commandSurface.command} ${Object.values(params).join(" ")}`
      : (input.message ?? "");
    const inputInfo: Record<string, unknown> = {
      command: commandText.trim() || null,
      message: input.message ?? null,
      digest: input.digest,
      fileCount: input.files.length,
      hasMessage: Boolean(input.message),
    };
    if (userId) inputInfo.userId = userId;
    if (userName) inputInfo.userName = userName;
    await _flowRunRepository.insert({
      flowId,
      workflowId,
      workflowTitle: workflow.title,
      status: "running",
      paramsJson: JSON.stringify(params),
      inputJson: JSON.stringify(inputInfo),
      nodeCount: workflow.nodes.length,
      triggeredBy,
      identityKey: identity.key.length > 255 ? identity.key.slice(0, 252) + "..." : identity.key,
      currentPhase: initialState.currentPhase,
      startedAt: Math.floor(Date.now() / 1000),
      credentialsJson: collected.credentialsJson,
      originSessionKey: collected.originSessionKey,
      originSessionId: collected.originSessionId,
      originBotId: collected.originBotId,
      userId: userId ?? null,
      pluginVersion: loadConfig().app.version ?? null,
      engine: _engineName,
    }).catch((err) => {
      const errMsg = err instanceof Error ? err.message : String(err);
      console.error("[taskguard] flowRunRepo.insert failed:", errMsg);
      enqueueRunLog({
        flow_id: flowId,
        level: "error",
        source: "engine",
        message: `flowRunRepo.insert failed: ${errMsg}`,
        timestamp: Date.now(),
      });
    });
  } else {
    console.warn(`[taskguard] flowRunRepository not initialized — flow run not persisted (flowId=${flowId})`);
  }

  // ── HTTP callback notification for workflow started ──
  // Hot-load callback configs for this workflowId before dispatching,
  // in case configs were created via clawweb UI after engine startup.
  await ensureHttpCallbackConfigForWorkflow(workflowId).catch((err) => recordFailure("ensureHttpCallbackConfigForWorkflow", flowId, undefined, err, "warn"));
  if (_httpCallbackDispatcher) {
    console.log(`[controller] httpCallback dispatchWorkflowStarted: workflowId=${workflowId} flowId=${flowId}`);
    enqueueRunLog({
      flow_id: flowId,
      level: "info",
      source: "notification",
      message: `HTTP callback dispatched: workflow_started, workflowId=${workflowId}`,
      timestamp: Date.now(),
    });
    void _httpCallbackDispatcher.dispatchWorkflowStarted(workflowId, flowId).catch((err) => {
      console.warn(`[controller] httpCallback dispatchWorkflowStarted failed: workflowId=${workflowId} flowId=${flowId} error=${err instanceof Error ? err.message : String(err)}`);
    });
  }

  if (workflow.messages?.onCreated) {
    const msg = resolveTemplate(workflow.messages.onCreated, buildTemplateContext(initialState, deps.skillRoot, {}, { userIdentity: resolveUserIdentityForContext(deps) }));
    await deps.chatInject(msg, `${flowId}:flow:created`);
  }

  // ── Verbose workflow started notification ──
  // Workflow bookends minLevel=perf (always inject at any level ≥ perf).
  const wfLevel = deps.chatInjectLevel ?? "full";
  const startedMsg = buildWorkflowStartedMessage({
    workflow,
    flowId,
    input: params as Record<string, string> | undefined,
    level: wfLevel,
  });
  await deps.chatInject(startedMsg, `${flowId}:flow:verbose-started`);

  // ── Flow control: check if workflow can run now or must queue ──
  if (deps.flowControl) {
    // Store sessionKey in payload so the dispatcher can resume in the correct session context.
    const fcPayload = JSON.stringify({ sessionKey: deps.sessionKey });
    // Resolve sessionId for session-liveness zombie detection
    const fcSessionId = resolveSessionId(deps.sessionKey) ?? undefined;
    const workflowScopeKey = `workflow:${workflowId}`;
    console.log(`[controller] FLOW_CONTROL_TRY_ACQUIRE flowId=${flowId} workflow=${workflowId} scopeKey=${workflowScopeKey} sessionId=${fcSessionId ?? "none"}`);
    const acquireResult = await deps.flowControl.tryAcquire({
      scope: "workflow",
      key: workflowScopeKey,
      flowId,
      payload: fcPayload,
      sessionId: fcSessionId,
    });
    console.log(`[controller] FLOW_CONTROL_ACQUIRE_RESULT flowId=${flowId} workflow=${workflowId} acquired=${acquireResult.acquired} queuePosition=${(acquireResult as { acquired: boolean; queuePosition?: number }).queuePosition ?? "N/A"}`);

    if (!acquireResult.acquired) {
      // Queued — set flow to waiting and return
      console.log(`[flow-control] workflow ${workflowId} queued (flowId=${flowId}), waiting for slot`);
      const waitState: WaitState = {
        kind: "platform-workflow",
        workflowId,
        params,
        activeNodes: [],
        waitingFor: "flow-control-queue",
        hint: `蓄流排队中：工作流 ${workflow.title} 并发已达上限`,
        userAction: "等待调度器自动恢复，无需手动操作",
      };
      await deps.boundTaskFlow.setWaiting({
        flowId,
        expectedRevision: revision ?? 1,
        currentStep: workflow.nodes[0]?.id ?? "start",
        stateJson: JSON.stringify(initialState),
        waitJson: JSON.stringify(waitState),
        blockedSummary: `蓄流排队中：${workflow.title}`,
      });
      appendFlowEvent(initialState, {
        type: "flow_control_queued",
        flowId,
        workflowId,
        data: { scope: "workflow", key: workflowScopeKey },
      });
      console.log(`[controller] FLOW_QUEUED flowId=${flowId} workflow=${workflow.id} reason=flow_control_slot_unavailable`);
      completeFlowRun(flowId, "waiting", initialState.currentPhase);
      return flowId;
    }
  } else {
    console.warn(`[controller] FLOW_CONTROL_SKIPPED flowId=${flowId} workflow=${workflowId} — deps.flowControl is null/undefined, concurrency limiting is not active`);
  }

const runWorkflow = async () => {
    enqueueRunLog({
      flow_id: flowId,
      level: "info",
      source: "engine",
      message: `runWorkflow starting: workflow=${workflowId} startAsync=${startAsync}`,
      timestamp: Date.now(),
    });
    try {
      await executeWorkflowForTest({
        deps,
        workflow,
        state: initialState,
        flowId,
        revision: revision ?? 1,
        runStartHooks: true,
        debug,
      });
} catch (err) {
      // Safety net: release slots if executeLoop threw without reaching a terminal state.
      console.error(`[controller] handleRunWorkflow: executeLoop threw, releasing flow control slots for flow ${flowId}:`, err);
      deps.flowControl?.releaseAllForFlow(flowId);
      if (!startAsync) throw err;
    }
  };

  if (startAsync) {
    // Fully detach workflow execution from the current tool-call stack.
    // Calling runWorkflow() inline would execute synchronously until its first await;
    // an embedded-agent node reached in that window can re-enter the agent runtime
    // while workflow_engine_dispatch is still waiting, recreating the deadlock we are avoiding.
    setTimeout(() => {
      void runWorkflow().catch((err) => {
        console.error(`[controller] runWorkflow (async detach) failed for flowId=${flowId}:`, err);
        recordFailure("runWorkflow (async)", flowId, undefined, err, "error");
      });
    }, 0);
    return flowId;
  }

  await runWorkflow();
  return flowId;
}

async function executeSubworkflowChild(
  deps: ControllerDeps,
  childWorkflow: WorkflowSpec,
  childParams: Record<string, string>,
  parentFlowState: FlowState,
  parentNodeId: string,
  parentFlowId: string,
  depth: number,
): Promise<SubworkflowCompletionResult> {
  const childState = buildChildFlowState(
    childWorkflow,
    childParams,
    parentFlowState,
    parentNodeId,
    parentFlowId,
    depth,
  );

  const paramsDesc = Object.entries(childParams).map(([k, v]) => `${k}=${v}`).join(", ");

  appendAuditLog(parentFlowState, parentNodeId, "subworkflow-started", `子流程 ${childWorkflow.id} 开始 (depth=${depth})`);
  enqueueRunLog({
    flow_id: parentFlowId,
    node_id: parentNodeId,
    level: "info",
    source: "node",
    message: `Sub-workflow started: child=${childWorkflow.id}, depth=${depth}, params=[${paramsDesc}]`,
    timestamp: Date.now(),
  });
  appendFlowEvent(parentFlowState, {
    type: "subworkflow_started",
    flowId: parentFlowId,
    workflowId: parentFlowState.workflowId,
    nodeId: parentNodeId,
    data: {
      childWorkflowId: childWorkflow.id,
      depth,
      params: childParams,
    },
  });
  const goalSuffix = [paramsDesc].filter(Boolean).join(", ");

  const flow = await deps.boundTaskFlow.createManaged({
    controllerId: CONTROLLER_ID,
    workflowId: childWorkflow.id,
    goal: `${childWorkflow.title}: ${goalSuffix || "subflow"}`,
    status: "running",
    currentStep: childWorkflow.nodes[0]?.id ?? "start",
    stateJson: JSON.stringify(childState),
  });

  const childFlowId = readFlowId(flow);
  const childRevision = (flow as Record<string, unknown>).revision as number | undefined ?? 1;

  try {
    await executeWorkflowForTest({
      deps,
      workflow: childWorkflow,
      state: childState,
      flowId: childFlowId,
      revision: childRevision,
      runStartHooks: true,
    });

    const isComplete = isWorkflowComplete(childWorkflow, childState.nodeStates);
    const hasFailed = Object.values(childState.nodeStates).some((ns) => ns.status === "failed");

    if (hasFailed || !isComplete) {
      const failedNodes = Object.entries(childState.nodeStates)
        .filter(([, ns]) => ns.status === "failed")
        .map(([id, ns]) => `${id}: ${ns.error ?? "unknown"}`);
      const errorDetail = failedNodes.length > 0
        ? failedNodes.join("; ")
        : "workflow did not complete";

      appendAuditLog(parentFlowState, parentNodeId, "subworkflow-failed", `子流程 ${childWorkflow.id} 失败: ${errorDetail}`);
      enqueueRunLog({
        flow_id: parentFlowId,
        node_id: parentNodeId,
        level: "error",
        source: "node",
        message: `Sub-workflow failed: child=${childWorkflow.id}, childFlowId=${childFlowId}, depth=${depth}, error=${errorDetail.slice(0, 200)}`,
        timestamp: Date.now(),
      });
      appendFlowEvent(parentFlowState, {
        type: "subworkflow_finished",
        flowId: parentFlowId,
        workflowId: parentFlowState.workflowId,
        nodeId: parentNodeId,
        data: { childFlowId, childWorkflowId: childWorkflow.id, depth, status: "failed" },
        error: errorDetail,
      });

      return {
        status: "failed",
        workflowData: childState.workflowData,
        error: errorDetail,
      };
    }

    resolveAndStoreWorkflowOutputs(childWorkflow, childState);

    appendAuditLog(parentFlowState, parentNodeId, "subworkflow-succeeded", `子流程 ${childWorkflow.id} 完成`);
    enqueueRunLog({
      flow_id: parentFlowId,
      node_id: parentNodeId,
      level: "info",
      source: "node",
      message: `Sub-workflow succeeded: child=${childWorkflow.id}, childFlowId=${childFlowId}, depth=${depth}`,
      timestamp: Date.now(),
    });
    appendFlowEvent(parentFlowState, {
      type: "subworkflow_finished",
      flowId: parentFlowId,
      workflowId: parentFlowState.workflowId,
      nodeId: parentNodeId,
      data: { childFlowId, childWorkflowId: childWorkflow.id, depth, status: "succeeded" },
    });

    const outputs = childWorkflow.outputs && Object.keys(childWorkflow.outputs).length > 0
      ? (childState.workflowData.outputs as Record<string, unknown> ?? {})
      : undefined;

    return {
      status: "succeeded",
      workflowData: childState.workflowData,
      outputs,
    };
  } catch (err) {
    const errMsg = err instanceof Error ? err.message : String(err);

    appendAuditLog(parentFlowState, parentNodeId, "subworkflow-failed", `子流程 ${childWorkflow.id} 异常: ${errMsg}`);
    enqueueRunLog({
      flow_id: parentFlowId,
      node_id: parentNodeId,
      level: "error",
      source: "node",
      message: `Sub-workflow crashed: child=${childWorkflow.id}, childFlowId=${childFlowId}, depth=${depth}, error=${errMsg.slice(0, 200)}`,
      timestamp: Date.now(),
    });
    appendFlowEvent(parentFlowState, {
      type: "subworkflow_finished",
      flowId: parentFlowId,
      workflowId: parentFlowState.workflowId,
      nodeId: parentNodeId,
      data: { childFlowId, childWorkflowId: childWorkflow.id, depth, status: "failed" },
      error: errMsg,
    }, { rawError: err });

    return {
      status: "failed",
      workflowData: childState.workflowData,
      error: errMsg,
    };
  }
}

function buildSubworkflowDepsForExecution(
  deps: ControllerDeps,
  parentFlowState: FlowState,
  parentFlowId: string,
  debug?: boolean,
): import("./executors/subworkflow.js").SubworkflowDeps {
  return {
    resolvedWorkflows: deps.resolvedWorkflows,
    workflowSpecRepo: deps.workflowSpecApiRepo ?? (deps.db && deps.db.dbType !== "noop" ? new WorkflowSpecRepository(deps.db) : undefined),
    debug,
    executeChildWorkflow: async ({ childWorkflow, childParams, parentNodeId, depth }) => {
      return executeSubworkflowChild(
        deps,
        childWorkflow,
        childParams,
        parentFlowState,
        parentNodeId,
        parentFlowId,
        depth,
      );
    },
  };
}

export async function handleRun(
  deps: ControllerDeps,
  options: {
    workflowId: string;
    params: Record<string, string>;
    message?: string;
    files?: string[];
    executionMode: ExecutionMode;
    bcsGroupId?: string;
    commandSurface?: WorkflowCommandSurface;
    debug?: boolean;
    startAsync?: boolean;
    chatInjectLevel?: import("./inject-level.js").InjectLevel;
    campaignId?: string;
  },
): Promise<string> {
  console.info("[taskguard] handleRun entry", { workflowId: options.workflowId, executionMode: options.executionMode, commandSurface: options.commandSurface, campaignId: options.campaignId });
  const { workflow, resolved } = await requireWorkflowLookup(deps, options.workflowId, options.debug);
  console.info("[taskguard] handleRun after requireWorkflowLookup", { workflowId: options.workflowId, workflowFound: !!workflow, resolvedSource: resolved?.source?.kind });
  _currentWorkflowSpec = workflow;

  // Campaign quota check (before any work is done)
  if (options.campaignId) {
    const { getCampaignRepository } = await import("./campaign/campaign-hooks.js");
    const campaignRepo = getCampaignRepository();
    if (campaignRepo) {
      const quota = await campaignRepo.checkQuota(options.campaignId);
      if (!quota.allowed) {
        throw new Error(`Campaign quota exceeded: ${quota.reason ?? "unknown"}`);
      }
    }
  }
  // Priority chain (high→low): trigger param > workflow YAML > process dep.
  // (env/DB/global yaml already resolved into deps.chatInjectLevel by loader.)
  // Clone deps so concurrent runs don't race on the shared process-level dep.
  // specLevel (WorkflowSpec.chatInject.level) is wired in T7; guarded with
  // optional chaining until then.
  const specLevel = resolved?.spec?.chatInject?.level as InjectLevel | undefined;
  const effectiveLevel: InjectLevel =
    options.chatInjectLevel ?? specLevel ?? deps.chatInjectLevel ?? "full";
  const runDeps: ControllerDeps = { ...deps, chatInjectLevel: effectiveLevel };
  if (options.message && !options.message.startsWith("--")) {
    const missing = requiredParamsForWorkflow(workflow).filter((p) => !options.params[p]);
    if (missing.length > 0) {
      options = { ...options, params: { ...options.params, [missing[0]]: options.message }, message: undefined };
    }
  }
  assertRequiredParams(workflow, options.workflowId, options.params, "run");

  const input = await normalizeFlowInput({
    workflowId: options.workflowId,
    workflowDigest: workflowDigestForLaunch(workflow, resolved),
    packDigest: resolved?.pack.digest,
    inputSpec: workflow.input,
    params: options.params,
    message: options.message,
    files: options.files,
    artifactDir: createFlowArtifactDir(options.workflowId),
  });
  const identity = resolveFlowIdentity(workflow, input, options.params);

  const activeFlowId = await assertNoDuplicateActiveFlow(runDeps, options.workflowId, identity);
  if (activeFlowId) return activeFlowId;

  const preflightResult = await runWorkflowPreflight({
    deps: runDeps,
    workflow,
    workflowId: options.workflowId,
    params: options.params,
    input,
    executionMode: options.executionMode,
    bcsGroupId: options.bcsGroupId,
  });

  return startWorkflowAfterPreflight(
    runDeps,
    workflow,
    options.workflowId,
    options.params,
    input,
    identity,
    options.executionMode,
    options.bcsGroupId,
    resolved,
    preflightResult,
    options.commandSurface,
    options.startAsync === true,
    options.debug === true,
    options.campaignId,
  );
}

export async function handleReopen(
  deps: ControllerDeps,
  workflowId: string,
  params: Record<string, string>,
  executionMode: ExecutionMode,
  bcsGroupId?: string,
  commandSurface?: WorkflowCommandSurface,
): Promise<string> {
  const { workflow, resolved } = await requireWorkflowLookup(deps, workflowId);
  assertRequiredParams(workflow, workflowId, params, "run");

  const input = await normalizeFlowInput({
    workflowId,
    workflowDigest: workflowDigestForLaunch(workflow, resolved),
    packDigest: resolved?.pack.digest,
    inputSpec: workflow.input,
    params,
    artifactDir: createFlowArtifactDir(workflowId),
  });
  const identity = resolveFlowIdentity(workflow, input, params);

  const preflightResult = await runWorkflowPreflight({
    deps,
    workflow,
    workflowId,
    params,
    input,
    executionMode,
    bcsGroupId,
  });

  const reason = params.reason?.trim() || `identity ${identity.label} 重新开启，软清理旧流程`;
  const activeFlows = await findActiveFlowsForIdentity(deps, workflowId, identity);
  const affectedFlowIds: string[] = [];

  for (const flow of activeFlows) {
    const flowId = readFlowId(flow);
    const revision = typeof flow.revision === "number" ? flow.revision : 1;
    const state = parseFlowState(flow);

    appendFlowEvent(state, {
      type: "workflow_reopened",
      flowId,
      workflowId,
      data: { reason, identity },
    });

    // BUG-25 fix: Wrap boundTaskFlow.fail() to prevent unhandled exceptions
    try {
      const failResult = await deps.boundTaskFlow.fail({
        flowId,
        expectedRevision: revision,
        stateJson: JSON.stringify(state),
        blockedSummary: reason,
        endedAt: now(),
      });
      if (
        failResult
        && typeof failResult === "object"
        && !Array.isArray(failResult)
        && (failResult as { applied?: unknown }).applied === false
      ) {
        throw new Error("旧流程状态更新冲突，请重试");
      }
    } catch (failErr) {
      if (failErr instanceof Error && failErr.message === "旧流程状态更新冲突，请重试") throw failErr;
      const errMsg = failErr instanceof Error ? failErr.message : String(failErr);
      console.error(`[controller] reopenWorkflow: boundTaskFlow.fail() threw for flowId=${flowId}:`, failErr);
      enqueueRunLog({
        flow_id: flowId,
        level: "error",
        source: "engine",
        message: `boundTaskFlow.fail() failed in reopenWorkflow: ${errMsg}`,
        timestamp: Date.now(),
      });
    }
    affectedFlowIds.push(flowId);
  }

  const newFlowId = await startWorkflowAfterPreflight(
    deps,
    workflow,
    workflowId,
    params,
    input,
    identity,
    executionMode,
    bcsGroupId,
    resolved,
    preflightResult,
    commandSurface,
  );
  await deps.chatInject(
    `${identity.label} 已重新开启，新流程 ${newFlowId}`,
    `${newFlowId}:flow:reopened`,
  );

  const oldFlowsSummary = affectedFlowIds.length > 0
    ? `已软清理旧流程：${affectedFlowIds.join(", ")}`
    : "没有需要软清理的旧流程";
  return `已重新开启 ${identity.label}，新流程 ${newFlowId}。${oldFlowsSummary}`;
}

export async function handleList(deps: ControllerDeps, filter?: string): Promise<string> {
  const resolvedWorkflows = deps.resolvedWorkflows ?? [];
  const failedWorkflows = deps.failedWorkflows ?? [];
  const filterLower = filter?.toLowerCase();

  // Early exit only when there are no local workflows AND no DB access
  // (if DB is reachable, there may be shared workflows to show)
  if (resolvedWorkflows.length === 0 && failedWorkflows.length === 0 && !deps.clawWebBaseUrl) {
    return "（暂无已注册的 workflow）";
  }

  if (resolvedWorkflows.length === 0 && failedWorkflows.length === 0) {
    return "（暂无已注册的 workflow）";
  }

  // Compute sync status for all workflows (best-effort, may fail if version deps missing)
  let syncMap: Map<string, { syncStatus: string; action: string; newerSide?: string; dbUpdatedAt?: string; localMtime?: string }> = new Map();
  try {
    const { computeSyncStatus } = await import("./controller/version-commands.js");
    for (const wf of resolvedWorkflows) {
      const wfId = (wf as any).id;
      const { syncStatus, action, newerSide, dbUpdatedAt, localMtime } = await computeSyncStatus(deps, wfId);
      syncMap.set(wfId, { syncStatus, action, newerSide, dbUpdatedAt, localMtime });
    }
  } catch {
    // Version deps may not be configured — skip sync info
  }

  // Table header
  const hasSync = syncMap.size > 0;
  const lines: string[] = ["| id | title | pack | nodes | path | sync_status |", "|---|---|---|---|---|---|"];

  for (const wf of resolvedWorkflows) {
    const wfId = (wf as any).id;
    const spec = (wf as any).spec;
    const title = spec?.title ?? "-";
    const nodes = spec?.nodes?.length ?? "?";
    const packId = (wf as any).pack?.id ?? "-";
    const packVersion = (wf as any).pack?.version;
    const packLabel = packId !== "-" && packVersion ? `${packId}@${packVersion}` : packId;

    // Local path (absolute)
    const absPath: string | undefined = (wf as any).absolutePath;
    const localPath = absPath ?? "-";

    // Sync status
    const sync = syncMap.get(wfId);
    let syncLabel = "-";
    if (sync) {
      const dirHint = sync.syncStatus === "differs" && sync.newerSide
        ? ` (${sync.newerSide === "db_newer" ? "DB更新" : sync.newerSide === "local_newer" ? "本地更新" : sync.newerSide})`
        : "";
      syncLabel = `${sync.syncStatus}${dirHint}${sync.action ? ` ${sync.action}` : ""}`;
    }

    // Apply filter
    if (filterLower) {
      const haystack = `${wfId} ${title} ${packId} ${localPath}`.toLowerCase();
      if (!haystack.includes(filterLower)) continue;
    }

    lines.push(`| ${wfId} | ${title} | ${packLabel} | ${nodes} | ${localPath} | ${syncLabel} |`);
  }

  // Failed workflows section
  for (const fw of failedWorkflows) {
    const packLabel = fw.packVersion ? `${fw.packId}@${fw.packVersion}` : fw.packId;

    // Apply filter
    if (filterLower) {
      const haystack = `${fw.id} ${fw.packId} ${fw.absolutePath} ${fw.error}`.toLowerCase();
      if (!haystack.includes(filterLower)) continue;
    }

    lines.push(`| ${fw.id} | ⚠️ load_error | ${packLabel} | - | ${fw.absolutePath} | ${fw.error} |`);
  }

  if (lines.length === 2) return "（暂无匹配的 workflow）";

  const totalOk = resolvedWorkflows.length;
  const totalFail = failedWorkflows.length;

  // Check DB for workflows accessible to this bot but not present locally
  // (e.g. shared by another bot via "workflow share"). These won't appear in
  // resolvedWorkflows (which scans local packs/) but the bot has permission
  // to view/execute them. Prompt the user to pull them.
  const localIds = new Set(resolvedWorkflows.map((w: any) => (w as any).id));
  if (deps.clawWebBaseUrl && (deps.botId || deps.ownerId)) {
    try {
      const dbResp = await fetch(
        `${deps.clawWebBaseUrl}/api/workflows?botOwnerId=${deps.ownerId ?? ""}${deps.botId ? `&botId=${deps.botId}` : ""}`,
      );
      if (dbResp.ok) {
        const dbData = await dbResp.json();
        const dbList: Array<{ workflowId?: string; workflow_id?: string; title?: string; packId?: string; pack_id?: string }> =
          Array.isArray(dbData) ? dbData : (dbData as any).workflows ?? [];
        const dbOnly = dbList.filter((w) => {
          const id = w.workflowId ?? w.workflow_id;
          return id && !localIds.has(id);
        });
        if (dbOnly.length > 0) {
          lines.push("", "---", `📋 DB 可访问但本地未安装 (${dbOnly.length}):`, "");
          for (const w of dbOnly) {
            const id = w.workflowId ?? w.workflow_id;
            const title = w.title ?? id;
            const pack = w.packId ?? w.pack_id ?? "-";
            lines.push(`| ${id} | ${title} | ${pack} | - | db_only | → pull 同步 |`);
          }
          lines.push("", `💡 使用 workflow pull <id> 将 DB 中的 workflow 同步到本地`);
        }
      }
    } catch {
      // DB query failed — non-fatal
    }
  }

  const summary = totalFail > 0
    ? `Total: ${totalOk} workflow(s)${totalFail > 0 ? `, ${totalFail} load error(s)` : ""}`
    : `Total: ${totalOk} workflow(s)`;
  lines.push("", summary);

  // Append timestamp details for workflows that differ from DB
  const differsWithTs = [...syncMap.entries()]
    .filter(([, s]) => s.syncStatus === "differs" && (s.dbUpdatedAt || s.localMtime))
    .map(([id, s]) => ({ id, ...s }));
  if (differsWithTs.length > 0) {
    lines.push("", "🕐 差异时间详情:");
    for (const d of differsWithTs) {
      const dirLabel = d.newerSide === "db_newer" ? "DB更新" : d.newerSide === "local_newer" ? "本地更新" : d.newerSide ?? "未知";
      lines.push(`  ${d.id}: ${dirLabel} | DB=${d.dbUpdatedAt ?? "?"} | 本地=${d.localMtime ?? "?"}`);
    }
  }

  return lines.join("\n");
}

export async function handlePacks(deps: ControllerDeps): Promise<string> {
  const packs = deps.resolvedPacks ?? [];
  const lines = ["**已安装的 Workflow Pack：**", ""];
  for (const pack of packs) {
    const workflowIds = pack.workflows.map((workflow) => workflow.id).join(", ");
    lines.push(`- **${pack.manifest.id}** @ ${pack.manifest.version} — workflows=${workflowIds || "-"} | source=${pack.source.kind}`);
  }
  if (lines.length === 2) lines.push("（暂无已发现的 workflow pack）");
  return lines.join("\n");
}

export async function handlePackInspect(deps: ControllerDeps, packId: string): Promise<string> {
  const pack = findPackById(deps, packId);
  if (!pack) throw new Error(`Workflow pack "${packId}" not found`);
  const workflows = workflowsForPack(deps, packId);
  const workflowLines = workflows.length > 0
    ? workflows.map((workflow) => `- ${workflow.id}：${workflow.spec.title}（${workflow.digest}）`)
    : ["- （无）"];
  const actionLines = (pack.manifest.actions ?? []).length > 0
    ? (pack.manifest.actions ?? []).map((action) => `- ${action.id}：${action.type}，目录 ${action.root}`)
    : ["- （无）"];
  const skillLines = (pack.manifest.skills?.required ?? []).length > 0
    ? (pack.manifest.skills?.required ?? []).map((skillName) => `- ${skillName}`)
    : ["- （无）"];
  const firstWorkflowId = workflows[0]?.id ?? pack.manifest.workflows[0]?.id ?? pack.manifest.id;
  const lines = [
    `**Workflow Pack 详情：${pack.manifest.id}**`,
    "",
    "**基础信息**",
    `- Pack：${pack.manifest.id}@${pack.manifest.version}`,
    `- 来源：${pack.source.kind}`,
    `- 安装位置：${pack.root}`,
    `- 内容指纹：${pack.digest}（用于确认 Pack 内容是否变化）`,
    "",
    "**包含 Workflow**",
    ...workflowLines,
    "",
    "**包含 Action**",
    ...actionLines,
    "",
    "**依赖 Skill**",
    ...skillLines,
    "",
    "**可继续执行**",
    `- /workflow validate ${firstWorkflowId}：校验该 workflow 是否可运行。`,
  ];
  return lines.join("\n");
}

async function validatePackResources(
  deps: Pick<ControllerDeps, "actionRegistry" | "resolvedWorkflows">,
  pack: ResolvedWorkflowPack,
): Promise<ValidationIssue[]> {
  const issues: ValidationIssue[] = [];
  for (const action of pack.manifest.actions ?? []) {
    if (!existsSync(join(pack.root, action.root))) {
      issues.push(issue(
        "ACTION_ROOT_MISSING",
        `Action 脚本目录不存在：${action.id}`,
        action.root,
        "确认 Pack 内 actions[].root 指向的目录已随 Pack 安装。",
      ));
    }
  }
  for (const skillName of pack.manifest.skills?.required ?? []) {
    if (!await skillExistsInExecutableRoots(skillName)) {
      issues.push(issue(
        "REQUIRED_SKILL_MISSING",
        `缺少必需 Skill：${skillName}`,
        "未在 OpenClaw 可执行 Skill 路径中找到。",
        "确认该 skill 已安装到 ~/openclawExt/clawmind/skills 或 ~/.openclaw/workspace/skills。",
      ));
    }
  }
  for (const facade of pack.manifest.facades ?? []) {
    const facadeSkillPath = join(pack.root, "skills", facade.command, "SKILL.md");
    if (!existsSync(facadeSkillPath)) {
      issues.push(issue(
        "FACADE_SKILL_MISSING",
        `Facade Skill 缺失：${facade.command}`,
        `skills/${facade.command}/SKILL.md`,
        "在 Pack 内补充对应 SKILL.md，以获得原生 slash/tool 体验；before_agent_reply hook 仍可兜底识别 facade command。",
      ));
    }
  }
  for (const workflow of workflowsForPack(deps, pack.manifest.id)) {
    try {
      validateWorkflowSemantics(workflow.spec);
      validateWorkflowResources(workflow.spec, { actionRegistry: deps.actionRegistry });
    } catch (err) {
      if (err instanceof WorkflowValidationError) {
        issues.push(issue(
          "WORKFLOW_VALIDATION_FAILED",
          `Workflow 配置校验失败：${workflow.id}`,
          [`Workflow：${workflow.id}`, formatValidationIssues(err.issues)].join("\n"),
          "根据 path 修改 workflow YAML 或 action 注册后重新校验。",
        ));
      } else {
        throw err;
      }
    }
  }
  return issues;
}

function renderPackValidateReport(packId: string, pack: ResolvedWorkflowPack, issues: ValidationIssue[]): string {
  const workflowIds = pack.workflows.map((workflow) => workflow.id).join(", ") || "-";
  const nextWorkflowId = pack.workflows[0]?.id ?? packId;
  const actionIds = (pack.manifest.actions ?? []).map((action) => action.id).join(", ") || "-";
  const lines = [
    `**Workflow Pack 校验：${packId}**`,
    "",
    issues.length === 0 ? "结论：✅ 通过" : `结论：❌ 未通过（${issues.length} 个问题）`,
    "",
    "**基础信息**",
    `- Pack：${pack.manifest.id}@${pack.manifest.version}`,
    `- 来源：${pack.source.kind}`,
    `- 路径：${pack.root}`,
    `- Workflows：${workflowIds}`,
    `- Actions：${actionIds}`,
    `- Required Skill Roots：${executableSkillRoots().join(", ")}`,
    "",
    "**检查项**",
    "✅ Manifest 可读取",
    hasIssue(issues, "WORKFLOW_VALIDATION_FAILED") ? "❌ Workflow 配置有效" : "✅ Workflow 配置有效",
    hasIssue(issues, "ACTION_ROOT_MISSING") ? "❌ Action 脚本存在" : "✅ Action 脚本存在",
    hasIssue(issues, "FACADE_SKILL_MISSING") ? "❌ Facade Skill 存在" : "✅ Facade Skill 存在",
    hasIssue(issues, "REQUIRED_SKILL_MISSING") ? "❌ Required Skills 缺失" : "✅ Required Skills 已安装",
    ...renderIssueList(issues),
  ];

  if (issues.length === 0) {
    lines.push("", "**可继续执行**", `- /workflow validate ${nextWorkflowId}`, `- /workflow cutover-check ${nextWorkflowId}`);
  } else {
    lines.push("", "**重新验证**", `- /workflow pack validate ${packId}`);
  }
  lines.push(
    "",
    "**推荐主路径**",
    `- /workflow validate ${nextWorkflowId}：校验 workflow 配置、Pack 资源和必需 Skill。`,
  );

  return lines.join("\n");
}

export async function handlePackValidate(deps: ControllerDeps, packId: string): Promise<string> {
  const pack = findPackById(deps, packId);
  if (!pack) throw new Error(`Workflow pack "${packId}" not found`);
  const issues = await validatePackResources(deps, pack);
  return renderPackValidateReport(packId, pack, issues);
}

export async function handleCutoverCheck(deps: ControllerDeps, workflowId: string): Promise<string> {
  const resolved = resolveWorkflowByIdFromPacks(workflowId, deps.resolvedWorkflows ?? []);
  if (!resolved) {
    const issues = [issue(
      "PACK_NOT_DISCOVERED",
      "Workflow Pack 未安装或未被发现",
      undefined,
      "建议安装对应 workflow pack 后重试",
    )];
    return [
      `**外置 Pack 切换检查：${workflowId}**`,
      "",
      "结论：❌ 未就绪（1 个问题）",
      "",
      "**检查项**",
      "❌ Pack 已安装",
      ...renderIssueList(issues),
      "",
      "**重新验证**",
      `- /workflow cutover-check ${workflowId}`,
    ].join("\n");
  }
  const pack = findPackById(deps, resolved.pack.id);
  const issues = pack
    ? await validatePackResources(deps, pack)
    : [issue(
      "PACK_NOT_FOUND",
      `Workflow Pack 未找到：${resolved.pack.id}`,
      "workflow 已解析，但对应 Pack 未在当前 catalog 中找到。",
      "重新安装 Pack 后执行 /workflow pack validate。",
    )];
  const flows = normalizeFlowListResult(await deps.boundTaskFlow.list());
  const related = flows.filter((flow) => safeParseFlowState(flow)?.workflowId === workflowId);
  const legacy = related.filter((flow) => {
    const state = safeParseFlowState(flow);
    return state && (!state.workflowPin || !state.workflowSnapshot);
  });
  const pinned = related.length - legacy.length;
  const legacyIssues = legacy.length === 0
    ? []
    : [issue(
      "CUTOVER_LEGACY_FLOWS_FOUND",
      `发现需补齐 Pack 绑定信息的旧流程：${legacy.length} 个`,
      "这些旧流程缺少 workflowPin/workflowSnapshot。旧流程不影响新 flow 启动；但恢复旧 flow 前建议先完成修复。",
      [
        `先预览：/workflow repair external-pack-pin --workflowId ${workflowId} --dryRun`,
        `确认后修复：/workflow repair external-pack-pin --workflowId ${workflowId}`,
        "如果只是历史 failed flow，可用 /workflow flows cleanup --identityKey <identity> --status failed 软清理。",
        `修复后复查：/workflow cutover-check ${workflowId}`,
      ].join("\n"),
    )];
  const allIssues = [...issues, ...legacyIssues];
  const issueCount = allIssues.length;
  const onlyLegacyIssues = issues.length === 0 && legacyIssues.length > 0;
  const lines = [
    `**外置 Pack 切换检查：${workflowId}**`,
    "",
    issueCount === 0
      ? "结论：✅ 就绪"
      : onlyLegacyIssues
        ? `结论：⚠️ 新 flow 可启动，旧流程待修复（${issueCount} 个问题）`
        : `结论：❌ 未就绪（${issueCount} 个问题）`,
    "",
    "**基础信息**",
    `- Pack：${resolved.pack.id}@${resolved.pack.version}`,
    `- 来源：${resolved.source.kind}`,
    `- Workflow Digest：${resolved.digest}`,
    `- 可见流程：${related.length}`,
    `- 已绑定 Pack 或 Snapshot：${pinned}`,
    `- 旧流程可恢复：${legacy.length}`,
    "",
    "**检查项**",
    "✅ Pack 已安装",
    issues.length === 0 ? "✅ Pack 资源校验通过" : "❌ Pack 资源校验未通过",
    legacy.length === 0 ? "✅ 未发现需补齐 Pack 绑定信息的旧流程" : "⚠️ 发现需补齐 Pack 绑定信息的旧流程",
    ...renderIssueList(allIssues),
  ];
  if (issueCount > 0) {
    lines.push("", "**重新验证**");
    if (issues.length > 0) lines.push(`- /workflow pack validate ${resolved.pack.id}`);
    lines.push(`- /workflow cutover-check ${workflowId}`);
  } else {
    lines.push("", "**可继续执行**", `- /workflow run ${workflowId} --key <value>`);
  }
  return lines.join("\n");
}

type HelpCommandGroup = "启动与检查" | "查看与排障" | "版本管理" | "人工推进" | "恢复与运维";

type HelpCommandEntry = {
  group: HelpCommandGroup;
  generic: string;
  command: string;
  description: string;
  facadeDescription?: string;
  visibility?: "all" | "workflow-only";
};

const HELP_COMMAND_GROUPS: HelpCommandGroup[] = ["启动与检查", "查看与排障", "版本管理", "人工推进", "恢复与运维"];

const HELP_COMMAND_CATALOG: HelpCommandEntry[] = [
  {
    group: "启动与检查",
    generic: "/workflow help [workflowId]",
    command: "help",
    description: "查看通用帮助；传入 workflowId 可查看该 workflow 的常用命令。",
    facadeDescription: "查看该 workflow 的常用命令。",
  },
  {
    group: "启动与检查",
    generic: "/workflow run <workflowId> [--key value ...] [--file <path>] [任务描述...]",
    command: "run",
    description: "以通用任务输入启动 workflow。",
    facadeDescription: "以任务输入启动该 workflow。",
  },
  {
    group: "启动与检查",
    generic: "/workflow inspect [flowId] [--analyze] [--full]",
    command: "inspect",
    description: "查看 flow 状态摘要、节点表与近期事件；--analyze 深度报告；--full 原始 JSON。",
  },
  {
    group: "启动与检查",
    generic: "/workflow detail <workflowId>",
    command: "detail",
    description: "查看 workflow 节点详情。",
  },
  {
    group: "启动与检查",
    generic: "/workflow validate <workflowId>",
    command: "validate",
    description: "启动前校验 workflow 配置。",
  },
  {
    group: "启动与检查",
    generic: "/workflow packs",
    command: "packs",
    description: "列出已安装 workflow pack。",
    visibility: "workflow-only",
  },
  {
    group: "启动与检查",
    generic: "/workflow pack inspect <packId>",
    command: "pack",
    description: "查看 pack 来源、workflow、action 和技能依赖。",
    visibility: "workflow-only",
  },
  {
    group: "启动与检查",
    generic: "/workflow cutover-check <workflowId>",
    command: "cutover-check",
    description: "检查 workflow 外置 pack 切换就绪状态。",
    visibility: "workflow-only",
  },
  {
    group: "启动与检查",
    generic: "/workflow list",
    command: "list",
    description: "列出所有已注册 workflow。",
    visibility: "workflow-only",
  },
  {
    group: "查看与排障",
    generic: "/workflow runs [--limit 20] [--includeHidden]",
    command: "runs",
    description: "查看当前 session 可见运行记录。",
  },
  {
    group: "查看与排障",
    generic: "/workflow runs --global [--identityKey <identity>] [--workflowId <workflowId>] [--status <status>]",
    command: "runs",
    description: "只读查询全局运行记录。",
    visibility: "workflow-only",
  },
  {
    group: "版本管理",
    generic: "/workflow deploy <workflowId> [--yes]",
    command: "deploy",
    description: "部署 workflow 到 DB，自动 git commit + tag。",
    facadeDescription: "部署该 workflow 到线上。",
  },
  {
    group: "版本管理",
    generic: "/workflow pull [workflowId]",
    command: "pull",
    description: "从 DB 同步 workflow 到本地（无参数=全量）。",
    visibility: "workflow-only",
  },
  {
    group: "版本管理",
    generic: "/workflow rollback <workflowId> --version <v> [--pack]",
    command: "rollback",
    description: "回退 workflow 到指定版本并重新部署。",
  },
  {
    group: "版本管理",
    generic: "/workflow deploys <workflowId> [--limit N]",
    command: "deploys",
    description: "查看 workflow 部署历史。",
  },
  {
    group: "版本管理",
    generic: "/workflow status [workflowId] [--diff]",
    command: "status",
    description: "查看本地 vs 线上版本对比。",
    visibility: "workflow-only",
  },
  {
    group: "版本管理",
    generic: "/workflow share <workflowId> --to <ownerId>/<botId>",
    command: "share",
    description: "共享 workflow 给目标 bot（授权+自动同步）。",
    visibility: "workflow-only",
  },
  {
    group: "版本管理",
    generic: "/workflow unshare <workflowId> --from <ownerId>/<botId>",
    command: "unshare",
    description: "撤销共享（撤销权限）。",
    visibility: "workflow-only",
  },
  {
    group: "人工推进",
    generic: "/workflow submit --node <nodeId> [--flowId <flowId>] [--result-json '<json>'] [文本...]",
    command: "submit",
    description: "提交协作节点人工结果。",
  },
  {
    group: "人工推进",
    generic: "/workflow revise [--node <approvalNodeId>] <审批意见> [--flowId <flowId>]",
    command: "revise",
    description: "要求修订并回退相关节点。",
  },
  {
    group: "人工推进",
    generic: "/workflow confirm [备注]",
    command: "confirm",
    description: "确认当前等待节点。",
  },
  {
    group: "人工推进",
    generic: "/workflow reject [理由]",
    command: "reject",
    description: "驳回当前等待节点。",
  },
  {
    group: "人工推进",
    generic: "/workflow resume <flowId> <revision>",
    command: "resume",
    description: "恢复指定流程。",
  },
  {
    group: "恢复与运维",
    generic: "/workflow reopen <workflowId> [--key value ...] [--reason ...]",
    command: "reopen",
    description: "按 workflow identity 软清理活跃流程并重新启动。",
  },
  {
    group: "恢复与运维",
    generic: "/workflow retry [--node <nodeId>] [--flowId <flowId>] [--reason ...]",
    command: "retry",
    description: "重试指定的非执行中节点，支持成功节点，重置该节点和下游。",
  },
  {
    group: "恢复与运维",
    generic: "/workflow skip --node <nodeId> <reason> [--flowId <flowId>] [--result-json '<json>'] [--no-hooks]",
    command: "skip",
    description: "人工跳过 waiting/failed/blocked 节点并继续下游。",
  },
  {
    group: "恢复与运维",
    generic: "/workflow runs cleanup --identityKey <identity> --status failed",
    command: "runs",
    description: "软清理当前 session 下指定 identity 的 failed 运行记录。",
    visibility: "workflow-only",
  },
  {
    group: "恢复与运维",
    generic: "/workflow repair legacy-identity --workflowId <workflowId> [--flowId <flowId>] [--dryRun]",
    command: "repair",
    description: "为旧 flow 补齐通用 identity，解决迁移后的恢复和筛选问题。",
    visibility: "workflow-only",
  },
  {
    group: "恢复与运维",
    generic: "/workflow repair external-pack-pin --workflowId <workflowId> [--flowId <flowId>] [--dryRun]",
    command: "repair",
    description: "为外置 Pack 迁移前创建的旧 flow 补齐 workflowPin/workflowSnapshot。",
    visibility: "workflow-only",
  },
  {
    group: "恢复与运维",
    generic: "/workflow export --flowId <flowId>",
    command: "export",
    description: "导出 flow state，可跨 session 导入。",
  },
  {
    group: "恢复与运维",
    generic: "/workflow import <exportToken>",
    command: "import",
    description: "把导出的 flow state 导入当前 session。",
  },
];

function renderHelpCommand(
  entry: HelpCommandEntry,
  workflowId: string | undefined,
  options: Pick<ControllerDeps, "formatWorkflowCommand">,
): string {
  if (!workflowId) return entry.generic;
  const args = entry.generic.split(/\s+/).slice(2).filter((arg) => arg !== "<workflowId>" && arg !== "[workflowId]");
  return formatWorkflowCommand(options, workflowId, entry.command, args);
}

function renderHelpFromCatalog(
  workflowId: string | undefined,
  options: Pick<ControllerDeps, "formatWorkflowCommand">,
): string {
  const lines = [workflowId ? `**${workflowId} 常用命令**` : "**Workflow Engine 命令**"];
  const visibleEntries = HELP_COMMAND_CATALOG.filter((item) => {
    if (!workflowId) return true;
    return item.visibility !== "workflow-only";
  });
  for (const group of HELP_COMMAND_GROUPS) {
    lines.push("", `### ${group}`);
    for (const entry of visibleEntries.filter((item) => item.group === group)) {
      const command = renderHelpCommand(entry, workflowId, options);
      const description = workflowId ? entry.facadeDescription ?? entry.description : entry.description;
      lines.push(`- \`${command}\`：${description}`);
    }
  }
  return lines.join("\n");
}

export function handleHelp(
  workflowId?: string,
  options: Pick<ControllerDeps, "formatWorkflowCommand"> = {},
): string {
  return renderHelpFromCatalog(workflowId, options);
}

export async function handleDetail(deps: ControllerDeps, workflowId: string, source?: "pack" | "db"): Promise<string> {
  const { workflow, resolved } = await requireWorkflowLookup(deps, workflowId);
  const detailText = renderWorkflowDetail(workflow, resolved);

  // Append sync status + DB deployed spec (always show, regardless of diff status)
  try {
    const { computeSyncStatus, getSyncDiffText } = await import("./controller/version-commands.js");
    const { syncStatus, deployedVersion, action, newerSide, dbUpdatedAt, localMtime } = await computeSyncStatus(deps, workflowId);
    const localPath = resolved ? (resolved as any).absolutePath ?? "" : "";

    // Format sync line with direction hint
    const dirHint = syncStatus === "differs" && newerSide
      ? ` (${newerSide === "db_newer" ? "DB更新" : newerSide === "local_newer" ? "本地更新" : newerSide === "same_age" ? "同龄" : "未知"})`
      : "";
    const syncLine = `📦 Sync: ${syncStatus}${dirHint} (db=v${deployedVersion ?? "none"}) ${action}`;

    // Always show DB deployed spec
    const diffResult = await getSyncDiffText(deps, workflowId);
    if (diffResult) {
      const footer: string[] = [
        "",
        syncLine,
        localPath ? `📄 Local: ${localPath}` : "",
      ];

      // Show timestamps when content differs
      if (syncStatus === "differs") {
        if (dbUpdatedAt) footer.push(`🕐 DB updated:    ${dbUpdatedAt}`);
        if (localMtime) footer.push(`🕐 Local mtime:   ${localMtime}`);
        if (newerSide === "db_newer") {
          footer.push(`⚠️ DB 比本地更新 — deploy 需加 --force，或先 pull 同步`);
        } else if (newerSide === "local_newer") {
          footer.push(`ℹ️ 本地比 DB 更新 — deploy 将覆盖 DB`);
        }

        // inline diff
        footer.push(
          "",
          `--- db (current, v${diffResult.deployedVersion} git=${diffResult.deployedTag})`,
          "+++ local",
          diffResult.diffText,
        );
      }

      footer.push(
        "",
        `**DB current spec (v${diffResult.deployedVersion}):**`,
        "```json",
        JSON.stringify(JSON.parse(diffResult.dbSpecJson), null, 2),
        "```",
      );
      return `${detailText}\n${footer.filter(Boolean).join("\n")}`;
    }

    return `${detailText}\n${syncLine}`;
  } catch {
    // Sync status is best-effort — don't block detail if version deps are missing
    return detailText;
  }
}

function renderWorkflowValidateReport(
  workflowId: string,
  issues: ValidationIssue[],
  checks: {
    schemaPassed: boolean;
    semanticPassed: boolean;
    semanticChecked: boolean;
    resourcesPassed: boolean;
    resourcesChecked: boolean;
    packResourcesPassed?: boolean;
    packResourcesChecked?: boolean;
  },
  warnings: { path: string; message: string }[] = [],
): string {
  const semanticMark = checks.semanticChecked
    ? checks.semanticPassed ? "✅" : "❌"
    : "⬜";
  const resourcesMark = checks.resourcesChecked
    ? checks.resourcesPassed ? "✅" : "❌"
    : "⬜";
  const packResourceLine = checks.packResourcesChecked
    ? [`${checks.packResourcesPassed ? "✅" : "❌"} Pack 资源有效`]
    : [];
  const conclusion = issues.length === 0
    ? (warnings.length > 0 ? `结论：✅ 通过（含 ${warnings.length} 条提示，不阻断）` : "结论：✅ 通过")
    : `结论：❌ 未通过（${issues.length} 个问题）`;
  const lines = [
    `**Workflow 校验：${workflowId}**`,
    "",
    conclusion,
    "",
    "**检查项**",
    `${checks.schemaPassed ? "✅" : "❌"} YAML 结构合法`,
    `${semanticMark} 节点依赖和语义合法`,
    `${resourcesMark} Action / Skill / 资源引用合法`,
    ...packResourceLine,
    ...renderIssueList(issues),
  ];

  // 静默零容忍:warning 必须显式列出,不得只报"通过"而吞掉死配置提示。
  if (warnings.length > 0) {
    lines.push("", `**⚠️ 配置提示（${warnings.length}，不阻断但建议修正）**`);
    warnings.forEach((w, i) => {
      lines.push(`${i + 1}. ${w.path}`);
      lines.push(`   - ${w.message}`);
    });
  }

  if (issues.length === 0) {
    lines.push("", "**可继续执行**", `- /workflow detail ${workflowId}`, `- /workflow run ${workflowId} --key <value>`);
  } else {
    lines.push("", "**重新验证**", `- /workflow validate ${workflowId}`);
  }

  return lines.join("\n");
}

export async function handleValidate(
  workflowId: string,
  options: {
    actionRegistry: ActionRegistry;
    loadWorkflowById?: (workflowId: string) => WorkflowSpec | undefined;
    listWorkflowIds?: () => string[];
    resolvedWorkflows?: ResolvedWorkflow[];
    resolvedPacks?: ResolvedWorkflowPack[];
    failedWorkflows?: FailedWorkflow[];
    loadWorkflowByIdFromFile?: string;
  },
): Promise<string> {
  let workflow: WorkflowSpec | undefined;
  let resolved: ResolvedWorkflow | undefined;
  const issues: ValidationIssue[] = [];
  const warnings: { path: string; message: string }[] = [];
  let schemaPassed = true;
  let semanticPassed = false;
  let semanticChecked = false;
  let resourcesPassed = false;
  let resourcesChecked = false;
  let packResourcesPassed = false;
  let packResourcesChecked = false;
  try {
    if (options.loadWorkflowByIdFromFile) {
      const specPath = resolve(options.loadWorkflowByIdFromFile);
      if (!existsSync(specPath)) {
        throw new Error(`❌ 文件不存在: ${specPath}`);
      }
      let raw: unknown;
      try {
        raw = yaml.parse(await fs.readFile(specPath, "utf-8"));
      } catch (err) {
        throw new Error(`❌ 读取/解析 ${specPath} 失败: ${err instanceof Error ? err.message : err}`);
      }
      // 走与 validate <id> 同源的 normalize,确保 --file 与 <id> 等价诊断
      workflow = normalizeWorkflowSpec(raw) as WorkflowSpec;
    } else if (options.loadWorkflowById) {
      workflow = options.loadWorkflowById(workflowId);
    } else {
      resolved = resolveWorkflowByIdFromPacks(workflowId, options.resolvedWorkflows ?? []);
      workflow = resolved?.spec;
    }
  } catch (err) {
    if (err instanceof WorkflowValidationError) {
      schemaPassed = false;
      issues.push(issue(
        "WORKFLOW_VALIDATION_FAILED",
        "Workflow 配置校验失败",
        formatValidationIssues(err.issues),
        "根据 path 修改 workflow YAML 后重新校验。",
      ));
      // D2:失败首屏追加本次 YAML 全节点可疑清单(有文件路径才调,零级联)
      const scanPath =
        options.loadWorkflowByIdFromFile
        ?? findFailedWorkflow(options.failedWorkflows, workflowId)?.absolutePath;
      if (scanPath) {
        const scan = quickScanSpecFile(scanPath);
        if (scan.ok && scan.findings.length > 0) {
          const lines = scan.findings.map((f) =>
            `  • ${f.node}${f.phase ? `(${f.phase})` : ""} [${f.severity}]: ${f.message}`,
          );
          issues.push(issue(
            "QUICK_SCAN_HINTS",
            `本次 YAML 同时检测到的其它问题(${scan.findings.length} 处)`,
            lines.join("\n"),
            "改主错时可一并处理这些项;详见上方 path 定位。",
          ));
        }
      }
      return renderWorkflowValidateReport(workflowId, issues, {
        schemaPassed,
        semanticPassed,
        semanticChecked,
        resourcesPassed,
        resourcesChecked,
        packResourcesPassed,
        packResourcesChecked,
      });
    }
    throw err;
  }

  if (!workflow) {
    const fw = findFailedWorkflow(options.failedWorkflows, workflowId);
    if (fw) {
      throw new Error([
        `Workflow "${workflowId}" 加载失败(load_error),无法校验。`,
        `  原因: ${fw.error}`,
        `  pack: ${fw.packId}${fw.packVersion ? `@${fw.packVersion}` : ""}`,
        `  文件: ${fw.absolutePath}`,
        ``,
        `  若为 id mismatch:三者须一致——文件名 = manifest workflows[].id = YAML 内 id:,改为 "${fw.id}" 或重命名文件/改 manifest。`,
        `  若为 YAML 解析/结构错:用 \`validate --file ${fw.absolutePath}\` 跑静态分析定位。`,
      ].join("\n"));
    }
    const available = (options.listWorkflowIds ?? (() => listWorkflowIdsFromPacks(options.resolvedWorkflows ?? [])))().join(", ");
    throw new Error(`Workflow "${workflowId}" 未找到。可用 workflow：${available}`);
  }

  try {
    const { warnings: semanticWarnings } = validateWorkflowSemantics(workflow);
    for (const w of semanticWarnings) warnings.push({ path: w.path, message: w.message });
    semanticPassed = true;
  } catch (err) {
    if (err instanceof WorkflowValidationError) {
      issues.push(issue(
        "WORKFLOW_VALIDATION_FAILED",
        "Workflow 配置校验失败",
        formatValidationIssues(err.issues),
        "根据 path 修改 workflow YAML 后重新校验。",
      ));
      return renderWorkflowValidateReport(workflowId, issues, {
        schemaPassed,
        semanticPassed,
        semanticChecked: true,
        resourcesPassed,
        resourcesChecked,
        packResourcesPassed,
        packResourcesChecked,
      }, warnings);
    }
    throw err;
  } finally {
    semanticChecked = true;
  }

  if (semanticPassed) {
    try {
      validateWorkflowResources(workflow, { actionRegistry: options.actionRegistry });
      resourcesPassed = true;
    } catch (err) {
      if (err instanceof WorkflowValidationError) {
        issues.push(issue(
          "WORKFLOW_VALIDATION_FAILED",
          "Workflow 配置校验失败",
          formatValidationIssues(err.issues),
          "根据 path 修改 workflow YAML 或 action 注册后重新校验。",
        ));
      } else {
        throw err;
      }
    } finally {
      resourcesChecked = true;
    }
  }

  const pack = resolved ? options.resolvedPacks?.find((item) => item.manifest.id === resolved?.pack.id) : undefined;
  if (pack) {
    const packIssues = await validatePackResources({
      actionRegistry: options.actionRegistry,
      resolvedWorkflows: options.resolvedWorkflows,
    }, pack);
    issues.push(...packIssues);
    packResourcesChecked = true;
    packResourcesPassed = packIssues.length === 0;
  }

  return renderWorkflowValidateReport(workflowId, issues, {
    schemaPassed,
    semanticPassed,
    semanticChecked,
    resourcesPassed,
    resourcesChecked,
    packResourcesPassed,
    packResourcesChecked,
  }, warnings);
}

export async function handleConfirm(
  deps: ControllerDeps,
  note?: string,
  opts?: { flowId?: string },
): Promise<string> {
  const flow = opts?.flowId
    ? await deps.boundTaskFlow.get(opts.flowId)
    : await inferSingleCommandFlow(deps);
  if (!flow) throw new Error("当前没有活跃的流程");

  const flowId = readFlowId(flow);
  let revision = flow.revision as number;
  const state = parseFlowState(flow);

  const workflow = await loadWorkflowForState(deps, state);
  let effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

  const waitingNode = state.activeNodes.find(
    (nId) => state.nodeStates[nId]?.status === "waiting",
  );
  if (!waitingNode) throw new Error("当前没有等待确认的节点");

  const nodeState = state.nodeStates[waitingNode];
  const node = effectiveWorkflow.nodes.find((n) => n.id === waitingNode);
  const existingResult = nodeState.result ?? {};
  const confirmResult: Record<string, unknown> = {
    ...existingResult,
    confirmed: true,
    confirmNote: note ?? "",
  };
  const gateActions = resolveWaitingGateActions(node, nodeState);
  try {
    if (gateActions) {
      const confirmAction = gateActions.confirm;
      if (!confirmAction) {
        throw new Error(`当前等待节点 ${waitingNode} 未声明 confirm 动作`);
      }
      const actionInput = buildHumanActionInput(
        confirmResult,
        resolveWaitingInputSchema(node, nodeState, confirmAction.inputSchema),
        note ?? "",
      );
      Object.assign(confirmResult, actionInput);
      applyHumanActionSaveAs({
        deps,
        state,
        workflow: effectiveWorkflow,
        flowId,
        nodeId: waitingNode,
        saveAs: resolveWaitingSaveAs(node, nodeState, confirmAction?.saveAs),
        input: actionInput,
      });
    } else {
      const humanInputSchema = nodeState.waitInputSchema
        ?? (node?.executor.type === "human" ? node.executor.inputSchema : undefined);
      const humanSaveAs = nodeState.waitSaveAs
        ?? (node?.executor.type === "human" ? node.executor.saveAs : undefined);
      if (humanInputSchema) {
        const parsedHumanInput = parseHumanInput(humanInputSchema, note ?? "");
        confirmResult.humanInput = parsedHumanInput;
        Object.assign(confirmResult, parsedHumanInput);
        if (humanSaveAs && Object.keys(humanSaveAs).length > 0) {
          applySaveAs(
            state.workflowData,
            humanSaveAs,
            parsedHumanInput,
            {
              ...buildActionContext(deps, state, effectiveWorkflow, flowId, waitingNode),
              input: parsedHumanInput,
            },
          );
        }
      }
    }
  } catch (error) {
    if (isHumanInputValidationError(error)) {
      return formatHumanInputValidationError({
        error,
        waitPrompt: resolveWaitingPromptForError(node, nodeState),
      });
    }
    throw error;
  }

  // Evaluate onResult branches for human nodes (e.g. coverage-audit with branchId routing).
  // Without this, onResult.branches on human nodes are silently ignored because
  // handleConfirm bypasses handleNodeResult which is where onResult is normally evaluated.
  let matchedBranchId: string | null = null;
  if (node?.onResult) {
    const onResultAction = evaluateOnResult(node.onResult, confirmResult);
    if (onResultAction.action === "branch" && node.onResult.branches) {
      matchedBranchId = node.onResult.branches[onResultAction.matchedBranchIndex]?.branchId ?? null;
    }
    // If onResult triggers a secondary wait (e.g. then.wait), apply it.
    // This handles the legacy if/then/else mode where onResult.then.wait is defined.
    if (onResultAction.action === "wait") {
      const onResultWaitSpec = resolveOnResultHumanWait(node, {
        status: "succeeded",
        phase: nodeState.phase,
        executor: nodeState.executor,
        result: confirmResult,
      });
      // For human nodes with actions, the onResult wait is intentionally NOT applied
      // because the actions.confirm/reject already define the user interaction model.
      // Only apply onResult wait for human nodes WITHOUT gate actions.
      if (!gateActions && onResultWaitSpec) {
        state.nodeStates[waitingNode] = {
          ...nodeState,
          status: "waiting",
          result: confirmResult,
          waitKind: onResultAction.waitKind,
          waitPrompt: undefined,
          waitInputSchema: onResultAction.inputSchema,
          waitSaveAs: onResultAction.saveAs,
        };
        applyPhaseAndStatus(effectiveWorkflow, state);
        const resolvedWaitPrompt = resolveTemplate(
          onResultAction.prompt,
          buildTemplateContext(state, deps.skillRoot, confirmResult, { currentNodeId: waitingNode, userIdentity: resolveUserIdentityForContext(deps) }),
        );
        state.nodeStates[waitingNode].waitPrompt = resolvedWaitPrompt;
        appendAuditLog(state, waitingNode, "onResult-wait", resolvedWaitPrompt);
        appendFlowEvent(state, {
          type: "node_waiting",
          flowId,
          workflowId: state.workflowId,
          nodeId: waitingNode,
          data: {
            executor: nodeState.executor,
            prompt: resolvedWaitPrompt,
            waitKind: onResultAction.waitKind,
            ...summarizeRecord(confirmResult),
            resultPath: `nodeStates.${waitingNode}.result`,
          },
        });
        const resumeResult = await deps.boundTaskFlow.resume({
          flowId,
          expectedRevision: revision,
          status: "running",
          currentStep: waitingNode,
          stateJson: JSON.stringify(state),
        });
        if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");
        const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;
        // Sync flow_runs.status back to "running" after human confirmation.
        syncFlowRunPhase(flowId, state.currentPhase, "running");
        const awaitOutcome = await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);
        const title = node?.title ?? waitingNode;
        if (awaitOutcome.status === "failed") {
          return `已确认 ${title}，onResult 等待节点执行失败，详情见上方消息。`;
        }
        if (awaitOutcome.status === "waiting") {
          return `已确认 ${title}，流程已进入 onResult 等待，请按上方提示继续。`;
        }
        if (awaitOutcome.status === "finished") {
          return `已确认 ${title}，流程已完成。`;
        }
        return `已确认 ${title}，onResult 等待已设置，进度以上方消息为准。`;
      }
    }
  }

  state.nodeStates[waitingNode] = {
    ...nodeState,
    status: "succeeded",
    completedAt: now(),
    result: confirmResult,
    waitKind: undefined,
    waitPrompt: undefined,
    waitInputSchema: undefined,
    waitSaveAs: undefined,
    ...(matchedBranchId != null ? { matchedBranchId } : {}),
  };

  // Persist latest node output to flow_runs.result_json (best-effort)
  persistNodeResult(flowId, waitingNode, confirmResult);

  // ── Emit node_succeeded event to update node_executions table ──
  // Without this, the node_executions row stays at "running" forever
  // because handleConfirm bypasses executeNodeWithRetry (which normally
  // emits this event). Mirrors handleBcsCallback line ~7659.
  const confirmExecutorType = nodeState.executor ?? node?.executor.type ?? "human";
  const confirmAttempt = nodeState.attempts ?? 1;
  emitNodeEvent("node_succeeded", {
    flowId,
    workflowId: state.workflowId,
    nodeId: waitingNode,
    executorType: confirmExecutorType,
    attempt: confirmAttempt,
    durationMs: 0,
    usage: null,
    inputJson: JSON.stringify({ confirmed: confirmResult.confirmed, confirmNote: confirmResult.confirmNote ?? "" }),
    outputJson: confirmResult ? JSON.stringify(confirmResult) : null,
    sessionKey: deps.sessionKey,
    sessionId: deps.sessionId,
    embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, waitingNode, flowId, confirmExecutorType),
    systemContext: { reason: "human_confirm", matchedBranchId },
  });

  appendAuditLog(state, waitingNode, "confirmed", note ? `${note} (branch: ${matchedBranchId ?? "none"})` : `业务确认 (branch: ${matchedBranchId ?? "none"})`);
  enqueueRunLog({
    flow_id: flowId,
    node_id: waitingNode,
    level: "info",
    source: "human",
    message: `Human confirmed: node=${waitingNode}, branch=${matchedBranchId ?? "none"}`,
    timestamp: Date.now(),
  });

  applyPhaseAndStatus(effectiveWorkflow, state);

  // ── Compensate stale approval nodes ──
  // After confirming this human-wait node, other approval-type nodes may
  // still be stuck in "waiting"/"running" due to the asyncAwareExecuteLoop
  // race. Reconcile them before resuming.
  const compensatedNodes = reconcileStaleApprovalNodes(state, effectiveWorkflow, flowId, waitingNode);
  if (compensatedNodes.length > 0) {
    effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
    applyPhaseAndStatus(effectiveWorkflow, state);
  }

  const hookOutcome = await runNodeSuccessHooks({
    deps,
    workflow: effectiveWorkflow,
    state,
    flowId,
    revision,
    node: node ?? {
      id: waitingNode,
      title: waitingNode,
      phase: state.nodeStates[waitingNode].phase,
      dependsOn: [],
      executor: { type: "done" },
    },
  });

  const title = node?.title ?? waitingNode;

  if (hookOutcome.blocked) {
    return `已确认 ${title}，流程已阻塞，需手动重试`;
  }

  const confirmDisplayMessage = note
    ? `${title} 已确认 — 备注：${note}`
    : `${title} 已确认`;
  await deps.chatInject(
    confirmDisplayMessage,
    `${flowId}:${waitingNode}:confirmed`,
  );

  const resumeResult = await deps.boundTaskFlow.resume({
    flowId,
    expectedRevision: hookOutcome.revision,
    status: "running",
    currentStep: waitingNode,
    stateJson: JSON.stringify(state),
  });

  if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");

  const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;
  // Sync flow_runs.status back to "running" after human confirmation,
  // so the flow-timeout watchdog can track it again.
  syncFlowRunPhase(flowId, state.currentPhase, "running");
  const outcome = await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);

  const noteSuffix = note ? `（备注：${note}）` : "";

  if (outcome.status === "failed") {
    return `已确认 ${title}${noteSuffix}，但后续节点执行失败，详情见上方工作流消息。`;
  }
  if (outcome.status === "waiting") {
    return `已确认 ${title}${noteSuffix}，流程已进入下一处人工确认，请按上方提示继续。`;
  }
  if (outcome.status === "finished") {
    return `已确认 ${title}${noteSuffix}，流程已完成。`;
  }
  if (outcome.status === "blocked") {
    return `已确认 ${title}${noteSuffix}，但后续执行被阻塞，请查看上方提示或使用 ${formatWorkflowCommand(deps, state.workflowId, "inspect", [flowId])} 排查。`;
  }
  return `已确认 ${title}${noteSuffix}，工作流仍在推进，进度以上方消息为准。`;
}

async function readCommandFlow(
  deps: ControllerDeps,
  flowId?: string,
  options?: { globalFlowStore?: GlobalFlowStore },
): Promise<Record<string, unknown>> {
  // 1. Try session-local store first
  const flow = flowId ? await deps.boundTaskFlow.get(flowId) : await inferSingleCommandFlow(deps);
  if (flow) return flow;

  // 2. When flowId is explicit and session-local lookup failed, fall back to global store
  //    (cross-session access — e.g. inspect a flow started in another session)
  if (flowId) {
    const store = await resolveGlobalFlowStore(deps, options?.globalFlowStore);
    const globalFlow = await store.get(flowId);
    if (globalFlow) return globalFlow;
  }

  throw new Error(flowId ? `Flow ${flowId} not found` : "当前没有活跃的流程");
}

async function inferSingleCommandFlow(deps: ControllerDeps): Promise<Record<string, unknown> | null> {
  const latest = await deps.boundTaskFlow.findLatest();
  const latestState = latest ? safeParseFlowState(latest) : null;
  const latestHidden = flowIsHidden(latestState);
  if (latest && !latestHidden) return latest;

  const listed = normalizeFlowListResult(await deps.boundTaskFlow.list());
  if (latestHidden) {
    return listed.find((flow) => !flowIsHidden(safeParseFlowState(flow))) ?? null;
  }

  const candidates = listed.filter((flow) => {
    const state = safeParseFlowState(flow);
    if (flowIsHidden(state)) return false;
    const status = readFlowRecordStatus(flow);
    return ACTIVE_FLOW_STATUSES.has(status) || (state?.activeNodes?.length ?? 0) > 0;
  });

  if (candidates.length === 1) return candidates[0];
  return null;
}

async function loadWorkflowForState(deps: ControllerDeps, state: FlowState): Promise<WorkflowSpec> {
  if (state.workflowSnapshot) return state.workflowSnapshot;
  const lookup = await findWorkflowLookup(deps, state.workflowId);
  if (!lookup) {
    throw new Error(`Workflow "${state.workflowId}" pack 未安装或未被发现`);
  }
  if (lookup.resolved) {
    if (state.workflowPin?.workflowDigest && state.workflowPin.workflowDigest !== lookup.resolved.digest) {
      throw new Error(
        `Workflow "${state.workflowId}" pack digest 不一致，当前=${lookup.resolved.digest}，flow pin=${state.workflowPin.workflowDigest}`,
      );
    }
    if (!state.workflowPin) {
      const pin = buildWorkflowPin(lookup.resolved);
      if (pin) {
        state.workflowPin = pin;
        state.workflowSnapshot = structuredClone(lookup.workflow);
        appendAuditLog(state, "-", "legacy-external-hydrated", `legacy flow 已从外置 pack ${lookup.resolved.pack.id}@${lookup.resolved.pack.version} hydrate`);
      }
    }
  }
  return lookup.workflow;
}

function matchesResultConditionForWait(
  result: Record<string, unknown>,
  condition: Record<string, string | number | boolean | null> | undefined,
): boolean {
  if (!condition) return true;
  return Object.entries(condition).every(([key, expected]) => result[key] === expected);
}

function resolveOnResultHumanWait(node: WorkflowNode | undefined, nodeState: NodeState | undefined): HumanWaitSpec | undefined {
  if (!node?.onResult || !isRecord(nodeState?.result)) return undefined;
  const branch = matchesResultConditionForWait(nodeState.result, node.onResult.if)
    ? node.onResult.then
    : node.onResult.else;
  return branch?.wait;
}

function resolveWaitingGateActions(node: WorkflowNode | undefined, nodeState: NodeState | undefined): HumanGateActions | undefined {
  const onResultWait = resolveOnResultHumanWait(node, nodeState);
  if (onResultWait?.actions) return onResultWait.actions;
  return node?.executor.type === "human" ? node.executor.actions : undefined;
}

function resolveWaitingInputSchema(
  node: WorkflowNode | undefined,
  nodeState: NodeState | undefined,
  actionSchema?: HumanGateConfirmAction["inputSchema"],
): HumanGateConfirmAction["inputSchema"] {
  return actionSchema
    ?? nodeState?.waitInputSchema
    ?? resolveOnResultHumanWait(node, nodeState)?.inputSchema
    ?? (node?.executor.type === "human" ? node.executor.inputSchema : undefined);
}

function resolveWaitingSaveAs(
  node: WorkflowNode | undefined,
  nodeState: NodeState | undefined,
  actionSaveAs?: HumanGateConfirmAction["saveAs"],
): HumanGateConfirmAction["saveAs"] {
  return actionSaveAs
    ?? nodeState?.waitSaveAs
    ?? resolveOnResultHumanWait(node, nodeState)?.saveAs
    ?? (node?.executor.type === "human" ? node.executor.saveAs : undefined);
}

function resolveWaitingPromptForError(node: WorkflowNode | undefined, nodeState: NodeState | undefined): string | undefined {
  return nodeState?.waitPrompt
    ?? resolveOnResultHumanWait(node, nodeState)?.prompt
    ?? (node?.executor.type === "human" ? node.executor.prompt : undefined);
}

function formatHumanInputValidationError(params: {
  error: HumanInputValidationError;
  waitPrompt?: string;
}): string {
  const fieldText = params.error.field ? `：${params.error.field}` : "";
  const headline = params.error.code === "missing_required_field"
    ? `❌ 当前确认缺少必填字段${fieldText}`
    : `❌ 当前确认字段格式不正确${fieldText}`;
  const lines = [
    headline,
    "",
    "请按当前节点提示重新提交。",
  ];
  const prompt = params.waitPrompt?.trim();
  if (prompt) {
    lines.push("", "当前节点提示：", truncateDebugText(prompt, 1_200));
  }
  return lines.join("\n");
}

function buildHumanActionInput(base: Record<string, unknown>, schema: HumanGateConfirmAction["inputSchema"], note: string): Record<string, unknown> {
  if (!schema) return base;
  const parsed = parseHumanInput(schema, note);
  return {
    ...base,
    humanInput: parsed,
    ...parsed,
  };
}

function applyHumanActionSaveAs(params: {
  deps: ControllerDeps;
  state: FlowState;
  workflow: WorkflowSpec;
  flowId: string;
  nodeId: string;
  saveAs?: Record<string, string>;
  input: Record<string, unknown>;
}): void {
  if (!params.saveAs || Object.keys(params.saveAs).length === 0) return;
  applySaveAs(
    params.state.workflowData,
    params.saveAs,
    params.input,
    {
      ...buildActionContext(params.deps, params.state, params.workflow, params.flowId, params.nodeId),
      input: params.input,
    },
  );
}

function resolveHumanGateFeedback(params: {
  deps: ControllerDeps;
  state: FlowState;
  workflow: WorkflowSpec;
  flowId: string;
  nodeId: string;
  action: HumanGateReviseAction;
  input: Record<string, unknown>;
  fallback: string;
}): string {
  if (!params.action.feedbackTemplate) return params.fallback;
  return String(resolveTemplateValue(
    params.action.feedbackTemplate,
    {
      ...buildActionContext(params.deps, params.state, params.workflow, params.flowId, params.nodeId),
      input: params.input,
    },
    params.input,
  ));
}

function formatApprovalRecoveryCandidates(
  workflow: WorkflowSpec,
  state: FlowState,
  nodes: WorkflowNode[],
): string {
  return nodes
    .map((node) => `- ${node.id} — ${node.title} (${state.nodeStates[node.id]?.status ?? "missing"})`)
    .join("\n");
}

function findRecoverableApprovalNodes(
  workflow: WorkflowSpec,
  state: FlowState,
): WorkflowNode[] {
  return workflow.nodes.filter((node) => (
    getLegacyApprovalExecutor(node)
    && RECOVERABLE_APPROVAL_STATUSES.has(state.nodeStates[node.id]?.status ?? "")
  ));
}

function resolveApprovalRecoveryNode(
  workflow: WorkflowSpec,
  state: FlowState,
  nodeId?: string,
): { node: WorkflowNode; nodeState: NodeState; alreadySucceeded: boolean } {
  if (nodeId) {
    const node = workflow.nodes.find((item) => item.id === nodeId);
    if (!node) throw new Error(`节点 ${nodeId} 不存在`);
    if (!getLegacyApprovalExecutor(node)) throw new Error(`${nodeId} 不是审批节点`);

    const nodeState = state.nodeStates[nodeId];
    const status = nodeState?.status;
    if (status === "succeeded") {
      return {
        node,
        nodeState: nodeState ?? { status: "succeeded", phase: node.phase, executor: getLegacyExecutorType(node) },
        alreadySucceeded: true,
      };
    }
    if (status === "pending" || status === "running" || status === "postActionsRunning") {
      throw new Error(`${nodeId} 当前状态为 ${status}，审批尚不可恢复`);
    }
    if (!RECOVERABLE_APPROVAL_STATUSES.has(status ?? "")) {
      throw new Error(`${nodeId} 当前状态为 ${status ?? "missing"}，不是可恢复审批节点`);
    }
    return {
      node,
      nodeState: nodeState ?? { status: "waiting", phase: node.phase, executor: getLegacyExecutorType(node) },
      alreadySucceeded: false,
    };
  }

  const candidates = findRecoverableApprovalNodes(workflow, state);
  if (candidates.length === 0) {
    throw new Error("当前流程没有可恢复的审批节点");
  }
  if (candidates.length > 1) {
    throw new Error([
      "存在多个可恢复审批节点，请使用 --node 指定",
      formatApprovalRecoveryCandidates(workflow, state, candidates),
    ].join("\n"));
  }

  const node = candidates[0];
  return {
    node,
    nodeState: state.nodeStates[node.id],
    alreadySucceeded: false,
  };
}

function parseWorkflowDataKey(path: string, label: string): string {
  const match = /^workflowData\.([A-Za-z_$][\w$-]*)$/.exec(path);
  if (!match) {
    throw new Error(`${label} 仅支持 workflowData.<key> 路径: ${path}`);
  }
  return match[1];
}

function collectTargetAndDescendants(workflow: WorkflowSpec, targetNodeId: string): string[] {
  const selected = new Set<string>([targetNodeId]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const node of workflow.nodes) {
      if (selected.has(node.id)) continue;
      if (node.dependsOn.some((dep) => selected.has(dep))) {
        selected.add(node.id);
        changed = true;
      }
    }
  }
  return workflow.nodes.filter((node) => selected.has(node.id)).map((node) => node.id);
}

function resetNodeForRevision(
  node: WorkflowNode,
  oldState: NodeState | undefined,
  executionMode: ExecutionMode,
): NodeState {
  return {
    status: "pending",
    phase: node.phase,
    executor: oldState?.executor ?? resolveExecutorType(node, executionMode),
    retry: oldState?.retry,
  };
}

function prepareBlockedApprovalDispatchRetry(workflow: WorkflowSpec, state: FlowState): void {
  for (const nodeId of state.activeNodes) {
    const node = workflow.nodes.find((item) => item.id === nodeId);
    const nodeState = state.nodeStates[nodeId];
    if (!node || !nodeState) continue;
    if (!getLegacyApprovalExecutor(node)) continue;
    if (nodeState.status !== "blocked") continue;
    if (nodeState.bcsApproval) continue;
    if (nodeState.waitKind !== "bcs-approval-batch-dispatch-failed") continue;

    state.nodeStates[nodeId] = resetNodeForRevision(node, nodeState, state.executionMode);
  }
}

export async function handleApprove(
  deps: ControllerDeps,
  reason: string,
  nodeId?: string,
  flowId?: string,
): Promise<string> {
  const overrideReason = reason.trim();
  if (!overrideReason) throw new Error("用法: /workflow submit --node <nodeId> <原因>");

  const flow = await readCommandFlow(deps, flowId);
  const resolvedFlowId = readFlowId(flow);
  let revision = typeof flow.revision === "number" ? flow.revision : 1;
  const state = parseFlowState(flow);
  const workflow = await loadWorkflowForState(deps, state);
  const { node, nodeState, alreadySucceeded } = resolveApprovalRecoveryNode(workflow, state, nodeId);

  if (alreadySucceeded) {
    return `${node.title} 已完成，跳过重复人工覆盖`;
  }

  const result = {
    ...decorateApprovalCallbackResult({
      workflow,
      state,
      node,
      fromBot: "manual-override",
      approved: true,
      reviewTime: isoNow(),
      note: `人工覆盖通过：${overrideReason}`,
    }),
    manualOverride: true,
    overrideReason,
    overrideSource: "workflow-command",
  };

  const contractOutcome = await failNodeOutputContract({
    deps,
    state,
    flowId: resolvedFlowId,
    revision,
    node,
    result,
    executorType: nodeState.executor ?? resolveExecutorType(node, state.executionMode),
  });
  if (contractOutcome) return `${node.id} 输出不符合契约，流程已失败`;

  state.nodeStates[node.id] = {
    ...nodeState,
    status: "succeeded",
    completedAt: now(),
    result,
    error: null,
    waitKind: undefined,
    waitPrompt: undefined,
    waitInputSchema: undefined,
    waitSaveAs: undefined,
    bcsApproval: undefined,
  };
  appendAuditLog(state, node.id, "manual-approve", `人工覆盖通过：${overrideReason}`);
  enqueueRunLog({
    flow_id: resolvedFlowId,
    node_id: node.id,
    level: "info",
    source: "human",
    message: `Manual approve: node=${node.id}, reason=${overrideReason}`,
    timestamp: Date.now(),
  });
  appendFlowEvent(state, {
    type: "collaboration_result_received",
    flowId: resolvedFlowId,
    workflowId: state.workflowId,
    nodeId: node.id,
    data: summarizeBcsApprovalResult(result),
  });

  const hookOutcome = await runNodeSuccessHooks({
    deps,
    workflow,
    state,
    flowId: resolvedFlowId,
    revision,
    node,
  });
  if (hookOutcome.blocked) return `已人工覆盖通过 ${node.title}，后置动作失败，流程已阻塞`;
  revision = hookOutcome.revision;

  applyPhaseAndStatus(workflow, state);
  const resumeResult = await deps.boundTaskFlow.resume({
    flowId: resolvedFlowId,
    expectedRevision: revision,
    status: "running",
    currentStep: node.id,
    stateJson: JSON.stringify(state),
  });

  if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");

  const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;
  await deps.chatInject(
    `已人工覆盖通过 ${node.title}`,
    `${resolvedFlowId}:${node.id}:manual-approve`,
  );
  // Sync flow_runs.status back to "running" after resuming from waiting.
  syncFlowRunPhase(resolvedFlowId, state.currentPhase, "running");
  await asyncAwareExecuteLoop(deps, workflow, state, resolvedFlowId, newRevision);

  return `已人工覆盖通过 ${node.title}，流程继续推进`;
}

export async function handleRevise(
  deps: ControllerDeps,
  note: string,
  options: { nodeId?: string; flowId?: string } = {},
): Promise<string> {
  const revisionNote = note.trim();
  if (!revisionNote) throw new Error("用法: /workflow revise <补充信息>");

  const flow = await readCommandFlow(deps, options.flowId);

  const flowId = readFlowId(flow);
  const revision = flow.revision as number;
  const state = parseFlowState(flow);

  const workflow = await loadWorkflowForState(deps, state);
  const effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

  const waitingNode = state.activeNodes.find(
    (nId) => state.nodeStates[nId]?.status === "waiting",
  );

  const nodeState = waitingNode ? state.nodeStates[waitingNode] : undefined;
  const waitingWorkflowNode = waitingNode ? effectiveWorkflow.nodes.find((item) => item.id === waitingNode) : undefined;
  const reviseAction = resolveWaitingGateActions(waitingWorkflowNode, nodeState)?.revise;
  if (waitingNode && nodeState && reviseAction) {
    const targetNode = effectiveWorkflow.nodes.find((item) => item.id === reviseAction.target);
    if (!targetNode) throw new Error(`${waitingNode} 回流目标 ${reviseAction.target} 不存在`);

    const actionInput = buildHumanActionInput(
      { revisionNote },
      resolveWaitingInputSchema(waitingWorkflowNode, nodeState, reviseAction.inputSchema),
      revisionNote,
    );
    const feedback = resolveHumanGateFeedback({
      deps,
      state,
      workflow: effectiveWorkflow,
      flowId,
      nodeId: waitingNode,
      action: reviseAction,
      input: actionInput,
      fallback: revisionNote,
    });
    const feedbackKey = parseWorkflowDataKey(reviseAction.feedbackPath, "feedbackPath");
    if ((reviseAction.feedbackMode ?? "replace") === "append-line") {
      const existingFeedback = typeof state.workflowData[feedbackKey] === "string"
        ? state.workflowData[feedbackKey].trim()
        : "";
      state.workflowData[feedbackKey] = existingFeedback ? `${existingFeedback}\n${feedback}` : feedback;
    } else {
      state.workflowData[feedbackKey] = feedback;
    }

    if (reviseAction.historyPath) {
      const historyKey = parseWorkflowDataKey(reviseAction.historyPath, "historyPath");
      const history = Array.isArray(state.workflowData[historyKey])
        ? state.workflowData[historyKey]
        : [];
      state.workflowData[historyKey] = [
        ...history,
        {
          time: isoNow(),
          note: revisionNote,
          feedback,
          input: actionInput,
          waitingNodeId: waitingNode,
          targetNodeId: targetNode.id,
          previousResult: state.nodeStates[targetNode.id]?.result ?? null,
        },
      ];
    }

    // Apply revise.saveAs — persist key-value pairs before resetting
    if (reviseAction.saveAs && Object.keys(reviseAction.saveAs).length > 0) {
      applyHumanActionSaveAs({
        deps,
        state,
        workflow: effectiveWorkflow,
        flowId,
        nodeId: waitingNode,
        saveAs: reviseAction.saveAs,
        input: actionInput,
      });
    }

    const resetNodeIds = reviseAction.reset === "target-and-descendants"
      ? collectTargetAndDescendants(effectiveWorkflow, targetNode.id)
      : [targetNode.id];
    for (const nodeIdToReset of resetNodeIds) {
      const nodeToReset = effectiveWorkflow.nodes.find((item) => item.id === nodeIdToReset);
      if (!nodeToReset) continue;
      state.nodeStates[nodeIdToReset] = resetNodeForRevision(
        nodeToReset,
        state.nodeStates[nodeIdToReset],
        state.executionMode,
      );
      // Clear loop-group runtime state for any loop-group nodes in the reset set
      if (isLoopGroupNode(nodeToReset)) {
        clearLoopRuntimeStateForManualRetry(state, nodeToReset.id);
      }
    }

    appendAuditLog(state, waitingNode, "revised", feedback);
    enqueueRunLog({
      flow_id: flowId,
      node_id: waitingNode,
      level: "info",
      source: "human",
      message: `Human revised: node=${waitingNode}, target=${targetNode.id}`,
      timestamp: Date.now(),
    });
    applyPhaseAndStatus(effectiveWorkflow, state);
    state.activeNodes = [targetNode.id];
    state.currentPhase = targetNode.phase;
    state.businessStatus = targetNode.businessStatus ?? state.businessStatus;

    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: "running",
      currentStep: targetNode.id,
      stateJson: JSON.stringify(state),
    });

    if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");

    const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;
    await deps.chatInject(
      `已收到修改意见，回流至 ${targetNode.title}`,
      `${flowId}:${waitingNode}:revised`,
    );
    // Sync flow_runs.status back to "running" after resuming from waiting.
    syncFlowRunPhase(flowId, state.currentPhase, "running");
    await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);

    return `已收到修改意见，回流至 ${targetNode.title}`;
  }

  const { node: approvalNode, alreadySucceeded } = resolveApprovalRecoveryNode(workflow, state, options.nodeId);
  if (alreadySucceeded) {
    return `${approvalNode.title} 已完成，跳过重复修改`;
  }

  const onRevise = getLegacyApprovalExecutor(approvalNode)?.onRevise;
  const targetNodeId = onRevise?.target
    ?? (approvalNode.dependsOn.length === 1 ? approvalNode.dependsOn[0] : undefined);
  if (!targetNodeId) {
    throw new Error(`${approvalNode.id} 无法自动判断回流目标，请在 workflow YAML 中配置 executor.onRevise.target`);
  }

  const targetNode = workflow.nodes.find((item) => item.id === targetNodeId);
  if (!targetNode) throw new Error(`${approvalNode.id} 回流目标 ${targetNodeId} 不存在`);

  const feedbackKey = parseWorkflowDataKey(
    onRevise?.feedbackPath ?? "workflowData.revisionFeedback",
    "feedbackPath",
  );
  const historyKey = parseWorkflowDataKey(
    onRevise?.historyPath ?? "workflowData.revisionHistory",
    "historyPath",
  );

  const existingFeedback = typeof state.workflowData[feedbackKey] === "string"
    ? state.workflowData[feedbackKey].trim()
    : "";
  state.workflowData[feedbackKey] = existingFeedback
    ? `${existingFeedback}\n${revisionNote}`
    : revisionNote;

  const history = Array.isArray(state.workflowData[historyKey])
    ? state.workflowData[historyKey]
    : [];
  state.workflowData[historyKey] = [
    ...history,
    {
      time: isoNow(),
      note: revisionNote,
      approvalNodeId: approvalNode.id,
      approvalNodeTitle: approvalNode.title,
      targetNodeId,
      previousResult: state.nodeStates[targetNodeId]?.result ?? null,
    },
  ];

  for (const nodeIdToReset of collectTargetAndDescendants(workflow, targetNodeId)) {
    const node = workflow.nodes.find((item) => item.id === nodeIdToReset);
    if (!node) continue;
    state.nodeStates[nodeIdToReset] = resetNodeForRevision(
      node,
      state.nodeStates[nodeIdToReset],
      state.executionMode,
    );
    // Clear loop-group runtime state for any loop-group nodes in the reset set
    if (isLoopGroupNode(node)) {
      clearLoopRuntimeStateForManualRetry(state, node.id);
    }
  }
  appendAuditLog(state, approvalNode.id, "approval-revised", revisionNote);
  applyPhaseAndStatus(workflow, state);
  state.activeNodes = [targetNodeId];
  state.currentPhase = targetNode.phase;
  state.businessStatus = targetNode.businessStatus ?? state.businessStatus;

  const resumeResult = await deps.boundTaskFlow.resume({
    flowId,
    expectedRevision: revision,
    status: "running",
    currentStep: targetNodeId,
    stateJson: JSON.stringify(state),
  });

  if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");

  const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;
  await deps.chatInject(
    `已收到修改意见，回流至 ${targetNode.title}`,
    `${flowId}:${approvalNode.id}:revised`,
  );
  // Sync flow_runs.status back to "running" after resuming from waiting.
  syncFlowRunPhase(flowId, state.currentPhase, "running");
  await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);

  return `已收到修改意见，回流至 ${targetNode.title}`;
}

export async function handleReject(
  deps: ControllerDeps,
  note?: string,
  opts?: { flowId?: string },
): Promise<string> {
  const flow = opts?.flowId
    ? await deps.boundTaskFlow.get(opts.flowId)
    : await inferSingleCommandFlow(deps);
  if (!flow) throw new Error("当前没有活跃的流程");

  const flowId = readFlowId(flow);
  let revision = flow.revision as number;
  const state = parseFlowState(flow);
  const workflow = await loadWorkflowForState(deps, state);
  const effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

  const waitingNode = state.activeNodes.find(
    (nId) => state.nodeStates[nId]?.status === "waiting",
  );
  if (!waitingNode) throw new Error("当前没有等待确认的节点");

  const nodeState = state.nodeStates[waitingNode];
  const node = effectiveWorkflow.nodes.find((item) => item.id === waitingNode);
  const gateActions = resolveWaitingGateActions(node, nodeState);
  const rejectAction = gateActions?.reject;
  if (gateActions && !rejectAction) {
    throw new Error(`当前等待节点 ${waitingNode} 未声明 reject 动作`);
  }
  const rejectResult = rejectAction
    ? buildHumanActionInput(
        {
          ...(nodeState.result ?? {}),
          rejected: true,
          rejectNote: note ?? "",
        },
        resolveWaitingInputSchema(node, nodeState, rejectAction.inputSchema),
        note ?? "",
      )
    : undefined;
  if (rejectAction) {
    applyHumanActionSaveAs({
      deps,
      state,
      workflow: effectiveWorkflow,
      flowId,
      nodeId: waitingNode,
      saveAs: rejectAction.saveAs,
      input: rejectResult ?? {},
    });
  }

  if (rejectAction?.next === "block-flow") {
    state.nodeStates[waitingNode] = {
      ...nodeState,
      status: "blocked",
      completedAt: now(),
      result: rejectResult,
      error: note ?? "业务驳回",
    };
    finalizeLoopAfterRuntimeNodeBlocked(workflow, state, flowId, waitingNode);
    appendAuditLog(state, waitingNode, "rejected-blocked", note ?? "业务驳回");
    const summary = `${waitingNode} 被驳回: ${note ?? ""}`;
    const waitState: WaitState = {
      kind: "platform-workflow",
      workflowId: state.workflowId,
      params: state.params,
      activeNodes: [waitingNode],
      waitingFor: "human-reject",
      hint: summary,
      userAction: formatWorkflowCommand(deps, state.workflowId, "inspect", [flowId]),
    };
    await blockFlow(deps, flowId, revision, state, waitState, summary, waitingNode);
    await deps.chatInject(
      `流程已阻塞: ${waitingNode} 被驳回 — ${note ?? ""}`,
      `${flowId}:${waitingNode}:rejected`,
    );
    return `已驳回 ${waitingNode}，流程已阻塞`;
  }

  state.nodeStates[waitingNode] = {
    ...nodeState,
    status: "rejected",
    completedAt: now(),
    ...(rejectResult ? { result: rejectResult } : {}),
    error: note ?? "业务驳回",
  };

  // ── Emit node_rejected event to update node_executions table ──
  // 业务驳回不是系统错误:用 node_rejected(节点记 "rejected")而非 node_failed,
  // 这样既不会触发节点级失败告警/钉钉通知,也不会计入 failed_count。
  const rejectExecutorType = nodeState.executor ?? node?.executor.type ?? "human";
  const rejectAttempt = nodeState.attempts ?? 1;
  emitNodeEvent("node_rejected", {
    flowId,
    workflowId: state.workflowId,
    nodeId: waitingNode,
    executorType: rejectExecutorType,
    attempt: rejectAttempt,
    durationMs: 0,
    error: note ?? "业务驳回",
    inputJson: rejectResult ? JSON.stringify({ rejected: true, rejectNote: note ?? "" }) : null,
    outputJson: rejectResult ? JSON.stringify(rejectResult) : null,
    sessionKey: deps.sessionKey,
    sessionId: deps.sessionId,
    embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, waitingNode, flowId, rejectExecutorType),
    systemContext: { reason: "human_reject" },
  });

  appendAuditLog(state, waitingNode, "rejected", note ?? "业务驳回");
  enqueueRunLog({
    flow_id: flowId,
    node_id: waitingNode,
    level: "warn",
    source: "human",
    message: `Human rejected: node=${waitingNode}, note=${(note ?? "").slice(0, 100)}`,
    timestamp: Date.now(),
  });

  // 业务驳回 → 流程"取消"(cancelled),而非"失败"(failed)。
  // 走 finish() 落 DB 为 completed,completeFlowRun 记 cancelled,
  // 失败告警(HTTP 回调里的 failed 分支、钉钉失败通知)因此不会触发。
  // BUG-25 fix: Wrap boundTaskFlow.finish() to ensure completeFlowRun and releaseAllForFlow
  // are always called, even if the API call fails.
  try {
    await deps.boundTaskFlow.finish({
      flowId,
      expectedRevision: revision,
      stateJson: JSON.stringify(state),
      endedAt: now(),
    });
  } catch (finishErr) {
    const errMsg = finishErr instanceof Error ? finishErr.message : String(finishErr);
    console.error(`[controller] handleReject: boundTaskFlow.finish() threw for flowId=${flowId}:`, finishErr);
    enqueueRunLog({
      flow_id: flowId,
      level: "error",
      source: "engine",
      message: `boundTaskFlow.finish() failed in handleReject: ${errMsg}`,
      timestamp: Date.now(),
    });
  }

  console.log(`[controller] FLOW_CANCELLED flowId=${flowId} reason=handle_rejected node=${waitingNode} note=${(note ?? "").slice(0, 100)}`);
  completeFlowRun(flowId, "cancelled", state.currentPhase, `${waitingNode} 被驳回: ${note ?? ""}`, computeDurationMs(state), state);
  console.log(`[controller] handleReject: releasing flow control slots for flow ${flowId} (rejected node: ${waitingNode})`);
  deps.flowControl?.releaseAllForFlow(flowId);

  await deps.chatInject(
    `流程已取消: ${waitingNode} 被驳回 — ${note ?? ""}`,
    `${flowId}:${waitingNode}:rejected`,
  );

  return `已驳回 ${waitingNode}，流程已取消`;
}

export async function handleResume(
  deps: ControllerDeps,
  flowId: string,
  revision: number,
): Promise<string> {
  const flow = await deps.boundTaskFlow.get(flowId);
  if (!flow) throw new Error(`Flow ${flowId} not found`);

  const currentRevision = flow.revision as number;
  if (currentRevision !== revision) {
    throw new Error(`revision 不匹配：期望 ${revision}，当前 ${currentRevision}。请用 /workflow inspect 查看最新 revision`);
  }

  const state = parseFlowState(flow);
  const status = flow.status as string;
  if (status !== "waiting" && status !== "blocked" && status !== "lost") {
    throw new Error(`当前 flow 状态为 ${status}，只有 waiting/blocked/lost 状态才能 resume`);
  }

  const workflow = await loadWorkflowForState(deps, state);

  prepareBlockedApprovalDispatchRetry(workflow, state);
  appendAuditLog(state, "-", "manual-resume", `手动恢复 flow (revision=${revision})`);
  enqueueRunLog({
    flow_id: flowId,
    level: "info",
    source: "workflow",
    message: `Flow manually resumed (revision=${revision})`,
    timestamp: Date.now(),
  });

  const resumeResult = await deps.boundTaskFlow.resume({
    flowId,
    expectedRevision: revision,
    status: "running",
    currentStep: state.activeNodes[0] ?? "resume",
    stateJson: JSON.stringify(state),
  });

  if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");

  const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;

  // Sync flow_runs status to "running" so that HTTP callbacks and DB queries
  // see the correct status after a manual resume. Without this, flow_runs.status
  // remains the previous terminal status (failed/blocked), causing stale state.
  syncFlowRunPhase(flowId, state.currentPhase, "running");

  await deps.chatInject(`流程已手动恢复，继续执行`, `${flowId}:flow:resumed`);
  await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);

  return `Flow ${flowId} 已恢复`;
}

/**
 * Resume a workflow that was queued by flow control.
 * Tries to acquire flow control slots; if successful, resumes the flow
 * and re-enters executeLoop. If still queued, leaves the flow waiting.
 *
 * Called by the flow control dispatcher's onWorkflowResume callback.
 */
export async function resumeQueuedWorkflow(
  deps: ControllerDeps,
  flowId: string,
): Promise<boolean> {
  const flow = await deps.boundTaskFlow.get(flowId);
  if (!flow) {
    // Flow not found — it was deleted or the session is gone.
    // This is a terminal state: reenqueuing will never help because the flow
    // data no longer exists. Release slots and return true (handled/dropped)
    // so the dispatcher does NOT reenqueue this zombie entry.
    console.log(`[controller] resumeQueuedWorkflow: flow ${flowId} not found — dropping zombie queue entry`);
    deps.flowControl?.releaseAllForFlow(flowId);
    return true;
  }

  const state = parseFlowState(flow);
  const flowStatus = flow.status as string;
  // OpenClaw may represent a queued flow as "blocked" (when the flow is paused
  // waiting for a flow-control slot) or "waiting". Both are resumable by the
  // dispatcher — the flow was enqueued because it hit a concurrency limit.
  // Other statuses (running, failed, succeeded, etc.) mean the flow has moved
  // on and the queue entry is stale — drop it.
  if (flowStatus !== "waiting" && flowStatus !== "blocked") {
    // Flow is no longer waiting/blocked — already resumed or finished.
    // This is a terminal state: no point reenqueuing. Release slots and drop.
    console.log(`[controller] resumeQueuedWorkflow: flow ${flowId} status=${flowStatus}, not waiting/blocked — dropping`);
    deps.flowControl?.releaseAllForFlow(flowId);
    return true;
  }
  // Verify this flow is actually queued by flow control (not blocked for
  // some other reason like a hook or approval). The waitingFor field is
  // in the flow record's waitJson, not on FlowState itself.
  const waitJson = parseWaitJson(flow);
  const waitingFor = waitJson?.waitingFor as string | undefined;
  if (waitingFor !== "flow-control-queue") {
    console.log(`[controller] resumeQueuedWorkflow: flow ${flowId} waitingFor=${waitingFor}, not flow-control-queue — dropping`);
    deps.flowControl?.releaseAllForFlow(flowId);
    return true;
  }

  const workflowId = state.workflowId;
  const { workflow } = await requireWorkflowLookup(deps, workflowId);

  // NOTE: In the simplified flow control model (perWorkflow only), the
  // dispatcher's tryDispatch already acquired the "workflow:xxx" slot before
  // calling onWorkflowResume. There's no second scope to acquire here.
  // If dispatch fails and the flow is re-enqueued, the dispatcher will
  // re-acquire the workflow slot on the next tick.

  // Resume the flow
  const revision = (flow as Record<string, unknown>).revision as number;
  const resumeResult = await deps.boundTaskFlow.resume({
    flowId,
    expectedRevision: revision,
    status: "running",
    currentStep: state.activeNodes[0] ?? "resume",
    stateJson: JSON.stringify(state),
  });

  if (!resumeResult.applied) {
    // Revision conflict — release slots we just acquired
    console.log(`[controller] resumeQueuedWorkflow: flow ${flowId} resume failed (revision conflict), revision=${revision}`);
    deps.flowControl?.releaseAllForFlow(flowId);
    return false;
  }

  appendFlowEvent(state, {
    type: "flow_control_resumed",
    flowId,
    workflowId,
    data: { reason: "dispatcher-slot-available" },
  });

  const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;

  // Update flow run status back to running
  appendAuditLog(state, "-", "flow-control-resumed", `蓄流恢复执行 (revision=${newRevision})`);
  enqueueRunLog({
    flow_id: flowId,
    level: "info",
    source: "workflow",
    message: `Flow resumed from queue (flow-control, revision=${newRevision})`,
    timestamp: Date.now(),
  });

  // Re-enter execution loop
  try {
    await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);
  } catch (err) {
    // Safety net: release slots on unhandled exception from executeLoop
    console.error(`[controller] resumeQueuedWorkflow: executeLoop threw, releasing flow control slots for flow ${flowId}:`, err);
    deps.flowControl?.releaseAllForFlow(flowId);
    throw err;
  }

  return true;
}

/**
 * Resume a single queued node that was blocked by executor-level flow control.
 * DEPRECATED: In the simplified flow control model (perWorkflow only), there
 * is no executor-level flow control. Node-level queueing is no longer used.
 * This function is kept as a no-op stub that drops stale queue entries.
 */
export async function resumeQueuedNode(
  _deps: ControllerDeps,
  flowId: string,
  nodeId: string,
): Promise<boolean> {
  // Executor-level flow control has been removed. Any remaining queue entries
  // for node-level scopes are stale — drop them.
  console.warn(`[controller] resumeQueuedNode: DEPRECATED — no executor-level flow control. Dropping stale queue entry for flow ${flowId}/${nodeId}`);
  return true;
}

function isRetryableLoopRuntimeState(state: FlowState, loopId: string): boolean {
  const loopStatus = state.loopGroups?.[loopId]?.status;
  return loopStatus === "waiting" || loopStatus === "blocked" || loopStatus === "failed";
}

function findRetryTargetNodeId(workflow: WorkflowSpec, state: FlowState, requestedNodeId?: string): string {
  if (requestedNodeId) {
    const nodeState = state.nodeStates[requestedNodeId];
    if (!nodeState) throw new Error(`节点 ${requestedNodeId} 不存在`);
    if (NON_RETRYABLE_ACTIVE_NODE_STATUSES.has(nodeState.status)) {
      const requestedNode = workflow.nodes.find((node) => node.id === requestedNodeId);
      if (!requestedNode || !isLoopGroupNode(requestedNode) || !isRetryableLoopRuntimeState(state, requestedNodeId)) {
        throw new Error(`${requestedNodeId} 当前状态为 ${nodeState.status}，节点正在执行，请先 stop/abort 或等待结束后再重试`);
      }
    }
    return requestedNodeId;
  }

  const found = Object.entries(state.nodeStates)
    .map(([nodeId, nodeState], index) => ({ nodeId, nodeState, index }))
    .filter(({ nodeState }) => RETRYABLE_NODE_STATUSES.has(nodeState.status))
    .sort((left, right) => {
      const timeDiff = retryTargetTime(right.nodeState) - retryTargetTime(left.nodeState);
      return timeDiff !== 0 ? timeDiff : right.index - left.index;
    })[0];
  if (!found) throw new Error("当前流程没有 failed/blocked/waiting 节点可重试");
  return found.nodeId;
}

function promoteLoopRuntimeRetryTarget(state: FlowState, nodeId: string): string {
  return state.runtimeNodeMeta?.[nodeId]?.loopId ?? nodeId;
}

function retryTargetTime(nodeState: NodeState): number {
  const record = nodeState as NodeState & Record<string, unknown>;
  for (const key of ["completedAt", "updatedAt", "endedAt", "failedAt", "blockedAt", "startedAt"]) {
    const value = record[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
    if (typeof value === "string") {
      const parsed = Date.parse(value);
      if (Number.isFinite(parsed)) return parsed;
    }
  }
  return 0;
}

function resetNodeForManualRetry(
  workflow: WorkflowSpec,
  state: FlowState,
  nodeId: string,
  isTarget: boolean,
): void {
  const node = workflow.nodes.find((item) => item.id === nodeId);
  const oldState = state.nodeStates[nodeId];
  if (!node || !oldState) return;
  state.nodeStates[nodeId] = {
    status: "pending",
    phase: node.phase,
    executor: oldState.executor ?? resolveExecutorType(node, state.executionMode),
    retry: oldState.retry,
    attempts: 0,
    manualRetries: isTarget ? (oldState.manualRetries ?? 0) + 1 : oldState.manualRetries,
    progressMessageIds: oldState.progressMessageIds,
  };
}

function clearLoopRuntimeStateForManualRetry(state: FlowState, loopId: string): void {
  const runtimeNodeIds = new Set<string>();
  for (const [runtimeNodeId, meta] of Object.entries(state.runtimeNodeMeta ?? {})) {
    if (meta.loopId === loopId) runtimeNodeIds.add(runtimeNodeId);
  }
  for (const iteration of Object.values(state.loopGroups?.[loopId]?.iterations ?? {})) {
    for (const runtimeNodeId of Object.values(iteration.nodeIds)) {
      runtimeNodeIds.add(runtimeNodeId);
    }
  }

  for (const runtimeNodeId of runtimeNodeIds) {
    delete state.nodeStates[runtimeNodeId];
    if (state.runtimeNodeMeta) delete state.runtimeNodeMeta[runtimeNodeId];
  }
  if (state.loopGroups) delete state.loopGroups[loopId];
  state.activeNodes = state.activeNodes.filter((nodeId) => !runtimeNodeIds.has(nodeId));
}

function parseSkipResultJson(value: string | undefined): Record<string, unknown> {
  if (!value) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(value);
  } catch (err) {
    throw new Error(`--result-json 不是合法 JSON: ${err instanceof Error ? err.message : String(err)}`);
  }
  if (parsed == null || typeof parsed !== "object" || Array.isArray(parsed)) {
    throw new Error("--result-json 必须是 JSON object");
  }
  return parsed as Record<string, unknown>;
}

function buildSkipResult(params: {
  deps: ControllerDeps;
  workflow: WorkflowSpec;
  node: WorkflowNode;
  nodeState: NodeState;
  reason: string;
  resultJson?: string;
}): Record<string, unknown> {
  const extra = parseSkipResultJson(params.resultJson);
  const skippedBy = {
    id: params.deps.user?.id ?? params.workflow.defaults?.user?.id ?? "unknown",
    name: params.deps.user?.name ?? params.workflow.defaults?.user?.name ?? "unknown",
  };
  const approvalResult = getLegacyApprovalExecutor(params.node)
    ? { approved: true, note: `人工跳过：${params.reason}` }
    : {};
  const skippedFields = {
    skipped: true,
    skipReason: params.reason,
    skippedBy,
    skippedAt: isoNow(),
    originalStatus: params.nodeState.status,
  };
  return {
    ...extra,
    ...approvalResult,
    ...skippedFields,
  };
}

function assertSkippableNodeState(nodeId: string, nodeState: NodeState | undefined): NodeState {
  if (!nodeState) throw new Error(`节点 ${nodeId} 不存在`);
  if (nodeState.status === "running" || nodeState.status === "postActionsRunning") {
    throw new Error(`${nodeId} 当前状态为 ${nodeState.status}，节点正在执行，请先 stop/abort 后再跳过`);
  }
  if (!SKIPPABLE_COMMAND_NODE_STATUSES.has(nodeState.status)) {
    throw new Error(`${nodeId} 当前状态为 ${nodeState.status}，只能跳过 waiting/failed/blocked 节点`);
  }
  return nodeState;
}

function parseSubmitResult(params: { resultJson?: string; text?: string }): Record<string, unknown> {
  if (params.resultJson !== undefined) {
    try {
      const parsed = JSON.parse(params.resultJson) as unknown;
      if (!isRecord(parsed)) {
        throw new Error("result-json 必须是 JSON object");
      }
      return parsed;
    } catch (err) {
      if (err instanceof Error && err.message === "result-json 必须是 JSON object") throw err;
      throw new Error(`result-json 解析失败: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  const summary = params.text?.trim();
  if (summary) return { summary };
  throw new Error("用法: /workflow submit --node <nodeId> [--flowId <flowId>] [--result-json '<json>'] [文本结果]");
}

export async function handleSubmit(
  deps: ControllerDeps,
  params: { nodeId: string; flowId?: string; resultJson?: string; text?: string },
): Promise<string> {
  const flow = await readCommandFlow(deps, params.flowId);
  const flowId = readFlowId(flow);
  let revision = typeof flow.revision === "number" ? flow.revision : 1;
  const state = parseFlowState(flow);
  const workflow = await loadWorkflowForState(deps, state);
  let effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
  const targetNode = effectiveWorkflow.nodes.find((node) => node.id === params.nodeId);
  if (!targetNode) throw new Error(`节点 ${params.nodeId} 不存在`);
  if (targetNode.executor.type !== "collaboration") {
    throw new Error(`${params.nodeId} 不是协作节点`);
  }

  const nodeState = state.nodeStates[params.nodeId];
  if (!nodeState) throw new Error(`节点 ${params.nodeId} 不存在`);
  if (!RECOVERABLE_APPROVAL_STATUSES.has(nodeState.status)) {
    throw new Error(`${params.nodeId} 当前状态为 ${nodeState.status}，只能提交 waiting/blocked/failed 协作节点`);
  }

  const result = parseSubmitResult(params);
  const contractOutcome = await failNodeOutputContract({
    deps,
    state,
    flowId,
    revision,
    node: targetNode,
    result,
    executorType: nodeState.executor ?? resolveExecutorType(targetNode, state.executionMode),
  });
  if (contractOutcome) return `${targetNode.id} 输出不符合契约，流程已失败`;

  state.nodeStates[targetNode.id] = {
    ...nodeState,
    status: "succeeded",
    completedAt: now(),
    result,
    error: null,
    waitKind: undefined,
    waitPrompt: undefined,
    waitInputSchema: undefined,
    waitSaveAs: undefined,
    bcsApproval: undefined,
  };

  // ── Emit node_succeeded event to update node_executions table ──
  const submitExecutorType = nodeState.executor ?? targetNode.executor.type ?? "collaboration";
  const submitAttempt = nodeState.attempts ?? 1;
  emitNodeEvent("node_succeeded", {
    flowId,
    workflowId: state.workflowId,
    nodeId: targetNode.id,
    executorType: submitExecutorType,
    attempt: submitAttempt,
    durationMs: 0,
    usage: null,
    inputJson: result ? JSON.stringify(result) : null,
    outputJson: result ? JSON.stringify(result) : null,
    sessionKey: deps.sessionKey,
    sessionId: deps.sessionId,
    embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, targetNode.id, flowId, submitExecutorType),
    systemContext: { reason: "collaboration_submit" },
  });

  appendAuditLog(state, targetNode.id, "collaboration-submitted", `${targetNode.title} 协作结果已提交`);
  appendFlowEvent(state, {
    type: "collaboration_result_received",
    flowId,
    workflowId: state.workflowId,
    nodeId: targetNode.id,
    data: summarizeBcsApprovalResult(result),
  });

  const title = `${targetNode.title}(${targetNode.id})`;
  const hookOutcome = await runNodeSuccessHooks({
    deps,
    workflow: effectiveWorkflow,
    state,
    flowId,
    revision,
    node: targetNode,
  });
  if (hookOutcome.blocked) return `${title} 已提交，但后置动作失败，流程已阻塞`;
  revision = hookOutcome.revision;

  effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
  applyPhaseAndStatus(effectiveWorkflow, state);
  const resumeResult = await deps.boundTaskFlow.resume({
    flowId,
    expectedRevision: revision,
    status: "running",
    currentStep: targetNode.id,
    stateJson: JSON.stringify(state),
  });
  if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");

  revision = (resumeResult.flow as Record<string, unknown>).revision as number;
  await deps.chatInject(
    `${title} 已提交`,
    `${flowId}:${targetNode.id}:collaboration-submitted`,
  );

  const outcome = await asyncAwareExecuteLoop(deps, workflow, state, flowId, revision);
  if (outcome.status === "failed") {
    return `${title} 已提交，但后续节点执行失败，详情见上方工作流消息。`;
  }
  if (outcome.status === "waiting") {
    return `${title} 已提交，流程已进入下一处等待，请按上方提示继续。`;
  }
  if (outcome.status === "finished") {
    return `${title} 已提交，流程已完成。`;
  }
  if (outcome.status === "blocked") {
    return `${title} 已提交，但后续执行被阻塞，请查看上方提示。`;
  }
  return `${title} 已提交，工作流继续推进。`;
}

export async function handleSkip(
  deps: ControllerDeps,
  params: { nodeId: string; reason: string; flowId?: string; resultJson?: string; runHooks: boolean },
): Promise<string> {
  const reason = params.reason.trim();
  if (!reason) throw new Error("用法: /workflow skip --node <nodeId> <reason> [--flowId <flowId>] [--result-json '<json>'] [--no-hooks]");

  const flow = await readCommandFlow(deps, params.flowId);
  const flowId = readFlowId(flow);
  let revision = typeof flow.revision === "number" ? flow.revision : 1;
  const state = parseFlowState(flow);
  const workflow = await loadWorkflowForState(deps, state);
  let effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
  const targetNode = effectiveWorkflow.nodes.find((node) => node.id === params.nodeId);
  if (!targetNode) throw new Error(`节点 ${params.nodeId} 不存在`);

  const nodeState = assertSkippableNodeState(params.nodeId, state.nodeStates[params.nodeId]);
  const result = buildSkipResult({
    deps,
    workflow,
    node: targetNode,
    nodeState,
    reason,
    resultJson: params.resultJson,
  });
  const contractIssues = validateOutputContractResult(targetNode.outputContract, result, targetNode.id);
  if (contractIssues.length > 0) {
    throw new Error(formatOutputContractIssues(targetNode.title, contractIssues));
  }

  state.nodeStates[targetNode.id] = {
    ...nodeState,
    status: params.runHooks ? "postActionsRunning" : "succeeded",
    result,
    completedAt: now(),
    error: null,
    waitKind: undefined,
    waitPrompt: undefined,
    bcsApproval: undefined,
  };
  appendAuditLog(state, targetNode.id, "manual-skip", reason);
  appendFlowEvent(state, {
    type: "node_skipped",
    flowId,
    workflowId: state.workflowId,
    nodeId: targetNode.id,
    data: {
      reason,
      runHooks: params.runHooks,
      originalStatus: nodeState.status,
      resultPath: `nodeStates.${targetNode.id}.result`,
      ...summarizeRecord(result),
    },
  });
  emitNodeEvent("node_skipped", {
    flowId,
    workflowId: state.workflowId,
    nodeId: targetNode.id,
    executorType: targetNode.executor.type,
    attempt: (nodeState.attempts ?? 0) + 1,
    nodeTitle: targetNode.title ?? undefined,
    error: reason,
    sessionKey: deps.sessionKey,
    sessionId: deps.sessionId,
    systemContext: {
      reason: "manual_skip",
      originalStatus: nodeState.status,
      runHooks: params.runHooks,
    },
  });
  notifyNodeSkipped(deps.chatInject, deps.chatInjectLevel ?? "full", flowId, targetNode, reason, "manual_skip");
  applyPhaseAndStatus(effectiveWorkflow, state);

  const skipResumeResult = await deps.boundTaskFlow.resume({
    flowId,
    expectedRevision: revision,
    status: "running",
    currentStep: targetNode.id,
    stateJson: JSON.stringify(state),
  });
  if (!skipResumeResult.applied) throw new Error("状态更新冲突，请重试");

  revision = (skipResumeResult.flow as Record<string, unknown>).revision as number;

  await deps.chatInject(
    `已人工跳过 ${targetNode.title}：${reason}`,
    `${flowId}:${targetNode.id}:manual-skip`,
  );

  if (params.runHooks) {
    const hookOutcome = await runNodeSuccessHooks({
      deps,
      workflow: effectiveWorkflow,
      state,
      flowId,
      revision,
      node: targetNode,
    });
    revision = hookOutcome.revision;
    if (hookOutcome.blocked) return `已人工跳过 ${targetNode.title}，但后置动作失败，流程已阻塞`;

    effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
    applyPhaseAndStatus(effectiveWorkflow, state);
    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: "running",
      currentStep: targetNode.id,
      stateJson: JSON.stringify(state),
    });
    if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");
    revision = (resumeResult.flow as Record<string, unknown>).revision as number;
  }

  const outcome = await asyncAwareExecuteLoop(deps, workflow, state, flowId, revision);
  if (outcome.status === "failed") {
    return `已人工跳过 ${targetNode.title}，但后续节点执行失败，详情见上方工作流消息。`;
  }
  if (outcome.status === "waiting") {
    return `已人工跳过 ${targetNode.title}，流程已进入等待状态，请按上方提示继续。`;
  }
  if (outcome.status === "finished") {
    return `已人工跳过 ${targetNode.title}，流程已完成。`;
  }
  if (outcome.status === "blocked") {
    return `已人工跳过 ${targetNode.title}，但后续执行被阻塞，请查看上方提示。`;
  }
  return `已人工跳过 ${targetNode.title}，工作流继续推进`;
}

/**
 * 拓扑守卫（仅 `retry --use-current-def` 调用）：用新定义续跑时，确保新图与保留的上游
 * nodeStates 对得上，防止静默跑出错误结果。
 *
 * 层 1（依赖满足，硬正确性）：scope 内每个节点的 dependsOn 必须来自 kept-succeeded 上游或 scope 自身。
 * 层 2（结构变化）：若 snapshot 图与 current 图的节点集 / dependsOn 邻接不同 → 拒绝（v1 只支持就地改）。
 * 无 snapshot（纯内存 registry flow）时跳过层 2，仅层 1 守住。
 */
export function validateRetrySegmentGraph(
  snapshot: WorkflowSpec | undefined,
  current: WorkflowSpec,
  state: FlowState,
  scope: string[],
): { ok: true } | { ok: false; message: string } {
  const scopeSet = new Set(scope);
  const keptUpstream = new Set<string>();
  for (const n of current.nodes) {
    if (scopeSet.has(n.id)) continue;
    const st = state.nodeStates[n.id]?.status;
    if (st === "succeeded" || st === "waiting") keptUpstream.add(n.id);
  }
  for (const nodeId of scope) {
    const node = current.nodes.find((n) => n.id === nodeId);
    if (!node) continue;
    for (const dep of node.dependsOn ?? []) {
      if (!keptUpstream.has(dep) && !scopeSet.has(dep)) {
        return { ok: false, message: `Blocked: '${nodeId}' (depends on '${dep}' which is neither kept-succeeded nor being re-run)。该依赖在新定义中既不在保留的上游、也不在重跑范围内，无法续跑。` };
      }
    }
  }
  if (snapshot) {
    const snapIds = new Set(snapshot.nodes.map((n) => n.id));
    const curIds = current.nodes.map((n) => n.id);
    const added = curIds.filter((id) => !snapIds.has(id));
    const removed = [...snapIds].filter((id) => !curIds.includes(id));
    const depChanged: string[] = [];
    for (const n of current.nodes) {
      if (!snapIds.has(n.id)) continue;
      const snapNode = snapshot.nodes.find((x) => x.id === n.id);
      const a = (snapNode?.dependsOn ?? []).slice().sort().join(",");
      const b = (n.dependsOn ?? []).slice().sort().join(",");
      if (a !== b) depChanged.push(n.id);
    }
    if (added.length || removed.length || depChanged.length) {
      const parts: string[] = [];
      if (added.length) parts.push(`新增节点: ${added.join(", ")}`);
      if (removed.length) parts.push(`删除节点: ${removed.join(", ")}`);
      if (depChanged.length) parts.push(`依赖变更节点: ${depChanged.join(", ")}`);
      return { ok: false, message: `结构改动 v1 不支持续跑（${parts.join("；")}）。请改用 run --debug 整条重跑，或回退 YAML 改动后再 retry。` };
    }
  }
  return { ok: true };
}

export async function handleRetry(
  deps: ControllerDeps,
  params: { nodeId?: string; flowId?: string; reason?: string; useCurrentDef?: boolean; debug?: boolean; inputOverrides?: Record<string, string> },
): Promise<string> {
  const flow = await readCommandFlow(deps, params.flowId);
  const flowId = readFlowId(flow);
  const revision = typeof flow.revision === "number" ? flow.revision : 1;
  const state = parseFlowState(flow);
  let workflow: WorkflowSpec;
  let currentResolved: ResolvedWorkflow | undefined = undefined;
  if (params.useCurrentDef) {
    const lookup = await requireWorkflowLookup(deps, state.workflowId, params.debug);
    workflow = lookup.workflow;
    currentResolved = lookup.resolved;
  } else {
    workflow = await loadWorkflowForState(deps, state);
  }
  // digest 守卫：--use-current-def 不带 --debug 时，若本地 pack 与采用的 DB 定义 digest 不同，提示加 --debug，避免默默跑旧版本
  let digestWarning = "";
  if (params.useCurrentDef && !params.debug) {
    const localResolved = deps.resolvedWorkflows?.find((r) => r.spec.id === state.workflowId);
    if (localResolved?.digest && currentResolved?.digest && localResolved.digest !== currentResolved.digest) {
      digestWarning = `⚠️ 本地 pack 与采用的 DB 定义不同（digest 不一致）。若想运行本地版本，请加 --debug；否则本次结果基于 DB 版本。\n`;
    }
  }
  const selectedNodeId = findRetryTargetNodeId(workflow, state, params.nodeId);
  const targetNodeId = promoteLoopRuntimeRetryTarget(state, selectedNodeId);
  const targetNode = workflow.nodes.find((node) => node.id === targetNodeId);
  if (!targetNode) throw new Error(`节点 ${targetNodeId} 不存在`);

  const resetNodeIds = collectTargetAndDescendants(workflow, targetNodeId);
  // 拓扑守卫（仅 --use-current-def）：用新定义续跑时，防止新图与保留的上游 nodeStates 对不上
  if (params.useCurrentDef) {
    const guardResult = validateRetrySegmentGraph(state.workflowSnapshot, workflow, state, resetNodeIds);
    if (guardResult.ok === false) return guardResult.message;
  }
  // 参数覆盖：--set k=v 写入 state.input.params，重跑段及下游的 {{input.params.xxx}}/{{params.xxx}} 按新值解析
  if (params.inputOverrides && Object.keys(params.inputOverrides).length > 0) {
    if (!state.input) state.input = { params: {}, files: [], digest: "", digestShort: "" };
    for (const [k, v] of Object.entries(params.inputOverrides)) state.input.params[k] = v;
  }
  if (isLoopGroupNode(targetNode)) {
    clearLoopRuntimeStateForManualRetry(state, targetNode.id);
  }
  for (const nodeId of resetNodeIds) {
    resetNodeForManualRetry(workflow, state, nodeId, nodeId === targetNodeId);
  }

  applyPhaseAndStatus(workflow, state);
  state.activeNodes = [];
  state.currentPhase = targetNode.phase;
  state.businessStatus = targetNode.businessStatus ?? state.businessStatus;

  const reason = params.reason ?? "用户手动重试";
  // 出处审计：用新定义续跑时，记录 continuedWithCurrentDef（不覆盖 workflowPin/workflowSnapshot）
  if (params.useCurrentDef) {
    state.continuedWithCurrentDef = {
      atRevision: revision,
      fromWorkflowDigest: state.workflowPin?.workflowDigest,
      currentDigest: currentResolved?.digest,
      source: currentResolved?.source?.kind,
      debug: !!params.debug,
      capturedAt: new Date().toISOString(),
    };
  }
  appendAuditLog(state, targetNodeId, params.useCurrentDef ? "manual-retry-current-def" : "manual-retry", reason);
  enqueueRunLog({
    flow_id: flowId,
    node_id: targetNodeId,
    level: "info",
    source: "node",
    message: `Node manually retried: ${targetNodeId}, reason=${reason}, resetNodes=[${resetNodeIds.join(",")}]`,
    timestamp: Date.now(),
  });
  appendFlowEvent(state, {
    type: "node_manual_retry",
    flowId,
    workflowId: state.workflowId,
    nodeId: targetNodeId,
    data: { reason, resetNodeIds, useCurrentDef: !!params.useCurrentDef, currentDigest: currentResolved?.digest },
  });

  const resumeResult = await deps.boundTaskFlow.resume({
    flowId,
    expectedRevision: revision,
    status: "running",
    currentStep: targetNodeId,
    stateJson: JSON.stringify(state),
  });
  if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");

  const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;

  // Sync flow_runs status to "running" so that HTTP callbacks and DB queries
  // see the correct status after a manual retry. Without this, flow_runs.status
  // remains "failed" from the previous run, causing stale state in callbacks.
  syncFlowRunPhase(flowId, state.currentPhase, "running");

  await deps.chatInject(
    `已重试 ${targetNode.title}，并重置下游节点：${resetNodeIds.join(", ")}`,
    `${flowId}:${targetNodeId}:manual-retry`,
  );
  const outcome = await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);
  const tailMessage =
    outcome.status === "failed" ? `已重试 ${targetNode.title}，但后续节点仍失败，详情见上方工作流消息。`
    : outcome.status === "waiting" ? `已重试 ${targetNode.title}，流程已进入等待状态，请按上方提示继续。`
    : outcome.status === "finished" ? `已重试 ${targetNode.title}，流程已完成。`
    : `已重试 ${targetNode.title}，工作流仍在推进，进度以上方消息为准。`;
  return `${digestWarning}${tailMessage}`;
}

export async function handleFlowsCleanup(
  deps: ControllerDeps,
  params: { identityKey: string; workflowId?: string; status: "failed" },
): Promise<string> {
  const targetIdentity = params.identityKey;
  if (!targetIdentity) {
    throw new Error("flows cleanup 需要 --identityKey <identity>");
  }
  const flows = normalizeFlowListResult(await deps.boundTaskFlow.list());
  let count = 0;
  for (const flow of flows) {
    if (readFlowRecordStatus(flow) !== params.status) continue;
    const state = safeParseFlowState(flow);
    if (!state) continue;
    if (params.workflowId && state.workflowId !== params.workflowId) continue;
    const stateIdentity = state.identity?.key;
    if (stateIdentity !== targetIdentity) continue;
    state.workflowData ??= {};
    state.workflowData.flowHidden = true;
    const flowId = readFlowId(flow);
    appendFlowEvent(state, {
      type: "flow_hidden",
      flowId,
      workflowId: state.workflowId,
      data: { identityKey: targetIdentity, workflowId: params.workflowId ?? state.workflowId, status: params.status },
    });
    // BUG-21 fix: Use try/finally to ensure flow control slots are released
    // even if boundTaskFlow.fail() throws an exception. Without this guard,
    // an exception from fail() would skip releaseAllForFlow, causing slot leaks.
    try {
      const failResult = await deps.boundTaskFlow.fail({
        flowId,
        expectedRevision: typeof flow.revision === "number" ? flow.revision : 1,
        status: "failed",
        stateJson: JSON.stringify(state),
        blockedSummary: `已软清理 failed flow: identity=${targetIdentity}`,
      });
      if (
        failResult
        && typeof failResult === "object"
        && !Array.isArray(failResult)
        && (failResult as { applied?: unknown }).applied === false
      ) {
        throw new Error("状态更新冲突，请重试");
      }
      count += 1;
    } finally {
      console.log(`[controller] handleFlowsCleanup: releasing flow control slots for flow ${flowId} (cleanup)`);
      deps.flowControl?.releaseAllForFlow(flowId);
    }
  }

  return `已软清理 ${count} 条 identity ${targetIdentity} 的 failed 运行记录。默认 runs 将不再展示，可用 ${formatWorkflowCommand(deps, params.workflowId ?? "workflow", "runs", ["--includeHidden"])} 查看。`;
}

function resolveLegacyRepairIdentity(
  deps: ControllerDeps,
  workflow: WorkflowSpec,
  state: FlowState,
): FlowIdentity {
  const spec = workflow.identity;
  if (!spec?.key) {
    throw new Error(`Workflow ${workflow.id} identity.key 为空或未声明，无法修复 legacy identity`);
  }
  const input = fallbackFlowInput(state);
  const contextState = ensureFlowStateDefaults({
    ...state,
    input,
    workflowData: state.workflowData ?? {},
    actionOutputs: state.actionOutputs ?? {},
    flowHooks: state.flowHooks ?? {},
    auditLog: state.auditLog ?? [],
  });
  const context = buildTemplateContext(contextState, deps.skillRoot, {}, { userIdentity: resolveUserIdentityForContext(deps) });
  const key = resolveTemplate(spec.key, context).trim();
  if (!key) {
    throw new Error(`Workflow ${workflow.id} identity.key 渲染为空`);
  }
  const label = spec.label ? resolveTemplate(spec.label, context).trim() : key;
  return {
    key,
    label: label || key,
    duplicatePolicy: spec.duplicatePolicy ?? "reject-active",
  };
}

function formatIdentitySample(identity: FlowIdentity | undefined): string {
  return identity ? `${identity.label} (${identity.key})` : "-";
}

export async function handleRepairLegacyIdentity(
  deps: ControllerDeps,
  params: { workflowId: string; flowId?: string; dryRun?: boolean },
): Promise<string> {
  const { workflow } = await requireWorkflowLookup(deps, params.workflowId);
  if (!workflow.identity?.key) {
    throw new Error(`Workflow ${params.workflowId} identity.key 为空或未声明，无法修复 legacy identity`);
  }

  const candidates = params.flowId
    ? [await deps.boundTaskFlow.get(params.flowId)].filter((flow): flow is Record<string, unknown> => Boolean(flow))
    : normalizeFlowListResult(await deps.boundTaskFlow.list());

  let repairable = 0;
  let repaired = 0;
  let skipped = params.flowId && candidates.length === 0 ? 1 : 0;
  let conflicts = 0;
  let sampleIdentity: FlowIdentity | undefined;
  const skipReasons: string[] = [];
  if (params.flowId && candidates.length === 0) {
    skipReasons.push(`${params.flowId}: flow not found`);
  }

  for (const flow of candidates) {
    const flowId = readFlowId(flow);
    const state = safeParseFlowState(flow);
    if (!state) {
      skipped += 1;
      skipReasons.push(`${flowId}: stateJson 解析失败`);
      continue;
    }
    if (state.workflowId !== params.workflowId) {
      if (params.flowId) {
        skipped += 1;
        skipReasons.push(`${flowId}: workflowId=${state.workflowId} 不匹配`);
      }
      continue;
    }
    if (state.identity?.key) {
      skipped += 1;
      skipReasons.push(`${flowId}: 已存在 identity`);
      continue;
    }

    let identity: FlowIdentity;
    try {
      identity = resolveLegacyRepairIdentity(deps, workflow, state);
    } catch (err) {
      skipped += 1;
      skipReasons.push(`${flowId}: ${err instanceof Error ? err.message : String(err)}`);
      continue;
    }
    repairable += 1;
    sampleIdentity ??= identity;

    if (params.dryRun) continue;

    const revision = flow.revision;
    if (typeof revision !== "number" || !Number.isFinite(revision)) {
      skipped += 1;
      skipReasons.push(`${flowId}: revision 缺失或非法`);
      continue;
    }

    state.identity = identity;
    appendAuditLog(state, "-", "repair-legacy-identity", `补齐 legacy flow identity: ${identity.label} (${identity.key})`);
    appendFlowEvent(state, {
      type: "workflow_repaired",
      flowId,
      workflowId: state.workflowId,
      data: { identity, repair: "legacy-identity" },
    }, { log: false });

    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: readFlowRecordStatus(flow),
      currentStep: readCurrentStep(flow) ?? state.activeNodes[0] ?? "repair-legacy-identity",
      stateJson: JSON.stringify(state),
    });
    if (!resumeResult.applied) {
      conflicts += 1;
      skipped += 1;
      skipReasons.push(`${flowId}: revision 冲突`);
      continue;
    }
    repaired += 1;
  }

  if (params.dryRun) {
    return [
      `legacy identity dry-run：可修复 ${repairable} 条，跳过 ${skipped} 条。`,
      `示例 identity：${formatIdentitySample(sampleIdentity)}`,
      ...(skipReasons.length > 0 ? ["跳过原因：", ...skipReasons.slice(0, 10).map((reason) => `- ${reason}`)] : []),
    ].join("\n");
  }

  return [
    `legacy identity repair：已修复 ${repaired} 条，可修复 ${repairable} 条，跳过 ${skipped} 条。`,
    `示例 identity：${formatIdentitySample(sampleIdentity)}`,
    ...(conflicts > 0 ? [`revision 冲突 ${conflicts} 条，请稍后重试。`] : []),
    ...(skipReasons.length > 0 ? ["跳过原因：", ...skipReasons.slice(0, 10).map((reason) => `- ${reason}`)] : []),
  ].join("\n");
}

export async function handleRepairExternalPackPin(
  deps: ControllerDeps,
  params: { workflowId: string; flowId?: string; dryRun?: boolean },
): Promise<string> {
  const lookup = await requireWorkflowLookup(deps, params.workflowId);
  const pin = buildWorkflowPin(lookup.resolved);
  if (!pin) {
    throw new Error(`Workflow ${params.workflowId} 不是外置 Pack workflow，无法执行 external-pack-pin 修复`);
  }

  const candidates = params.flowId
    ? [await deps.boundTaskFlow.get(params.flowId)].filter((flow): flow is Record<string, unknown> => Boolean(flow))
    : normalizeFlowListResult(await deps.boundTaskFlow.list());

  let repairable = 0;
  let repaired = 0;
  let skipped = params.flowId && candidates.length === 0 ? 1 : 0;
  let conflicts = 0;
  const skipReasons: string[] = [];
  if (params.flowId && candidates.length === 0) {
    skipReasons.push(`${params.flowId}: flow not found`);
  }

  for (const flow of candidates) {
    const flowId = readFlowId(flow);
    const state = safeParseFlowState(flow);
    if (!state) {
      skipped += 1;
      skipReasons.push(`${flowId}: stateJson 解析失败`);
      continue;
    }
    if (state.workflowId !== params.workflowId) {
      if (params.flowId) {
        skipped += 1;
        skipReasons.push(`${flowId}: workflowId=${state.workflowId} 不匹配`);
      }
      continue;
    }
    if (state.workflowPin && state.workflowSnapshot) {
      skipped += 1;
      skipReasons.push(`${flowId}: 已存在 workflowPin/workflowSnapshot`);
      continue;
    }
    if (state.workflowPin && !workflowPinMatches(state.workflowPin, pin)) {
      conflicts += 1;
      skipped += 1;
      skipReasons.push(`${flowId}: workflowPin 与当前 Pack 不一致，已跳过`);
      continue;
    }
    if (state.workflowSnapshot && !workflowSnapshotMatches(state.workflowSnapshot, lookup.workflow)) {
      conflicts += 1;
      skipped += 1;
      skipReasons.push(`${flowId}: workflowSnapshot 与当前 Pack 不一致，已跳过`);
      continue;
    }

    repairable += 1;
    if (params.dryRun) continue;

    const revision = flow.revision;
    if (typeof revision !== "number" || !Number.isFinite(revision)) {
      skipped += 1;
      skipReasons.push(`${flowId}: revision 缺失或非法`);
      continue;
    }

    state.auditLog = Array.isArray(state.auditLog) ? state.auditLog : [];
    if (!state.workflowPin) state.workflowPin = pin;
    if (!state.workflowSnapshot) state.workflowSnapshot = structuredClone(lookup.workflow);
    appendAuditLog(state, "-", "repair-external-pack-pin", `补齐 external pack pin/snapshot: ${pin.packId}@${pin.packVersion}`);
    appendFlowEvent(state, {
      type: "workflow_repaired",
      flowId,
      workflowId: state.workflowId,
      data: { repair: "external-pack-pin", workflowPin: pin },
    }, { log: false });

    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: readFlowRecordStatus(flow),
      currentStep: readCurrentStep(flow) ?? state.activeNodes[0] ?? "repair-external-pack-pin",
      stateJson: JSON.stringify(state),
    });
    if (!resumeResult.applied) {
      conflicts += 1;
      skipped += 1;
      skipReasons.push(`${flowId}: revision 冲突`);
      continue;
    }
    repaired += 1;
  }

  if (params.dryRun) {
    return [
      `external pack pin dry-run：可修复 ${repairable} 条，跳过 ${skipped} 条。`,
      ...(skipReasons.length > 0 ? ["跳过原因：", ...skipReasons.slice(0, 10).map((reason) => `- ${reason}`)] : []),
    ].join("\n");
  }

  return [
    `external pack pin repair：已修复 ${repaired} 条，跳过 ${skipped} 条。`,
    ...(conflicts > 0 ? [`revision 冲突 ${conflicts} 条，请稍后重试。`] : []),
    ...(skipReasons.length > 0 ? ["跳过原因：", ...skipReasons.slice(0, 10).map((reason) => `- ${reason}`)] : []),
  ].join("\n");
}

function encodeFlowExport(payload: Record<string, unknown>): string {
  return Buffer.from(JSON.stringify(payload), "utf8").toString("base64url");
}

function decodeFlowExport(token: string): Record<string, unknown> {
  let parsed: unknown;
  try {
    parsed = JSON.parse(Buffer.from(token.trim(), "base64url").toString("utf8")) as unknown;
  } catch {
    throw new Error("导入 token 格式无效");
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("导入 token 格式无效");
  return parsed as Record<string, unknown>;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) return false;
  const proto = Object.getPrototypeOf(value);
  return proto === Object.prototype || proto === null;
}

function nonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function validateOptionalRecord(
  issues: string[],
  owner: Record<string, unknown>,
  key: string,
): void {
  if (owner[key] !== undefined && !isPlainRecord(owner[key])) {
    issues.push(`${key} 必须是对象`);
  }
}

function validateOptionalString(
  issues: string[],
  owner: Record<string, unknown>,
  key: string,
): void {
  if (owner[key] !== undefined && typeof owner[key] !== "string") {
    issues.push(`${key} 必须是字符串`);
  }
}

function validateOptionalSafeInteger(
  issues: string[],
  owner: Record<string, unknown>,
  key: string,
): void {
  if (owner[key] !== undefined && (!Number.isSafeInteger(owner[key]) || Number(owner[key]) < 0)) {
    issues.push(`${key} 必须是非负整数`);
  }
}

function validateFlowImportPayload(payload: Record<string, unknown>): { state?: FlowState; issues: string[] } {
  const issues: string[] = [];
  if (payload.schemaVersion !== 1) issues.push("schemaVersion 不支持");
  validateOptionalString(issues, payload, "sourceFlowId");
  validateOptionalString(issues, payload, "workflowId");
  validateOptionalString(issues, payload, "currentStep");
  validateOptionalString(issues, payload, "status");
  validateOptionalSafeInteger(issues, payload, "revision");
  if (payload.status !== undefined && typeof payload.status === "string" && !IMPORTABLE_FLOW_STATUSES.has(payload.status)) {
    issues.push(`status 非法: ${payload.status}`);
  }

  const stateValue = payload.state;
  if (!isPlainRecord(stateValue)) {
    issues.push("state 必须是对象");
    return { issues };
  }

  const stateRecord = stateValue;
  if (!nonEmptyString(stateRecord.workflowId)) issues.push("workflowId 必须是非空字符串");
  if (stateRecord.workflowVersion !== undefined && !Number.isSafeInteger(stateRecord.workflowVersion)) {
    issues.push("workflowVersion 必须是整数");
  }
  if (stateRecord.executionMode !== "private" && stateRecord.executionMode !== "bcs-group" && stateRecord.executionMode !== "dingtalk-group") {
    issues.push("executionMode 必须是 private、bcs-group 或 dingtalk-group");
  }
  if (!nonEmptyString(stateRecord.currentPhase)) issues.push("currentPhase 必须是非空字符串");
  if (!nonEmptyString(stateRecord.businessStatus)) issues.push("businessStatus 必须是非空字符串");
  if (stateRecord.activeNodes !== undefined && !Array.isArray(stateRecord.activeNodes)) {
    issues.push("activeNodes 必须是数组");
  }
  if (stateRecord.auditLog !== undefined && !Array.isArray(stateRecord.auditLog)) {
    issues.push("auditLog 必须是数组");
  }
  if (stateRecord.flowEvents !== undefined && !Array.isArray(stateRecord.flowEvents)) {
    issues.push("flowEvents 必须是数组");
  }

  for (const key of ["params", "workflowData", "flowHooks", "actionOutputs", "nodeOutput", "actors"]) {
    validateOptionalRecord(issues, stateRecord, key);
  }
  if (!isPlainRecord(stateRecord.nodeStates)) {
    issues.push("nodeStates 必须是对象");
  } else {
    for (const [nodeId, nodeState] of Object.entries(stateRecord.nodeStates)) {
      if (!isPlainRecord(nodeState)) {
        issues.push(`nodeStates.${nodeId} 必须是对象`);
        continue;
      }
      if (!nonEmptyString(nodeState.status)) {
        issues.push(`nodeStates.${nodeId}.status 必须是非空字符串`);
      } else if (!IMPORTABLE_NODE_STATUSES.has(nodeState.status)) {
        issues.push(`nodeStates.${nodeId}.status 非法: ${nodeState.status}`);
      }
      if (!nonEmptyString(nodeState.phase)) issues.push(`nodeStates.${nodeId}.phase 必须是非空字符串`);
      if (!nonEmptyString(nodeState.executor)) issues.push(`nodeStates.${nodeId}.executor 必须是非空字符串`);
      for (const timeKey of ["startedAt", "completedAt", "updatedAt"]) {
        if (nodeState[timeKey] !== undefined && typeof nodeState[timeKey] !== "number") {
          issues.push(`nodeStates.${nodeId}.${timeKey} 必须是数字`);
        }
      }
    }
  }

  if (issues.length > 0) return { issues };
  return { state: ensureFlowStateDefaults(stateRecord as FlowState), issues };
}

function sanitizeImportedFlowState(state: FlowState): { warnings: string[] } {
  const warnings: string[] = [];
  if (state.bcsGroupId) {
    delete state.bcsGroupId;
    warnings.push("已清理旧 bcsGroupId");
  }

  state.activeNodes = Array.isArray(state.activeNodes)
    ? state.activeNodes.filter((nodeId): nodeId is string => typeof nodeId === "string" && nodeId.trim().length > 0)
    : [];
  state.auditLog = Array.isArray(state.auditLog) ? state.auditLog : [];
  state.params = isPlainRecord(state.params) ? state.params as Record<string, string> : {};
  state.workflowData = isPlainRecord(state.workflowData) ? state.workflowData : {};
  state.actionOutputs = isPlainRecord(state.actionOutputs) ? state.actionOutputs as FlowState["actionOutputs"] : {};
  state.flowHooks = isPlainRecord(state.flowHooks) ? state.flowHooks as FlowState["flowHooks"] : {};

  let convertedWaiting = false;
  for (const nodeState of Object.values(state.nodeStates)) {
    if (nodeState.status === "waiting") {
      nodeState.status = "blocked";
      nodeState.error = "导入迁移：旧等待状态依赖原 session，已转为 blocked，请使用 retry/submit/revise 恢复。";
      convertedWaiting = true;
    }
    nodeState.waitKind = undefined;
    nodeState.waitPrompt = undefined;
    nodeState.bcsApproval = undefined;
    nodeState.childSessionKey = undefined;
  }
  if (convertedWaiting) warnings.push("旧等待状态已转为 blocked");
  return { warnings };
}

export async function handleFlowExport(
  deps: ControllerDeps,
  flowId: string,
  options: { globalFlowStore?: GlobalFlowStore } = {},
): Promise<string> {
  const localFlow = await deps.boundTaskFlow.get(flowId);
  const flow = localFlow ?? await (await resolveGlobalFlowStore(deps, options.globalFlowStore)).get(flowId);
  if (!flow) throw new Error(`Flow ${flowId} not found`);
  const state = parseFlowState(flow);
  const token = encodeFlowExport({
    schemaVersion: 1,
    exportedAt: isoNow(),
    sourceFlowId: flowId,
    workflowId: state.workflowId,
    state,
    currentStep: readCurrentStep(flow) ?? state.activeNodes[0] ?? "",
    status: readFlowRecordStatus(flow),
  });
  return [
    `已导出 flow ${flowId}。`,
    "",
    `\`${token}\``,
    "",
    `导入命令：${formatWorkflowCommand(deps, state.workflowId, "import", ["<exportToken>"])}`,
  ].join("\n");
}

export async function handleFlowImport(
  deps: ControllerDeps,
  token: string,
): Promise<string> {
  let payload: Record<string, unknown>;
  try {
    payload = decodeFlowExport(token);
  } catch (err) {
    return `导入 token 无效：${err instanceof Error ? err.message : String(err)}`;
  }
  const validation = validateFlowImportPayload(payload);
  if (!validation.state) {
    return `导入 token 无效：${validation.issues.join("；")}`;
  }
  const state = validation.state;
  const sourceFlowId = typeof payload.sourceFlowId === "string" ? payload.sourceFlowId : "unknown";
  const sanitizeResult = sanitizeImportedFlowState(state);
  state.workflowData ??= {};
  state.workflowData.migratedFromFlowId = sourceFlowId;

  const currentStep = typeof payload.currentStep === "string" && payload.currentStep
    ? payload.currentStep
    : state.activeNodes[0] ?? Object.keys(state.nodeStates)[0] ?? "imported";
  const status = typeof payload.status === "string" && payload.status
    ? payload.status
    : "running";
  appendFlowEvent(state, {
    type: "flow_imported",
    flowId: "pending",
    workflowId: state.workflowId,
    data: { sourceFlowId },
  }, { log: false });

  const flow = await deps.boundTaskFlow.createManaged({
    controllerId: CONTROLLER_ID,
    workflowId: state.workflowId,
    goal: `Imported ${state.workflowId} from ${sourceFlowId}`,
    status,
    currentStep,
    stateJson: JSON.stringify(state),
  });
  const newFlowId = readFlowId(flow);
  for (const event of state.flowEvents ?? []) {
    if (event.type === "flow_imported" && event.flowId === "pending") {
      event.flowId = newFlowId;
    }
  }

  return [
    `已导入 flow，新的 flowId: ${newFlowId}`,
    `来源 flowId: ${sourceFlowId}`,
    ...sanitizeResult.warnings,
    `建议查看：${formatWorkflowCommand(deps, state.workflowId, "inspect")}`,
    `调试命令：${formatWorkflowCommand(deps, state.workflowId, "inspect", [newFlowId])}`,
  ].join("\n");
}

export async function handleState(
  deps: ControllerDeps,
  flowId?: string,
): Promise<string> {
  const flow = await readCommandFlow(deps, flowId).catch((err) => {
    if (err instanceof Error && (err.message === "当前没有活跃的流程" || err.message === `Flow ${flowId} not found`)) return null;
    throw err;
  });
  if (!flow) return flowId ? `Flow ${flowId} not found` : "当前没有活跃的流程";

  const state = parseFlowState(flow);
  const status = flow.status as string;
  const paramsDesc = Object.entries(state.params).map(([k, v]) => `${k}=${v}`).join(", ");
  const startedAt = flow.createdAt ?? flow.gmt_create ?? firstFlowEventTime(state);
  const updatedAt = flow.updatedAt ?? flow.gmt_modified ?? lastFlowEventTime(state);
  const currentFlowId = readFlowId(flow);
  const usageSummary = formatTokenUsage((state.usage ?? recomputeWorkflowUsage(state.nodeStates))?.total);
  const workflow = await loadWorkflowForState(deps, state);
  const input = fallbackFlowInput(state);
  const publicOutputs = pickPublicWorkflowOutputs(
    workflow.outputs,
    isRecord(state.workflowData.outputs) ? state.workflowData.outputs : {},
  );
  const lines: string[] = [
    `**${state.workflowId}** (${paramsDesc}) — ${status}`,
    `flow_id: ${currentFlowId}`,
    `阶段: ${state.currentPhase} | 状态: ${state.businessStatus}`,
    `模式: ${state.executionMode}`,
    ...(state.identity ? [`Identity: ${state.identity.label} (${state.identity.key})`] : []),
    ...(state.input ? [`输入: digest=${input.digestShort} | files=${input.files.length}${input.message ? " | message=yes" : ""}`] : []),
    ...(state.workflowPin ? [`Workflow 来源: ${state.workflowPin.source} | pack=${state.workflowPin.packId ?? "-"}@${state.workflowPin.packVersion ?? "-"} | digest=${state.workflowPin.workflowDigest}`] : []),
    ...(usageSummary ? [`Token 用量: ${usageSummary}`] : []),
    `开始时间: ${formatLocalDateTime(startedAt)}`,
    `更新时间: ${formatLocalDateTime(updatedAt)}`,
    "",
    "**节点进度:**",
  ];

  if (workflow) {
    for (const node of workflow.nodes) {
      if (state.runtimeNodeMeta?.[node.id]) continue;
      const ns = state.nodeStates[node.id];
      const statusIcon =
        ns?.status === "succeeded" ? "✅" :
        ns?.status === "running" ? "🔄" :
        ns?.status === "postActionsRunning" ? "🔁" :
        ns?.status === "waiting" ? "⏳" :
        ns?.status === "blocked" ? "🚧" :
        ns?.status === "failed" ? "❌" : "⬜";
      lines.push(`${statusIcon} ${node.title} (${node.id})${formatLoopStateSummary(state, workflow, node)}${formatSubworkflowStateSummary(state, node)}${nodeDurationLabel(ns)}`);
    }
  }

  const outputEntries = Object.entries(publicOutputs);
  if (outputEntries.length > 0) {
    lines.push("", "**输出:**");
    for (const [key, value] of outputEntries) {
      lines.push(`- ${key}: ${markdownCell(value)}`);
    }
  }

  if (state.activeNodes.length > 0) {
    lines.push("", `**等待中:** ${state.activeNodes.join(", ")}`);
    const waitJson = parseWaitJson(flow);
    if (waitJson?.hint) lines.push(`提示: ${waitJson.hint}`);
    if (waitJson?.userAction) lines.push(`操作: ${waitJson.userAction}`);
  }

  // In async mode or for running flows, show recent events as a failsafe
  // notification layer (user can always run `state` to see what happened)
  if (status === "running" && state.flowEvents && state.flowEvents.length > 0) {
    const recentEvents = state.flowEvents.slice(-5);
    lines.push("", "**最近事件:**");
    for (const evt of recentEvents) {
      const evtTime = formatLocalDateTime(new Date(evt.time));
      const evtType = evt.type ?? "unknown";
      const evtNode = evt.nodeId ?? "";
      lines.push(`- [${evtTime}] ${evtType}${evtNode ? ` (${evtNode})` : ""}`);
    }
  }

  return lines.join("\n");
}

function findLoopBodyNode(workflow: WorkflowSpec | undefined, loopId: string, bodyNodeId: string): WorkflowNode | undefined {
  for (const node of workflow?.nodes ?? []) {
    if (node.id !== loopId || !isLoopGroupNode(node)) continue;
    return node.executor.body.find((bodyNode) => bodyNode.id === bodyNodeId);
  }
  return undefined;
}

function runtimeNodeTitle(workflow: WorkflowSpec | undefined, state: FlowState, runtimeNodeId: string): string {
  const meta = state.runtimeNodeMeta?.[runtimeNodeId];
  if (!meta) return runtimeNodeId;
  return findLoopBodyNode(workflow, meta.loopId, meta.bodyNodeId)?.title ?? runtimeNodeId;
}

function currentLoopRuntimeNodeIds(loopState: LoopGroupRuntimeState): string[] {
  const iteration = loopState.iterations[String(loopState.currentIteration)];
  return iteration ? Object.values(iteration.nodeIds) : [];
}

function loopExitReasonLabel(reason: LoopGroupRuntimeState["exitReason"]): string | undefined {
  if (reason === "until-matched") return "条件满足";
  if (reason === "max-iterations-continue") return "达到最大轮次后继续";
  if (reason === "max-iterations-fail") return "达到最大轮次后失败";
  return reason;
}

function formatLoopStateSummary(state: FlowState, workflow: WorkflowSpec | undefined, node: WorkflowNode): string {
  if (!isLoopGroupNode(node)) return "";
  const loopState = state.loopGroups?.[node.id];
  if (!loopState) return "";

  const parts: string[] = [];
  if (loopState.status === "running" || loopState.status === "waiting") {
    parts.push(`第 ${loopState.currentIteration}/${loopState.maxIterations} 轮`);
    const waitingRuntimeNodes = currentLoopRuntimeNodeIds(loopState)
      .filter((runtimeNodeId) => state.nodeStates[runtimeNodeId]?.status === "waiting")
      .map((runtimeNodeId) => `${runtimeNodeTitle(workflow, state, runtimeNodeId)} (${runtimeNodeId})`);
    if (waitingRuntimeNodes.length > 0) {
      parts.push(`当前等待：${waitingRuntimeNodes.join("、")}`);
    }
  } else if (loopState.status === "succeeded" || loopState.status === "failed" || loopState.status === "blocked") {
    const completedIterations = loopState.lastIteration ?? loopState.currentIteration;
    parts.push(`已完成 ${completedIterations}/${loopState.maxIterations} 轮`);
    const exitReason = loopExitReasonLabel(loopState.exitReason);
    if (exitReason) parts.push(`退出原因：${exitReason}`);
  }

  return parts.length > 0 ? ` — ${parts.join(" | ")}` : "";
}

function clampLogLimit(limit: number | undefined): number {
  if (!Number.isFinite(limit)) return 20;
  return Math.min(100, Math.max(1, Math.trunc(limit)));
}

function formatEventTime(time: number): string {
  const date = new Date(time);
  if (Number.isNaN(date.getTime())) return String(time);
  return date.toISOString();
}

function asFlowEventList(value: unknown): FlowEvent[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is FlowEvent => Boolean(item && typeof item === "object"));
}

function formatFlowEvent(event: FlowEvent): string {
  const eventTime = typeof event.time === "number" ? formatEventTime(event.time) : "unknown-time";
  const eventType = typeof event.type === "string" ? event.type : "unknown-event";
  const parts = [
    eventTime,
    eventType,
  ];
  if (event.nodeId) parts.push(`node=${event.nodeId}`);
  if (event.actionId) parts.push(`action=${event.actionId}`);
  if (event.error) parts.push(`error=${event.error}`);
  return `- ${parts.join(" | ")}`;
}

export async function handleLogs(
  deps: ControllerDeps,
  flowIdOrLimit?: string | number,
  limit = 20,
): Promise<string> {
  const flowId = typeof flowIdOrLimit === "string" ? flowIdOrLimit : undefined;
  const requestedLimit = typeof flowIdOrLimit === "number" ? flowIdOrLimit : limit;
  const flow = await readCommandFlow(deps, flowId).catch((err) => {
    if (err instanceof Error && (err.message === "当前没有活跃的流程" || err.message === `Flow ${flowId} not found`)) return null;
    throw err;
  });
  if (!flow) return flowId ? `Flow ${flowId} not found` : "当前没有可查看日志的流程";

  const state = parseFlowState(flow);
  const normalizedLimit = clampLogLimit(requestedLimit);
  const events = asFlowEventList(state.flowEvents);
  const recentEvents = events.slice(-normalizedLimit);
  const lines = [`**${state.workflowId}** 最近 ${recentEvents.length} 条流程事件`];

  for (const event of recentEvents) {
    lines.push(formatFlowEvent(event));
  }

  return lines.join("\n");
}

type HandleFlowsOptions = {
  includeHidden?: boolean;
  global?: boolean;
  identityKey?: string;
  workflowId?: string;
  status?: string;
  globalFlowStore?: GlobalFlowStore;
};

function runtimeGlobalFlowStore(api: unknown): GlobalFlowStore | null {
  const taskFlow = (api as { runtime?: { taskFlow?: Record<string, unknown> } } | undefined)?.runtime?.taskFlow;
  const listAll = taskFlow?.listAll;
  if (typeof listAll !== "function") return null;
  const getAnyOwner = taskFlow?.getAnyOwner;
  return {
    list: async () => normalizeFlowListResult(await listAll.call(taskFlow) as { flows?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>),
    get: async (flowId: string) => {
      if (typeof getAnyOwner === "function") {
        return await getAnyOwner.call(taskFlow, flowId) as RawTaskFlowRecord | null;
      }
      const flows = normalizeFlowListResult(await listAll.call(taskFlow) as { flows?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>);
      return flows.find((flow) => readOptionalFlowId(flow) === flowId) ?? null;
    },
  };
}

async function resolveGlobalFlowStore(deps: ControllerDeps, provided?: GlobalFlowStore): Promise<GlobalFlowStore> {
  if (provided) return provided;
  return runtimeGlobalFlowStore(deps.api) ?? await createSqliteGlobalFlowStore();
}

function flowIsHidden(state: FlowState | null): boolean {
  return state?.workflowData?.flowHidden === true;
}

function flowMatchesOptions(flow: Record<string, unknown>, options: HandleFlowsOptions): boolean {
  const state = safeParseFlowState(flow);
  if (!options.includeHidden && flowIsHidden(state)) return false;

  const workflowId = state?.workflowId ?? flow.workflowId ?? flow.workflow_id;
  const identityKey = state?.identity?.key;
  const status = readFlowRecordStatus(flow);
  if (options.workflowId && workflowId !== options.workflowId) return false;
  if (options.identityKey && identityKey !== options.identityKey) return false;
  if (options.status && status !== options.status) return false;
  return true;
}

function appendFlowsNextStepHints(lines: string[]): void {
  lines.push(
    "",
    "**下一步**",
    "- /workflow inspect <flowId>：查看 flow 状态、节点表与近期事件（自动跨 session）。",
    "- /workflow inspect <flowId> --analyze：查看深度分析报告。",
    "- /workflow inspect <flowId> --full：查看原始 JSON。",
    "- /workflow runs --includeHidden：包含已软清理隐藏的旧 flow。",
  );
}

export async function handleFlows(
  deps: ControllerDeps,
  limit = 20,
  options: HandleFlowsOptions = {},
): Promise<string> {
  const normalizedLimit = clampLogLimit(limit);
  const store = options.global ? await resolveGlobalFlowStore(deps, options.globalFlowStore) : null;
  const sourceFlows = store
    ? await store.list()
    : normalizeFlowListResult(await deps.boundTaskFlow.list());
  const flows = sourceFlows.filter((flow) => flowMatchesOptions(flow, options)).slice(0, normalizedLimit);
  if (flows.length === 0) {
    const base = options.global ? "全局 flows 查询没有匹配记录" : "当前 session 下没有可见的 workflow flow";
    return store?.unavailableReason ? `${base}\n${store.unavailableReason}` : base;
  }

  const lines = [
    `${options.global ? "全局 flows" : "当前 session 可见 flows"}（${flows.length} 条）：`,
    "",
    options.global
      ? "| flowId | ownerKey | workflowId | identity | status | currentStep | revision | time |"
      : "| flowId | workflowId | identity | status | currentStep | revision | time |",
    options.global
      ? "| --- | --- | --- | --- | --- | --- | --- | --- |"
      : "| --- | --- | --- | --- | --- | --- | --- |",
  ];

  for (const flow of flows) {
    const state = safeParseFlowState(flow);
    const workflowId = state?.workflowId ?? flow.workflowId ?? flow.workflow_id ?? "-";
    const identity = state?.identity?.key ?? "缺失 identity，请执行 /workflow repair legacy-identity";
    const status = flowIsHidden(state)
      ? `${readFlowRecordStatus(flow)}(hidden)`
      : readFlowRecordStatus(flow);
    const subInfo = state?.subworkflowMeta
      ? ` [子流程 depth=${state.subworkflowMeta.depth}]`
      : "";
    const cells = [
      markdownCell(readOptionalFlowId(flow)),
      ...(options.global ? [markdownCell(flow.ownerKey ?? flow.owner_key ?? "-")] : []),
      markdownCell(`${workflowId}${subInfo}`),
      markdownCell(identity),
      markdownCell(status),
      markdownCell(readCurrentStep(flow) ?? "-"),
      markdownCell(readFlowRecordRevision(flow)),
      markdownCell(readFlowRecordTime(flow)),
    ];
    lines.push(cells.join(" | ").replace(/^/, "| ").replace(/$/, " |"));
  }

  if (store?.unavailableReason) lines.push("", store.unavailableReason);
  appendFlowsNextStepHints(lines);

  return lines.join("\n");
}

/**
 * Handle the `injectNodes` action — inject dynamic nodes into a running flow.
 * Used externally (dispatch/MCP) and internally (orchestrator iteration).
 */
export async function handleInjectNodes(
  deps: ControllerDeps,
  flowId: string,
  sourceNodeId: string,
  nodes: WorkflowNode[],
): Promise<string> {
  const flow = await readCommandFlow(deps, flowId).catch(() => null);
  if (!flow) return `Flow ${flowId} not found`;

  const state = safeParseFlowState(flow);
  if (!state) return `Failed to parse flow state for ${flowId}`;

  const flowStatus = (flow.status as string) ?? "unknown";
  if (!ACTIVE_FLOW_STATUSES.has(flowStatus)) {
    return `Flow ${flowId} is not active (status: ${flowStatus})`;
  }

  const resolved = (deps.resolvedWorkflows ?? []).find((r) => r.id === state.workflowId);
  const fallbackWorkflow: WorkflowSpec = { id: state.workflowId, version: 1, title: "unknown", nodes: [] as WorkflowNode[] };
  const workflow: WorkflowSpec = resolved?.spec ?? fallbackWorkflow;

  const records: InjectedNodeRecord[] = nodes.map((node, i) => ({
    nodeId: node.id,
    sourceNodeId,
    actionName: node.executor?.type ?? "unknown",
    stepNum: i,
    materializedAt: Date.now(),
  }));

  const result = injectNodesIntoWorkflow(state, workflow, nodes, records, sourceNodeId, deps, flowId);

  if (result.success) {
    await persistStateToFlow(deps, state, flowId);
    const injectedIds = nodes.map((n) => n.id).join(", ");
    return `Injected ${nodes.length} node(s) into flow ${flowId}: ${injectedIds}`;
  }

  return `Failed to inject nodes: ${result.success === false ? result.reason : "unknown"}`;
}

/**
 * Unified inspect command — replaces `state`, `logs`, and `debug`.
 *
 * Auto-resolves flow from session-local store, falling back to global store
 * for cross-session access. Output depth controlled by flags:
 * - default: status summary + wait state + node table + recent events
 * - --analyze: deep analysis report (P5 standard)
 * - --full: raw JSON dump
 */
export async function handleInspect(
  deps: ControllerDeps,
  flowId?: string,
  options: { analyze?: boolean; full?: boolean } = {},
): Promise<string> {
  const flow = await readCommandFlow(deps, flowId);
  const state = parseFlowState(flow);
  const waitJson = parseWaitJson(flow) as Partial<WaitState> | null;

  // --full: raw JSON dump
  if (options.full) {
    return JSON.stringify(
      {
        flowId: readFlowId(flow),
        status: flow.status,
        revision: flow.revision,
        stateJson: state,
        waitJson,
      },
      null,
      2,
    );
  }

  // --analyze: P5 deep analysis report
  if (options.analyze) {
    return renderAnalyzeReport(deps, flow);
  }

  // Default: compact inspect (status summary + wait state + node table + events)
  // Equivalent to the former `debug` compact output
  return renderCompactDebug(deps, flow);
}

/**
 * Render P5 standard analysis report for a flow.
 *
 * Phase 2: will integrate embedded-session analysis, trajectory, token usage, etc.
 * Currently outputs a structured report based on state + wait + engine log data.
 */
function renderAnalyzeReport(deps: ControllerDeps, flow: Record<string, unknown>): string {
  const flowId = readFlowId(flow);
  const state = parseFlowState(flow);
  const waitJson = parseWaitJson(flow) as Partial<WaitState> | null;
  const workflow = (() => {
    try {
      if (state.workflowSnapshot) return state.workflowSnapshot;
      return resolveWorkflowByIdFromPacks(state.workflowId, deps.resolvedWorkflows ?? [])?.spec;
    } catch {
      return undefined;
    }
  })();

  const summaryKeys = debugSummaryKeys(workflow);
  const flowStatus = (flow.status as string) ?? "unknown";
  const firstEventTime = firstFlowEventTime(state);

  // === Section 1: Execution Overview ===
  const duration = state.flowEvents?.length
    ? (() => {
        const events = asFlowEventList(state.flowEvents);
        const first = events[0]?.time;
        const last = events[events.length - 1]?.time;
        if (first && last) return `${((last - first) / 1000).toFixed(1)}s`;
        return "-";
      })()
    : "-";
  const criticalPath = Object.keys(state.nodeStates ?? {})
    .filter((id) => !state.runtimeNodeMeta?.[id])
    .join(" → ") || "-";

  const lines: string[] = [
    "=== 1. 执行概览 ===",
    `- Flow ID: ${flowId}`,
    `- 状态: ${flowStatus}`,
    `- 目标: ${state.identity?.label ?? state.identity?.key ?? "-"}`,
    `- workflow: ${state.workflowId} (v${state.workflowVersion})`,
    `- 开始: ${firstEventTime ? formatLocalDateTime(firstEventTime) : "-"}`,
    `- 更新: ${readFlowRecordTime(flow)}`,
    `- 耗时: ${duration}`,
    `- 关键路径: ${criticalPath}`,
  ];

  // === Section 2: Node Execution Details ===
  lines.push("", "=== 2. 节点执行详情 ===");
  const nodeRows = renderCompactDebugNodeRows(state, workflow);
  lines.push(...nodeRows);

  // === Section 3: Token Consumption (placeholder — Phase 2) ===
  lines.push("", "=== 3. Token 消耗 ===");
  lines.push("  (token 分析需 embedded session 数据，Phase 2 实现)");

  // === Section 4: Issue Summary ===
  lines.push("", "=== 4. 问题汇总 ===");
  const issues: string[] = [];
  for (const [nodeId, nodeState] of Object.entries(state.nodeStates ?? {})) {
    if (state.runtimeNodeMeta?.[nodeId]) continue;
    if (nodeState?.status === "failed") {
      issues.push(`🔴 ${nodeId}: 节点执行失败${nodeState.error ? ` — ${summarizeDebugValue(nodeState.error, 200)}` : ""}`);
    }
  }
  if (waitJson?.waitingFor) {
    const waitingNodes = Array.isArray(waitJson.activeNodes) ? waitJson.activeNodes.join(", ") : "-";
    issues.push(`⏳ 当前等待: ${waitJson.waitingFor} (节点: ${waitingNodes})`);
  }
  if (issues.length > 0) {
    lines.push(...issues.map((i) => `  ${i}`));
  } else {
    lines.push("  ✅ 所有节点执行完毕，无显著问题");
  }

  // === Section 5: Optimization Suggestions (placeholder — Phase 2) ===
  lines.push("", "=== 5. 优化建议 ===");
  lines.push("  (详细优化建议需 trajectory 数据，Phase 2 实现)");

  return lines.join("\n");
}

export async function handleDebug(
  deps: ControllerDeps,
  flowId?: string,
  options: { full?: boolean } = {},
): Promise<string> {
  const flow = await readCommandFlow(deps, flowId).catch((err) => {
    if (err instanceof Error && (err.message === "当前没有活跃的流程" || err.message === `Flow ${flowId} not found`)) return null;
    throw err;
  });
  if (!flow) return flowId ? `Flow ${flowId} not found` : "当前没有活跃的流程";

  if (!options.full) return renderCompactDebug(deps, flow);

  return JSON.stringify(
    {
      flowId: readFlowId(flow),
      status: flow.status,
      revision: flow.revision,
      stateJson: parseFlowState(flow),
      waitJson: parseWaitJson(flow),
    },
    null,
    2,
  );
}

const DEFAULT_DEBUG_SUMMARY_KEYS = [
  "approved",
  "confirmed",
  "needsHuman",
  "status",
  "reason",
  "note",
  "message",
  "error",
  "childSessionKey",
];

function summarizeDebugValue(value: unknown, max = 160): string {
  if (value === undefined) return "-";
  if (value === null) return "null";
  if (typeof value === "string") return truncateDebugText(value.replace(/\s+/g, " ").trim(), max);
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return `Array(${value.length})`;
  if (typeof value === "object") {
    const keys = Object.keys(value as Record<string, unknown>);
    return keys.length > 0 ? `Object(${keys.slice(0, 8).join(", ")}${keys.length > 8 ? ", ..." : ""})` : "{}";
  }
  return String(value);
}

function debugSummaryKeys(workflow: WorkflowSpec | undefined): string[] {
  const keys = new Set(DEFAULT_DEBUG_SUMMARY_KEYS);
  for (const key of workflow?.debug?.summaryKeys ?? []) keys.add(key);
  return [...keys];
}

function summarizeDebugRecord(
  value: Record<string, unknown> | undefined | null,
  max = 240,
  summaryKeys = DEFAULT_DEBUG_SUMMARY_KEYS,
): string {
  if (!value) return "-";
  const parts: string[] = [];
  for (const key of summaryKeys) {
    if (value[key] !== undefined) parts.push(`${key}=${summarizeDebugValue(value[key], 80)}`);
  }
  if (parts.length > 0) return truncateDebugText(parts.join(", "), max);
  const keys = Object.keys(value);
  if (keys.length === 0) return "{}";
  return truncateDebugText(`keys=${keys.slice(0, 12).join(", ")}${keys.length > 12 ? ` (+${keys.length - 12})` : ""}`, max);
}

function renderCompactDebugNodeRows(state: FlowState, workflow: WorkflowSpec | undefined): string[] {
  const nodeById = new Map((workflow?.nodes ?? []).map((node) => [node.id, node]));
  const summaryKeys = debugSummaryKeys(workflow);
  const ids = Object.keys(state.nodeStates).filter((nodeId) => !state.runtimeNodeMeta?.[nodeId]);
  if (ids.length === 0) return ["（暂无节点状态）"];

  const lines = [
    "| 节点 | 状态 | 阶段 | 尝试 | 时间 | 摘要 |",
    "|---|---|---|---:|---|---|",
  ];
  const nowMs = now();
  for (const nodeId of ids) {
    const nodeState = state.nodeStates[nodeId];
    const node = nodeById.get(nodeId);
    const title = node ? `${node.title} (${nodeId})` : nodeId;
    const attempts = nodeState?.attempts ?? 0;
    const time = [
      nodeState?.startedAt ? `开始 ${formatLocalDateTime(nodeState.startedAt)}` : "",
      nodeState?.updatedAt ? `更新 ${formatLocalDateTime(nodeState.updatedAt)}` : "",
      nodeDurationLabel(nodeState, nowMs).replace(/^ — /, ""),
    ].filter(Boolean).join(" / ") || "-";
    const summary = nodeState?.error
      ? `error=${summarizeDebugValue(nodeState.error, 180)}`
      : summarizeDebugRecord(nodeState?.result, 220, summaryKeys);
    lines.push([
      markdownCell(title),
      markdownCell(nodeState?.status ?? "-"),
      markdownCell(nodeState?.phase ?? "-"),
      markdownCell(attempts),
      markdownCell(time),
      markdownCell(summary),
    ].join(" | ").replace(/^/, "| ").replace(/$/, " |"));
  }
  return lines;
}

function renderCompactDebugLoopGroups(state: FlowState, workflow: WorkflowSpec | undefined): string[] {
  const entries = Object.entries(state.loopGroups ?? {});
  if (entries.length === 0) return [];

  const lines = [
    "**循环组**",
    "| loopId | status | current/max | 当前 runtime nodes | waiting nodes | exitReason |",
    "|---|---|---:|---|---|---|",
  ];
  for (const [loopId, loopState] of entries) {
    const runtimeNodeIds = currentLoopRuntimeNodeIds(loopState);
    const waitingNodeIds = runtimeNodeIds.filter((runtimeNodeId) =>
      state.nodeStates[runtimeNodeId]?.status === "waiting"
    );
    const waitingNodes = waitingNodeIds.map((runtimeNodeId) =>
      `${runtimeNodeTitle(workflow, state, runtimeNodeId)} (${runtimeNodeId})`
    );
    lines.push([
      markdownCell(loopId),
      markdownCell(loopState.status),
      markdownCell(`${loopState.currentIteration}/${loopState.maxIterations}`),
      markdownCell(runtimeNodeIds.length > 0 ? runtimeNodeIds.join(", ") : "-"),
      markdownCell(waitingNodes.length > 0 ? waitingNodes.join(", ") : "-"),
      markdownCell(loopState.exitReason ?? "-"),
    ].join(" | ").replace(/^/, "| ").replace(/$/, " |"));
  }
  return lines;
}

function renderCompactDebugEvents(state: FlowState, workflow: WorkflowSpec | undefined): string[] {
  const summaryKeys = debugSummaryKeys(workflow);
  const events = state.flowEvents?.slice(-15) ?? [];
  if (events.length === 0) return ["（暂无最近事件）"];
  return events.map((event) => {
    const target = [event.nodeId, event.actionId].filter(Boolean).join(" / ") || "-";
    const detail = event.error
      ? `error=${summarizeDebugValue(event.error, 200)}`
      : summarizeDebugRecord(event.data, 220, summaryKeys);
    return `- ${formatLocalDateTime(event.time)} ${event.type} ${target} ${detail}`;
  });
}

function renderCompactDebug(deps: ControllerDeps, flow: Record<string, unknown>): string {
  const flowId = readFlowId(flow);
  const state = parseFlowState(flow);
  const waitJson = parseWaitJson(flow) as Partial<WaitState> | null;
  const workflow = (() => {
    try {
      if (state.workflowSnapshot) return state.workflowSnapshot;
      return resolveWorkflowByIdFromPacks(state.workflowId, deps.resolvedWorkflows ?? [])?.spec;
    } catch {
      return undefined;
    }
  })();
  const command = formatWorkflowCommand(deps, state.workflowId, "inspect", [flowId, "--full"]);
  const lines = [
    "**Workflow Debug 摘要**",
    "",
    `- flowId: ${flowId}`,
    `- workflow: ${state.workflowId} (v${state.workflowVersion})`,
    `- identity: ${state.identity?.label ?? state.identity?.key ?? "-"}`,
    `- status: ${readFlowRecordStatus(flow)}`,
    `- revision: ${readFlowRecordRevision(flow)}`,
    `- phase: ${state.currentPhase}`,
    `- businessStatus: ${state.businessStatus}`,
    `- executionMode: ${state.executionMode}`,
    `- activeNodes: ${state.activeNodes.length > 0 ? state.activeNodes.join(", ") : "-"}`,
    `- startedAt: ${formatLocalDateTime(firstFlowEventTime(state))}`,
    `- updatedAt: ${readFlowRecordTime(flow)}`,
    "",
    "**等待态**",
  ];

  if (waitJson) {
    const waitExtras = Object.fromEntries(
      Object.entries(waitJson).filter(([key]) => !["kind", "workflowId", "params", "activeNodes", "waitingFor", "received", "pending", "hint", "userAction"].includes(key)),
    );
    lines.push(
      `- waitingFor: ${waitJson.waitingFor ?? "-"}`,
      `- activeNodes: ${Array.isArray(waitJson.activeNodes) ? waitJson.activeNodes.join(", ") : "-"}`,
      `- hint: ${summarizeDebugValue(waitJson.hint, 220)}`,
      `- userAction: ${summarizeDebugValue(waitJson.userAction, 220)}`,
    );
    if (Object.keys(waitExtras).length > 0) {
      lines.push(`- detail: ${summarizeDebugRecord(waitExtras, 260, debugSummaryKeys(workflow))}`);
    }
  } else {
    lines.push("- 当前无 waitJson");
  }

  const loopGroupLines = renderCompactDebugLoopGroups(state, workflow);
  if (loopGroupLines.length > 0) lines.push("", ...loopGroupLines);
  lines.push("", "**节点状态**", ...renderCompactDebugNodeRows(state, workflow));
  lines.push("", "**最近事件**", ...renderCompactDebugEvents(state, workflow));
  lines.push("", `完整 JSON：\`${command}\``);
  return lines.join("\n");
}

export async function handleBcsCallback(
  deps: ControllerDeps,
  flowId: string,
  nodeId: string,
  result: Record<string, unknown>,
): Promise<string> {
  const flow = await deps.boundTaskFlow.get(flowId);
  if (!flow) throw new Error(`Flow ${flowId} not found`);

  let revision = flow.revision as number;
  const state = parseFlowState(flow);

  const workflow = await loadWorkflowForState(deps, state);
  let effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
  const node = effectiveWorkflow.nodes.find((n) => n.id === nodeId);

  if (state.nodeStates[nodeId]?.status === "succeeded" || state.nodeStates[nodeId]?.status === "failed") {
    return `${nodeId} 已处于终态(${state.nodeStates[nodeId]?.status})，跳过回写`;
  }

  if (node) {
    const contractOutcome = await failNodeOutputContract({
      deps,
      state,
      flowId,
      revision,
      node,
      result,
      executorType: state.nodeStates[nodeId]?.executor ?? resolveExecutorType(node, state.executionMode),
    });
    if (contractOutcome) return `${nodeId} 输出不符合契约，流程已失败`;
  }

  state.nodeStates[nodeId] = {
    ...state.nodeStates[nodeId],
    status: "succeeded",
    completedAt: now(),
    result,
  };
  // ── Approval saveAs: persist callback result into workflowData ──
  if (node && getLegacyApprovalExecutor(node) && (node.executor as { saveAs?: Record<string, string> }).saveAs) {
    const approvalSaveAs = (node.executor as { saveAs?: Record<string, string> }).saveAs!;
    applySaveAs(
      state.workflowData,
      approvalSaveAs,
      result,
      {
        ...buildActionContext(deps, state, workflow, flowId, nodeId),
        input: result,
      },
    );
  }

  // Emit node_succeeded event so node_executions table and flow_runs counters are updated
  const executorType = node?.executor?.type ?? state.nodeStates[nodeId]?.executor ?? "approval";
  const attempt = state.nodeStates[nodeId]?.attempts ?? 1;
  emitNodeEvent("node_succeeded", {
    flowId,
    workflowId: state.workflowId,
    nodeId,
    executorType,
    attempt,
    durationMs: 0, // approval duration not tracked here
    usage: null,
    inputJson: JSON.stringify({ approved: result?.approved, reviewer: result?.reviewer, source: result?.source }),
    outputJson: result ? JSON.stringify(result) : null,
    sessionKey: deps.sessionKey,
    sessionId: deps.sessionId,
    embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, nodeId, flowId),
    systemContext: { reason: "bcs_approval_callback" },
  });

  appendAuditLog(state, nodeId, "bcs-callback", `${node?.title ?? nodeId} 审批完成`);
  appendFlowEvent(state, {
    type: "collaboration_result_received",
    flowId,
    workflowId: state.workflowId,
    nodeId,
    data: summarizeBcsApprovalResult(result),
  });

  if (node) {
    const hookOutcome = await runNodeSuccessHooks({
      deps,
      workflow: effectiveWorkflow,
      state,
      flowId,
      revision,
      node,
    });

    if (hookOutcome.blocked) return `${nodeId} 后置动作失败，流程已阻塞`;
    revision = hookOutcome.revision;
  }

  effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
  applyPhaseAndStatus(effectiveWorkflow, state);

  // ── Compensate stale approval nodes ──
  // After this callback resolved the current node to "succeeded", other
  // approval-type nodes may still be stuck in "waiting"/"running" due to
  // the asyncAwareExecuteLoop race (callback writes to TaskFlow but the
  // in-flight async engine skips executeLoop). Scan and reconcile them
  // before deciding whether to resume or keep waiting.
  const compensatedNodes = reconcileStaleApprovalNodes(state, workflow, flowId, nodeId);
  if (compensatedNodes.length > 0) {
    effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
    applyPhaseAndStatus(effectiveWorkflow, state);
  }

  const readyNodes = getReadyNodes(effectiveWorkflow, state.nodeStates);
  const waitingApprovals = Object.entries(state.nodeStates)
    .filter(([, ns]) => ns.status === "waiting" && (ns.executor === "bcs-route" || ns.executor === "subagent"))
    .map(([nId]) => nId);
  const shouldResume = readyNodes.length > 0 || waitingApprovals.length === 0;

  if (shouldResume) {
    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: "running",
      currentStep: nodeId,
      stateJson: JSON.stringify(state),
    });

    if (!resumeResult.applied) throw new Error("revision 冲突，请重试");

    const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;

    await deps.chatInject(
      `已收到 ${node?.title ?? nodeId} 审批结果`,
      `${flowId}:${nodeId}:bcs-callback`,
    );

    // Sync flow_runs.status back to "running" after resuming from waiting.
    syncFlowRunPhase(flowId, state.currentPhase, "running");
    await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);
  } else {
    const receivedApprovals = Object.entries(state.nodeStates)
      .filter(([, ns]) => ns.status === "succeeded" && (ns.executor === "bcs-route" || ns.executor === "subagent"))
      .map(([nId]) => nId);

    const totalApprovals = waitingApprovals.length + receivedApprovals.length;

    const waitState: WaitState = {
      kind: "platform-workflow",
      workflowId: state.workflowId,
      params: state.params,
      activeNodes: waitingApprovals,
      waitingFor: "bcs-route-responses",
      received: receivedApprovals,
      pending: waitingApprovals,
      hint: `已收到 ${receivedApprovals.length}/${totalApprovals} 审批回复，等待 ${waitingApprovals.length} 个`,
      userAction: "/workflow inspect 查看进度",
    };

    const waitingResult = await deps.boundTaskFlow.setWaiting({
      flowId,
      expectedRevision: revision,
      currentStep: nodeId,
      stateJson: JSON.stringify(state),
      waitJson: JSON.stringify(waitState),
      blockedSummary: `等待审批: ${waitingApprovals.join(", ")}`,
    });

    if (!waitingResult.applied) throw new Error("状态更新冲突，请重试");

    await deps.chatInject(
      `已收到 ${node?.title ?? nodeId} 审批结果 (${receivedApprovals.length}/${totalApprovals})`,
      `${flowId}:${nodeId}:partial-approval`,
    );
  }

  return `BCS 回调处理完成: ${nodeId}`;
}

/**
 * Handle an async-callback HTTP callback.
 *
 * Validates the callback token, marks the node as succeeded (or failed if the
 * callback payload indicates failure), and resumes the workflow.
 *
 * This is the Controller-level bridge between the HTTP callback API and the
 * workflow execution loop.
 */
export async function handleAsyncCallback(
  deps: ControllerDeps,
  flowId: string,
  nodeId: string,
  callbackToken: string,
  result: Record<string, unknown>,
  userId?: string,
): Promise<string> {
  const flow = await deps.boundTaskFlow.get(flowId);
  if (!flow) throw new Error(`Flow ${flowId} not found`);

  let revision = flow.revision as number;
  const state = parseFlowState(flow);

  // Validate node is in waiting state and executor type matches
  const nodeState = state.nodeStates[nodeId];
  if (!nodeState) throw new Error(`Node ${nodeId} not found in flow ${flowId}`);
  if (nodeState.status !== "waiting") {
    return `${nodeId} 不在等待状态(当前: ${nodeState.status})，跳过回调`;
  }
  if (nodeState.executor !== "async-callback") {
    return `${nodeId} 不是 async-callback 节点(当前: ${nodeState.executor})，跳过回调`;
  }

  // Validate callback token matches the one stored on the node
  if (nodeState.callbackToken && nodeState.callbackToken !== callbackToken) {
    return `回调 token 不匹配，拒绝回调`;
  }

  const workflow = await loadWorkflowForState(deps, state);
  let effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
  const node = effectiveWorkflow.nodes.find((n) => n.id === nodeId);

  // Determine success/failure from the callback payload
  const callbackStatus: "succeeded" | "failed" =
    (result.status as string) === "failed" ? "failed" : "succeeded";
  // Strip the meta `status` field from the result data
  const { status: _s, ...resultData } = result;
  const nodeResult = resultData as Record<string, unknown>;

  // Check output contract if node succeeded
  if (node && callbackStatus === "succeeded") {
    const contractOutcome = await failNodeOutputContract({
      deps,
      state,
      flowId,
      revision,
      node,
      result: nodeResult,
      executorType: "async-callback",
    });
    if (contractOutcome) return `${nodeId} 输出不符合契约，流程已失败`;
  }

  // Update node state
  state.nodeStates[nodeId] = {
    ...state.nodeStates[nodeId],
    status: callbackStatus,
    completedAt: now(),
    result: nodeResult,
    ...(callbackStatus === "failed" ? { error: (nodeResult.error as string) ?? "回调返回失败" } : {}),
  };

  // Apply saveAs from the executor config
  if (node && node.executor.type === "async-callback" && node.executor.saveAs) {
    applySaveAs(
      state.workflowData,
      node.executor.saveAs,
      nodeResult,
      {
        ...buildActionContext(deps, state, workflow, flowId, nodeId),
        input: nodeResult,
      },
    );
  }

  // Emit node event
  const attempt = nodeState.attempts ?? 1;
  emitNodeEvent(callbackStatus === "succeeded" ? "node_succeeded" : "node_failed", {
    flowId,
    workflowId: state.workflowId,
    nodeId,
    executorType: "async-callback",
    attempt,
    durationMs: 0,
    usage: null,
    inputJson: JSON.stringify({ callbackToken, userId }),
    outputJson: nodeResult ? JSON.stringify(nodeResult) : null,
    sessionKey: deps.sessionKey,
    sessionId: deps.sessionId,
    embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, nodeId, flowId),
    systemContext: { reason: "async_callback", callbackToken, callbackUserId: userId ?? null },
  });

  appendAuditLog(state, nodeId, "async-callback", `${node?.title ?? nodeId} 异步回调完成 (${callbackStatus})`);
  appendFlowEvent(state, {
    type: callbackStatus === "succeeded" ? "node_succeeded" : "node_failed",
    flowId,
    workflowId: state.workflowId,
    nodeId,
    data: { source: "async-callback", callbackToken, userId: userId ?? null, status: callbackStatus },
  });

  // Run node success hooks
  if (node && callbackStatus === "succeeded") {
    const hookOutcome = await runNodeSuccessHooks({
      deps,
      workflow: effectiveWorkflow,
      state,
      flowId,
      revision,
      node,
    });
    if (hookOutcome.blocked) return `${nodeId} 后置动作失败，流程已阻塞`;
    revision = hookOutcome.revision;
  }

  // Determine if workflow should resume
  effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
  applyPhaseAndStatus(effectiveWorkflow, state);

  const readyNodes = getReadyNodes(effectiveWorkflow, state.nodeStates);
  const shouldResume = readyNodes.length > 0 || isWorkflowComplete(effectiveWorkflow, state.nodeStates);

  if (shouldResume) {
    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: "running",
      currentStep: nodeId,
      stateJson: JSON.stringify(state),
    });

    if (!resumeResult.applied) throw new Error("revision 冲突，请重试");

    const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;

    await deps.chatInject(
      `已收到 ${node?.title ?? nodeId} 异步回调结果 (${callbackStatus})`,
      `${flowId}:${nodeId}:async-callback`,
    );

    // Sync flow_runs.status back to "running" after resuming from waiting.
    syncFlowRunPhase(flowId, state.currentPhase, "running");
    await asyncAwareExecuteLoop(deps, workflow, state, flowId, newRevision);
  } else {
    // Still have other waiting nodes — stay in waiting state
    const waitState: WaitState = {
      kind: "platform-workflow",
      workflowId: state.workflowId,
      params: state.params,
      activeNodes: Object.entries(state.nodeStates)
        .filter(([, ns]) => ns.status === "waiting")
        .map(([nId]) => nId),
      waitingFor: "async-callback-responses",
      received: [nodeId],
      pending: Object.entries(state.nodeStates)
        .filter(([, ns]) => ns.status === "waiting")
        .map(([nId]) => nId),
      hint: `${node?.title ?? nodeId} 回调已完成，其他节点仍在等待`,
      userAction: "/workflow inspect 查看进度",
    };

    const waitingResult = await deps.boundTaskFlow.setWaiting({
      flowId,
      expectedRevision: revision,
      currentStep: nodeId,
      stateJson: JSON.stringify(state),
      waitJson: JSON.stringify(waitState),
      blockedSummary: `等待其他节点回调`,
    });

    if (!waitingResult.applied) throw new Error("状态更新冲突，请重试");

    await deps.chatInject(
      `已收到 ${node?.title ?? nodeId} 异步回调结果 (${callbackStatus})`,
      `${flowId}:${nodeId}:async-callback-partial`,
    );
  }

  return `异步回调处理完成: ${nodeId} (${callbackStatus})`;
}

function stableJson(value: unknown): string {
  return JSON.stringify(sortJsonValue(value));
}

function sortJsonValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(sortJsonValue);
  if (!value || typeof value !== "object") return value;
  const record = value as Record<string, unknown>;
  return Object.fromEntries(
    Object.keys(record)
      .sort()
      .map((key) => [key, sortJsonValue(record[key])]),
  );
}

const BCS_COLLABORATION_WAIT_KINDS = new Set([
  "bcs-route",
  "bcs-route-responses",
  "bcs-collaboration",
]);

function isBcsCollaborationCallbackNode(node: WorkflowNode): boolean {
  return node.executor.type === "collaboration"
    || getLegacyApprovalExecutor(node) !== undefined
    || getLegacyExecutorType(node) === "bcs-route";
}

function isNodeStateWaitingForBcsCollaboration(nodeState: NodeState): boolean {
  return nodeState.status === "waiting"
    && (
      nodeState.bcsApproval !== undefined
      || nodeState.executor === "bcs-route"
      || (nodeState.waitKind !== undefined && BCS_COLLABORATION_WAIT_KINDS.has(nodeState.waitKind))
    );
}

function doesBcsCollaborationTaskMatch(nodeState: NodeState, taskId: string | undefined): boolean {
  if (!taskId) return true;
  const expectedTaskId = nodeState.bcsApproval?.taskId;
  return expectedTaskId === taskId;
}

function isValidBcsCollaborationCallbackTarget(
  workflow: WorkflowSpec,
  state: FlowState,
  nodeId: string,
  taskId: string | undefined,
): boolean {
  const node = workflow.nodes.find((item) => item.id === nodeId);
  if (!node || !isBcsCollaborationCallbackNode(node)) return false;
  const nodeState = state.nodeStates[nodeId];
  if (!nodeState || !isNodeStateWaitingForBcsCollaboration(nodeState)) return false;
  return doesBcsCollaborationTaskMatch(nodeState, taskId);
}

function findNodeByBcsCollaboration(
  workflow: WorkflowSpec,
  state: FlowState,
  message: BcsCollaborationMessage,
): string | undefined {
  const taskId = "taskId" in message ? message.taskId : undefined;

  const messageNodeId = "nodeId" in message ? message.nodeId : undefined;
  if (messageNodeId) {
    if (isValidBcsCollaborationCallbackTarget(workflow, state, messageNodeId, taskId)) {
      return messageNodeId;
    }
    return undefined;
  }

  if (taskId) {
    const found = Object.entries(state.nodeStates).find(([id, nodeState]) =>
      nodeState.bcsApproval?.taskId === taskId
        && isValidBcsCollaborationCallbackTarget(workflow, state, id, taskId),
    );
    if (found) return found[0];
  }

  return undefined;
}

function isCompletedBcsCollaborationCallbackTarget(
  workflow: WorkflowSpec,
  state: FlowState,
  nodeId: string,
  taskId: string | undefined,
): boolean {
  const node = workflow.nodes.find((item) => item.id === nodeId);
  if (!node || !isBcsCollaborationCallbackNode(node)) return false;
  const nodeState = state.nodeStates[nodeId];
  if (!nodeState || nodeState.status !== "succeeded") return false;
  return doesBcsCollaborationTaskMatch(nodeState, taskId);
}

function findCompletedNodeByBcsCollaboration(
  workflow: WorkflowSpec,
  state: FlowState,
  message: BcsCollaborationMessage,
): string | undefined {
  const taskId = "taskId" in message ? message.taskId : undefined;
  const messageNodeId = "nodeId" in message ? message.nodeId : undefined;

  if (messageNodeId) {
    return isCompletedBcsCollaborationCallbackTarget(workflow, state, messageNodeId, taskId)
      ? messageNodeId
      : undefined;
  }

  if (taskId) {
    const found = Object.entries(state.nodeStates).find(([id, nodeState]) =>
      nodeState.bcsApproval?.taskId === taskId
        && isCompletedBcsCollaborationCallbackTarget(workflow, state, id, taskId),
    );
    if (found) return found[0];
  }

  return undefined;
}

function isSupportedBcsCollaborationProtocol(protocolVersion: string): boolean {
  return protocolVersion === WORKFLOW_COLLABORATION_PROTOCOL_VERSION;
}

function validateBcsCollaborationMessageScope(params: {
  workflow: WorkflowSpec;
  state: FlowState;
  flow: Record<string, unknown>;
  flowId: string;
  message: BcsCollaborationMessage;
  deps: ControllerDeps;
}): void {
  const actualFlowId = readFlowId(params.flow);
  if (actualFlowId && actualFlowId !== params.flowId) {
    throw new Error(`BCS 协作回包 flowId 不匹配: ${params.flowId} (expected ${actualFlowId})`);
  }

  if (params.message.workflowId !== params.state.workflowId) {
    throw new Error(`BCS 协作回包 workflowId 不匹配: ${params.message.workflowId} (expected ${params.state.workflowId})`);
  }
}

function resultPayloadFromCollaborationMessage(message: BcsCollaborationResultMessage): Record<string, unknown> {
  if (message.result && typeof message.result === "object" && !Array.isArray(message.result)) {
    return message.result as Record<string, unknown>;
  }
  return message.result === undefined ? {} : { result: message.result };
}

export async function handleBcsCollaborationMessage(
  deps: ControllerDeps,
  message: BcsCollaborationMessage,
): Promise<string> {
  if (!isSupportedBcsCollaborationProtocol(message.protocolVersion)) {
    return "非 workflow BCS 协作协议消息，跳过";
  }

  const flowId = message.flowId;
  if (!flowId) throw new Error("BCS 协作回包缺少 flowId");

  const flow = await deps.boundTaskFlow.get(flowId);
  if (!flow) throw new Error(`Flow ${flowId} not found`);

  let revision = flow.revision as number;
  const state = parseFlowState(flow);
  const workflow = await loadWorkflowForState(deps, state);
  let effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

  validateBcsCollaborationMessageScope({ workflow, state, flow, flowId, message, deps });

  const nodeId = findNodeByBcsCollaboration(effectiveWorkflow, state, message);
  const taskId = "taskId" in message ? message.taskId : undefined;
  if (!nodeId) {
    if (message.messageType === "collaboration_result" && message.status === "succeeded") {
      const completedNodeId = findCompletedNodeByBcsCollaboration(effectiveWorkflow, state, message);
      if (completedNodeId) {
        const resultPayload = resultPayloadFromCollaborationMessage(message);
        if (stableJson(state.nodeStates[completedNodeId]?.result ?? {}) === stableJson(resultPayload)) {
          return `${completedNodeId} 已完成，跳过重复回写`;
        }
      }
    }

    throw new Error(
      `未找到 BCS 协作节点: taskId=${taskId ?? "unknown"}, nodeId=${message.nodeId ?? "unknown"}`,
    );
  }

  const node = effectiveWorkflow.nodes.find((item) => item.id === nodeId);
  if (!node) throw new Error(`Workflow node ${nodeId} not found`);

  const nodeState = state.nodeStates[nodeId];
  if (!nodeState) throw new Error(`Flow state missing node ${nodeId}`);

  if (message.messageType === "collaboration_result" && message.status === "succeeded") {
    const resultPayload = resultPayloadFromCollaborationMessage(message);
    if (nodeState.status === "succeeded") {
      if (stableJson(nodeState.result ?? {}) === stableJson(resultPayload)) {
        return `${nodeId} 已完成，跳过重复回写`;
      }

      state.nodeStates[nodeId] = {
        ...nodeState,
        status: "blocked",
        error: `协作结果冲突: ${message.taskId ?? nodeId}`,
      };
      applyPhaseAndStatus(effectiveWorkflow, state);
      const recoveryHint = formatApprovalRecoveryCommandHint(deps, effectiveWorkflow, state);
      const conflictHint = [
        `协作结果冲突: ${message.taskId ?? nodeId}`,
        recoveryHint,
      ].filter(Boolean).join("\n");
      appendFlowEvent(state, {
        type: "collaboration_result_rejected",
        flowId,
        workflowId: state.workflowId,
        nodeId,
        data: { taskId: message.taskId, reason: "conflicting-result" },
        error: `协作结果冲突: ${message.taskId ?? nodeId}`,
      });
      await blockFlow(
        deps,
        flowId,
        revision,
        state,
        {
          kind: "platform-workflow",
          workflowId: state.workflowId,
          params: state.params,
          activeNodes: state.activeNodes,
          waitingFor: "bcs-route-responses",
          hint: conflictHint,
          userAction: recoveryHint ?? `${formatWorkflowCommand(deps, state.workflowId, "inspect", [flowId])} 查看最新状态`,
        },
        `协作结果冲突: ${message.taskId ?? nodeId}`,
        nodeId,
      );
      return `${nodeId} 协作结果冲突，流程已阻塞`;
    }

    const contractOutcome = await failNodeOutputContract({
      deps,
      state,
      flowId,
      revision,
      node,
      result: resultPayload,
      executorType: nodeState.executor ?? resolveExecutorType(node, state.executionMode),
    });
    if (contractOutcome) return `${nodeId} 输出不符合契约，流程已失败`;

    // 审批策略检查：all/majority 模式下需要等待多人审批
    const approvalExecutor = getLegacyApprovalExecutor(node);
    const approvalPolicy = approvalExecutor?.approvalPolicy ?? "any";
    if (approvalPolicy !== "any" && approvalExecutor?.approvers && approvalExecutor.approvers.length > 0) {
      const existingResults = collectApprovalResults(state, nodeId);
      const approved = resultPayload.approved === true;
      const senderId = (resultPayload.reviewerId as string) ?? (resultPayload.reviewerBot as string) ?? "unknown";
      existingResults.push({ senderId, approved });

      const policyResult = evaluateApprovalPolicy({
        policy: approvalPolicy,
        approvers: approvalExecutor.approvers,
        approvedResults: existingResults,
      });

      if (!policyResult.passed) {
        // 未达通过阈值，记录部分审批但保持 waiting
        appendAuditLog(state, nodeId, "partial-approval", policyResult.reason);
        appendFlowEvent(state, {
          type: "collaboration_result_received",
          flowId,
          workflowId: state.workflowId,
          nodeId,
          data: { ...summarizeBcsApprovalResult(resultPayload), partialApproval: true, policyResult },
        });

        const partialWaitState: WaitState = {
          kind: "platform-workflow",
          workflowId: state.workflowId,
          params: state.params,
          activeNodes: [nodeId],
          waitingFor: "bcs-route-responses",
          received: existingResults.filter((r) => r.approved).map((_, i) => `${nodeId}:partial-${i}`),
          pending: approvalExecutor.approvers.filter((a) => !existingResults.some((r) => r.senderId === a.empId)).map((a) => a.empId),
          hint: policyResult.reason,
          userAction: "/workflow inspect 查看进度",
        };

        const waitingResult = await deps.boundTaskFlow.setWaiting({
          flowId,
          expectedRevision: revision,
          currentStep: nodeId,
          stateJson: JSON.stringify(state),
          waitJson: JSON.stringify(partialWaitState),
          blockedSummary: policyResult.reason,
        });

        if (!waitingResult.applied) throw new Error("状态更新冲突，请重试");

        await deps.chatInject(
          `${node.title} 部分审批结果: ${policyResult.reason}`,
          `${flowId}:${nodeId}:partial-approval`,
        );

        return `${nodeId} 部分审批: ${policyResult.reason}`;
      }
    }

    state.nodeStates[nodeId] = {
      ...nodeState,
      status: "succeeded",
      completedAt: now(),
      result: resultPayload,
      error: null,
    };
    appendAuditLog(state, nodeId, "bcs-collaboration-result", `${node.title} 协作完成`);

    // ── Approval saveAs: persist callback result into workflowData ──
    // BUG-1 fix: handleBcsCollaborationMessage was missing applySaveAs,
    // causing approval node saveAs rules to never execute when the callback
    // comes through the BCS collaboration path. Mirror handleBcsCallback.
    if (node && getLegacyApprovalExecutor(node) && (node.executor as { saveAs?: Record<string, string> }).saveAs) {
      const approvalSaveAs = (node.executor as { saveAs?: Record<string, string> }).saveAs!;
      applySaveAs(
        state.workflowData,
        approvalSaveAs,
        resultPayload,
        {
          ...buildActionContext(deps, state, workflow, flowId, nodeId),
          input: resultPayload,
        },
      );
      console.log(`[handleBcsCollaborationMessage] applySaveAs for node=${nodeId}`, {
        keys: Object.keys(approvalSaveAs),
        approved: resultPayload?.approved,
      });
    }

    // ── Emit node_succeeded event (mirror handleBcsCallback) ──
    // BUG-1 fix: handleBcsCollaborationMessage was missing emitNodeEvent("node_succeeded"),
    // causing node_executions table and flow_runs counters to not be updated.
    const collaborationExecutorType = node?.executor?.type ?? nodeState.executor ?? "approval";
    const collaborationAttempt = nodeState.attempts ?? 1;
    emitNodeEvent("node_succeeded", {
      flowId,
      workflowId: state.workflowId,
      nodeId,
      executorType: collaborationExecutorType,
      attempt: collaborationAttempt,
      durationMs: 0,
      usage: null,
      inputJson: JSON.stringify({ approved: resultPayload?.approved, reviewer: resultPayload?.reviewer, source: resultPayload?.source }),
      outputJson: resultPayload ? JSON.stringify(resultPayload) : null,
      sessionKey: deps.sessionKey,
      sessionId: deps.sessionId,
      embeddedSessionKey: deriveEmbeddedSessionKey(deps.sessionKey, nodeId, flowId),
      systemContext: { reason: "bcs_collaboration_callback" },
    });

    appendFlowEvent(state, {
      type: "collaboration_result_received",
      flowId,
      workflowId: state.workflowId,
      nodeId,
      data: summarizeBcsApprovalResult(resultPayload),
    });

    const hookOutcome = await runNodeSuccessHooks({
      deps,
      workflow: effectiveWorkflow,
      state,
      flowId,
      revision,
      node,
    });
    if (hookOutcome.blocked) {
      return `${nodeId} 协作结果已写入，但后置动作失败，流程已阻塞`;
    }
    revision = hookOutcome.revision;
  } else {
    const errorMessage = message.messageType === "collaboration_error"
      ? `${message.errorCode}: ${message.errorMessage}`
      : `collaboration_${message.status}: ${message.status === "failed" ? "协作任务失败" : "协作任务拒绝"}`;
    state.nodeStates[nodeId] = {
      ...nodeState,
      status: "blocked",
      completedAt: now(),
      error: errorMessage,
    };
    finalizeLoopAfterRuntimeNodeBlocked(workflow, state, flowId, nodeId);
    applyPhaseAndStatus(effectiveWorkflow, state);
    appendAuditLog(state, nodeId, "bcs-collaboration-error", `${node.title} 协作回包未通过`);
    appendFlowEvent(state, {
      type: "collaboration_result_rejected",
      flowId,
      workflowId: state.workflowId,
      nodeId,
      data: message.messageType === "collaboration_error"
        ? { errorCode: message.errorCode, taskId: message.taskId }
        : { status: message.status, taskId: message.taskId },
      error: message.messageType === "collaboration_error" ? message.errorMessage : errorMessage,
    });
    const recoveryHint = formatApprovalRecoveryCommandHint(deps, effectiveWorkflow, state);
    const errorHint = [
      `${node.title} 协作回包未通过: ${errorMessage}`,
      recoveryHint,
    ].filter(Boolean).join("\n");
    await blockFlow(
      deps,
      flowId,
      revision,
      state,
      {
        kind: "platform-workflow",
        workflowId: state.workflowId,
        params: state.params,
        activeNodes: state.activeNodes,
        waitingFor: "bcs-route-responses",
        hint: errorHint,
          userAction: recoveryHint ?? `${formatWorkflowCommand(deps, state.workflowId, "inspect", [flowId])} 查看最新状态`,
        },
      `${node.title} 协作回包未通过: ${errorMessage}`,
      nodeId,
    );
    return `${nodeId} 协作回包未通过，流程已阻塞`;
  }

  effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
  applyPhaseAndStatus(effectiveWorkflow, state);

  // ── Compensate stale approval nodes ──
  // Same rationale as handleBcsCallback: reconcile approval-type nodes
  // that are still in a non-terminal state after this callback resolved
  // the current node.
  const compensatedNodes = reconcileStaleApprovalNodes(state, workflow, flowId, nodeId);
  if (compensatedNodes.length > 0) {
    effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
    applyPhaseAndStatus(effectiveWorkflow, state);
  }

  const readyNodes = getReadyNodes(effectiveWorkflow, state.nodeStates);
  const waitingCollaborations = Object.entries(state.nodeStates)
    .filter(([, ns]) => ns.status === "waiting" && ns.bcsApproval)
    .map(([id]) => id);
  const shouldResume = readyNodes.length > 0 || waitingCollaborations.length === 0;

  if (shouldResume) {
    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: "running",
      currentStep: nodeId,
      stateJson: JSON.stringify(state),
    });

    if (!resumeResult.applied) throw new Error("revision 冲突，请重试");

    revision = (resumeResult.flow as Record<string, unknown>).revision as number;
    await deps.chatInject(`已收到 ${node.title} 协作结果`, `${flowId}:${nodeId}:bcs-collaboration-result`);
    await asyncAwareExecuteLoop(deps, workflow, state, flowId, revision);
  } else {
    const receivedCollaborations = Object.entries(state.nodeStates)
      .filter(([, ns]) => ns.status === "succeeded" && ns.bcsApproval)
      .map(([id]) => id);
    const totalCollaborations = waitingCollaborations.length + receivedCollaborations.length;

    const waitState: WaitState = {
      kind: "platform-workflow",
      workflowId: state.workflowId,
      params: state.params,
      activeNodes: waitingCollaborations,
      waitingFor: "bcs-route-responses",
      received: receivedCollaborations,
      pending: waitingCollaborations,
      hint: `已收到 ${receivedCollaborations.length}/${totalCollaborations} 协作回复，等待 ${waitingCollaborations.length} 个`,
      userAction: "/workflow inspect 查看进度",
    };

    const waitingResult = await deps.boundTaskFlow.setWaiting({
      flowId,
      expectedRevision: revision,
      currentStep: nodeId,
      stateJson: JSON.stringify(state),
      waitJson: JSON.stringify(waitState),
      blockedSummary: `等待协作: ${waitingCollaborations.join(", ")}`,
    });

    if (!waitingResult.applied) throw new Error("状态更新冲突，请重试");

    await deps.chatInject(
      `已收到 ${node.title} 协作结果 (${receivedCollaborations.length}/${totalCollaborations})`,
      `${flowId}:${nodeId}:partial-collaboration`,
    );
  }

  return `BCS 协作回包处理完成: ${nodeId}`;
}

export const handleBcsApprovalMessage = handleBcsCollaborationMessage;

// ── Core Execute Loop ──

function collaborationDeliveryPrimaryToExecutorType(primary: string): string {
  if (primary === "bcs-cli") return "action";
  return primary;
}

export function resolveExecutorType(node: WorkflowNode, executionMode: ExecutionMode): string {
  if (getLegacyApprovalExecutor(node)) {
    // Respect delivery config from YAML for approval nodes
    const executor = getLegacyApprovalExecutor(node);
    let delivery: { primary?: string } | undefined;
    if (executionMode === "private") {
      delivery = executor.delivery?.private;
    } else if (executionMode === "dingtalk-group") {
      delivery = executor.delivery?.dingtalkGroup;
    } else {
      delivery = executor.delivery?.collaboration;
    }
    if (delivery?.primary) {
      return delivery.primary;
    }
    // Defaults when no delivery config specified
    if (executionMode === "private") return "card-dingtalk";
    if (executionMode === "dingtalk-group") return "card-dingtalk";
    return "bcs-route"; // bcs-group
  }
  if (node.executor.type === "collaboration") {
    let delivery: { primary?: string; action?: string } | undefined;
    if (executionMode === "private") {
      delivery = node.executor.delivery?.private;
    } else if (executionMode === "dingtalk-group") {
      delivery = node.executor.delivery?.dingtalkGroup;
    } else {
      delivery = node.executor.delivery?.collaboration;
    }
    const defaultPrimary = executionMode === "private" ? "subagent"
      : executionMode === "dingtalk-group" ? "subagent"
      : "bcs-route";
    const primary = delivery?.primary ?? defaultPrimary;
    return collaborationDeliveryPrimaryToExecutorType(primary);
  }
  return getLegacyExecutorType(node);
}

export function isParallelCandidate(node: WorkflowNode, executionMode: ExecutionMode): boolean {
  const resolvedType = resolveExecutorType(node, executionMode);
  return resolvedType === "subagent" || resolvedType === "bcs-route" || resolvedType === "embedded-agent" || resolvedType === "cli-script";
}

// Guard #1/#4 (LLM burst): cap concurrent embedded-agent executions per
// ready batch. embedded-agent is pure-LLM-driven (unlike subagent), so an
// unbounded same-flow burst of ready embedded-agent nodes triggers 429
// rate-limit cascades faster than serial would. The executor's
// staggered-start only delays the FIRST embedded-agent node per flow — it
// does NOT protect multiple ready embedded-agent nodes fired in one loop
// tick (see src/executors/embedded-agent.ts `staggeredFlowIds`).
export const MAX_PARALLEL_EMBEDDED_AGENTS = 3;

// Cap concurrent cli-script executions per ready batch. Each cli-script
// spawns an OS child process (no shared session/LLM resources), so the risk
// profile is much lower than embedded-agent. The cap is a defense against
// unintended process explosion (e.g. loop-groups generating many cli-script
// nodes) rather than resource contention.
export const MAX_PARALLEL_CLI_SCRIPTS = 5;

/**
 * Guards for running an embedded-agent node inside the parallel batch.
 *
 * Guard #2 (session-file isolation): "inherit" history makes every
 * parallel embedded-agent append to AND compress the SAME shared main
 * session file — a read/write race that corrupts it. Only isolated
 * per-node session files (structured/tail/compacted/isolated) are safe.
 *
 * Guard #3 (lane-key derivation): deriveEmbeddedSessionKey() returns ""
 * when the parent session key is empty, so all parallel embedded-agents
 * would collapse onto the single empty lane and re-serialize (or contend).
 */
export function canRunEmbeddedAgentInParallel(
  node: WorkflowNode,
  workflow: WorkflowSpec,
  executionMode: ExecutionMode,
  sessionKey: string | undefined,
): boolean {
  if (!sessionKey) return false;
  const policy = resolveEffectiveContextPolicy({ workflow, node, executionMode });
  if (policy.history === "inherit") return false;
  return true;
}

function isBcsApprovalBatchApi(api: unknown): api is BcsApprovalBatchApi {
  const runtime = (api as { runtime?: unknown } | undefined)?.runtime;
  const agent = (runtime as { agent?: unknown } | undefined)?.agent;
  return typeof (agent as { runEmbeddedPiAgent?: unknown } | undefined)?.runEmbeddedPiAgent === "function";
}

function isBcsBatchableRouteNode(node: WorkflowNode, executionMode: ExecutionMode): boolean {
  return resolveExecutorType(node, executionMode) === "bcs-route"
    && (Boolean(getLegacyApprovalExecutor(node)) || node.executor.type === "collaboration");
}

type NodeOutcome = {
  action: "continue" | "waiting" | "failed" | "blocked";
  revision: number;
  waitPrompt?: string;
  waitKind?: string;
  waitActions?: HumanGateActions;
  waitCommandHints?: HumanCommandHints;
};

type ExecuteLoopOutcome =
  | { status: "running"; message?: string }
  | { status: "waiting"; nodeIds: string[]; message?: string }
  | { status: "failed"; nodeIds: string[]; message?: string }
  | { status: "blocked"; message?: string }
  | { status: "finished"; message?: string };

function formatWorkflowCommand(
  deps: Pick<ControllerDeps, "formatWorkflowCommand"> | undefined,
  workflowId: string,
  command: string,
  args: string[] = [],
  options: { surface?: WorkflowCommandSurface } = {},
): string {
  const formatted = deps?.formatWorkflowCommand?.(workflowId, command, args, options);
  if (formatted?.trim()) return formatted.trim();
  const needsWorkflowId = new Set(["run", "reopen", "detail", "validate", "cutover-check", "help"]);
  const workflowArgs = needsWorkflowId.has(command) ? [workflowId, ...args] : args;
  return `/workflow ${[command, ...workflowArgs].filter((part) => part.length > 0).join(" ")}`;
}

function workflowCommandSurfaceForState(state: FlowState): WorkflowCommandSurface {
  return state.commandSurface ?? { type: "workflow" };
}

function formatWorkflowCommandForState(
  deps: Pick<ControllerDeps, "formatWorkflowCommand"> | undefined,
  state: FlowState,
  command: string,
  args: string[] = [],
): string {
  return formatWorkflowCommand(deps, state.workflowId, command, args, {
    surface: workflowCommandSurfaceForState(state),
  });
}

function stableHash(value: string): number {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return hash >>> 0;
}

function selectStableVariant(variants: string[], seed: string): string {
  return variants[stableHash(seed) % variants.length];
}

function resolveWorkflowFinishedMessage(
  workflow: WorkflowSpec,
  state: FlowState,
  skillRoot: string,
  flowId: string,
  userIdentity?: Record<string, unknown>,
): string | undefined {
  const messages = workflow.messages;
  if (!messages) return undefined;

  const rareVariants = messages.onFinishedRareVariants ?? [];
  const normalVariants = messages.onFinishedVariants ?? [];
  const rareHit = rareVariants.length > 0 && stableHash(`${flowId}:finished-rare`) % 100 < 7;
  const template = rareHit
    ? selectStableVariant(rareVariants, `${flowId}:finished-rare-pick`)
    : normalVariants.length > 0
      ? selectStableVariant(normalVariants, `${flowId}:finished`)
      : messages.onFinished;

  return template ? resolveTemplate(template, buildTemplateContext(state, skillRoot, {}, { userIdentity: userIdentity ?? {} })) : undefined;
}

export const resolveWorkflowFinishedMessageForTest = resolveWorkflowFinishedMessage;

function resolveAndStoreWorkflowOutputs(workflow: WorkflowSpec, state: FlowState): void {
  if (!workflow.outputs || Object.keys(workflow.outputs).length === 0) return;
  const { values, warnings } = resolveWorkflowOutputs(workflow.outputs, {
    params: state.params,
    input: fallbackFlowInput(state),
    businessStatus: state.businessStatus,
    currentPhase: state.currentPhase,
    workflowData: state.workflowData,
    actionOutputs: state.actionOutputs,
    flowHooks: state.flowHooks,
    nodeOutput: buildNodeOutputContext(state.nodeStates),
  });
  state.workflowData.outputs = values;
  if (warnings.length > 0) state.workflowData.outputWarnings = warnings;
}

function firstFailedNode(workflow: WorkflowSpec, state: FlowState): { id: string; error: string } | undefined {
  const order = new Map(workflow.nodes.map((node, index) => [node.id, index]));
  const failed = Object.entries(state.nodeStates)
    .filter(([, nodeState]) => nodeState.status === "failed")
    .sort(([leftId, left], [rightId, right]) =>
      (left.completedAt ?? Number.MAX_SAFE_INTEGER) - (right.completedAt ?? Number.MAX_SAFE_INTEGER)
      || (order.get(leftId) ?? Number.MAX_SAFE_INTEGER) - (order.get(rightId) ?? Number.MAX_SAFE_INTEGER),
    )[0];
  if (!failed) return undefined;

  const [id, nodeState] = failed;
  const node = workflow.nodes.find((item) => item.id === id);
  return { id, error: `${node?.title ?? id} 失败: ${nodeState.error ?? "执行失败"}` };
}

const FLOW_STATE_CONFLICT_MESSAGE = "状态更新冲突，请重试";

function assertFlowStateUpdateApplied(result: {
  applied?: boolean;
  status?: unknown;
  flow?: Record<string, unknown>;
}): void {
  if (
    result.applied === false
    || result.status === "not_found"
    || result.flow?.status === "not_found"
  ) {
    throw new Error(FLOW_STATE_CONFLICT_MESSAGE);
  }
}

function isFlowStateConflict(error: unknown): boolean {
  if (!(error instanceof Error) || !error.message.startsWith(FLOW_STATE_CONFLICT_MESSAGE)) return false;
  const revisionDetail = error.message.slice(FLOW_STATE_CONFLICT_MESSAGE.length);
  return revisionDetail === "" || /^ \(expected \d+, current \d+\)$/.test(revisionDetail);
}

/**
 * Retry setWaiting on revision conflict by re-reading the latest revision
 * from TaskFlow and retrying once. This prevents the async engine from
 * silently exiting when a concurrent writer (e.g., webhook callback,
 * manual retry) bumps the revision between the last read and setWaiting.
 *
 * Without this, a revision conflict on setWaiting throws "状态更新冲突"
 * which propagates to launchAsyncExecution's crash handler where
 * isFlowStateConflict silently returns — leaving the flow stuck in
 * "running" state until the watchdog reaps it.
 */
async function setWaitingWithRevisionRetry(
  deps: ControllerDeps,
  flowId: string,
  expectedRevision: number,
  currentStep: string,
  state: FlowState,
  waitState: WaitState,
  blockedSummary: string,
): Promise<{ applied: boolean; flow: Record<string, unknown> }> {
  try {
    const setResult = await deps.boundTaskFlow.setWaiting({
      flowId,
      expectedRevision,
      currentStep,
      stateJson: JSON.stringify(state),
      waitJson: JSON.stringify(waitState),
      blockedSummary,
    });
    if (!setResult.applied) {
      const flowStatus = setResult.flow?.status;
      if (flowStatus === "not_found") {
        throw new Error(`Flow ${flowId} not found in TaskFlow (setWaiting)`);
      }
      throw new Error(FLOW_STATE_CONFLICT_MESSAGE);
    }
    return setResult;
  } catch (err) {
    if (!isFlowStateConflict(err)) throw err;

    // Revision conflict — re-read the latest revision from TaskFlow and retry once.
    console.warn(
      `[controller] setWaiting revision conflict for flowId=${flowId} ` +
      `(expectedRevision=${expectedRevision}), re-reading latest revision and retrying`,
    );
    enqueueRunLog({
      flow_id: flowId,
      level: "warn",
      source: "engine",
      message: `setWaiting revision conflict: expectedRevision=${expectedRevision}, retrying`,
      timestamp: Date.now(),
    });
    const latestFlow = await deps.boundTaskFlow.get(flowId);
    if (!latestFlow) {
      throw new Error(`Flow ${flowId} not found during setWaiting revision retry`);
    }
    const latestRevision = typeof latestFlow.revision === "number"
      ? latestFlow.revision
      : Number(latestFlow.revision ?? 0);
    const latestStateJson = latestFlow.stateJson;
    const latestState = latestStateJson ? parseFlowState({ stateJson: latestStateJson, ...latestFlow }) : state;

    // Merge our in-memory node state changes into the latest state so we don't
    // lose the "waiting" status and other mutations from the current loop iteration.
    for (const [nodeId, nodeState] of Object.entries(state.nodeStates)) {
      latestState.nodeStates[nodeId] = nodeState;
    }
    if (state.activeNodes) latestState.activeNodes = state.activeNodes;

    const retryResult = await deps.boundTaskFlow.setWaiting({
      flowId,
      expectedRevision: latestRevision,
      currentStep,
      stateJson: JSON.stringify(latestState),
      waitJson: JSON.stringify(waitState),
      blockedSummary,
    });
    if (!retryResult.applied) {
      const retryFlowStatus = retryResult.flow?.status;
      if (retryFlowStatus === "not_found") {
        throw new Error(`Flow ${flowId} not found in TaskFlow (setWaiting retry)`);
      }
      throw new Error(
        `${FLOW_STATE_CONFLICT_MESSAGE} (expected ${latestRevision})`,
      );
    }
    return retryResult;
  }
}

/** TaskFlow statuses that mark a flow as terminally closed. Any other
 *  status (running/waiting/blocked) means a concurrent writer only bumped
 *  the revision without closing the flow — so the local worker that just
 *  observed a node failure is still the authoritative closer. */
const TERMINAL_FLOW_STATUSES = new Set(["succeeded", "failed", "cancelled", "completed"]);

/**
 * Persist a terminal flow failure with one revision-conflict retry.
 *
 * Mirrors {@link setWaitingWithRevisionRetry}, but for the failure path.
 * When `boundTaskFlow.fail()` hits a revision conflict, we re-read the flow
 * to decide who owns the close:
 *
 * - Flow gone, or already terminally closed by another writer → rethrow the
 *   original conflict (CAS safety: a stale local worker must not close
 *   another writer's flow, and must not overwrite another writer's
 *   completion with a local completeFlowRun).
 * - Flow still non-terminal (e.g. a concurrent setWaiting/resume only
 *   bumped the revision) → THIS worker holds the authoritative node-failure
 *   result. Re-read the latest revision and retry `fail()` once so the flow
 *   is closed and `completeFlowRun` runs, instead of leaving flow_runs
 *   stuck in "running" with the failed node un-reflected.
 *
 * Without this, a non-terminal concurrent write racing the terminal fail()
 * caused `completeFlowRun` to be skipped (rethrow) — so flow_runs.status
 * never transitioned to "failed" and the failed node's completedAt in
 * state_json was never persisted (stale end time). See handleNodeResult /
 * persistFinalizeOutcome / failCompletedWorkflowIfNeeded / parallel fail.
 */
async function failWithRevisionRetry(
  deps: ControllerDeps,
  flowId: string,
  expectedRevision: number,
  state: FlowState,
  params: { status?: string; blockedSummary?: string; endedAt?: number; resultJson?: string } = {},
): Promise<{ applied: boolean; flow: Record<string, unknown> }> {
  const failParams: Record<string, unknown> = {
    flowId,
    expectedRevision,
    status: params.status ?? "failed",
    stateJson: JSON.stringify(state),
  };
  if (params.blockedSummary != null) failParams.blockedSummary = params.blockedSummary;
  if (params.endedAt != null) failParams.endedAt = params.endedAt;
  if (params.resultJson != null) failParams.resultJson = params.resultJson;

  try {
    const failResult = await deps.boundTaskFlow.fail(failParams);
    const record = failResult as { applied?: boolean; flow?: Record<string, unknown> };
    assertFlowStateUpdateApplied(record);
    return { applied: true, flow: record.flow ?? {} };
  } catch (err) {
    if (!isFlowStateConflict(err)) throw err;

    // Revision conflict — re-read the flow to decide if we still own the close.
    console.warn(
      `[controller] failWithRevisionRetry revision conflict for flowId=${flowId} ` +
      `(expectedRevision=${expectedRevision}), re-reading latest revision`,
    );
    enqueueRunLog({
      flow_id: flowId,
      level: "warn",
      source: "engine",
      message: `failWithRevisionRetry revision conflict: expectedRevision=${expectedRevision}, retrying`,
      timestamp: Date.now(),
    });
    let latestFlow: Record<string, unknown> | null;
    try {
      latestFlow = await deps.boundTaskFlow.get(flowId);
    } catch {
      latestFlow = null;
    }
    if (!latestFlow) {
      // Flow is gone → not ours. Preserve CAS: rethrow the original conflict
      // so the caller skips local completion (mirrors pre-fix behavior).
      throw err;
    }
    const latestStatus = typeof latestFlow.status === "string" ? latestFlow.status : "";
    if (TERMINAL_FLOW_STATUSES.has(latestStatus)) {
      // Another writer already terminally closed the flow → don't double-close.
      throw err;
    }
    const latestRevisionRaw = latestFlow.revision;
    const latestRevision = typeof latestRevisionRaw === "number"
      ? latestRevisionRaw
      : Number(latestRevisionRaw ?? 0);

    console.warn(
      `[controller] fail revision conflict for flowId=${flowId} ` +
      `(expectedRevision=${expectedRevision}, latest=${latestRevision}, status=${latestStatus}) ` +
      `— flow still non-terminal, retrying fail() as authoritative closer`,
    );

    const retryResult = await deps.boundTaskFlow.fail({ ...failParams, expectedRevision: latestRevision });
    const retryRecord = retryResult as { applied?: boolean; flow?: Record<string, unknown> };
    assertFlowStateUpdateApplied(retryRecord);
    return { applied: true, flow: retryRecord.flow ?? {} };
  }
}

async function persistSuccessfulFlowFinish(params: {
  deps: ControllerDeps;
  state: FlowState;
  flowId: string;
  revision: number;
}): Promise<void> {
  try {
    const finishResult = await params.deps.boundTaskFlow.finish({
      flowId: params.flowId,
      expectedRevision: params.revision,
      stateJson: JSON.stringify(params.state),
      endedAt: now(),
    });
    assertFlowStateUpdateApplied(finishResult as { applied?: boolean });
  } catch (finishErr) {
    if (isFlowStateConflict(finishErr)) throw finishErr;
    console.error(`[controller] workflow finish persistence threw for flowId=${params.flowId}:`, finishErr);
  }
}

async function failCompletedWorkflowIfNeeded(params: {
  deps: ControllerDeps;
  workflow: WorkflowSpec;
  state: FlowState;
  flowId: string;
  revision: number;
}): Promise<boolean> {
  const failed = firstFailedNode(params.workflow, params.state);
  if (!failed) return false;

  resolveAndStoreWorkflowOutputs(params.workflow, params.state);
  appendAuditLog(params.state, "-", "flow-failed", failed.error);
  try {
    await failWithRevisionRetry(params.deps, params.flowId, params.revision, params.state, {
      blockedSummary: failed.error,
      endedAt: now(),
    });
  } catch (failErr) {
    if (isFlowStateConflict(failErr)) throw failErr;
    const errMsg = failErr instanceof Error ? failErr.message : String(failErr);
    console.error(`[controller] completed workflow failure persistence threw for flowId=${params.flowId}:`, failErr);
    enqueueRunLog({
      flow_id: params.flowId,
      level: "error",
      source: "engine",
      message: `boundTaskFlow.fail() failed in failCompletedWorkflowIfNeeded: ${errMsg}`,
      timestamp: Date.now(),
    });
  }
  console.log(`[controller] FLOW_FAILED flowId=${params.flowId} reason=completed_workflow_failed node=${failed.id} error=${failed.error.slice(0, 100)}`);
  completeFlowRun(params.flowId, "failed", params.state.currentPhase, failed.error, computeDurationMs(params.state), params.state);
  return true;
}

function approvalDisplayName(node: WorkflowNode): string {
  const executor = getLegacyApprovalExecutor(node);
  const routeTarget = executor?.route?.to?.find((target) =>
    (target.type === "name" || target.type === "bot") && typeof target.value === "string" && target.value.trim(),
  );
  return routeTarget && "value" in routeTarget ? routeTarget.value : `${node.title} Bot`;
}

function formatApprovalNodeResultMessage(params: {
  deps: Pick<ControllerDeps, "formatWorkflowCommand">;
  node: WorkflowNode;
  result: ExecutorResult;
  flowId: string;
  workflowId: string;
}): string {
  const { deps, node, result, flowId, workflowId } = params;
  const payload = result.result ?? {};
  const approved = payload.approved === true;
  const note = typeof payload.note === "string" && payload.note.trim()
    ? payload.note.trim()
    : (result.error ?? "未返回审批结论");
  const childSessionKey = typeof payload.childSessionKey === "string" && payload.childSessionKey.trim()
    ? payload.childSessionKey.trim()
    : undefined;
  const passed = result.status === "succeeded" && approved;
  const lines = [
    `${passed ? "✅" : "❌"} ${node.title} ${passed ? "通过" : "失败"}`,
    "",
    `审批 Bot：${approvalDisplayName(node)}`,
    passed ? `结论：${note}` : `原因：${note}`,
  ];

  if (childSessionKey) lines.push(`子会话：${childSessionKey}`);
  if (!passed) {
    lines.push(
      "",
      "可复制恢复命令：",
      formatWorkflowCommand(deps, workflowId, "retry", ["--node", node.id]),
      formatWorkflowCommand(deps, workflowId, "submit", ["--node", node.id, "<人工确认通过原因>"]),
      formatWorkflowCommand(deps, workflowId, "revise", ["--node", node.id, "<审批意见>"]),
      formatWorkflowCommand(deps, workflowId, "inspect", [flowId]),
    );
  }

  return lines.join("\n");
}

function formatApprovalRecoveryCommandHint(
  deps: Pick<ControllerDeps, "formatWorkflowCommand">,
  workflow: WorkflowSpec,
  state: FlowState,
): string | undefined {
  const approvalNodes = findRecoverableApprovalNodes(workflow, state);
  if (approvalNodes.length === 0) return undefined;

  if (approvalNodes.length === 1) {
    const nodeId = approvalNodes[0].id;
    return [
      "可复制审批恢复命令：",
      formatWorkflowCommand(deps, state.workflowId, "retry", ["--node", nodeId]),
      formatWorkflowCommand(deps, state.workflowId, "submit", ["--node", nodeId, "<人工确认通过原因>"]),
      formatWorkflowCommand(deps, state.workflowId, "revise", ["<审批意见>"]),
    ].join("\n");
  }

  const candidates = approvalNodes
    .map((node) => `- ${node.id} (${node.title || node.id})`)
    .join("\n");
  return [
    "可恢复审批节点：",
    candidates,
    "可复制审批恢复命令：",
    formatWorkflowCommand(deps, state.workflowId, "retry", ["--node", "<approvalNodeId>"]),
    formatWorkflowCommand(deps, state.workflowId, "submit", ["--node", "<approvalNodeId>", "<人工确认通过原因>"]),
    formatWorkflowCommand(deps, state.workflowId, "revise", ["--node", "<approvalNodeId>", "<审批意见>"]),
  ].join("\n");
}

function formatParallelFailureHint(
  deps: Pick<ControllerDeps, "formatWorkflowCommand">,
  failedNodes: WorkflowNode[],
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
): string {
  const workflowId = state.workflowId;
  const failedTitles = failedNodes.map((node) => node.title || node.id);
  const baseHint = `并行任务执行失败：${failedTitles.join("、")}。可使用 ${formatWorkflowCommand(deps, workflowId, "inspect", [flowId])} 查看详情`;
  if (!failedNodes.some((node) => getLegacyApprovalExecutor(node))) return baseHint;

  const recoveryHint = formatApprovalRecoveryCommandHint(deps, workflow, state);
  return recoveryHint ? `${baseHint}\n${recoveryHint}` : baseHint;
}

function promptIncludesCommand(prompt: string, commandText: string): boolean {
  const commandPrefix = commandText.trim().split(/\s+/).slice(0, 2).join(" ");
  if (!commandPrefix) return false;
  return new RegExp(`(^|\\s)${escapeRegExp(commandPrefix)}(?=\\s|$)`).test(prompt);
}

type HumanGateActionName = "confirm" | "reject" | "revise";

const DEFAULT_HUMAN_COMMAND_ARGS: Record<HumanGateActionName, string[]> = {
  confirm: ["<备注>"],
  reject: ["<理由>"],
  revise: ["<补充信息>"],
};

function enabledHumanGateActionNames(actions?: HumanGateActions): HumanGateActionName[] {
  if (!actions) return ["confirm", "reject"];
  return (["confirm", "reject", "revise"] as HumanGateActionName[]).filter((action) => Boolean(actions[action]));
}

function formatHumanWaitPrompt(
  deps: Pick<ControllerDeps, "formatWorkflowCommand">,
  prompt: string,
  state: FlowState,
  _waitKind?: string,
  actions?: HumanGateActions,
  commandHints?: HumanCommandHints,
  inputSchema?: { type?: string; required?: string[]; properties?: Record<string, any> },
): string {
  const commands = enabledHumanGateActionNames(actions).map((command) => {
    const hint = commandHints?.[command];
    const args = hint?.args ?? DEFAULT_HUMAN_COMMAND_ARGS[command];
    return {
      command,
      label: hint?.label,
      text: formatWorkflowCommandForState(deps, state, command, args),
    };
  });
  const missingCommands = commands.filter(({ text }) => !prompt.includes(text) && !promptIncludesCommand(prompt, text));

  // R4: Use structured waitPrompt template when the node has choice fields
  const choiceField = inputSchema?.properties
    ? Object.values(inputSchema.properties).find(
        (f: any) => f?.type === "string" && Array.isArray(f?.enum) && f.enum.length > 0,
      ) as any
    : undefined;

  if (choiceField && missingCommands.length > 0) {
    const choices: WaitPromptChoice[] = choiceField.enum.map((val: string) => ({
      label: val,
      command: commands.find((c) => c.command === "confirm")?.text ?? `/workflow confirm choice: ${val}`,
    }));
    const rejectCmd = commands.find((c) => c.command === "reject")?.text;
    return renderStructuredWaitPrompt({
      workflowName: state.workflowId,
      nodeTitle: prompt.split("\n")[0].slice(0, 50),
      choices,
      rejectCommand: rejectCmd,
    });
  }

  // Fallback: original plain-text format
  if (missingCommands.length === 0) return prompt;

  return `${prompt}\n\n可执行命令：\n${missingCommands.map(({ text, label }) => (
    label ? `- \`${text}\`：${label}。` : `- ${text}`
  )).join("\n")}`;
}

function formatHumanGateUserAction(
  deps: Pick<ControllerDeps, "formatWorkflowCommand">,
  state: FlowState,
  _waitKind?: string,
  actions?: HumanGateActions,
  commandHints?: HumanCommandHints,
): string {
  const commands = enabledHumanGateActionNames(actions).map((command) => {
    const hint = commandHints?.[command];
    const args = hint?.args ?? DEFAULT_HUMAN_COMMAND_ARGS[command];
    return formatWorkflowCommandForState(deps, state, command, args);
  });
  return commands.length > 0 ? commands.join(" 或 ") : "请查看上方提示";
}

async function failNodeOutputContract(params: {
  deps: ControllerDeps;
  state: FlowState;
  flowId: string;
  revision: number;
  node: WorkflowNode;
  result: Record<string, unknown> | undefined;
  executorType: string;
  persistFailure?: boolean;
}): Promise<NodeOutcome | null> {
  const contractIssues = validateOutputContractResult(params.node.outputContract, params.result, params.node.id);
  if (contractIssues.length === 0) return null;

  const error = formatOutputContractIssues(params.node.title, contractIssues);
  const resultPath = `nodeStates.${params.node.id}.result`;
  params.state.nodeStates[params.node.id] = {
    ...params.state.nodeStates[params.node.id],
    status: "failed",
    completedAt: now(),
    result: params.result,
    error,
  };
  appendAuditLog(params.state, params.node.id, "output-contract-failed", error);
  appendFlowEvent(params.state, {
    type: "node_output_contract_failed",
    flowId: params.flowId,
    workflowId: params.state.workflowId,
    nodeId: params.node.id,
    data: {
      executor: params.executorType,
      issues: contractIssues,
      resultPath,
    },
    error,
  });
  // Emit node_failed event so HTTP callback and node_executions are updated.
  // executeNodeWithRetry does NOT emit node_failed for output-contract failures
  // (it returns the result as "succeeded"), so we must emit it here.
  emitNodeEvent("node_failed", {
    flowId: params.flowId,
    workflowId: params.state.workflowId,
    nodeId: params.node.id,
    executorType: params.executorType,
    attempt: params.state.nodeStates[params.node.id]?.attempts ?? 1,
    durationMs: 0,
    error,
    inputJson: null,
    outputJson: params.result ? JSON.stringify(params.result) : null,
    sessionKey: params.deps.sessionKey,
    sessionId: params.deps.sessionId,
    embeddedSessionKey: deriveEmbeddedSessionKey(params.deps.sessionKey, params.node.id, params.flowId, params.executorType),
    systemContext: { failureReason: "output-contract-failed", willRetry: false },
  });

  if (params.persistFailure ?? true) {
    await failWithRevisionRetry(params.deps, params.flowId, params.revision, params.state, {
      blockedSummary: `${params.node.title} 失败: ${error}`,
      endedAt: now(),
    });
    await params.deps.chatInject(`${params.node.title} 执行失败: ${error}`, `${params.flowId}:${params.node.id}:failed`);
  }

  return { action: "failed", revision: params.revision };
}

async function handleNodeResult(
  deps: ControllerDeps,
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
  revision: number,
  node: WorkflowNode,
  result: ExecutorResult,
  executorType: string,
  options: { persistFailure?: boolean } = {},
): Promise<NodeOutcome> {
  const persistFailure = options.persistFailure ?? true;
  recordNodeUsage(state, node.id, result);

  if (result.status === "waiting") {
    const prompt = result.waitConfig?.prompt ?? "等待中";
    const waitActions = node.executor.type === "human" ? node.executor.actions : undefined;
    const waitCommandHints = node.executor.type === "human" ? node.executor.commandHints : undefined;
    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      status: "waiting",
      waitKind: result.waitConfig?.waitKind,
      waitPrompt: prompt,
      waitInputSchema: node.executor.type === "human" ? node.executor.inputSchema : undefined,
      waitSaveAs: node.executor.type === "human" ? node.executor.saveAs : undefined,
      /** Persist callback token for async-callback nodes so the resume handler can validate it. */
      callbackToken: node.executor.type === "async-callback" ? (result.result?.callbackToken as string | undefined) : undefined,
    };
    applyPhaseAndStatus(workflow, state);
    appendAuditLog(state, node.id, "waiting", prompt);
    appendFlowEvent(state, {
      type: "node_waiting",
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      data: { executor: executorType, prompt, waitKind: result.waitConfig?.waitKind },
    });
    return {
      action: "waiting",
      revision,
      waitPrompt: prompt,
      waitKind: result.waitConfig?.waitKind,
      waitActions,
      waitCommandHints,
    };
  }

  if (result.status === "failed") {
    const debugResult = sanitizeFailedResultDebug(result.result);
    const retryHint = debugResult ? formatNodeRetryCommandHint(deps, state.workflowId, node.id) : undefined;
    const failedSummary = `${node.title} 失败: ${result.error}`;
    const failedMessage = `${node.title} 执行失败: ${result.error}`;
    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      status: "failed",
      completedAt: now(),
      ...(debugResult ? { result: debugResult } : {}),
      error: result.error,
    };
    appendAuditLog(state, node.id, "failed", result.error ?? "执行失败");
    appendFlowEvent(state, {
      type: "node_failed",
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      data: {
        executor: executorType,
        ...(debugResult
          ? {
              ...summarizeRecord(debugResult),
              ...debugResult,
              resultPath: `nodeStates.${node.id}.result`,
            }
          : {}),
      },
      error: result.error ?? "执行失败",
    }, { rawError: result.rawError });

    if (persistFailure) {
      // BUG-25: non-conflict persistence failures remain best-effort. A revision
      // conflict must escape so a stale local worker cannot close another writer's flow.
      try {
        await failWithRevisionRetry(deps, flowId, revision, state, {
          blockedSummary: retryHint ? `${failedSummary}\n\n${retryHint}` : failedSummary,
          endedAt: now(),
        });
      } catch (failErr) {
        if (isFlowStateConflict(failErr)) throw failErr;
        const errMsg = failErr instanceof Error ? failErr.message : String(failErr);
        console.error(`[controller] handleNodeResult: boundTaskFlow.fail() threw for flowId=${flowId}:`, failErr);
        enqueueRunLog({
          flow_id: flowId,
          node_id: node.id,
          level: "error",
          source: "engine",
          message: `boundTaskFlow.fail() failed in handleNodeResult: ${errMsg}`,
          timestamp: Date.now(),
        });
        // Continue — the flow state is already updated in memory (nodeStates[node.id].status = "failed")
        // and will be persisted on the next opportunity (e.g. flow control cleanup, zombie recovery).
      }
      await deps.chatInject(
        retryHint ? `${failedMessage}\n\n${retryHint}` : failedMessage,
        `${flowId}:${node.id}:failed`,
      );
    }
    return { action: "failed", revision };
  }

  const contractOutcome = await failNodeOutputContract({
    deps,
    state,
    flowId,
    revision,
    node,
    result: result.result,
    executorType,
    persistFailure,
  });
  if (contractOutcome) return contractOutcome;

  // ── Validation template check ──
  if (shouldValidateNode(node) && _validationTemplateResolver) {
    try {
      const template = await _validationTemplateResolver(node.validationTemplateId!);
      if (template) {
        const minScore = node.validationMinScore ?? 60;
        const outputText = typeof result.result === "string"
          ? result.result
          : JSON.stringify(result.result ?? "");
        const validationResult = await validateNodeOutput(template, outputText, minScore);

        appendAuditLog(state, node.id, "validation", `Validation score: ${validationResult.score}/${minScore} — ${validationResult.passed ? "PASSED" : "FAILED"} — ${validationResult.feedback}`);
        appendFlowEvent(state, {
          type: "node_validation_result",
          flowId,
          workflowId: state.workflowId,
          nodeId: node.id,
          data: {
            executor: executorType,
            validationTemplateId: node.validationTemplateId,
            validationScore: validationResult.score,
            validationMinScore: minScore,
            validationPassed: validationResult.passed,
            validationFeedback: validationResult.feedback,
            validationDetails: validationResult.details,
          },
        });

        // Alert on low score if node has alerting configured
        if (!validationResult.passed && node.alerting && _alertDispatcher) {
          const validationEvent: NodeFailureEvent = {
            nodeId: node.id,
            flowId,
            workflowId: state.workflowId,
            error: `Validation score ${validationResult.score} below threshold ${minScore}: ${validationResult.feedback}`,
            attempt: 1,
          };
          void _alertDispatcher.dispatchNodeFailure(validationEvent).catch((err) => recordFailure("validateNodeOutput.dispatchNodeFailure", flowId, node.id, err, "warn"));
        }
      }
    } catch (validationErr) {
      const msg = validationErr instanceof Error ? validationErr.message : String(validationErr);
      appendAuditLog(state, node.id, "validation-error", `Validation check failed: ${msg}`);
      // Validation errors do NOT fail the node — best effort
    }
  }

  // ── Node validation: block-node mode ──
  if (node.validation?.onFailure === "block-node") {
    // Make the current node's output available to validation actions via
    // {{nodeOutput}} / {{nodeOutput.<nodeId>}} before building the context.
    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      result: result.result,
    };
    const validationContext = buildActionContext(deps, state, workflow, flowId, node.id);
    const validationOutcome = await runNodeValidationActions({
      node,
      state,
      flowId,
      registry: deps.actionRegistry,
      context: validationContext,
    });

    if (validationOutcome.status === "blocked") {
      appendFlowEvent(state, {
        type: "validation_failed",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        actionId: validationOutcome.hookId,
        data: { action: validationOutcome.action },
        error: validationOutcome.error,
      });
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "blocked",
        error: `${validationOutcome.action}/${validationOutcome.hookId}: ${validationOutcome.error}`,
      };
      applyPhaseAndStatus(workflow, state);
      appendAuditLog(state, node.id, "validation-blocked", `${node.title} 校验失败: ${validationOutcome.hookId} — ${validationOutcome.error}`);
      const newRevision = await blockOnHookFailure(
        deps,
        flowId,
        revision,
        state,
        `${node.title} 校验失败: ${validationOutcome.hookId} — ${validationOutcome.error}`,
      );
      await deps.chatInject(
        `节点 **${node.title}** 的校验 **${validationOutcome.hookId}** 执行失败，流程已阻塞。\n失败原因：${validationOutcome.error}`,
        `${flowId}:${node.id}:validation-blocked`,
      );
      return { action: "blocked", revision: newRevision };
    }
  }

  // succeeded — check onResult
  let onResultAction = evaluateOnResult(node.onResult, result.result ?? {});
  let llmEvaluationResult: import("./types.js").LlmEvaluationResult | undefined;

  // ── LLM-based onResult evaluation (tasks 6.5–6.7) ──
  // If the onResult has branches with llmEvaluate, run LLM evaluation.
  // LLM results take precedence over rule-based matching; on failure, fall back to rules.
  if (node.onResult?.branches?.some((b) => b.llmEvaluate)) {
    try {
      const { evaluateBranchesWithLlm, toLlmEvaluationResult } = await import("./llm/evaluate-branch.js");
      const templateCtx = buildTemplateContext(state, deps.skillRoot, result.result ?? {}, { currentNodeId: node.id, userIdentity: resolveUserIdentityForContext(deps) });
      const llmBranchResult = await evaluateBranchesWithLlm(node.onResult.branches, templateCtx);
      llmEvaluationResult = toLlmEvaluationResult(llmBranchResult);

      if (llmBranchResult.met && llmBranchResult.matchedBranchIndex >= 0) {
        // LLM matched a branch — override the rule-based result
        onResultAction = { action: "branch", matchedBranchIndex: llmBranchResult.matchedBranchIndex };
      }
      // If LLM didn't match any branch, keep the rule-based onResultAction (fallback)

      // Persist LLM evaluation to nodeState
      if (llmEvaluationResult) {
        state.nodeStates[node.id] = {
          ...state.nodeStates[node.id],
          llmEvaluation: llmEvaluationResult,
        };
      }

      // Emit observability event
      if (deps.eventEmitter) {
        deps.eventEmitter.emitLlmEvaluation(flowId, state.workflowId, node.id, {
          condition: llmBranchResult.reason,
          result: llmBranchResult.met ? "met" : "not-met",
          reason: llmBranchResult.reason,
          model: llmBranchResult.model,
          tokenUsage: llmBranchResult.usage?.totalTokens,
        }).catch(() => { /* best-effort */ });
      }
    } catch (llmErr) {
      // LLM evaluation failed — fall back to rule-based matching entirely
      const msg = llmErr instanceof Error ? llmErr.message : String(llmErr);
      appendAuditLog(state, node.id, "llm-eval-fallback", `LLM evaluation failed, falling back to rules: ${msg}`);
    }
  }

  if (onResultAction.action === "wait") {
    const onResultWaitSpec = resolveOnResultHumanWait(node, {
      status: "waiting",
      phase: node.phase,
      executor: executorType,
      result: result.result,
    });
    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      status: "waiting",
      result: result.result,
      waitKind: onResultAction.waitKind,
      waitPrompt: undefined,
      waitInputSchema: onResultAction.inputSchema,
      waitSaveAs: onResultAction.saveAs,
    };
    applyPhaseAndStatus(workflow, state);
    const resolvedPrompt = resolveTemplate(
      onResultAction.prompt,
      buildTemplateContext(state, deps.skillRoot, result.result ?? {}, { currentNodeId: node.id, userIdentity: resolveUserIdentityForContext(deps) }),
    );
    state.nodeStates[node.id].waitPrompt = resolvedPrompt;
    appendAuditLog(state, node.id, "onResult-wait", resolvedPrompt);
    appendFlowEvent(state, {
      type: "node_waiting",
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      data: {
        executor: executorType,
        prompt: resolvedPrompt,
        waitKind: onResultAction.waitKind,
        ...summarizeRecord(result.result),
        resultPath: `nodeStates.${node.id}.result`,
      },
    });
    return {
      action: "waiting",
      revision,
      waitPrompt: resolvedPrompt,
      waitKind: onResultAction.waitKind,
      waitActions: onResultWaitSpec?.actions,
      waitCommandHints: onResultWaitSpec?.commandHints,
    };
  }

  // ── rerun: auto-reset target and descendants, then re-execute from target ──
  if (onResultAction.action === "rerun") {
    const rerun = onResultAction.rerun;
    const targetNodeId = rerun.target;
    const effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

    // Mark the current node as succeeded first (it completed normally — rerun is a side-effect)
    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      status: "succeeded",
      result: result.result,
      completedAt: now(),
    };
    appendAuditLog(state, node.id, "onResult-rerun", `Auto-rerun to ${targetNodeId}`);
    appendFlowEvent(state, {
      type: "node_succeeded",
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      data: {
        executor: executorType,
        rerunTarget: targetNodeId,
        ...summarizeRecord(result.result),
        resultPath: `nodeStates.${node.id}.result`,
      },
    });

    // Apply saveAs before resetting (so target nodes can see the new workflowData)
    if (rerun.saveAs && Object.keys(rerun.saveAs).length > 0) {
      const templateCtx = buildTemplateContext(state, deps.skillRoot, result.result ?? {}, { currentNodeId: node.id });
      applySaveAs(state.workflowData, rerun.saveAs, result.result ?? {}, {
        ...buildActionContext(deps, state, workflow, flowId, node.id),
        input: result.result ?? {},
        ...Object.fromEntries(Object.entries(templateCtx)),
      });
    }

    // Apply feedback text
    if (rerun.feedbackPath && rerun.feedbackTemplate) {
      const feedbackKey = parseWorkflowDataKey(rerun.feedbackPath, "feedbackPath");
      const feedbackText = String(resolveTemplateValue(
        rerun.feedbackTemplate,
        {
          ...buildActionContext(deps, state, workflow, flowId, node.id),
          input: result.result ?? {},
        },
        result.result ?? {},
      ));
      const existingFeedback = typeof state.workflowData[feedbackKey] === "string"
        ? (state.workflowData[feedbackKey] as string).trim()
        : "";
      state.workflowData[feedbackKey] = existingFeedback
        ? `${existingFeedback}\n${feedbackText}`
        : feedbackText;
    }

    // Reset target + descendants
    const resetScope = rerun.reset ?? "target-and-descendants";
    const resetNodeIds = resetScope === "target-and-descendants"
      ? collectTargetAndDescendants(effectiveWorkflow, targetNodeId)
      : [targetNodeId];

    for (const nodeIdToReset of resetNodeIds) {
      const nodeToReset = effectiveWorkflow.nodes.find((item) => item.id === nodeIdToReset)
        ?? workflow.nodes.find((item) => item.id === nodeIdToReset);
      if (!nodeToReset) continue;
      // Don't reset the current node (it just succeeded)
      if (nodeIdToReset === node.id) continue;
      state.nodeStates[nodeIdToReset] = resetNodeForRevision(
        nodeToReset,
        state.nodeStates[nodeIdToReset],
        state.executionMode,
      );
      // Clear loop-group runtime state for any loop-group nodes in the reset set
      if (isLoopGroupNode(nodeToReset)) {
        clearLoopRuntimeStateForManualRetry(state, nodeToReset.id);
      }
    }

    applyPhaseAndStatus(effectiveWorkflow, state);
    state.activeNodes = [targetNodeId];
    const targetNode = workflow.nodes.find((item) => item.id === targetNodeId);
    state.currentPhase = targetNode?.phase ?? state.currentPhase;
    state.businessStatus = targetNode?.businessStatus ?? state.businessStatus;

    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: "running",
      currentStep: targetNodeId,
      stateJson: JSON.stringify(state),
    });
    if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");

    const newRevision = (resumeResult.flow as Record<string, unknown>).revision as number;
    await deps.chatInject(
      `已自动回退至 ${targetNode?.title ?? targetNodeId}`,
      `${flowId}:${node.id}:rerun`,
    );
    await executeLoop(deps, workflow, state, flowId, newRevision);

    return { action: "continue", revision: newRevision };
  }

  // ── LLM-Orchestrator handling (tasks 7.5–7.10) ──
  // If this node is an llm-orchestrator, handle injection/iteration/completion.
  if (executorType === "llm-orchestrator" && result.result?._orchestratorComplete !== undefined) {
    const orchestratorResult = result.result;
    const orchestrationState = orchestratorResult._orchestrationState as import("./types.js").OrchestrationRuntimeState | undefined;

    // Persist orchestration state
    if (orchestrationState) {
      state.orchestrationState = {
        ...state.orchestrationState,
        [node.id]: orchestrationState,
      };
    }

    // Emit orchestrator iteration event
    if (deps.eventEmitter && orchestrationState) {
      const lastIter = orchestrationState.iterations.at(-1);
      deps.eventEmitter.emitOrchestratorIteration(flowId, state.workflowId, node.id, {
        iteration: orchestrationState.currentIteration,
        action: lastIter?.selectedAction ?? "unknown",
        reason: lastIter?.llmReasoning ?? String(orchestratorResult._orchestratorReason ?? ""),
      }).catch(() => { /* best-effort */ });
    }

    // If orchestrator is complete (goal met, failed, or budget exhausted)
    if (orchestratorResult._orchestratorComplete === true) {
      const status = orchestratorResult._orchestratorStatus as string;
      appendAuditLog(state, node.id, "orchestrator-complete", `Orchestrator finished: ${status} — ${orchestratorResult._orchestratorReason ?? ""}`);

      // Mark node as succeeded or failed based on orchestrator status
      if (status === "succeeded") {
        state.nodeStates[node.id] = {
          ...state.nodeStates[node.id],
          status: "succeeded",
          result: result.result,
          completedAt: now(),
        };
      } else {
        state.nodeStates[node.id] = {
          ...state.nodeStates[node.id],
          status: "failed",
          error: `Orchestrator ${status}: ${orchestratorResult._orchestratorReason ?? "unknown"}`,
          completedAt: now(),
        };
      }
      appendFlowEvent(state, {
        type: "node_succeeded",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: {
          executor: executorType,
          orchestratorStatus: status,
          iterations: orchestrationState?.iterations?.length ?? 0,
          ...summarizeRecord(result.result),
        },
      });
      return { action: "continue", revision };
    }

    // Orchestrator is still iterating — inject the materialized node
    const materializedNodeSpec = orchestratorResult._materializedNode as import("./executors/llm-orchestrator.js").MaterializedNodeSpec | undefined;
    const injectedNode = orchestratorResult._injectedNode as import("./types.js").InjectedNodeRecord | undefined;

    if (materializedNodeSpec && injectedNode) {
      // Build the WorkflowNode from the materialized spec
      const materializedNode: import("./types.js").WorkflowNode = {
        id: materializedNodeSpec.id,
        title: materializedNodeSpec.title,
        phase: materializedNodeSpec.phase,
        dependsOn: materializedNodeSpec.dependsOn,
        executor: materializedNodeSpec.executor as import("./types.js").NodeExecutor,
      };

      // Inject the node into the effective workflow and state
      const injectResult = injectNodesIntoWorkflow(state, workflow, [materializedNode], [injectedNode], node.id, deps, flowId);

      if (injectResult.success) {
        appendAuditLog(state, node.id, "orchestrator-inject", `Injected node ${materializedNodeSpec.id} (action: ${injectedNode.actionName}, step: ${injectedNode.stepNum})`);

        // Keep orchestrator node in "running" state
        state.nodeStates[node.id] = {
          ...state.nodeStates[node.id],
          status: "running",
          result: result.result,
        };

        // Persist orchestration state
        await persistStateToFlow(deps, state, flowId);

        // Execute the injected node immediately
        const templateCtx = buildTemplateContext(state, deps.skillRoot, {}, { currentNodeId: materializedNodeSpec.id, userIdentity: resolveUserIdentityForContext(deps) });
        const injectedResult = await executeNodeWithRetry(deps, materializedNode, templateCtx, state, flowId, workflow);

        // Record the result in orchestration state for the next iteration
        if (state.orchestrationState?.[node.id]) {
          const iterIndex = state.orchestrationState[node.id].iterations.length - 1;
          if (iterIndex >= 0) {
            state.orchestrationState[node.id].iterations[iterIndex].result = injectedResult.result ?? {};
            state.orchestrationState[node.id].iterations[iterIndex].completedAt = Date.now();
          }
        }

        await persistStateToFlow(deps, state, flowId);

        // Re-trigger the orchestrator for the next iteration
        const priorIterations = state.orchestrationState?.[node.id]?.iterations ?? [];
        const { executeLlmOrchestrator } = await import("./executors/llm-orchestrator.js");
        const nextResult = await executeLlmOrchestrator(
          node,
          buildTemplateContext(state, deps.skillRoot, result.result ?? {}, { currentNodeId: node.id, userIdentity: resolveUserIdentityForContext(deps) }),
          priorIterations,
          state.orchestrationState?.[node.id],
        );

        // Recursively process the orchestrator's next decision
        return handleNodeResult(deps, workflow, state, flowId, revision, node, nextResult, executorType, { persistFailure: false });
      } else {
        appendAuditLog(state, node.id, "orchestrator-inject-failed", `Failed to inject node: ${injectResult.success === false ? injectResult.reason : "unknown"}`);
      }
    }

    // Fallback: treat as continue
    return { action: "continue", revision };
  }

  // complete
  const matchedBranchId = onResultAction.action === "branch" && node.onResult?.branches
    ? (node.onResult.branches[onResultAction.matchedBranchIndex]?.branchId ?? null)
    : null;

  state.nodeStates[node.id] = {
    ...state.nodeStates[node.id],
    status: "postActionsRunning",
    result: result.result,
    completedAt: now(),
    ...(matchedBranchId != null ? { matchedBranchId } : {}),
  };

  // Persist latest node output to flow_runs.result_json (best-effort)
  persistNodeResult(flowId, node.id, result.result);

  const hookOutcome = await runNodeSuccessHooks({
    deps,
    workflow,
    state,
    flowId,
    revision,
    node,
    notifyOnBlock: true,
  });
  if (hookOutcome.blocked) {
    return { action: "blocked", revision: hookOutcome.revision };
  }

  appendFlowEvent(state, {
    type: "node_succeeded",
    flowId,
    workflowId: state.workflowId,
    nodeId: node.id,
    data: {
      executor: executorType,
      ...summarizeRecord(result.result),
      resultPath: `nodeStates.${node.id}.result`,
    },
  });

  if (node.progressMessage) {
    const resolved = resolveTemplate(
      node.progressMessage,
      buildTemplateContext(state, deps.skillRoot, {}, { currentNodeId: node.id, userIdentity: resolveUserIdentityForContext(deps) }),
    );
    await deps.chatInject(resolved, `${flowId}:${node.id}:progress`);
    // Emit progress event for DB persistence
    emitNodeEvent("node_progress", {
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      executorType: node.executor.type,
      attempt: 1,
      progressMessage: resolved,
    });
  }

  return { action: "continue", revision: hookOutcome.revision };
}

type ParallelNodeSettledValue = {
  node: WorkflowNode;
  result: ExecutorResult;
};

async function handleParallelNodeSettledResults(params: {
  deps: ControllerDeps;
  workflow: WorkflowSpec;
  state: FlowState;
  flowId: string;
  revision: number;
  parallelNodes: WorkflowNode[];
  settled: PromiseSettledResult<ParallelNodeSettledValue>[];
}): Promise<{
  action: "continue" | "blocked";
  hasWaiting: boolean;
  hasFailed: boolean;
  waitingNodes: string[];
}> {
  let hasWaiting = false;
  let hasFailed = false;
  const waitingNodes: string[] = [];

  for (const [index, entry] of params.settled.entries()) {
    if (entry.status === "rejected") {
      const failedNode = params.parallelNodes[index];
      const failedError = entry.reason instanceof Error ? `${entry.reason.name}: ${entry.reason.message}` : String(entry.reason);
      const result: ExecutorResult = { status: "failed", error: failedError, rawError: entry.reason };
      const failedExecutorType = resolveExecutorType(failedNode, params.state.executionMode);
      params.state.nodeStates[failedNode.id] = {
        ...params.state.nodeStates[failedNode.id],
        status: "failed",
        completedAt: now(),
        error: failedError,
      };
      appendAuditLog(params.state, failedNode.id, "failed", failedError);
      appendFlowEvent(params.state, {
        type: "node_failed",
        flowId: params.flowId,
        workflowId: params.state.workflowId,
        nodeId: failedNode.id,
        error: failedError,
      }, { rawError: entry.reason });
      // Emit node_failed event for parallel node rejection (unhandled exception).
      // Normally executeNodeWithRetry catches all errors and emits node_failed
      // internally, but if it throws (e.g. OOM, stack overflow), the
      // Promise.allSettled entry will be "rejected" and no event was emitted.
      emitNodeEvent("node_failed", {
        flowId: params.flowId,
        workflowId: params.state.workflowId,
        nodeId: failedNode.id,
        executorType: failedExecutorType,
        attempt: 1,
        durationMs: 0,
        error: failedError,
        sessionKey: params.deps.sessionKey,
        sessionId: params.deps.sessionId,
        embeddedSessionKey: deriveEmbeddedSessionKey(params.deps.sessionKey, failedNode.id, params.flowId, failedExecutorType),
        systemContext: { failureReason: "parallel_node_rejected", willRetry: false },
      });
      if (getLegacyApprovalExecutor(failedNode)) {
        await params.deps.chatInject(
          formatApprovalNodeResultMessage({
            deps: params.deps,
            node: failedNode,
            result,
            flowId: params.flowId,
            workflowId: params.state.workflowId,
          }),
          `${params.flowId}:${failedNode.id}:approval-result:${result.status}`,
        );
      }
      hasFailed = true;
      continue;
    }

    const { node, result } = entry.value;
    const executorType = resolveExecutorType(node, params.state.executionMode);
    const outcome = await handleNodeResult(
      params.deps,
      params.workflow,
      params.state,
      params.flowId,
      params.revision,
      node,
      result,
      executorType,
      { persistFailure: false },
    );
    if (getLegacyApprovalExecutor(node) && (result.status === "succeeded" || result.status === "failed")) {
      await params.deps.chatInject(
        formatApprovalNodeResultMessage({
          deps: params.deps,
          node,
          result,
          flowId: params.flowId,
          workflowId: params.state.workflowId,
        }),
        `${params.flowId}:${node.id}:approval-result:${result.status}`,
      );
    }

    if (outcome.action === "waiting") {
      hasWaiting = true;
      waitingNodes.push(node.id);
    } else if (outcome.action === "blocked") {
      return { action: "blocked", hasWaiting, hasFailed, waitingNodes };
    } else if (outcome.action === "failed") {
      hasFailed = true;
    }
  }

  return { action: "continue", hasWaiting, hasFailed, waitingNodes };
}

export const handleParallelNodeSettledResultsForTest = handleParallelNodeSettledResults;

async function runPendingHooks(
  deps: ControllerDeps,
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
  revision: number,
): Promise<{ blocked: boolean; revision: number }> {
  let currentRevision = revision;
  const startHooks = workflow.workflow?.onStart ?? [];
  const shouldRunStartHooks = startHooks.some((hook) => {
    const status = state.flowHooks.onStart?.[hook.id]?.status;
    return status == null || (hook.required === true && status === "failed");
  });

  if (shouldRunStartHooks) {
    const startOutcome = await runFlowStartHooks({ deps, workflow, state, flowId, revision });
    if (startOutcome.blocked) return startOutcome;
    currentRevision = startOutcome.revision;
  }

  for (const node of workflow.nodes) {
    const nodeState = state.nodeStates[node.id];
    if (!nodeState || nodeState.status !== "blocked") continue;

    const hasBlockNodeValidation = node.validation?.onFailure === "block-node";
    const hasOnSuccessHooks = node.onSuccess && node.onSuccess.length > 0;
    if (!hasBlockNodeValidation && !hasOnSuccessHooks) continue;

    // Retry failed validation actions first (block-node mode)
    if (node.validation?.onFailure === "block-node") {
      const validationOutcome = await runNodeValidationActions({
        node,
        state,
        flowId,
        registry: deps.actionRegistry,
        context: buildActionContext(deps, state, workflow, flowId, node.id),
      });
      if (validationOutcome.status === "blocked") {
        // Re-block with updated revision using existing helper
        currentRevision = await blockOnHookFailure(
          deps,
          flowId,
          currentRevision,
          state,
          `${node.title} 校验失败: ${validationOutcome.hookId} — ${validationOutcome.error}`,
        );
        return { blocked: true, revision: currentRevision };
      }
    }

    const outcome = await runNodeSuccessHooks({
      deps,
      workflow,
      state,
      flowId,
      revision: currentRevision,
      node,
      notifyOnBlock: true,
    });
    currentRevision = outcome.revision;
    if (outcome.blocked) {
      return { blocked: true, revision: currentRevision };
    }
  }

  return { blocked: false, revision: currentRevision };
}

export const runPendingHooksForTest = runPendingHooks;

function isLoopGroupNode(node: WorkflowNode): node is WorkflowNode & { executor: LoopGroupExecutor } {
  return node.executor.type === "loop-group";
}

function isSubworkflowNode(node: WorkflowNode): node is WorkflowNode & { executor: SubworkflowExecutor } {
  return node.executor.type === "subworkflow";
}

function isDynamicTemplateNode(node: WorkflowNode): node is WorkflowNode & { executor: DynamicTemplateExecutor } {
  return node.executor.type === "dynamic-template";
}

function formatSubworkflowStateSummary(state: FlowState, node: WorkflowNode): string {
  if (!isSubworkflowNode(node)) return "";
  const executor = node.executor;
  const parts: string[] = [`depth=${state.subworkflowMeta?.depth ?? 0}`];
  const ns = state.nodeStates[node.id];
  if (ns?.status === "running" || ns?.status === "succeeded" || ns?.status === "failed") {
    parts.push(`workflowId=${executor.workflowId}`);
    if (executor.packId) parts.push(`pack=${executor.packId}`);
  }
  return parts.length > 0 ? ` — ${parts.join(" | ")}` : "";
}

/**
 * Compensate stale waiting/running nodes after an approval callback succeeds.
 *
 * When a BCS approval / human-wait callback resolves a node to "succeeded",
 * other approval-type nodes in the same flow may still be stuck in
 * "waiting" / "running" / "postActionsRunning" even though their审批 has
 * been completed externally. This typically happens due to the
 * asyncAwareExecuteLoop race (the callback writes to TaskFlow but the
 * in-flight async engine skips executeLoop because it sees itself as
 * still active).
 *
 * This function scans all non-terminal nodes and, for approval-type nodes
 * (bcs-route, collaboration, human) that are still in a non-terminal state,
 * checks whether there is evidence the approval was already resolved
 * (e.g. the node has a result payload from a prior partial write). If so,
 * it marks them as "succeeded" so the flow can progress.
 *
 * Returns the list of node IDs that were compensated.
 */
function reconcileStaleApprovalNodes(
  state: FlowState,
  workflow: WorkflowSpec,
  flowId: string,
  resolvedNodeId: string,
): string[] {
  const compensated: string[] = [];
  const effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

  for (const node of effectiveWorkflow.nodes) {
    if (node.id === resolvedNodeId) continue;

    const ns = state.nodeStates[node.id];
    if (!ns || isTerminalNodeStatus(ns.status)) continue;

    // Only compensate approval-type nodes (bcs-route, collaboration, human)
    const executorType = ns.executor ?? node.executor.type;
    const isApprovalNode =
      executorType === "bcs-route"
      || executorType === "collaboration"
      || getLegacyApprovalExecutor(node) !== undefined
      || node.executor.type === "human";

    if (!isApprovalNode) continue;

    // If the node already has a result payload, it means the approval
    // result was written (e.g. via a prior partial callback or BCS
    // collaboration message) but the status wasn't updated to "succeeded".
    // Compensate by marking it as succeeded.
    if (ns.result && typeof ns.result === "object" && "approved" in ns.result) {
      state.nodeStates[node.id] = {
        ...ns,
        status: "succeeded",
        completedAt: ns.completedAt ?? now(),
        error: null,
      };
      appendAuditLog(state, node.id, "reconciled-succeeded",
        `${node.title} 补偿标记为成功 (先前状态: ${ns.status})`);
      appendFlowEvent(state, {
        type: "node_succeeded",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: {
          executor: executorType,
          reconciled: true,
          previousStatus: ns.status,
          ...summarizeRecord(ns.result as Record<string, unknown>),
          resultPath: `nodeStates.${node.id}.result`,
        },
      });
      emitNodeEvent("node_succeeded", {
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        executorType,
        attempt: ns.attempts ?? 1,
        durationMs: 0,
        usage: null,
        sessionKey: undefined,
        sessionId: undefined,
        systemContext: { reason: "reconcile_stale_approval", previousStatus: ns.status },
      });
      compensated.push(node.id);
      continue;
    }

    // For human-wait nodes that are still "waiting" but the flow is
    // being resumed (meaning the human gate was passed), mark as succeeded
    // with the existing result or a default confirm result.
    if (ns.status === "waiting" && node.executor.type === "human") {
      const humanResult = ns.result ?? { confirmed: true, reconciled: true };
      state.nodeStates[node.id] = {
        ...ns,
        status: "succeeded",
        completedAt: now(),
        result: humanResult,
        error: null,
        waitKind: undefined,
        waitPrompt: undefined,
        waitInputSchema: undefined,
        waitSaveAs: undefined,
      };
      appendAuditLog(state, node.id, "reconciled-succeeded",
        `${node.title} 人工节点补偿标记为成功 (先前状态: waiting)`);
      appendFlowEvent(state, {
        type: "node_succeeded",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: {
          executor: executorType,
          reconciled: true,
          previousStatus: "waiting",
          ...summarizeRecord(humanResult as Record<string, unknown>),
          resultPath: `nodeStates.${node.id}.result`,
        },
      });
      emitNodeEvent("node_succeeded", {
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        executorType,
        attempt: ns.attempts ?? 1,
        durationMs: 0,
        usage: null,
        sessionKey: undefined,
        sessionId: undefined,
        systemContext: { reason: "reconcile_stale_human_wait", previousStatus: "waiting" },
      });
      compensated.push(node.id);
    }
  }

  if (compensated.length > 0) {
    console.log(`[controller] reconcileStaleApprovalNodes: compensated ${compensated.length} node(s) for flowId=${flowId}: ${compensated.join(", ")}`);
  }

  return compensated;
}

function isSuccessfulTerminalStatus(status: NodeState["status"] | undefined): boolean {
  return status === "succeeded" || status === "skipped";
}

function isTerminalNodeStatus(status: NodeState["status"] | undefined): boolean {
  return status === "succeeded" || status === "failed" || status === "rejected" || status === "skipped";
}

function isWorkflowTerminal(workflow: WorkflowSpec, nodeStates: Record<string, NodeState>): boolean {
  return workflow.nodes.every((node) => isTerminalNodeStatus(nodeStates[node.id]?.status));
}

function resolvedTriggerRule(node: WorkflowNode): WorkflowNode["triggerRule"] {
  if (node.triggerRule) return node.triggerRule;
  return node.join === "any" ? "one_success" : "all_success";
}

function hasReachableAllDoneFinalizer(
  workflow: WorkflowSpec,
  nodeStates: Record<string, NodeState>,
  failedNodeId: string,
): boolean {
  const simulatedStates: Record<string, NodeState> = {
    ...nodeStates,
    [failedNodeId]: {
      ...nodeStates[failedNodeId],
      status: "failed",
    },
  };
  for (const node of findSkippableNodesFixedPoint(workflow, simulatedStates)) {
    simulatedStates[node.id] = { ...simulatedStates[node.id], status: "skipped" };
  }

  const children = new Map<string, string[]>();
  for (const node of workflow.nodes) {
    for (const dependencyId of node.dependsOn) {
      const descendants = children.get(dependencyId) ?? [];
      descendants.push(node.id);
      children.set(dependencyId, descendants);
    }
  }

  const reachable = new Set<string>();
  const pending = [...(children.get(failedNodeId) ?? [])];
  while (pending.length > 0) {
    const nodeId = pending.shift()!;
    if (reachable.has(nodeId)) continue;
    reachable.add(nodeId);
    pending.push(...(children.get(nodeId) ?? []));
  }

  return workflow.nodes.some((node) =>
    reachable.has(node.id)
    && resolvedTriggerRule(node) === "all_done"
    && !isTerminalNodeStatus(simulatedStates[node.id]?.status),
  );
}

function getPathValue(value: Record<string, unknown> | undefined, path: string): unknown {
  if (!value) return undefined;
  const parts = path.replace(/\[(\d+)\]/g, ".$1").split(".").filter(Boolean);
  let current: unknown = value;
  for (const part of parts) {
    if (current == null || typeof current !== "object") return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

/**
 * Inject nodes into the running workflow DAG.
 *
 * Validates node ID uniqueness, initializes nodeStates, and records
 * the injection in state.injectedNodes for observability.
 *
 * Returns { success: true } on success, or { success: false, reason } on failure.
 */
function injectNodesIntoWorkflow(
  state: FlowState,
  workflow: WorkflowSpec,
  nodes: import("./types.js").WorkflowNode[],
  records: import("./types.js").InjectedNodeRecord[],
  sourceNodeId: string,
  deps: Pick<ControllerDeps, "eventEmitter">,
  flowIdForEvent: string,
): { success: true } | { success: false; reason: string } {
  // Validate uniqueness of node IDs against existing nodes
  const existingIds = new Set(workflow.nodes.map((n) => n.id));
  for (const node of nodes) {
    if (existingIds.has(node.id)) {
      return { success: false, reason: `Node ID "${node.id}" already exists in the workflow` };
    }
    if (state.nodeStates[node.id]) {
      return { success: false, reason: `Node ID "${node.id}" already has state` };
    }
    // Check against already-injected nodes
    const alreadyInjected = state.injectedNodes?.some((r) => r.nodeId === node.id);
    if (alreadyInjected) {
      return { success: false, reason: `Node ID "${node.id}" was already injected` };
    }
    existingIds.add(node.id);
  }

  // Initialize nodeStates for injected nodes
  for (const node of nodes) {
    state.nodeStates[node.id] = {
      status: "pending",
      phase: node.phase,
      executor: node.executor.type,
    };
  }

  // Record injected nodes
  state.injectedNodes = [...(state.injectedNodes ?? []), ...records];

  // Emit observability events
  if (deps.eventEmitter) {
    for (const record of records) {
      deps.eventEmitter.emitInjected(flowIdForEvent, state.workflowId, sourceNodeId, {
        action: record.actionName ?? "unknown",
        reason: `Injected node ${record.nodeId}`,
        stepNum: record.stepNum ?? 0,
      }).catch(() => { /* best-effort */ });
    }
  }

  return { success: true };
}

export function buildEffectiveWorkflow(workflow: WorkflowSpec, state: FlowState): WorkflowSpec {
  const nodes: WorkflowNode[] = [];
  for (const node of workflow.nodes) {
    nodes.push(node);
    if (!isLoopGroupNode(node) && !isDynamicTemplateNode(node)) continue;

    // Loop-group expansion
    if (isLoopGroupNode(node)) {
      const loopState = state.loopGroups?.[node.id];
      if (!loopState) continue;
      for (const iteration of Object.values(loopState.iterations).sort((a, b) => a.iteration - b.iteration)) {
        const materialized = materializeLoopIteration({
          loopId: node.id,
          iteration: iteration.iteration,
          iterationVar: loopState.iterationVar,
          body: node.executor.body,
        });
        nodes.push(...materialized.runtimeNodes);
      }
      continue;
    }

    // Dynamic-template expansion
    if (isDynamicTemplateNode(node)) {
      const dtState = state.dynamicTemplates?.[node.id];
      if (!dtState) continue;
      const templateDef = workflow.nodeTemplates?.[dtState.templateName];
      if (!templateDef) continue;

      for (const item of Object.values(dtState.items).sort((a, b) => a.index - b.index)) {
        const { runtimeNodes } = materializeBody(
          templateDef.body,
          (bodyNodeId) => dynamicTemplateRuntimeNodeId(dtState.templateName, node.id, item.index, bodyNodeId),
        );
        nodes.push(...runtimeNodes);
      }
    }
  }

  // Injected nodes (from llm-orchestrator)
  if (state.injectedNodes && state.injectedNodes.length > 0) {
    // Reconstruct injected nodes from state. The executor spec is stored in
    // nodeStates[nodeId].result.__executorSpec or we look it up from orchestration state.
    // For now, we rely on the fact that injectNodesIntoWorkflow adds nodes to
    // workflow.nodes directly via the running effectiveWorkflow, so injected nodes
    // are already present in the base workflow at subsequent buildEffectiveWorkflow calls.
    // However, initial injection also adds them here for correctness.
    for (const record of state.injectedNodes) {
      const existingNode = nodes.find((n) => n.id === record.nodeId);
      if (!existingNode) {
        // Injected node not yet in the nodes list — reconstruct from nodeState
        const nodeState = state.nodeStates[record.nodeId];
        if (nodeState) {
          nodes.push({
            id: record.nodeId,
            title: `${record.actionName ?? "injected"} (step ${record.stepNum ?? "?"})`,
            phase: nodeState.phase || "main",
            dependsOn: [record.sourceNodeId],
            executor: { type: nodeState.executor } as import("./types.js").NodeExecutor,
          });
        }
      }
    }
  }

  return { ...workflow, nodes };
}

function isLoopParentWaitingPlaceholder(state: FlowState, nodeId: string): boolean {
  const loopState = state.loopGroups?.[nodeId];
  if (!loopState || loopState.status !== "waiting") return false;
  const iterationState = loopState.iterations[String(loopState.currentIteration)];
  if (!iterationState) return false;
  return Object.values(iterationState.nodeIds).some((runtimeNodeId) =>
    state.nodeStates[runtimeNodeId]?.status === "waiting"
  );
}

function applyPhaseAndStatus(
  workflow: WorkflowSpec,
  state: FlowState,
): { currentPhase: string; businessStatus: string; activeNodes: string[] } {
  const phaseInfo = computePhaseAndStatus(workflow, state.nodeStates);
  phaseInfo.activeNodes = phaseInfo.activeNodes.filter((nodeId) =>
    !isLoopParentWaitingPlaceholder(state, nodeId)
  );
  Object.assign(state, phaseInfo);
  return phaseInfo;
}

function collectLoopIterationOutputs(
  loopNode: WorkflowNode & { executor: LoopGroupExecutor },
  state: FlowState,
  loopState: LoopGroupRuntimeState,
  iteration: number,
): Record<string, Record<string, unknown>> {
  const iterationState = loopState.iterations[String(iteration)];
  const outputs: Record<string, Record<string, unknown>> = {};
  if (!iterationState) return outputs;
  for (const bodyNode of loopNode.executor.body) {
    const runtimeId = iterationState.nodeIds[bodyNode.id];
    const result = runtimeId ? state.nodeStates[runtimeId]?.result : undefined;
    if (result) outputs[bodyNode.id] = result;
  }
  return outputs;
}

function setLoopParentResult(params: {
  state: FlowState;
  loopNode: WorkflowNode & { executor: LoopGroupExecutor };
  loopState: LoopGroupRuntimeState;
  status: "succeeded" | "failed";
  untilMatched: boolean;
  exitReason: NonNullable<LoopGroupRuntimeState["exitReason"]>;
  lastIterationOutputs?: Record<string, Record<string, unknown>>;
  error?: string;
}): void {
  const completedAt = now();
  params.loopState.status = params.status;
  params.loopState.exitReason = params.exitReason;
  params.loopState.lastIteration = params.loopState.currentIteration;
  const result: Record<string, unknown> = {
    loopGroup: true,
    iterations: params.loopState.currentIteration,
    lastIteration: params.loopState.currentIteration,
    untilMatched: params.untilMatched,
    exitReason: params.exitReason,
  };
  if (params.lastIterationOutputs) {
    result.lastIterationOutputs = params.lastIterationOutputs;
  }
  params.state.nodeStates[params.loopNode.id] = {
    ...params.state.nodeStates[params.loopNode.id],
    status: params.status,
    phase: params.loopNode.phase,
    executor: "loop-group",
    completedAt,
    result,
    error: params.error ?? null,
  };
}

function materializeLoopRuntimeIteration(params: {
  state: FlowState;
  loopNode: WorkflowNode & { executor: LoopGroupExecutor };
  loopState: LoopGroupRuntimeState;
  iteration: number;
  flowId: string;
}): void {
  const iterationKey = String(params.iteration);
  if (params.loopState.iterations[iterationKey]) return;

  const materialized = materializeLoopIteration({
    loopId: params.loopNode.id,
    iteration: params.iteration,
    iterationVar: params.loopState.iterationVar,
    body: params.loopNode.executor.body,
  });
  const startedAt = now();
  params.loopState.iterations[iterationKey] = {
    iteration: params.iteration,
    status: "running",
    nodeIds: materialized.nodeIds,
    startedAt,
  };
  params.state.runtimeNodeMeta ??= {};
  Object.assign(params.state.runtimeNodeMeta, materialized.meta);
  for (const runtimeNode of materialized.runtimeNodes) {
    params.state.nodeStates[runtimeNode.id] ??= {
      status: "pending",
      phase: runtimeNode.phase,
      executor: resolveExecutorType(runtimeNode, params.state.executionMode),
    };
  }
  appendFlowEvent(params.state, {
    type: "loop_iteration_started",
    flowId: params.flowId,
    workflowId: params.state.workflowId,
    nodeId: params.loopNode.id,
    data: {
      loopId: params.loopNode.id,
      iteration: params.iteration,
      runtimeNodeIds: Object.values(materialized.nodeIds),
    },
  });
}

function prepareLoopGroupsForExecution(
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
): boolean {
  ensureFlowStateDefaults(state);
  let changed = false;
  const readyLoopNodes = getReadyNodes(workflow, state.nodeStates).filter(isLoopGroupNode);
  for (const node of readyLoopNodes) {
    if (state.nodeStates[node.id]?.status !== "pending") continue;

    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      status: "running",
      phase: node.phase,
      executor: "loop-group",
      startedAt: state.nodeStates[node.id]?.startedAt ?? now(),
      error: null,
    };
    state.loopGroups ??= {};
    state.loopGroups[node.id] ??= {
      loopId: node.id,
      status: "running",
      currentIteration: 1,
      maxIterations: node.executor.maxIterations,
      iterationVar: node.executor.iterationVar,
      iterations: {},
    };
    state.loopGroups[node.id] = {
      ...state.loopGroups[node.id],
      status: "running",
      currentIteration: state.loopGroups[node.id].currentIteration || 1,
      maxIterations: node.executor.maxIterations,
      iterationVar: node.executor.iterationVar,
      iterations: state.loopGroups[node.id].iterations ?? {},
    };
    appendFlowEvent(state, {
      type: "loop_started",
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      data: {
        loopId: node.id,
        maxIterations: node.executor.maxIterations,
        iterationVar: node.executor.iterationVar,
      },
    });
    materializeLoopRuntimeIteration({
      state,
      loopNode: node,
      loopState: state.loopGroups[node.id],
      iteration: 1,
      flowId,
    });
    changed = true;
  }
  return changed;
}

// ── Dynamic Template Expansion ──

/**
 * Generate a runtime node ID for a dynamic-template materialized node.
 * Convention: ${templateName}__${sourceNodeId}__${index}__${bodyNodeId}
 */
function dynamicTemplateRuntimeNodeId(
  templateName: string,
  sourceNodeId: string,
  index: number,
  bodyNodeId: string,
): string {
  return `${templateName}__${sourceNodeId}__${index}__${bodyNodeId}`;
}

/**
 * When a `dynamic-template` node becomes ready, resolve its `forEach`
 * expression, materialize the template body once per item, and inject
 * the runtime nodes into the FlowState.
 *
 * This mirrors `prepareLoopGroupsForExecution` but is simpler because
 * all iterations are known up-front (no until-condition or sequential
 * iteration — every item is materialized simultaneously).
 */
function prepareDynamicTemplatesForExecution(
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
  deps?: ControllerDeps,
): boolean {
  ensureFlowStateDefaults(state);
  let changed = false;
  const readyNodes = getReadyNodes(workflow, state.nodeStates).filter(isDynamicTemplateNode);

  for (const node of readyNodes) {
    if (state.nodeStates[node.id]?.status !== "pending") continue;
    const executor = node.executor;

    // Look up the template definition
    const templateDef = workflow.nodeTemplates?.[executor.template];
    if (!templateDef) {
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "failed",
        phase: node.phase,
        executor: "dynamic-template",
        completedAt: now(),
        error: `template "${executor.template}" not found in nodeTemplates`,
      };
      appendFlowEvent(state, {
        type: "node_failed" as const,
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        error: `template "${executor.template}" not found`,
        data: { error: `template "${executor.template}" not found` },
      });
      changed = true;
      continue;
    }

    // Resolve the forEach expression
    const templateCtx = buildTemplateContext(state, "", {}, { currentNodeId: node.id });
    const forEachStr = resolveTemplate(executor.forEach, templateCtx);

    // Parse the forEach value from workflowData / nodeOutput
    let forEachValue: unknown;
    try {
      forEachValue = JSON.parse(forEachStr);
    } catch {
      // If it's not JSON, try to get it as a raw value from the template context
      const rawPath = executor.forEach.replace(/\{\{|\}\}/g, "").trim();
      const parts = rawPath.split(".");
      let val: unknown = templateCtx;
      for (const part of parts) {
        if (val == null || typeof val !== "object") { val = undefined; break; }
        val = (val as Record<string, unknown>)[part];
      }
      forEachValue = val;
    }

    // Validate: forEach must resolve to an array
    if (!Array.isArray(forEachValue)) {
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "failed",
        phase: node.phase,
        executor: "dynamic-template",
        completedAt: now(),
        error: "forEach expression must resolve to an array",
      };
      appendFlowEvent(state, {
        type: "node_failed" as const,
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        error: "forEach must resolve to an array",
        data: { error: "forEach must resolve to an array", forEachExpr: executor.forEach },
      });
      changed = true;
      continue;
    }

    const maxItems = executor.maxItems ?? 100;
    const truncated = forEachValue.length > maxItems;
    const items = truncated ? forEachValue.slice(0, maxItems) : forEachValue;

    // Mark the source node as running
    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      status: "running",
      phase: node.phase,
      executor: "dynamic-template",
      startedAt: state.nodeStates[node.id]?.startedAt ?? now(),
    };

    // Initialize dynamic template runtime state
    state.dynamicTemplates ??= {};
    state.dynamicTemplates[node.id] = {
      sourceNodeId: node.id,
      templateName: executor.template,
      status: items.length > 0 ? "running" : "succeeded",
      iterationVar: executor.iterationVar,
      items: {},
      materializedCount: items.length,
      truncated,
    };

    if (items.length === 0) {
      // Empty array — complete immediately with no materialized nodes
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "succeeded",
        result: { materializedCount: 0, nodes: [] },
        completedAt: now(),
      };
      appendFlowEvent(state, {
        type: "node_succeeded" as const,
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: { executor: "dynamic-template", materializedCount: 0 },
      });
      changed = true;
      continue;
    }

    // Materialize each item
    for (let i = 0; i < items.length; i++) {
      const item = items[i];
      const { runtimeNodes, nodeIds } = materializeBody(
        templateDef.body,
        (bodyNodeId) => dynamicTemplateRuntimeNodeId(executor.template, node.id, i, bodyNodeId),
      );

      state.dynamicTemplates[node.id].items[String(i)] = {
        index: i,
        nodeIds,
      };

      // Store the iteration variable in workflowData so templates can reference {{item.path}} etc.
      // We use a namespaced key: __dt_${sourceNodeId}__${index}
      const itemKey = `__dt_${node.id}__${i}`;
      state.workflowData[itemKey] = item;

      // Also store as runtimeNodeMeta for template context injection
      state.runtimeNodeMeta ??= {};
      for (const rn of runtimeNodes) {
        state.runtimeNodeMeta[rn.id] = {
          loopId: node.id,
          iteration: i,
          bodyNodeId: rn.id.split("__").pop() ?? rn.id,
          iterationVar: executor.iterationVar,
        };
      }

      // Initialize nodeStates for each materialized node
      for (const rn of runtimeNodes) {
        state.nodeStates[rn.id] ??= {
          status: "pending",
          phase: rn.phase,
          executor: resolveExecutorType(rn, state.executionMode),
        };
      }

      // Track injected nodes
      state.injectedNodes ??= [];
      for (const rn of runtimeNodes) {
        state.injectedNodes.push({
          nodeId: rn.id,
          sourceNodeId: node.id,
          stepNum: i,
          materializedAt: now(),
        });
      }

      appendFlowEvent(state, {
        type: "node_materialized" as const,
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: {
          template: executor.template,
          index: i,
          runtimeNodeIds: Object.values(nodeIds),
        },
      });

      // ── Observability: emit node_materialized event for persistent logging ──
      if (deps?.eventEmitter) {
        deps.eventEmitter.emitMaterialized(flowId, state.workflowId, node.id, {
          templateName: executor.template,
          sourceNodeId: node.id,
          index: i,
        }).catch(() => { /* best-effort */ });
      }
    }

    if (truncated) {
      console.warn(
        `[controller] forEach truncated from ${forEachValue.length} to ${maxItems} items for node=${node.id}`,
      );
    }

    changed = true;
  }

  return changed;
}

/**
 * Check whether all materialized body nodes of dynamic-template nodes
 * have completed, and if so, aggregate their outputs and mark the
 * source node as succeeded/failed.
 */
function finalizeCompletedDynamicTemplates(
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
): { changed: boolean } {
  ensureFlowStateDefaults(state);
  let changed = false;

  for (const node of workflow.nodes.filter(isDynamicTemplateNode)) {
    const dtState = state.dynamicTemplates?.[node.id];
    if (!dtState || dtState.status === "succeeded" || dtState.status === "failed") continue;

    // Collect runtime node IDs across all items
    const allRuntimeNodeIds: string[] = [];
    for (const item of Object.values(dtState.items)) {
      allRuntimeNodeIds.push(...Object.values(item.nodeIds));
    }

    const runtimeStates = allRuntimeNodeIds.map((id) => state.nodeStates[id]);

    // If any runtime node is waiting, the template is waiting
    if (runtimeStates.some((s) => s?.status === "waiting")) {
      dtState.status = "waiting";
      changed = true;
      continue;
    }

    // If any runtime node failed, the template fails
    const failedNode = runtimeStates.find((s) => s?.status === "failed");
    if (failedNode) {
      dtState.status = "failed";
      dtState.error = failedNode.error ?? "materialized node failed";
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "failed",
        phase: node.phase,
        executor: "dynamic-template",
        completedAt: now(),
        error: dtState.error,
      };
      appendFlowEvent(state, {
        type: "node_failed" as const,
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        error: dtState.error ?? "materialized node failed",
        data: { error: dtState.error },
      });
      changed = true;
      continue;
    }

    // Check if all runtime nodes have completed (succeeded or skipped)
    const allDone = runtimeStates.every(
      (s) => s?.status === "succeeded" || s?.status === "skipped",
    );
    if (!allDone) continue; // still in progress

    // Aggregate outputs: collect results from terminal nodes of each item
    const results: Record<string, unknown>[] = [];
    for (const item of Object.values(dtState.items)) {
      // Find the "terminal" node in each item — the node with no dependent
      // within the same item. For simple single-node templates this is
      // just the only node.
      const itemNodeIds = Object.values(item.nodeIds);
      const terminalNodeId = itemNodeIds[itemNodeIds.length - 1]; // last node = terminal
      const nodeResult = state.nodeStates[terminalNodeId]?.result;
      results.push(nodeResult ?? {});
    }

    dtState.status = "succeeded";
    state.nodeStates[node.id] = {
      ...state.nodeStates[node.id],
      status: "succeeded",
      phase: node.phase,
      executor: "dynamic-template",
      result: {
        materializedCount: dtState.materializedCount,
        results,
      },
      completedAt: now(),
    };
    appendFlowEvent(state, {
      type: "node_succeeded" as const,
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      data: {
        executor: "dynamic-template",
        materializedCount: dtState.materializedCount,
        truncated: dtState.truncated ?? false,
      },
    });
    changed = true;
  }

  return { changed };
}

type FinalizeLoopOutcome =
  | { action: "continue"; changed: boolean }
  | { action: "failed" | "blocked"; nodeId: string; message: string };

/**
 * Within active loop iterations, if the until condition is already satisfied
 * by a completed body node's output (or by orWorkflowData), skip any remaining
 * pending body nodes. This prevents unnecessary work (e.g., sending an approval
 * card when the completeness check already passed).
 *
 * Returns the list of node IDs that were skipped.
 */
function skipLoopBodyNodesAfterUntilMatch(
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
): string[] {
  ensureFlowStateDefaults(state);
  const skipped: string[] = [];

  for (const loopNode of workflow.nodes.filter(isLoopGroupNode)) {
    const until = loopNode.executor.until;
    if (!until) continue;
    const loopState = state.loopGroups?.[loopNode.id];
    if (!loopState || loopState.status === "succeeded" || loopState.status === "failed" || loopState.status === "blocked") continue;

    const iterationState = loopState.iterations[String(loopState.currentIteration)];
    if (!iterationState) continue;

    // Check if the until node has completed in this iteration
    const untilRuntimeId = iterationState.nodeIds[until.node];
    if (!untilRuntimeId) continue;
    const untilNodeState = state.nodeStates[untilRuntimeId];
    if (!untilNodeState?.result) continue; // until node hasn't completed yet

    // Evaluate the until condition
    let actual: unknown = getPathValue(untilNodeState.result, until.path);
    const expected = until.equals;
    if (typeof expected === "boolean" && typeof actual === "string") {
      actual = actual === "true";
    } else if (typeof expected === "number" && typeof actual === "string") {
      actual = Number(actual);
      if (Number.isNaN(actual)) actual = undefined;
    }
    let untilMatched = actual === expected;

    // Also check orWorkflowData
    if (!untilMatched && until.orWorkflowData) {
      for (const [dataPath, expectedValue] of Object.entries(until.orWorkflowData)) {
        let actualValue: unknown = getPathValue(state.workflowData, dataPath);
        if (typeof expectedValue === "boolean" && typeof actualValue === "string") {
          actualValue = actualValue === "true";
        } else if (typeof expectedValue === "number" && typeof actualValue === "string") {
          actualValue = Number(actualValue);
          if (Number.isNaN(actualValue)) actualValue = undefined;
        }
        if (actualValue === expectedValue) {
          untilMatched = true;
          break;
        }
      }
    }

    if (!untilMatched) continue;

    // Until condition is satisfied — skip all pending body nodes in this iteration.
    // For approval body nodes with saveAs, we set status="succeeded" with a default
    // skipWhen result so that: (1) saveAs rules fire and persist into workflowData,
    // (2) the result is available in collectLoopIterationOutputs for downstream nodes,
    // and (3) orWorkflowData exit paths (e.g. supplement_approved=false) remain
    // consistent even when the approval node is bypassed by the until condition.
    for (const bodyNodeId of Object.values(iterationState.nodeIds)) {
      const bodyNodeState = state.nodeStates[bodyNodeId];
      if (bodyNodeState?.status !== "pending") continue;

      const runtimeMeta = state.runtimeNodeMeta?.[bodyNodeId];
      const bodyNodeDef = runtimeMeta
        ? loopNode.executor.body.find((b) => b.id === runtimeMeta.bodyNodeId)
        : undefined;
      const executor = bodyNodeDef?.executor;
      const isApproval = executor?.type === "approval";
      const approvalSaveAs = isApproval
        ? (executor as { saveAs?: Record<string, string> }).saveAs
        : undefined;

      // Determine the executor type for events
      const executorType = runtimeMeta
        ? (bodyNodeState?.executor ?? "approval")
        : (workflow.nodes.find((n) => n.id === bodyNodeId)?.executor.type ?? "unknown");

      if (isApproval) {
        // Approval node whose skipWhen would have matched — auto-succeed with
        // a default result so saveAs fires and lastIterationOutputs includes it.
        const skipResult: Record<string, unknown> = {
          approved: true,
          skipped: true,
          reason: "until_condition_satisfied",
        };
        state.nodeStates[bodyNodeId] = {
          ...bodyNodeState,
          status: "succeeded",
          completedAt: now(),
          result: skipResult,
          error: null,
        };

        // Apply approval saveAs so workflowData picks up values like
        // supplement_approved, supplement_note, risk_adjustment, etc.
        if (approvalSaveAs && Object.keys(approvalSaveAs).length > 0) {
          const nodeOutput = buildScopedNodeOutputContext(state, bodyNodeId);
          const saveAsContext: ActionExecutionContext = {
            flowId,
            workflowId: state.workflowId,
            nodeId: bodyNodeId,
            sessionKey: "",
            executionMode: state.executionMode ?? "private",
            bcsGroupId: state.bcsGroupId,
            params: state.params,
            input: state.input,
            workflowData: state.workflowData,
            nodeOutput,
            actionOutputs: state.actionOutputs ?? {},
            loop: runtimeMeta
              ? { id: runtimeMeta.loopId, iteration: runtimeMeta.iteration, bodyNodeId: runtimeMeta.bodyNodeId }
              : undefined,
            user: { id: undefined, name: undefined },
            workflow,
          };
          applySaveAs(state.workflowData, approvalSaveAs, skipResult, saveAsContext);
          console.log(`[skipLoopBodyNodesAfterUntilMatch] applied saveAs for approval node ${bodyNodeId} in loop ${loopNode.id}`);
        }

        skipped.push(bodyNodeId);
        appendAuditLog(state, bodyNodeId, "skipWhen-matched", "loop until condition satisfied — approval auto-succeeded");
        appendFlowEvent(state, {
          type: "node_succeeded",
          flowId,
          workflowId: state.workflowId,
          nodeId: bodyNodeId,
          data: { reason: "until_condition_satisfied", loopId: loopNode.id, untilNode: until.node, skipWhenMatched: true },
        });
        emitNodeEvent("node_succeeded", {
          flowId,
          workflowId: state.workflowId,
          nodeId: bodyNodeId,
          executorType,
          attempt: 1,
          sessionKey: undefined,
          sessionId: undefined,
          systemContext: {
            reason: "until_condition_satisfied",
            loopId: loopNode.id,
            untilNode: until.node,
            skipWhenMatched: true,
          },
        });
        console.log(`[skipLoopBodyNodesAfterUntilMatch] auto-succeeded approval ${bodyNodeId} in loop ${loopNode.id} — until condition satisfied`);
      } else {
        // Non-approval node: skip as before
        state.nodeStates[bodyNodeId] = {
          ...bodyNodeState,
          status: "skipped",
          completedAt: now(),
          error: "loop until condition already satisfied",
        };
        skipped.push(bodyNodeId);
        appendAuditLog(state, bodyNodeId, "skipped", "loop until condition already satisfied");
        appendFlowEvent(state, {
          type: "node_skipped",
          flowId,
          workflowId: state.workflowId,
          nodeId: bodyNodeId,
          data: { reason: "until_condition_satisfied", loopId: loopNode.id, untilNode: until.node },
          error: "loop until condition already satisfied",
        });
        emitNodeEvent("node_skipped", {
          flowId,
          workflowId: state.workflowId,
          nodeId: bodyNodeId,
          executorType,
          attempt: 1,
          error: "loop until condition already satisfied",
          sessionKey: undefined,
          sessionId: undefined,
          systemContext: {
            reason: "until_condition_satisfied",
            loopId: loopNode.id,
            untilNode: until.node,
          },
        });
        // Verbose chatInject: loop-body skip notification. Deps aren't available here,
        // so route through THIS flow's bound inject (resolved by flowId) to keep
        // it from landing in a concurrent flow's session.
        const loopFlowChatInject = resolveChatInjectForFlow(flowId);
        const loopFlowVerbosity = resolveInjectLevelForFlow(flowId);
        if (loopFlowChatInject && shouldInjectForFlow(flowId, "node-skipped")) {
          const bodyNode = workflow.nodes.find((n) => n.id === bodyNodeId);
          if (bodyNode) {
            notifyNodeSkipped(loopFlowChatInject, loopFlowVerbosity, flowId, bodyNode, "loop until condition already satisfied", "until_condition_satisfied");
          }
        }
        console.log(`[skipLoopBodyNodesAfterUntilMatch] skipped ${bodyNodeId} in loop ${loopNode.id} — until condition already satisfied`);
      }
    }
  }

  return skipped;
}

function finalizeCompletedLoopIterations(
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
): FinalizeLoopOutcome {
  ensureFlowStateDefaults(state);
  let changed = false;

  for (const node of workflow.nodes.filter(isLoopGroupNode)) {
    const loopState = state.loopGroups?.[node.id];
    if (!loopState || loopState.status === "succeeded" || loopState.status === "failed" || loopState.status === "blocked") {
      continue;
    }
    const iterationState = loopState.iterations[String(loopState.currentIteration)];
    if (!iterationState) continue;

    const runtimeNodeIds = Object.values(iterationState.nodeIds);
    const runtimeStates = runtimeNodeIds.map((runtimeId) => state.nodeStates[runtimeId]);
    if (runtimeStates.some((runtimeState) => runtimeState?.status === "waiting")) {
      loopState.status = "waiting";
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "running",
        phase: node.phase,
        executor: "loop-group",
      };
      changed = true;
      continue;
    }

    const blocked = runtimeStates.find((runtimeState) => runtimeState?.status === "blocked");
    if (blocked) {
      loopState.status = "blocked";
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "blocked",
        phase: node.phase,
        executor: "loop-group",
        error: blocked.error ?? "loop body node blocked",
      };
      appendFlowEvent(state, {
        type: "loop_failed",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        error: state.nodeStates[node.id].error ?? undefined,
      });
      return { action: "blocked", nodeId: node.id, message: state.nodeStates[node.id].error ?? "loop blocked" };
    }

    const failed = runtimeStates.find((runtimeState) => runtimeState?.status === "failed");
    if (failed) {
      const error = failed.error ?? "loop body node failed";
      loopState.status = "failed";
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "failed",
        phase: node.phase,
        executor: "loop-group",
        completedAt: now(),
        error,
      };
      appendFlowEvent(state, {
        type: "loop_failed",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        error,
      });
      return { action: "failed", nodeId: node.id, message: error };
    }

    if (loopState.status === "waiting") {
      loopState.status = "running";
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "running",
        phase: node.phase,
        executor: "loop-group",
      };
      changed = true;
    }

    if (!runtimeStates.every((runtimeState) => isSuccessfulTerminalStatus(runtimeState?.status))) {
      continue;
    }

    iterationState.status = runtimeStates.every((runtimeState) => runtimeState?.status === "skipped") ? "skipped" : "succeeded";
    iterationState.completedAt = iterationState.completedAt ?? now();
    appendFlowEvent(state, {
      type: "loop_iteration_completed",
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      data: {
        loopId: node.id,
        iteration: loopState.currentIteration,
        status: iterationState.status,
      },
    });

    const until = node.executor.until;
    const untilResult = until
      ? state.nodeStates[iterationState.nodeIds[until.node]]?.result
      : undefined;
    let untilMatched = false;
    if (until) {
      let actual = getPathValue(untilResult, until.path);
      const expected = until.equals;
      // Coerce for comparison: boolean "true"/"false", numbers, etc.
      if (typeof expected === "boolean" && typeof actual === "string") {
        actual = actual === "true";
      } else if (typeof expected === "number" && typeof actual === "string") {
        actual = Number(actual);
        if (Number.isNaN(actual)) actual = undefined;
      }
      console.log(`[evaluateUntil] loop=${node.id} path=${until.path} expected=${JSON.stringify(expected)} actual=${JSON.stringify(actual)} match=${actual === expected}`);
      untilMatched = actual === expected;
    }

    // Check orWorkflowData conditions — if ANY workflowData path matches
    // its expected value, exit the loop even if the primary until hasn't matched.
    let orWorkflowDataMatched = false;
    let orWorkflowDataMatchKey: string | undefined;
    if (until?.orWorkflowData && !untilMatched) {
      for (const [dataPath, expectedValue] of Object.entries(until.orWorkflowData)) {
        let actualValue: unknown = getPathValue(state.workflowData, dataPath);
        // Coerce for comparison (same logic as until)
        if (typeof expectedValue === "boolean" && typeof actualValue === "string") {
          actualValue = actualValue === "true";
        } else if (typeof expectedValue === "number" && typeof actualValue === "string") {
          actualValue = Number(actualValue);
          if (Number.isNaN(actualValue)) actualValue = undefined;
        }
        if (actualValue === expectedValue) {
          orWorkflowDataMatched = true;
          orWorkflowDataMatchKey = dataPath;
          break;
        }
      }
      if (orWorkflowDataMatched) {
        console.log(`[evaluateUntil] loop=${node.id} orWorkflowData matched: ${orWorkflowDataMatchKey}, exiting loop`);
      }
    }

    const lastIterationOutputs = collectLoopIterationOutputs(node, state, loopState, loopState.currentIteration);

    if (untilMatched || orWorkflowDataMatched) {
      const exitReason = orWorkflowDataMatched ? "until-workflow-data-matched" : "until-matched";
      setLoopParentResult({
        state,
        loopNode: node,
        loopState,
        status: "succeeded",
        untilMatched: true,
        exitReason,
        lastIterationOutputs,
      });
      appendFlowEvent(state, {
        type: "loop_completed",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: state.nodeStates[node.id].result,
      });
      changed = true;
      continue;
    }

    if (loopState.currentIteration < loopState.maxIterations) {
      loopState.currentIteration += 1;
      loopState.status = "running";
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "running",
        phase: node.phase,
        executor: "loop-group",
      };
      materializeLoopRuntimeIteration({
        state,
        loopNode: node,
        loopState,
        iteration: loopState.currentIteration,
        flowId,
      });
      changed = true;
      continue;
    }

    const onMaxIterations = node.executor.onMaxIterations ?? { action: "fail" as const };
    if (onMaxIterations.action === "continue") {
      setLoopParentResult({
        state,
        loopNode: node,
        loopState,
        status: "succeeded",
        untilMatched: false,
        exitReason: "max-iterations-continue",
        ...(onMaxIterations.saveLastIteration ? { lastIterationOutputs } : {}),
      });
      appendFlowEvent(state, {
        type: "loop_completed",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: state.nodeStates[node.id].result,
      });
      changed = true;
      continue;
    }

    const error = `Loop ${node.id} reached max iterations (${loopState.maxIterations})`;
    setLoopParentResult({
      state,
      loopNode: node,
      loopState,
      status: "failed",
      untilMatched: false,
      exitReason: "max-iterations-fail",
      ...(onMaxIterations.saveLastIteration ? { lastIterationOutputs } : {}),
      error,
    });
    appendFlowEvent(state, {
      type: "loop_failed",
      flowId,
      workflowId: state.workflowId,
      nodeId: node.id,
      data: state.nodeStates[node.id].result,
      error,
    });
    return { action: "failed", nodeId: node.id, message: error };
  }

  return { action: "continue", changed };
}

function finalizeLoopAfterRuntimeNodeBlocked(
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
  runtimeNodeId: string,
): boolean {
  ensureFlowStateDefaults(state);
  const runtimeMeta = state.runtimeNodeMeta?.[runtimeNodeId];
  if (!runtimeMeta) return false;

  const loopNode = workflow.nodes.find((node) => node.id === runtimeMeta.loopId);
  if (!loopNode || !isLoopGroupNode(loopNode)) return false;

  const loopState = state.loopGroups?.[runtimeMeta.loopId];
  if (!loopState || loopState.status === "blocked") return false;

  const runtimeNodeState = state.nodeStates[runtimeNodeId];
  const error = runtimeNodeState?.error ?? "loop body node blocked";
  loopState.status = "blocked";
  state.nodeStates[loopNode.id] = {
    ...state.nodeStates[loopNode.id],
    status: "blocked",
    phase: loopNode.phase,
    executor: "loop-group",
    error,
  };
  appendFlowEvent(state, {
    type: "loop_failed",
    flowId,
    workflowId: state.workflowId,
    nodeId: loopNode.id,
    error,
  });
  return true;
}

async function executeLoop(
  deps: ControllerDeps,
  workflow: WorkflowSpec,
  state: FlowState,
  flowId: string,
  currentRevision: number,
): Promise<ExecuteLoopOutcome> {
  // Set global chatInject so module-scope functions (completeFlowRun, emitNodeEvent)
  // can send notifications to teclaw when flow completes or nodes fail.
  setGlobalChatInject(deps.chatInject, flowId);
  setFlowInjectLevel(deps.chatInjectLevel ?? "full", flowId);
  _botId = deps.botId ?? null;
  _ownerId = deps.ownerId ?? null;
  _sessionKey = deps.sessionKey ?? null;

  let revision = currentRevision;

  // ── Loop-stage tracing (debug_loop_stage) ──
  // Diagnoses whether inter-node gaps are caused by DB persistence
  // (boundTaskFlow writes) or by chat.inject back-pressure.
  // Each mark records the loop iteration counter, a stage name and the
  // current node id so post-processing can compute per-stage durations.
  let loopIter = 0;
  const mark = (stage: string, nodeId?: string): void => {
    void appendWorkflowJsonlLog(
      buildDirectLogRecord({
        flowId,
        eventType: "debug_loop_stage",
        message: `[debug-loop] ${stage}`,
        nodeId: nodeId ?? null,
        botId: _botId,
        sessionKey: _sessionKey,
        details: {
          stage,
          loop_iter: loopIter,
          node_id: nodeId ?? null,
          phase: state.currentPhase ?? null,
        },
      }),
    ).catch(() => { /* best-effort log */ });
  };

  const persistFinalizeOutcome = async (outcome: FinalizeLoopOutcome): Promise<ExecuteLoopOutcome | null> => {
    if (outcome.action === "continue") return null;
    if (outcome.action === "blocked") {
      const effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
      applyPhaseAndStatus(effectiveWorkflow, state);
      const waitState: WaitState = {
        kind: "platform-workflow",
        workflowId: state.workflowId,
        params: state.params,
        activeNodes: state.activeNodes,
        waitingFor: "loop-blocked",
        hint: `${outcome.nodeId} 阻塞: ${outcome.message}`,
        userAction: `${formatWorkflowCommand(deps, state.workflowId, "inspect", [flowId])} 查看最新状态`,
      };
      revision = await blockFlow(
        deps,
        flowId,
        revision,
        state,
        waitState,
        `${outcome.nodeId} 阻塞: ${outcome.message}`,
        outcome.nodeId,
      );
      // Flow control: release slots on terminal state
      deps.flowControl?.releaseAllForFlow(flowId);
      return { status: "blocked", message: outcome.message };
    }

    // BUG-25: non-conflict persistence failures remain best-effort. A revision
    // conflict must escape so a stale local worker cannot close another writer's flow.
    try {
      await failWithRevisionRetry(deps, flowId, revision, state, {
        blockedSummary: `${outcome.nodeId} 失败: ${outcome.message}`,
        endedAt: now(),
      });
    } catch (failErr) {
      if (isFlowStateConflict(failErr)) throw failErr;
      const errMsg = failErr instanceof Error ? failErr.message : String(failErr);
      console.error(`[controller] persistFinalizeOutcome: boundTaskFlow.fail() threw for flowId=${flowId}:`, failErr);
      enqueueRunLog({
        flow_id: flowId,
        level: "error",
        source: "engine",
        message: `boundTaskFlow.fail() failed in persistFinalizeOutcome: ${errMsg}`,
        timestamp: Date.now(),
      });
    }
    console.log(`[controller] FLOW_FAILED flowId=${flowId} reason=finalize_outcome_failed node=${outcome.nodeId} error=${(outcome.message ?? "").slice(0, 100)}`);
    completeFlowRun(flowId, "failed", state.currentPhase, outcome.message, computeDurationMs(state), state);
    // Flow control: release slots on terminal state
    deps.flowControl?.releaseAllForFlow(flowId);
    await deps.chatInject(`${outcome.nodeId} 执行失败: ${outcome.message}`, `${flowId}:${outcome.nodeId}:failed`);
    return { status: "failed", nodeIds: [outcome.nodeId], message: outcome.message };
  };

  // eslint-disable-next-line no-constant-condition
  while (true) {
    loopIter += 1;
    mark("loop_top");

    // ── Cross-process terminal-state guard ──
    // Check if the flow has already been marked as terminal (failed/succeeded)
    // by an external writer (e.g., the flow-timeout watchdog running in a
    // different process). The in-memory TaskFlow revision CAS cannot detect
    // this because TaskFlow state is process-local (memory Map or per-process
    // file system), so the watchdog's boundTaskFlow.fail() in process A does
    // not cause a revision conflict in process B's executeLoop.
    //
    // This check queries flow_runs.status directly from the DB, which is
    // shared across processes. If the flow is already terminal, exit the loop
    // immediately without starting new nodes.
    if (_flowRunRepository) {
      try {
        const _flowRow = await _flowRunRepository.findByFlowId(flowId);
        if (_flowRow?.status && _flowRow.status !== "running" && _flowRow.status !== "waiting" && _flowRow.status !== "blocked") {
          console.warn(
            `[controller] executeLoop: flowId=${flowId} already terminal (status=${_flowRow.status}) in flow_runs — ` +
            `exiting loop (external writer, e.g. watchdog). nodes_started=${loopIter - 1}`,
          );
          // NOTE: reconcileStaleRunning is intentionally NOT called here.
          // The normal completion path (completeFlowRun) handles it synchronously.
          // When an external writer marks the flow as terminal, the reconcile is
          // skipped here — the watchdog or the next reconcile cycle will pick up
          // any stale running nodes. See Phase 2.4 of system-architecture-hardening.
          deps.flowControl?.releaseAllForFlow(flowId);
          if (_flowRow.status === "succeeded") {
            return { status: "finished", message: `流程已被外部标记为 succeeded` };
          }
          return { status: "failed", nodeIds: [], message: `流程已被外部标记为 ${_flowRow.status}` };
        }
      } catch (e) {
        // DB query failed — don't block the loop, just log and continue.
        console.warn(`[controller] executeLoop: flow_runs status check failed for flowId=${flowId}: ${(e as Error)?.message ?? e}`);
      }
    }

    // BUG-10 heartbeat: Touch gmt_modified on each loop iteration.
    // This ensures the flow_run row is updated even during long LLM calls
    // (5-15 min embedded-agent executions) so that time-based fallback
    // zombie detection (1-hour threshold) doesn't falsely kill active flows.
    // The primary fix is session-liveness detection, but this is a defense-in-depth.
    syncFlowRunPhase(flowId, state.currentPhase);

    // ── Budget check: enforce limits before processing more nodes ──
    if (workflow.budget && deps.eventEmitter) {
      const { BudgetEnforcer } = await import("./budget/enforcer.js");
      const { createBudgetEventCallback } = await import("./budget/observability.js");
      const eventCallback = createBudgetEventCallback(
        deps.eventEmitter,
        flowId,
        state.workflowId,
        "__flow_budget__",
      );
      const enforcer = new BudgetEnforcer(workflow.budget, eventCallback);
      // Track injected nodes from state
      if (state.injectedNodes) {
        enforcer.tracker.recordInjectedNode(state.injectedNodes.length);
      }
      const budgetResult = enforcer.enforce(
        typeof state.workflowData.__flowStartedAt === "number"
          ? state.workflowData.__flowStartedAt
          : Date.now()
      );
      if (!budgetResult.allowed) {
        appendFlowEvent(state, {
          type: "budget_exhausted" as const,
          flowId,
          workflowId: state.workflowId,
          nodeId: "__budget__",
          data: { reason: budgetResult.reason, strategy: workflow.budget.strategy ?? "hard-stop" },
          error: budgetResult.reason,
        });
        console.warn(`[controller] Budget exhausted for flowId=${flowId}: ${budgetResult.reason}`);
        enqueueRunLog({
          flow_id: flowId,
          level: "error",
          source: "workflow",
          message: `Budget exhausted: ${budgetResult.reason}`,
          timestamp: Date.now(),
        });
        deps.flowControl?.releaseAllForFlow(flowId);
        return { status: "failed", nodeIds: [], message: budgetResult.reason };
      }
    }

    const pendingHooks = await runPendingHooks(deps, workflow, state, flowId, revision);
    revision = pendingHooks.revision;
    if (pendingHooks.blocked) {
      // Flow control: release slots on terminal state (blocked by hooks)
      deps.flowControl?.releaseAllForFlow(flowId);
      return { status: "blocked" };
    }

    prepareLoopGroupsForExecution(workflow, state, flowId);
    prepareDynamicTemplatesForExecution(workflow, state, flowId, deps);
    let effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

    const skippableNodes = findSkippableNodesFixedPoint(effectiveWorkflow, state.nodeStates);
    for (const node of skippableNodes) {
      state.nodeStates[node.id] = {
        ...state.nodeStates[node.id],
        status: "skipped",
        completedAt: now(),
        error: `dependencies did not satisfy triggerRule ${node.triggerRule ?? node.join ?? "all_success"}`,
      };
      appendAuditLog(state, node.id, "skipped", state.nodeStates[node.id].error ?? "skipped");
      enqueueRunLog({
        flow_id: flowId,
        node_id: node.id,
        level: "warn",
        source: "node",
        message: `Node skipped: ${node.id}, triggerRule=${node.triggerRule ?? node.join ?? "all_success"}`,
        timestamp: Date.now(),
      });
      appendFlowEvent(state, {
        type: "node_skipped",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: { triggerRule: node.triggerRule ?? node.join ?? "all_success" },
        error: state.nodeStates[node.id].error,
      });
      emitNodeEvent("node_skipped", {
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        executorType: node.executor.type,
        attempt: 1,
        nodeTitle: node.title ?? undefined,
        error: state.nodeStates[node.id].error,
        sessionKey: deps.sessionKey,
        sessionId: deps.sessionId,
        systemContext: {
          reason: "trigger_rule_not_satisfied",
          triggerRule: node.triggerRule ?? node.join ?? "all_success",
        },
      });
      notifyNodeSkipped(deps.chatInject, deps.chatInjectLevel ?? "full", flowId, node, state.nodeStates[node.id].error ?? `triggerRule ${node.triggerRule ?? node.join ?? "all_success"} not satisfied`, node.triggerRule ?? node.join ?? "all_success");
    }
    if (skippableNodes.length > 0) {
      applyPhaseAndStatus(effectiveWorkflow, state);
      syncFlowRunPhase(flowId, state.currentPhase);
    }

    const finalizedAfterSkips = finalizeCompletedLoopIterations(workflow, state, flowId);
    mark("persist_skips_pre");
    const finalizedAfterSkipsOutcome = await persistFinalizeOutcome(finalizedAfterSkips);
    mark("persist_skips_post");
    if (finalizedAfterSkipsOutcome) return finalizedAfterSkipsOutcome;
    const dtFinalizedAfterSkips = finalizeCompletedDynamicTemplates(workflow, state, flowId);
    effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
    if (finalizedAfterSkips.action === "continue" && finalizedAfterSkips.changed) {
      applyPhaseAndStatus(effectiveWorkflow, state);
    }
    if (dtFinalizedAfterSkips.changed) {
      applyPhaseAndStatus(effectiveWorkflow, state);
    }

    // ── Skip pending body nodes within loop iterations where until condition
    // is already satisfied. When a loop-group's until node has completed and
    // its output matches the until condition (or orWorkflowData matches), any
    // remaining pending body nodes in the same iteration are skipped — they
    // are unnecessary since the loop is about to exit.
    // This prevents e.g. an approval card being sent after completeness check
    // already returned is_complete=true.
    const skippedByUntil = skipLoopBodyNodesAfterUntilMatch(workflow, state, flowId);
    if (skippedByUntil.length > 0) {
      effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
      applyPhaseAndStatus(effectiveWorkflow, state);

      // BUG-FIX: Re-evaluate loop exit after skipping body nodes.
      // skipLoopBodyNodesAfterUntilMatch may set pending body nodes to terminal
      // status ("skipped" for non-approval nodes, "succeeded" for approval nodes
      // with saveAs), which can make all runtime nodes in the iteration reach
      // a successful terminal status. Without re-running
      // finalizeCompletedLoopIterations here, the loop parent node remains
      // "running" forever because the earlier finalize call (line ~8496) saw
      // those body nodes as still "pending" and could not evaluate the until
      // condition. This caused flows like completeness_loop (where
      // check_completeness returns is_complete=true and request_supplement
      // gets auto-succeeded/skipped) to stall indefinitely.
      const finalizedAfterSkip = finalizeCompletedLoopIterations(workflow, state, flowId);
      const finalizedAfterSkipOutcome = await persistFinalizeOutcome(finalizedAfterSkip);
      if (finalizedAfterSkipOutcome) return finalizedAfterSkipOutcome;
      effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
      if (finalizedAfterSkip.action === "continue" && finalizedAfterSkip.changed) {
        applyPhaseAndStatus(effectiveWorkflow, state);
      }
    }

    const readyNodes = getReadyNodes(effectiveWorkflow, state.nodeStates)
      .filter((node) => node.executor.type !== "loop-group");
    mark("ready_found", readyNodes.length > 0 ? readyNodes.map((n) => n.id).join(",").slice(0, 120) : undefined);
    if (readyNodes.length === 0) {
      if (isWorkflowComplete(effectiveWorkflow, state.nodeStates) || isWorkflowTerminal(effectiveWorkflow, state.nodeStates)) {
        if (await failCompletedWorkflowIfNeeded({ deps, workflow, state, flowId, revision })) {
          deps.flowControl?.releaseAllForFlow(flowId);
          return { status: "failed", nodeIds: Object.keys(state.nodeStates).filter((id) => state.nodeStates[id]?.status === "failed") };
        }
        appendAuditLog(state, "-", "flow-finished", `${workflow.title} 全流程完成`);

        const finishOutcome = await runFlowFinishHooks({
          deps,
          workflow,
          state,
          flowId,
          revision,
        });
        revision = finishOutcome.revision;
        if (finishOutcome.blocked) {
          deps.flowControl?.releaseAllForFlow(flowId);
          return { status: "blocked" };
        }

        resolveAndStoreWorkflowOutputs(workflow, state);
        appendFlowEvent(state, {
          type: "workflow_finished",
          flowId,
          workflowId: state.workflowId,
          data: { title: workflow.title },
        });

        // Post-workflow analysis (best-effort, non-blocking)
        const flowStartTime = firstFlowEventTime(state);
        if (flowStartTime > 0) {
          void runPostWorkflowAnalysis(state.workflowId, flowId, flowStartTime).catch((err) => {
            console.error(`[controller] runPostWorkflowAnalysis failed for flowId=${flowId}:`, err);
          });
        }

        await persistSuccessfulFlowFinish({ deps, state, flowId, revision });

        // Persist flow run completion to engine DB
        console.log(`[controller] FLOW_SUCCEEDED flowId=${flowId} reason=no_ready_nodes_workflow_complete workflow=${workflow.id}`);
        completeFlowRun(flowId, "succeeded", state.currentPhase, undefined, computeDurationMs(state), state);

        // Flow control: release slots on terminal state
        deps.flowControl?.releaseAllForFlow(flowId);

        const msg = resolveWorkflowFinishedMessage(workflow, state, deps.skillRoot, flowId, resolveUserIdentityForContext(deps));
        if (msg) await deps.chatInject(msg, `${flowId}:flow:finished`);

        return { status: "finished" };
      }
      if (skippableNodes.length > 0) {
        const resumeResult = await deps.boundTaskFlow.resume({
          flowId,
          expectedRevision: revision,
          currentStep: state.activeNodes[0] ?? skippableNodes[0]?.id ?? "loop",
          stateJson: JSON.stringify(state),
        });
        if (!resumeResult.applied) throw new Error("状态更新冲突，请重试");
        revision = (resumeResult.flow as Record<string, unknown>).revision as number;
        // Re-evaluate from loop top: skipping nodes may unblock other
        // pending nodes or cause isWorkflowComplete to return true.
        // Previously we returned "running" here, which required an
        // external event (confirm/resume) to re-enter the loop —
        // but no such event would arrive for auto-skipped nodes,
        // causing the flow to stall indefinitely.
        continue;
      }
      return { status: "running" };
    }

    for (const node of readyNodes) {
      appendFlowEvent(state, {
        type: "node_ready",
        flowId,
        workflowId: state.workflowId,
        nodeId: node.id,
        data: {
          title: node.title,
          executor: resolveExecutorType(node, state.executionMode),
        },
      });
    }

    // split into parallel candidates and sequential nodes.
    // For embedded-agent candidates, apply the parallel guards (history
    // isolation + non-empty session lane) and the concurrency cap; any node
    // failing a guard or exceeding the cap is downgraded to sequential to
    // avoid session-file corruption, lane collapse, and LLM 429 bursts.
    // For cli-script candidates, only apply a concurrency cap to prevent
    // unintended process explosion — no session or LLM resources are shared.
    const parallelNodes: WorkflowNode[] = [];
    const sequentialNodes: WorkflowNode[] = [];
    let embeddedParallelCount = 0;
    let cliScriptParallelCount = 0;
    for (const node of readyNodes) {
      if (!isParallelCandidate(node, state.executionMode)) {
        sequentialNodes.push(node);
        continue;
      }
      const resolvedType = resolveExecutorType(node, state.executionMode);
      if (resolvedType === "embedded-agent") {
        if (
          !canRunEmbeddedAgentInParallel(node, workflow, state.executionMode, deps.sessionKey)
          || embeddedParallelCount >= MAX_PARALLEL_EMBEDDED_AGENTS
        ) {
          sequentialNodes.push(node);
          continue;
        }
        embeddedParallelCount++;
      } else if (resolvedType === "cli-script") {
        if (cliScriptParallelCount >= MAX_PARALLEL_CLI_SCRIPTS) {
          sequentialNodes.push(node);
          continue;
        }
        cliScriptParallelCount++;
      }
      parallelNodes.push(node);
    }

    // ── Execute parallel nodes (subagent / bcs-route) with Promise.allSettled ──
    if (parallelNodes.length > 1) {
      const shouldBatchBcsRoutes = state.executionMode === "bcs-group"
        && parallelNodes.every((node) => isBcsBatchableRouteNode(node, state.executionMode));

      if (shouldBatchBcsRoutes) {
        const batchOutcome = isBcsApprovalBatchApi(deps.api)
          ? await executeBcsApprovalBatch({
              nodes: parallelNodes,
              workflow: effectiveWorkflow,
              flowState: state,
              flowId,
              skillRoot: deps.skillRoot,
              api: deps.api,
            })
          : {
              status: "failed" as const,
              error: "缺少插件 API，无法批量分发 BCS 审批",
            };

        if (batchOutcome.status === "failed") {
          for (const node of parallelNodes) {
            state.nodeStates[node.id] = {
              ...state.nodeStates[node.id],
              status: "blocked",
              phase: node.phase,
              executor: getLegacyApprovalExecutor(node)
                ? node.executor.type
                : resolveExecutorType(node, state.executionMode),
              error: batchOutcome.error,
              waitKind: "bcs-approval-batch-dispatch-failed",
            };
            appendAuditLog(state, node.id, "batch-dispatch-failed", batchOutcome.error);
            appendFlowEvent(state, {
              type: "node_failed",
              flowId,
              workflowId: state.workflowId,
              nodeId: node.id,
              data: { executor: "bcs-route", dispatch: "batch" },
              error: batchOutcome.error,
            }, { rawError: "rawError" in batchOutcome ? batchOutcome.rawError : undefined });
          }

          applyPhaseAndStatus(effectiveWorkflow, state);
          state.activeNodes = parallelNodes.map((node) => node.id);
          const recoveryHint = formatApprovalRecoveryCommandHint(deps, workflow, state);
          const hint = [
            batchOutcome.error,
            recoveryHint,
          ].filter(Boolean).join("\n");
          const waitState: WaitState = {
            kind: "platform-workflow",
            workflowId: state.workflowId,
            params: state.params,
            activeNodes: state.activeNodes,
            waitingFor: "bcs-route-responses",
            pending: state.activeNodes,
            hint,
            userAction: recoveryHint ?? "/workflow inspect 查看进度",
          };
          await blockFlow(
            deps,
            flowId,
            revision,
            state,
            waitState,
            `BCS 协作批量分发失败: ${batchOutcome.error}`,
            state.activeNodes[0] ?? "bcs-collaboration-batch",
          );
          return { status: "blocked", message: batchOutcome.error };
        }

        const tasksByNodeId = new Map(batchOutcome.batch.tasks.map((task) => [task.nodeId, task]));
        const waitingNodes = parallelNodes.map((node) => node.id);
        for (const node of parallelNodes) {
          const task = tasksByNodeId.get(node.id);
          state.nodeStates[node.id] = {
            ...initNodeState(node, "bcs-route", state.nodeStates[node.id]),
            status: "waiting",
            bcsApproval: task
              ? {
                  protocolVersion: task.protocolVersion,
                  batchId: task.batchId,
                  taskId: task.taskId,
                  workflowId: task.workflowId,
                  flowId: task.flowId,
                  nodeId: task.nodeId,
                  taskKind: task.taskKind,
                  skillId: task.skillId,
                  participant: task.participant,
                  route: task.route,
                }
              : undefined,
          };
          appendFlowEvent(state, {
            type: "node_started",
            flowId,
            workflowId: state.workflowId,
            nodeId: node.id,
            attempt: 1,
            data: { executor: "bcs-route", dispatch: "batch" },
          });
          appendFlowEvent(state, {
            type: "node_waiting",
            flowId,
            workflowId: state.workflowId,
            nodeId: node.id,
            data: { executor: "bcs-route", batchId: task?.batchId, taskId: task?.taskId },
          });
          appendAuditLog(state, node.id, "waiting", batchOutcome.waitPrompt);
        }

        const finalizedWaitingBatch = finalizeCompletedLoopIterations(workflow, state, flowId);
        const finalizedWaitingBatchOutcome = await persistFinalizeOutcome(finalizedWaitingBatch);
        if (finalizedWaitingBatchOutcome) return finalizedWaitingBatchOutcome;
        effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
        applyPhaseAndStatus(effectiveWorkflow, state);

        const waitState: WaitState = {
          kind: "platform-workflow",
          workflowId: state.workflowId,
          params: state.params,
          activeNodes: waitingNodes,
          waitingFor: "bcs-route-responses",
          received: [],
          pending: waitingNodes,
          hint: batchOutcome.waitPrompt,
          userAction: "/workflow inspect 查看进度",
        };

        const setResult = await setWaitingWithRevisionRetry(
          deps, flowId, revision,
          waitingNodes[0] ?? parallelNodes[0].id,
          state, waitState, batchOutcome.waitPrompt,
        );

        revision = (setResult.flow as Record<string, unknown>).revision as number;

        await deps.chatInject(
          batchOutcome.waitPrompt,
          `${flowId}:bcs-approval-batch:waiting`,
        );

        return { status: "waiting", nodeIds: waitingNodes, message: batchOutcome.waitPrompt };
      }

      for (const node of parallelNodes) {
        const executorType = resolveExecutorType(node, state.executionMode);
        state.nodeStates[node.id] = initNodeState(node, executorType, state.nodeStates[node.id]);
      }

      // Async progress: notify parallel node execution started
      if (loadConfig().app.execution.asyncRun && shouldInjectForFlow(flowId, "parallel-progress")) {
        const nodeNames = parallelNodes.map((n) => `▶ ${n.title ?? n.id}`).join("\n");
        fireProgressChatInject(
          deps,
          `${parallelNodes.length} 个并行任务开始执行：\n${nodeNames}`,
          `${flowId}:parallel:started-async`,
        );
      } else {
        await deps.chatInject(
          `已发起 ${parallelNodes.length} 个并行任务：${parallelNodes.map((node) => node.title).join("、")}`,
          `${flowId}:parallel:started`,
        );
      }

      // Note: Executor-level flow control is not applied to parallel nodes here.
      // Parallel nodes are already gated by workflow-level flow control (Task 9).
      // If per-executor throttling is needed for parallel nodes, it should be
      // applied inside individual executor implementations.
      const promises = parallelNodes.map(async (node) => {
        const templateCtx = buildTemplateContext(state, deps.skillRoot, {}, { currentNodeId: node.id, userIdentity: resolveUserIdentityForContext(deps) });
        const result = await executeNodeWithRetry(deps, node, templateCtx, state, flowId, effectiveWorkflow);
        return { node, result };
      });

      const settled = await Promise.allSettled(promises);
      const settledOutcome = await handleParallelNodeSettledResults({
        deps,
        workflow: effectiveWorkflow,
        state,
        flowId,
        revision,
        parallelNodes,
        settled,
      });
      if (settledOutcome.action === "blocked") {
        deps.flowControl?.releaseAllForFlow(flowId);
        return { status: "blocked" };
      }
      const { hasWaiting, hasFailed, waitingNodes } = settledOutcome;
      // Log parallel node execution outcomes
      {
        const nodeSummary = parallelNodes.map((n) => {
          const s = state.nodeStates[n.id]?.status ?? "unknown";
          const e = state.nodeStates[n.id]?.error;
          return `${n.id}=${s}${e ? `(${e.slice(0, 50)})` : ""}`;
        }).join(", ");
        console.log(`[controller] PARALLEL_NODES_COMPLETE flowId=${flowId} hasFailed=${hasFailed} hasWaiting=${hasWaiting} nodes=[${nodeSummary}]`);
      }

      // Async progress: notify parallel node results
      if (loadConfig().app.execution.asyncRun && shouldInjectForFlow(flowId, "parallel-progress")) {
        const failedNodesAsync = parallelNodes.filter((n) => state.nodeStates[n.id]?.status === "failed");
        for (const fn of failedNodesAsync) {
          const nodeError = state.nodeStates[fn.id]?.error;
          fireProgressChatInject(
            deps,
            `❌ ${fn.title ?? fn.id} 执行失败${nodeError ? `: ${nodeError.slice(0, 120)}` : ""}`,
            `${flowId}:${fn.id}:flow-error`,
          );
        }
      }

      const finalizedAfterParallel = finalizeCompletedLoopIterations(workflow, state, flowId);
      const finalizedAfterParallelOutcome = await persistFinalizeOutcome(finalizedAfterParallel);
      if (finalizedAfterParallelOutcome) return finalizedAfterParallelOutcome;
      effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

      const failedIds = hasFailed
        ? parallelNodes
          .filter((n) => state.nodeStates[n.id]?.status === "failed")
          .map((n) => n.id)
        : [];
      const hasDeferredFinalizer = failedIds.length > 0
        && failedIds.every((nodeId) => hasReachableAllDoneFinalizer(effectiveWorkflow, state.nodeStates, nodeId));

      if (hasFailed && !hasDeferredFinalizer) {
        // BUG-25: non-conflict persistence failures remain best-effort. A revision
        // conflict must escape so a stale local worker cannot close another writer's flow.
        try {
          await failWithRevisionRetry(deps, flowId, revision, state, {
            blockedSummary: `节点失败: ${failedIds.join(", ")}`,
            endedAt: now(),
          });
        } catch (failErr) {
          if (isFlowStateConflict(failErr)) throw failErr;
          const errMsg = failErr instanceof Error ? failErr.message : String(failErr);
          console.error(`[controller] parallelNodes: boundTaskFlow.fail() threw for flowId=${flowId}:`, failErr);
          enqueueRunLog({
            flow_id: flowId,
            level: "error",
            source: "engine",
            message: `boundTaskFlow.fail() failed in parallelNodes: ${errMsg}`,
            timestamp: Date.now(),
          });
        }
        const failedNodes = parallelNodes
          .filter((node) => state.nodeStates[node.id]?.status === "failed");
        const failedErrors = failedNodes
          .map((node) => state.nodeStates[node.id]?.error)
          .filter(Boolean)
          .join("; ");
        console.log(`[controller] FLOW_FAILED flowId=${flowId} reason=parallel_nodes_failed nodes=[${failedIds.join(",")}] errors=${(failedErrors ?? "").slice(0, 120)}`);
        completeFlowRun(flowId, "failed", state.currentPhase, failedErrors || undefined, computeDurationMs(state), state);
        // Flow control: release slots on terminal state
        deps.flowControl?.releaseAllForFlow(flowId);
        await deps.chatInject(
          formatParallelFailureHint(deps, failedNodes, workflow, state, flowId),
          `${flowId}:parallel:failed`,
        );
        return { status: "failed", nodeIds: failedIds };
      }

      if (hasWaiting) {
        applyPhaseAndStatus(effectiveWorkflow, state);

        const succeededNodes = parallelNodes
          .filter((n) => state.nodeStates[n.id]?.status === "succeeded")
          .map((n) => n.id);

        const waitState: WaitState = {
          kind: "platform-workflow",
          workflowId: state.workflowId,
          params: state.params,
          activeNodes: waitingNodes,
          waitingFor: state.executionMode === "bcs-group" ? "bcs-route-responses" : "subagent-responses",
          received: succeededNodes,
          pending: waitingNodes,
          hint: `已完成 ${succeededNodes.length}/${parallelNodes.length}，等待 ${waitingNodes.length} 个节点`,
          userAction: "/workflow inspect 查看进度",
        };

        const setResult = await setWaitingWithRevisionRetry(
          deps, flowId, revision, waitingNodes[0] ?? parallelNodes[0].id, state, waitState,
          `等待: ${waitingNodes.join(", ")}`,
        );

        revision = (setResult.flow as Record<string, unknown>).revision as number;
        // Sync flow_runs.status to "waiting" so the flow-timeout watchdog
        // does not reap flows that are legitimately waiting for parallel
        // subagent/bcs-route responses.
        syncFlowRunPhase(flowId, state.currentPhase, "waiting");

        await deps.chatInject(
          `已发起 ${parallelNodes.length} 个并行任务（完成 ${succeededNodes.length}，等待 ${waitingNodes.length}）`,
          `${flowId}:parallel:batch-waiting`,
        );

        return { status: "waiting", nodeIds: waitingNodes };
      }

      // No parallel nodes remain waiting. Persist the batch state before the
      // next loop runs an all_done finalizer or completes a successful flow.
      if (finalizedAfterParallel.action === "continue" && finalizedAfterParallel.changed) {
        applyPhaseAndStatus(effectiveWorkflow, state);
      }
      const resumeResult = await deps.boundTaskFlow.resume({
        flowId,
        expectedRevision: revision,
        status: "running",
        currentStep: parallelNodes[parallelNodes.length - 1].id,
        stateJson: JSON.stringify(state),
      });
      assertFlowStateUpdateApplied(resumeResult);
      revision = (resumeResult.flow as Record<string, unknown>).revision as number;
      continue;
    }

    // ── Execute sequential nodes one by one ──
    const nodesToRun = parallelNodes.length === 1 ? [...parallelNodes, ...sequentialNodes] : sequentialNodes;

    for (const node of nodesToRun) {
      const executorType = resolveExecutorType(node, state.executionMode);

      // ── Flow control: executor-level concurrency removed ──
      // In the simplified flow control model (perWorkflow only), there is no
      // executor-level flow control. Nodes execute without per-executor limits.
      // The fcSlotPreAcquired mechanism is also removed — no pre-acquired slots.
      // Executor-level flow control removed — nodes execute without per-executor limits

      state.nodeStates[node.id] = initNodeState(node, executorType, state.nodeStates[node.id]);

      // Async progress: notify node execution started
      if (loadConfig().app.execution.asyncRun && shouldInjectForFlow(flowId, "parallel-progress")) {
        fireProgressChatInject(
          deps,
          `▶ ${node.title ?? node.id} 开始执行 (${executorType})`,
          `${flowId}:${node.id}:started`,
        );
      }

      const templateCtx = buildTemplateContext(state, deps.skillRoot, {}, { currentNodeId: node.id, userIdentity: resolveUserIdentityForContext(deps) });
      const deferFailure = hasReachableAllDoneFinalizer(effectiveWorkflow, state.nodeStates, node.id);

      // Execute node (no executor slot management in simplified flow control model)
      mark("node_exec_pre", node.id);
      const result = await executeNodeWithRetry(deps, node, templateCtx, state, flowId, effectiveWorkflow);
      mark("node_exec_post", node.id);
      const isRuntimeNode = Boolean(state.runtimeNodeMeta?.[node.id]);
      mark("handle_result_pre", node.id);
      const outcome = await handleNodeResult(
        deps,
        effectiveWorkflow,
        state,
        flowId,
        revision,
        node,
        result,
        executorType,
        { persistFailure: !isRuntimeNode && !deferFailure },
      );
      mark("handle_result_post", node.id);
      console.log(`[controller] NODE_OUTCOME flowId=${flowId} node=${node.id} executor=${executorType} action=${outcome.action} nodeStatus=${state.nodeStates[node.id]?.status ?? "n/a"}`);

      // Async progress: notify node execution result
      if (loadConfig().app.execution.asyncRun && shouldInjectForFlow(flowId, "parallel-progress")) {
        if (outcome.action === "waiting") {
          fireProgressChatInject(
            deps,
            `⏳ ${node.title ?? node.id} 等待中`,
            `${flowId}:${node.id}:waiting-async`,
          );
        } else if (outcome.action === "failed") {
          const nodeError = state.nodeStates[node.id]?.error;
          fireProgressChatInject(
            deps,
            `❌ ${node.title ?? node.id} 执行失败${nodeError ? `: ${nodeError.slice(0, 120)}` : ""}`,
            `${flowId}:${node.id}:flow-error`,
            false,
          );
        }
      }

      if (outcome.action === "waiting") {
        const finalizedWaiting = finalizeCompletedLoopIterations(workflow, state, flowId);
        const finalizedWaitingOutcome = await persistFinalizeOutcome(finalizedWaiting);
        if (finalizedWaitingOutcome) return finalizedWaitingOutcome;
        effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
        applyPhaseAndStatus(effectiveWorkflow, state);

        const rawHint = outcome.waitPrompt ?? result.waitConfig?.hint ?? result.waitConfig?.prompt ?? `${node.title} 等待中`;
        const waitKind = outcome.waitKind ?? result.waitConfig?.waitKind;
        const waitActions = outcome.waitActions;
        const waitCommandHints = outcome.waitCommandHints;
        const hint = formatHumanWaitPrompt(
          deps, rawHint, state, waitKind, waitActions, waitCommandHints,
          node.executor.type === "human" ? node.executor.inputSchema : undefined,
        );
        const waitState: WaitState = {
          kind: "platform-workflow",
          workflowId: state.workflowId,
          params: state.params,
          activeNodes: state.activeNodes,
          waitingFor: waitKind ?? (executorType === "bcs-route" ? "bcs-route-responses" : "human-confirm"),
          hint,
          userAction: formatHumanGateUserAction(deps, state, waitKind, waitActions, waitCommandHints),
        };

        const setResult = await setWaitingWithRevisionRetry(
          deps, flowId, revision, node.id, state, waitState, hint,
        );

        revision = (setResult.flow as Record<string, unknown>).revision as number;
        // Sync flow_runs.status to "waiting" so the flow-timeout watchdog
        // (findStaleRunning WHERE status='running') does not reap flows that
        // are legitimately waiting for human confirmation.
        syncFlowRunPhase(flowId, state.currentPhase, "waiting");
        // Route the human-wait hint through the per-flow serial lane (key=flowId),
        // NOT a direct await. A direct await bypasses the queue and can deliver
        // this review card BEFORE an upstream node's queued finalOutput (e.g. the
        // dima-formatter report it summarizes) lands — the "格式化输出跑到人工审核
        // 后面" misorder. On the flowId lane it serializes AFTER those outputs.
        // Detaching is correctness-safe: setWaitingWithRevisionRetry above already
        // persisted the flow as waiting, and injection is display-only.
        enqueueInject(
          flowId,
          () => deps.chatInject(hint, `${flowId}:${node.id}:waiting`),
          { droppable: false },
        );
        return { status: "waiting", nodeIds: [node.id], message: hint };
      }

      if (outcome.action === "blocked") {
        const finalizedBlocked = finalizeCompletedLoopIterations(workflow, state, flowId);
        const finalizedBlockedOutcome = await persistFinalizeOutcome(finalizedBlocked);
        if (finalizedBlockedOutcome) return finalizedBlockedOutcome;
        // Flow control: release slots on terminal state
        deps.flowControl?.releaseAllForFlow(flowId);
        return { status: "blocked" };
      }

      if (outcome.action === "failed") {
        const finalizedFailed = finalizeCompletedLoopIterations(workflow, state, flowId);
        const finalizedFailedOutcome = await persistFinalizeOutcome(finalizedFailed);
        if (finalizedFailedOutcome) return finalizedFailedOutcome;
        if (deferFailure) {
          continue;
        }
        console.log(`[controller] FLOW_FAILED flowId=${flowId} reason=node_outcome_failed node=${node.id} executor=${node.executor.type} error=${(state.nodeStates[node.id]?.error ?? "").slice(0, 100)}`);
        completeFlowRun(flowId, "failed", state.currentPhase, state.nodeStates[node.id]?.error ?? undefined, computeDurationMs(state), state);
        // Flow control: release slots on terminal state
        deps.flowControl?.releaseAllForFlow(flowId);
        return { status: "failed", nodeIds: [node.id] };
      }
    }

    const finalizedAfterSequential = finalizeCompletedLoopIterations(workflow, state, flowId);
    mark("persist_sequential_pre");
    const finalizedAfterSequentialOutcome = await persistFinalizeOutcome(finalizedAfterSequential);
    mark("persist_sequential_post");
    if (finalizedAfterSequentialOutcome) return finalizedAfterSequentialOutcome;
    const dtFinalizedAfterSequential = finalizeCompletedDynamicTemplates(workflow, state, flowId);
    effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
    if (finalizedAfterSequential.action === "continue" && finalizedAfterSequential.changed) {
      applyPhaseAndStatus(effectiveWorkflow, state);
    }
    if (dtFinalizedAfterSequential.changed) {
      applyPhaseAndStatus(effectiveWorkflow, state);
    }

    // ── DONE-node fast-path ────────────────────────────────────
    // When a "done" executor node has succeeded, the workflow is
    // semantically complete. Skip any remaining non-terminal nodes
    // and mark the flow as succeeded immediately, without waiting
    // for isWorkflowComplete to see ALL nodes as terminal.
    // This fixes the common case where branch-skipped nodes remain
    // pending and block the isWorkflowComplete check indefinitely.
    // ────────────────────────────────────────────────────────────
    const doneNodeSucceeded = effectiveWorkflow.nodes.some(
      n => n.executor.type === "done" && state.nodeStates[n.id]?.status === "succeeded",
    );
    if (doneNodeSucceeded && !isWorkflowComplete(effectiveWorkflow, state.nodeStates)) {
      // Mark all remaining non-terminal, non-failed nodes as skipped
      for (const n of effectiveWorkflow.nodes) {
        const ns = state.nodeStates[n.id];
        const isTerminal = ns && isSuccessfulTerminalStatus(ns.status);
        const isFailed = ns?.status === "failed";
        if (!isTerminal && !isFailed) {
          const prevStatus = ns?.status ?? "pending";
          state.nodeStates[n.id] = {
            ...ns,
            status: "skipped",
            completedAt: now(),
            error: `workflow completed via DONE node — node was ${prevStatus}, skipped`,
          };
          appendAuditLog(state, n.id, "skipped",
            `工作流通过 DONE 节点完成，节点从 ${prevStatus} 跳过`);
          appendFlowEvent(state, {
            type: "node_skipped",
            flowId,
            workflowId: state.workflowId,
            nodeId: n.id,
            data: { reason: "done_node_fast_path" },
            error: state.nodeStates[n.id].error,
          });
          emitNodeEvent("node_skipped", {
            flowId,
            workflowId: state.workflowId,
            nodeId: n.id,
            executorType: n.executor.type,
            attempt: ns?.attempts ?? 1,
            nodeTitle: n.title ?? undefined,
            error: state.nodeStates[n.id].error,
            sessionKey: deps.sessionKey,
            sessionId: deps.sessionId,
            systemContext: { reason: "done_node_fast_path" },
          });
          notifyNodeSkipped(deps.chatInject, deps.chatInjectLevel ?? "full", flowId, n, state.nodeStates[n.id].error ?? "workflow completed via DONE node", "done_node_fast_path");
        }
      }
      effectiveWorkflow = buildEffectiveWorkflow(workflow, state);
      applyPhaseAndStatus(effectiveWorkflow, state);
      console.log(`[controller] DONE_NODE_FAST_PATH flowId=${flowId} — skipped remaining non-terminal nodes, workflow will complete`);
    }

    // check if workflow is complete
    if (isWorkflowComplete(effectiveWorkflow, state.nodeStates) || isWorkflowTerminal(effectiveWorkflow, state.nodeStates)) {
      if (await failCompletedWorkflowIfNeeded({ deps, workflow, state, flowId, revision })) {
        deps.flowControl?.releaseAllForFlow(flowId);
        return { status: "failed", nodeIds: Object.keys(state.nodeStates).filter((id) => state.nodeStates[id]?.status === "failed") };
      }
      appendAuditLog(state, "-", "flow-finished", `${workflow.title} 全流程完成`);

      const finishOutcome = await runFlowFinishHooks({
        deps,
        workflow,
        state,
        flowId,
        revision,
      });

      revision = finishOutcome.revision;
      if (finishOutcome.blocked) {
        deps.flowControl?.releaseAllForFlow(flowId);
        return { status: "blocked" };
      }

      resolveAndStoreWorkflowOutputs(workflow, state);
      appendFlowEvent(state, {
        type: "workflow_finished",
        flowId,
        workflowId: state.workflowId,
        data: { title: workflow.title },
      });

      // Post-workflow analysis (best-effort, non-blocking)
      const flowStartTime2 = firstFlowEventTime(state);
      if (flowStartTime2 > 0) {
        void runPostWorkflowAnalysis(state.workflowId, flowId, flowStartTime2).catch((err) => {
                  console.error(`[controller] runPostWorkflowAnalysis failed for flowId=${flowId}:`, err);
        });
      }

      await persistSuccessfulFlowFinish({ deps, state, flowId, revision });

      // Persist flow run completion to engine DB
      console.log(`[controller] FLOW_SUCCEEDED flowId=${flowId} reason=workflow_complete workflow=${workflow.id} title="${workflow.title}"`);
      completeFlowRun(flowId, "succeeded", state.currentPhase, undefined, computeDurationMs(state), state);

      // Flow control: release slots on terminal state
      deps.flowControl?.releaseAllForFlow(flowId);

      const msg = resolveWorkflowFinishedMessage(workflow, state, deps.skillRoot, flowId, resolveUserIdentityForContext(deps));
      if (msg) await deps.chatInject(msg, `${flowId}:flow:finished`);

      return { status: "finished" };
    }

    // persist intermediate state
    const resumeResult = await deps.boundTaskFlow.resume({
      flowId,
      expectedRevision: revision,
      status: "running",
      currentStep: state.activeNodes[0] ?? "loop",
      stateJson: JSON.stringify(state),
    });

    assertFlowStateUpdateApplied(resumeResult);
    revision = (resumeResult.flow as Record<string, unknown>).revision as number;
  }
}

// ── Test Command Handler ──

export type HandleTestOptions = {
  dryRun: boolean;
  mockFile?: string;
  assertEnabled: boolean;
  json?: boolean;
  resolvedWorkflows?: ResolvedWorkflow[];
  resolvedPacks?: ResolvedWorkflowPack[];
};

export async function handleTest(
  workflowId: string,
  options: HandleTestOptions,
): Promise<{ output: string; exitCode: number }> {
  const { MockRegistry } = await import("./runner/mock-registry.js");
  const { executeDryRun } = await import("./runner/dry-run.js");
  const { evaluateTestCase } = await import("./runner/assertions.js");
  const { formatText, formatJson, computeExitCode, buildTestReport } = await import("./runner/test-reporter.js");

  // Resolve workflow spec
  const resolved = resolveWorkflowByIdFromPacks(workflowId, options.resolvedWorkflows ?? []);
  if (!resolved?.spec) {
    const available = listWorkflowIdsFromPacks(options.resolvedWorkflows ?? []).join(", ");
    return {
      output: `Error: Workflow "${workflowId}" not found. Available: ${available}`,
      exitCode: 2,
    };
  }
  const workflow = resolved.spec;

  // Validate including test cases
  try {
    validateWorkflowSemantics(workflow);
  } catch (err) {
    if (err instanceof WorkflowValidationError) {
      return {
        output: `Validation error: ${formatValidationIssues(err.issues)}`,
        exitCode: 2,
      };
    }
    throw err;
  }

  // Build mock registry
  const registry = new MockRegistry();

  // Register inline mocks from workflow nodes
  registry.buildFromWorkflow(workflow.nodes);

  // Load external mock file if provided
  if (options.mockFile) {
    try {
      registry.loadExternalFile(options.mockFile);
    } catch (err) {
      return {
        output: `Error loading mock file: ${(err as Error).message}`,
        exitCode: 2,
      };
    }
  }

  // No test cases defined — default dry-run validation
  const testCases = workflow.tests ?? [];
  if (testCases.length === 0) {
    const params: Record<string, string> = {};
    const { flowState, nodeReports } = await executeDryRun(workflow, params, registry);
    const report: TestReport = {
      workflowId: workflow.id,
      version: String(workflow.version ?? 0),
      timestamp: new Date().toISOString(),
      testCases: [{
        name: "default-dry-run",
        description: "Default dry-run validation (no test cases defined)",
        params,
        status: "passed",
        duration: 0,
        results: nodeReports,
        summary: { total: 0, passed: 0, failed: 0 },
      }],
      summary: { total: 0, passed: 0, failed: 0 },
      status: "passed",
    };

    const output = options.json ? formatJson(report) : formatText(report) + "\n\nWarning: No test cases defined; performed default dry-run validation.";
    return { output, exitCode: 0 };
  }

  // Run each test case with isolated FlowState
  const testCaseReports: TestCaseReport[] = [];
  for (const testCase of testCases) {
    const testRegistry = new MockRegistry();
    testRegistry.buildFromWorkflow(workflow.nodes);
    if (options.mockFile) {
      testRegistry.loadExternalFile(options.mockFile);
    }
    if (testCase.mockOverrides) {
      testRegistry.applyTestOverrides(testCase.mockOverrides);
    }

    const params = (testCase.params as Record<string, string>) ?? {};
    let flowState: FlowState;
    let nodeReports: NodeExecutionReport[];
    try {
      const result = await executeDryRun(workflow, params, testRegistry);
      flowState = result.flowState;
      nodeReports = result.nodeReports;
    } catch (err) {
      testCaseReports.push({
        name: testCase.name,
        description: testCase.description,
        params: testCase.params,
        status: "error",
        duration: 0,
        results: [],
        summary: { total: 0, passed: 0, failed: 0 },
      });
      continue;
    }

    let report: TestCaseReport;
    if (options.assertEnabled) {
      report = evaluateTestCase(flowState, testCase, nodeReports);
    } else {
      report = {
        name: testCase.name,
        description: testCase.description,
        params: testCase.params,
        status: "passed",
        duration: 0,
        results: nodeReports,
        summary: { total: 0, passed: 0, failed: 0 },
      };
    }
    testCaseReports.push(report);
  }

  const report = buildTestReport(workflow.id, String(workflow.version ?? 0), testCaseReports);
  const output = options.json ? formatJson(report) : formatText(report);
  const exitCode = computeExitCode(report);

  return { output, exitCode };
}

// ── Synthesize (Layer D: LLM YAML Synthesis) ──

/**
 * Handle the "synthesize" action — generate a WorkflowSpec from a natural language goal.
 *
 * Pipeline:
 *   1. Load synthesis config
 *   2. Call synthesizer core (LLM generates YAML → validation → correction loop)
 *   3. If validation fails, return SynthesisResult with errors
 *   4. Check human approval gate
 *   5. If validateOnly, return result without execution
 *   6. If human approval needed, return waiting state
 *   7. Otherwise, pass the synthesized WorkflowSpec to handleRun
 */
export async function handleSynthesize(
  deps: ControllerDeps,
  goal: string,
  options?: {
    model?: string;
    validateOnly?: boolean;
    maxCorrections?: number;
  },
): Promise<string> {
  console.info("[taskguard] handleSynthesize entry", { goal: goal.slice(0, 100), validateOnly: options?.validateOnly });

  const { synthesize: synthesizeCore } = await import("./synthesis/synthesizer.js");
  const { loadSynthesisConfig } = await import("./synthesis/config.js");
  const { checkHumanApprovalNeeded } = await import("./synthesis/human-gate.js");

  // 1. Load config (allow overrides from options)
  let config = loadSynthesisConfig();
  if (options?.model) {
    config = { ...config, defaultModel: options.model };
  }
  if (options?.maxCorrections !== undefined) {
    config = { ...config, defaultMaxCorrections: options.maxCorrections };
  }

  // 2. Run synthesizer
  const result = await synthesizeCore(goal, config, {
    startedAtMs: Date.now(),
    getPackTemplates: () => {
      // Collect nodeTemplates from loaded packs
      try {
        const templates: Record<string, string> = {};
        // Pack templates are loaded during pack discovery
        // We return an empty map if no packs are loaded
        return templates;
      } catch {
        return {};
      }
    },
    recordTokens: (count: number) => {
      console.info("[taskguard] synthesis token usage:", count);
    },
  });

  // 3. If synthesis failed, return error
  if (!result.success) {
    const errorSummary = result.validationErrors
      ?.map((e) => `[${e.stage}] ${e.path}: ${e.message}`)
      .join("\n") ?? "Unknown synthesis failure";

    return `⚠️ YAML 合成失败\n\n` +
      `目标: ${goal.slice(0, 200)}\n` +
      `修正轮数: ${result.correctionRounds}\n` +
      `LLM 模型: ${result.llmModel}\n` +
      `Token 消耗: ${result.llmUsage.totalTokens}\n\n` +
      `验证错误:\n${errorSummary}`;
  }

  // 4. validateOnly mode — return result without execution
  if (options?.validateOnly) {
    return `✅ YAML 合成成功（仅验证模式）\n\n` +
      `目标: ${goal.slice(0, 200)}\n` +
      `修正轮数: ${result.correctionRounds}\n` +
      `LLM 模型: ${result.llmModel}\n` +
      `Token 消耗: ${result.llmUsage.totalTokens}\n\n` +
      `生成的 YAML:\n\`\`\`yaml\n${result.rawYaml}\n\`\`\``;
  }

  // 5. Check human approval gate
  const spec = result.workflowSpec!;
  const gateDecision = checkHumanApprovalNeeded(spec, config, {
    totalTokens: result.llmUsage.totalTokens,
  });

  if (gateDecision.needsApproval) {
    const warningText = gateDecision.triggeredWarnings.length > 0
      ? `\n\n⚠️ 触发警告:\n${gateDecision.triggeredWarnings.map((w) => `- ${w}`).join("\n")}`
      : "";

    return `🔒 合成工作流需要人工审批\n\n` +
      `目标: ${goal.slice(0, 200)}\n` +
      `原因: ${gateDecision.reason}${warningText}\n\n` +
      `生成的 YAML:\n\`\`\`yaml\n${result.rawYaml}\n\`\`\`\n\n` +
      `请使用 \`/workflow confirm\` 批准执行，或 \`/workflow reject\` 拒绝。`;
  }

  // 6. No approval needed — execute the synthesized workflow
  // The synthesized spec is passed to handleRun as a dynamically resolved workflow
  console.info("[taskguard] handleSynthesize: proceeding to execution", {
    workflowId: spec.id,
    nodeCount: spec.nodes.length,
  });

  return `✅ YAML 合成成功，开始执行\n\n` +
    `目标: ${goal.slice(0, 200)}\n` +
    `工作流: ${spec.id}\n` +
    `节点数: ${spec.nodes.length}\n` +
    `修正轮数: ${result.correctionRounds}\n` +
    `LLM 模型: ${result.llmModel}\n` +
    `Token 消耗: ${result.llmUsage.totalTokens}\n\n` +
    `生成的 YAML:\n\`\`\`yaml\n${result.rawYaml}\n\`\`\``;
}

// ── Debug Segment ──
//
// `handleDebugSegment` executes a workflow segment starting at `fromNode`,
// with upstream outputs provided by the caller. It is a debugging aid: it does
// NOT persist to `boundTaskFlow` (no `flow_run` records) and does NOT send
// `chatInject` notifications. Callers pass a no-op `boundTaskFlow` and no-op
// `chatInject` via `ControllerDeps` to guarantee these properties.

const DEBUG_UNSUPPORTED_EXECUTORS = ["loop-group", "dynamic-template", "llm-orchestrator"] as const;

type DebugExecutedNode = {
  nodeId: string;
  title?: string;
  status: "succeeded" | "failed" | "waiting";
  result?: Record<string, unknown>;
  error?: string;
};

/**
 * Compute the set of node IDs that should be executed for a debug-segment run.
 *
 * If `toNode` is omitted, returns all descendants of `fromNode` (including
 * `fromNode` itself). If `toNode` is specified, returns only the nodes that lie
 * on a path from `fromNode` to `toNode` (inclusive of both): i.e. nodes that
 * are descendants of `fromNode` AND that are `toNode` itself or an ancestor of
 * `toNode`. This excludes parallel siblings of `toNode` that branch off before
 * reaching it, which the caller did not ask to execute.
 */
export function computeSegmentScope(
  workflow: WorkflowSpec,
  fromNode: string,
  toNode?: string,
): Set<string> {
  // collectTargetAndDescendants returns [fromNode, ...descendants].
  const descendants = new Set(collectTargetAndDescendants(workflow, fromNode));
  if (!toNode) return descendants;

  if (toNode === fromNode) return new Set([toNode]);

  // Walk backwards from toNode over dependsOn, collecting ancestors. A node is
  // on the fromNode→toNode axis iff it is an ancestor of toNode (or toNode
  // itself) and also a descendant of fromNode.
  const ancestorsOfToNode = new Set<string>([toNode]);
  let changed = true;
  while (changed) {
    changed = false;
    for (const candidate of [...ancestorsOfToNode]) {
      const nodeSpec = workflow.nodes.find((n) => n.id === candidate);
      if (!nodeSpec) continue;
      for (const dep of nodeSpec.dependsOn) {
        if (!ancestorsOfToNode.has(dep)) {
          ancestorsOfToNode.add(dep);
          changed = true;
        }
      }
    }
  }

  const scope = new Set<string>();
  for (const id of ancestorsOfToNode) {
    if (descendants.has(id)) scope.add(id);
  }
  return scope;
}

/**
 * Returns true if `node` declares an automatic `rerun` onResult branch. Such
 * nodes are not supported by the debug segment loop because `handleNodeResult`
 * drives the rerun scenario through `executeLoop`, which would write to the
 * production `boundTaskFlow`. Guarding against it keeps the debug run side-effect free.
 */
function hasRerunOnResult(node: WorkflowNode): boolean {
  const onResult = node.onResult;
  if (!onResult) return false;
  if (onResult.then?.rerun || onResult.else?.rerun) return true;
  return false;
}

/**
 * Detect all in-scope nodes that declare an unsupported executor or a rerun
 * onResult branch. These nodes would either crash the loop or write to the
 * production TaskFlow, so we refuse the run up front with a clear message.
 */
function findUnsupportedDebugNodes(
  workflow: WorkflowSpec,
  scope: Set<string>,
): { nodeId: string; reason: string }[] {
  const problems: { nodeId: string; reason: string }[] = [];
  for (const node of workflow.nodes) {
    if (!scope.has(node.id)) continue;
    if ((DEBUG_UNSUPPORTED_EXECUTORS as readonly string[]).includes(node.executor.type)) {
      problems.push({ nodeId: node.id, reason: `unsupported executor type "${node.executor.type}"` });
    }
    if (hasRerunOnResult(node)) {
      problems.push({ nodeId: node.id, reason: "automatic rerun onResult is not supported in debug segment" });
    }
  }
  return problems;
}

/**
 * Handle the "debug-segment" action: execute a workflow segment starting from
 * a specified node, with upstream outputs provided by the caller.
 *
 * Unlike a normal workflow run, this:
 * - Does NOT persist to boundTaskFlow (no production side effects)
 * - Does NOT send chatInject notifications
 * - Executes nodes sequentially (no parallel dispatch)
 * - Stops on first failure or waiting node
 */
export async function handleDebugSegment(
  deps: ControllerDeps,
  action: {
    action: "debug-segment";
    workflowId: string;
    fromNode: string;
    toNode?: string;
    nodeOutput: Record<string, Record<string, unknown>>;
    workflowData?: Record<string, unknown>;
    input?: Record<string, unknown>;
  },
): Promise<string> {
  // ── Step 1: Load & validate workflow spec ──
  let workflow: WorkflowSpec;
  try {
    const lookup = await requireWorkflowLookup(deps, action.workflowId);
    workflow = lookup.workflow;
  } catch (err) {
    // requireWorkflowLookup throws when the workflow pack is not installed —
    // surface it as a structured failure rather than letting it escape.
    return JSON.stringify({
      status: "failed",
      error: (err as Error).message,
      executedNodes: [],
      finalWorkflowData: {},
    });
  }

  const fromNodeSpec = workflow.nodes.find((n) => n.id === action.fromNode);
  if (!fromNodeSpec) {
    return JSON.stringify({
      status: "failed",
      error: `Node "${action.fromNode}" not found in workflow`,
      executedNodes: [],
      finalWorkflowData: {},
    });
  }

  if ((DEBUG_UNSUPPORTED_EXECUTORS as readonly string[]).includes(fromNodeSpec.executor.type)) {
    return JSON.stringify({
      status: "failed",
      error: `Debug fromNode with executor type "${fromNodeSpec.executor.type}" is not supported`,
      executedNodes: [],
      finalWorkflowData: {},
    });
  }

  if (action.toNode) {
    const toNodeSpec = workflow.nodes.find((n) => n.id === action.toNode);
    if (!toNodeSpec) {
      return JSON.stringify({
        status: "failed",
        error: `Node "${action.toNode}" not found in workflow`,
        executedNodes: [],
        finalWorkflowData: {},
      });
    }
  }

  // ── Step 2: Compute execution scope ──
  const scope = computeSegmentScope(workflow, action.fromNode, action.toNode);

  // Guard: refuse to run segments containing unsupported executors or rerun
  // onResult nodes up front. These would either fail or escape the loop into
  // executeLoop (which writes to the production TaskFlow).
  const problems = findUnsupportedDebugNodes(workflow, scope);
  if (problems.length > 0) {
    return JSON.stringify({
      status: "failed",
      error: `Debug segment contains unsupported nodes: ${problems
        .map((p) => `${p.nodeId} (${p.reason})`)
        .join(", ")}`,
      executedNodes: [],
      finalWorkflowData: {},
    });
  }

  // ── Step 3: Build initial FlowState with seeded nodeOutput ──
  const flowId = `debug-${now()}-${Math.random().toString(36).slice(2, 8)}`;
  const initialState: FlowState = {
    workflowId: workflow.id,
    workflowVersion: workflow.version ?? 0,
    params: {},
    input: action.input
      ? { params: {}, digest: "", digestShort: "", files: [], ...action.input }
      : { params: {}, digest: "", digestShort: "", files: [] },
    executionMode: "private",
    businessStatus: "INIT",
    currentPhase: fromNodeSpec.phase,
    activeNodes: [],
    nodeStates: {},
    workflowData: action.workflowData ? structuredClone(action.workflowData) : {},
    actionOutputs: {},
    flowHooks: {},
    auditLog: [],
  };

  // Seed model-provided nodeOutput into nodeStates as "succeeded".
  // `__matchedBranchId` is a side-channel hint: it is extracted into the real
  // NodeState.matchedBranchId field (consumed by branch gating in getReadyNodes)
  // and stripped from `result` so it never leaks into nodeOutput template context.
  for (const [nodeId, rawOutput] of Object.entries(action.nodeOutput)) {
    const nodeSpec = workflow.nodes.find((n) => n.id === nodeId);
    if (!nodeSpec) continue; // ignore unknown nodeIds silently
    const { __matchedBranchId, ...cleanOutput } = rawOutput as Record<string, unknown> & {
      __matchedBranchId?: unknown;
    };
    initialState.nodeStates[nodeId] = {
      status: "succeeded",
      phase: nodeSpec.phase,
      executor: nodeSpec.executor.type,
      result: cleanOutput,
      completedAt: now(),
      ...(__matchedBranchId != null
        ? { matchedBranchId: __matchedBranchId as string | null }
        : {}),
    };
  }

  // Mark all out-of-scope nodes as "skipped" so getReadyNodes ignores them.
  for (const node of workflow.nodes) {
    if (!initialState.nodeStates[node.id]) {
      initialState.nodeStates[node.id] = {
        status: "skipped",
        phase: node.phase,
        executor: node.executor.type,
      };
    }
  }

  // Mark in-scope nodes (not already seeded) as "pending".
  for (const nodeId of scope) {
    const existing = initialState.nodeStates[nodeId];
    if (!existing || existing.status === "skipped") {
      const nodeSpec = workflow.nodes.find((n) => n.id === nodeId)!;
      initialState.nodeStates[nodeId] = {
        status: "pending",
        phase: nodeSpec.phase,
        executor: nodeSpec.executor.type,
      };
    }
  }

  // ── Step 4: Lightweight execution loop ──
  const state = initialState;
  const executedNodes: DebugExecutedNode[] = [];
  let revision = 0;
  let outcome: "completed" | "partial" | "failed" = "completed";

  // eslint-disable-next-line no-constant-condition
  while (true) {
    const effectiveWorkflow = buildEffectiveWorkflow(workflow, state);

    // Mark skippable nodes (e.g. branch not matched) within scope as skipped.
    const skippable = findSkippableNodesFixedPoint(effectiveWorkflow, state.nodeStates);
    for (const node of skippable) {
      if (scope.has(node.id)) {
        state.nodeStates[node.id] = {
          ...state.nodeStates[node.id],
          status: "skipped",
        };
      }
      // Out-of-scope nodes are already skipped from initialization.
    }

    // Find ready nodes, restricted to the debug scope.
    const readyNodes = getReadyNodes(effectiveWorkflow, state.nodeStates).filter((n) =>
      scope.has(n.id),
    );

    if (readyNodes.length === 0) break; // no more in-scope nodes to run

    // Execute each ready node sequentially (easier to observe while debugging).
    for (const node of readyNodes) {
      const templateCtx = buildTemplateContext(state, deps.skillRoot, {}, {
        currentNodeId: node.id,
      });

      try {
        const result = await executeNodeWithRetry(deps, node, templateCtx, state, flowId, effectiveWorkflow);

        const nodeOutcome = await handleNodeResult(
          deps,
          effectiveWorkflow,
          state,
          flowId,
          revision,
          node,
          result,
          node.executor.type,
          { persistFailure: true },
        );
        revision = nodeOutcome.revision;

        const finalNodeState = state.nodeStates[node.id];
        executedNodes.push({
          nodeId: node.id,
          title: node.title,
          status: (finalNodeState.status === "waiting"
            ? "waiting"
            : finalNodeState.status === "failed"
              ? "failed"
              : "succeeded") as DebugExecutedNode["status"],
          result: finalNodeState.result,
          error: finalNodeState.error ?? undefined,
        });

        // Pause on a waiting node (e.g. human/approval).
        if (result.status === "waiting" || finalNodeState.status === "waiting") {
          outcome = "partial";
          break;
        }

        // Stop on failure.
        if (nodeOutcome.action === "failed" || finalNodeState.status === "failed") {
          outcome = "failed";
          break;
        }
      } catch (err) {
        executedNodes.push({
          nodeId: node.id,
          title: node.title,
          status: "failed",
          error: (err as Error).message,
        });
        outcome = "failed";
        break;
      }
    }

    if (outcome !== "completed") break;
  }

  // ── Empty-execution guard (建议 2+4) ──
  // A `completed` outcome with zero executed nodes — or a fromNode that ended
  // up `skipped`/`pending` instead of running — means the model did not supply
  // enough upstream context: the fromNode's dependencies were out of scope and
  // not provided via nodeOutput, so `findSkippableNodes` skipped the fromNode
  // silently and the loop ran nothing. Surface this as an explicit `failed`
  // with a precise diagnosis (which dependencies are missing) instead of a
  // misleading empty success. Scope-internal nodes that are still `skipped`
  // here are exactly the "intended to run but couldn't" nodes — out-of-scope
  // nodes were never added to `scope`.
  if (outcome === "completed") {
    const fromFinal = state.nodeStates[action.fromNode];
    const fromDidNotExecute =
      !fromFinal ||
      (fromFinal.status !== "succeeded" &&
        fromFinal.status !== "failed" &&
        fromFinal.status !== "waiting");
    if (executedNodes.length === 0 || fromDidNotExecute) {
      const blocked: Array<{ nodeId: string; missingDeps: string[] }> = [];
      for (const nodeId of scope) {
        const st = state.nodeStates[nodeId];
        if (!st || st.status !== "skipped") continue;
        const nodeSpec = workflow.nodes.find((n) => n.id === nodeId);
        const missingDeps = (nodeSpec?.dependsOn ?? []).filter(
          (depId) => state.nodeStates[depId]?.status === "skipped",
        );
        if (missingDeps.length > 0) blocked.push({ nodeId, missingDeps });
      }
      const blockedSummary =
        blocked.length > 0
          ? blocked
              .map(
                (b) =>
                  `'${b.nodeId}' (missing upstream: ${b.missingDeps.map((d) => `'${d}'`).join(", ")})`,
              )
              .join("; ")
          : `fromNode '${action.fromNode}' did not execute`;
      return JSON.stringify(
        {
          status: "failed",
          error:
            `Debug segment executed 0 nodes though fromNode '${action.fromNode}' exists. ` +
            `Its upstream dependencies were neither provided via nodeOutput nor produced ` +
            `by an in-scope node, so they were skipped and the fromNode could not become ready. ` +
            `Provide the skipped upstream outputs via nodeOutput. Blocked: ${blockedSummary}.`,
          executedNodes,
          finalWorkflowData: state.workflowData,
        },
        null,
        2,
      );
    }
  }

  // ── Step 5: Format and return result ──
  const lastExecuted = executedNodes[executedNodes.length - 1];
  const resultPayload: Record<string, unknown> = {
    status: outcome,
    executedNodes,
    finalWorkflowData: state.workflowData,
  };

  if (outcome === "partial" && lastExecuted?.status === "waiting") {
    const waitState = state.nodeStates[lastExecuted.nodeId];
    resultPayload.waitingNode = {
      nodeId: lastExecuted.nodeId,
      prompt: waitState?.waitPrompt ?? "",
      inputSchema: waitState?.waitInputSchema,
      actions: ["confirm", "revise", "reject"],
    };
  }

  if (outcome === "failed" && lastExecuted) {
    resultPayload.failedNode = {
      nodeId: lastExecuted.nodeId,
      error: lastExecuted.error ?? "Unknown error",
    };
  }

  return JSON.stringify(resultPayload, null, 2);
}
