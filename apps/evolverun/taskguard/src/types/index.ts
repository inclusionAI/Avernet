/**
 * Central type re-exports for ClawFlow.
 *
 * Import from "../types/index.js" to access any public type
 * without knowing the internal module layout.
 */

// ── Config Types ──
export type {
  StatePersistenceConfig,
  YuQueSourceConfig,
  AgentMindSourceConfig,
  KnowledgeConfig,
  RetryConfig,
  ThresholdsConfig,
  AnalysisConfig,
  DingTalkConfig,
  AlertingConfig,
  ApiConfig,
  AppConfig,
} from "../config/types.js";

// ── Knowledge Types ──
export type {
  KnowledgeBase,
  KnowledgeBaseSearchResult,
  CacheEntry,
  KnowledgeContext,
  YuQueAdapterConfig,
  AgentMindAdapterConfig,
} from "../knowledge/index.js";

// ── Retry Types ──
export type {
  PendingErrorContext,
  RetryDirective,
} from "../retry/index.js";

// ── Analysis Types ──
export type {
  AnalysisResult,
  NodeMetrics,
  ThresholdBreach,
  HealthReport,
} from "../analysis/index.js";

// ── Alert Types ──
export type {
  DingTalkSendResult,
  DingTalkAlertPayload,
  NodeFailureEvent,
  DispatchResult,
  NodeFailureAlert,
} from "../alerts/index.js";

// ── API Types ──
export type { ApiRepositories } from "../api/index.js";

// ── Database Repository Types ──
export type {
  FlowRunRow,
  FlowRunInsert,
  FlowRunCompletion,
  FindFlowRunsOptions,
} from "../db/repositories/flow-run-repository.js";

export type {
  FlowEventRow,
  FlowEventInsert,
  FindOptions as FlowEventFindOptions,
  TimeRangeOptions as FlowEventTimeRangeOptions,
} from "../db/repositories/event-repository.js";

export type {
  NodeExecutionRow,
  NodeExecutionInsert,
  NodeExecutionCompletion,
  FindNodeExecutionsOptions,
} from "../db/repositories/node-execution-repository.js";

export type {
  FlowMetricsRow,
  MetricsAggregateResult,
  AggregateOptions,
} from "../db/repositories/metrics-repository.js";

export type {
  TriggeredAlertRow,
  FindUnacknowledgedOptions,
} from "../db/repositories/alert-repository.js";

// ── Workflow Spec Types ──
export type {
  WorkflowNode,
  NodeAlertingSpec,
  NodeRetrySpec,
  NodeExecutor,
  HookActionSpec,
  WorkflowSpec,
  FlowState,
  NodeState,
} from "../types.js";