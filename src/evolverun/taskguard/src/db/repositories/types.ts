/**
 * Repository interfaces for the workflow engine.
 *
 * These interfaces decouple consumers (controller hooks, API server, scheduler, etc.)
 * from concrete implementations (SQL-backed or API-backed). This enables
 * swapping the persistence layer without changing business logic.
 */

import type {
  ScheduledTrigger,
  CreateTriggerInput,
  UpdateTriggerInput,
} from "../../scheduler/types.js";
import type {
  WebhookTrigger,
  WebhookEvent,
} from "../../webhook/types.js";

/** Terminal flow_runs statuses — once in these states, result_json must not
 *  be overwritten by node-level writes (see the flow_runs.result_json race fix). */
const TERMINAL_FLOW_STATUSES = new Set(["succeeded", "failed", "cancelled", "completed"]);

/** Check whether a flow_runs status is terminal. */
export function isTerminalFlowStatus(status: string): boolean {
  return TERMINAL_FLOW_STATUSES.has(status);
}

// ── Flow Run ──

export type FlowRunRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  workflow_title: string | null;
  status: string;
  params_json: string | null;
  input_json: string | null;
  result_json: string | null;
  node_count: number;
  succeeded_count: number;
  failed_count: number;
  total_duration_ms: number | null;
  total_token_usage: number | null;
  triggered_by: string | null;
  identity_key: string | null;
  current_phase: string | null;
  started_at: number;
  completed_at: number | null;
  credentials_json: string | null;
  origin_session_key: string | null;
  origin_session_id: string | null;
  origin_bot_id: string | null;
  user_id: string | null;
  plugin_version: string | null;
  engine: string | null;
  gmt_create: number;
  gmt_modified: number | null;
};

export type FlowRunInsert = {
  flowId: string;
  workflowId: string;
  workflowTitle?: string | null;
  status: string;
  paramsJson?: string | null;
  inputJson?: string | null;
  nodeCount?: number;
  triggeredBy?: string | null;
  identityKey?: string | null;
  currentPhase?: string | null;
  startedAt: number;
  credentialsJson?: string | null;
  originSessionKey?: string | null;
  originSessionId?: string | null;
  originBotId?: string | null;
  userId?: string | null;
  pluginVersion?: string | null;
  engine?: string | null;
};

export type FlowRunCompletion = {
  status: string;
  resultJson?: string | null;
  inputJson?: string | null;
  totalDurationMs?: number | null;
  totalTokenUsage?: number | null;
  currentPhase?: string | null;
  succeededCount?: number;
  failedCount?: number;
  completedAt: number;
};

export type FindFlowRunsOptions = {
  workflowId?: string;
  status?: string;
  identityKey?: string;
  currentPhase?: string;
  limit?: number;
  offset?: number;
};

export interface IFlowRunRepository {
  insert(run: FlowRunInsert): Promise<boolean>;
  incrementNodeCount(flowId: string, field: "succeeded_count" | "failed_count"): Promise<boolean>;
  updateCompletion(flowId: string, completion: FlowRunCompletion): Promise<boolean>;
  updateStatus(flowId: string, status: string): Promise<boolean>;
  updateCurrentPhase(flowId: string, currentPhase: string): Promise<boolean>;
  updateNodeCount(flowId: string, nodeCount: number): Promise<boolean>;
  /** Overwrite result_json with the last successful node's output. Best-effort. */
  updateResultJson(flowId: string, nodeId: string, result: Record<string, unknown>): Promise<boolean>;
  findByFlowId(flowId: string): Promise<FlowRunRow | null>;
  findRuns(options: FindFlowRunsOptions): Promise<FlowRunRow[]>;
  /** Find flows still in "running" status whose started_at is older than the cutoff (epoch seconds). */
  findStaleRunning(cutoffEpochSecs: number, limit?: number): Promise<FlowRunRow[]>;
  /** Find running flows matching the given bot ID and engine, ordered by started_at ascending. */
  findRunningByOrigin(botId: string, engine: string, limit?: number): Promise<Pick<FlowRunRow, "flow_id" | "status" | "started_at">[]>;
}

// ── Node Execution ──

export type NodeExecutionRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string;
  executor_type: string | null;
  status: string;
  attempt: number;
  input_json: string | null;
  output_json: string | null;
  error_text: string | null;
  duration_ms: number | null;
  token_usage_json: string | null;
  node_title: string | null;
  progress_message: string | null;
  session_key: string | null;
  session_id: string | null;
  triggered_by: string | null;
  branch_id: string | null;
  embedded_session_key: string | null;
  system_context_json: string | null;
  resolved_prompt: string | null;
  version: number;
  started_at: number;
  completed_at: number | null;
  gmt_create: number;
  gmt_modified: number | null;
};

export type NodeExecutionInsert = {
  flowId: string;
  workflowId: string;
  nodeId: string;
  executorType?: string | null;
  status: string;
  attempt: number;
  inputJson?: string | null;
  outputJson?: string | null;
  errorText?: string | null;
  durationMs?: number | null;
  tokenUsageJson?: string | null;
  nodeTitle?: string | null;
  progressMessage?: string | null;
  sessionKey?: string | null;
  sessionId?: string | null;
  embeddedSessionKey?: string | null;
  systemContextJson?: string | null;
  resolvedPrompt?: string | null;
  version?: number;
  startedAt: number;
  completedAt?: number | null;
};

export type NodeExecutionCompletion = {
  status: string;
  outputJson?: string | null;
  errorText?: string | null;
  durationMs?: number | null;
  tokenUsageJson?: string | null;
  embeddedSessionKey?: string | null;
  systemContextJson?: string | null;
  resolvedPrompt?: string | null;
  completedAt: number;
  /** When provided (manual retry reset), overwrites started_at so a re-run
   *  colliding with the prior attempt's row shows the new start time. */
  startedAt?: number | null;
  /** Expected version for optimistic locking. When provided, the UPDATE will
   *  only succeed if the current version matches (WHERE version = ?). */
  expectedVersion?: number;
};

export type FindNodeExecutionsOptions = {
  nodeId?: string;
  status?: string;
  limit?: number;
  offset?: number;
};

export interface INodeExecutionRepository {
  insert(exec: NodeExecutionInsert): Promise<{ insertId: number; affectedRows: number }>;
  updateCompletion(id: number, completion: NodeExecutionCompletion): Promise<boolean>;
  updateCompletionByFlowNode(flowId: string, nodeId: string, attempt: number, completion: NodeExecutionCompletion): Promise<boolean>;
  /** Optimistic-locking variant: only succeeds when version matches. */
  updateCompletionByFlowNode(flowId: string, nodeId: string, attempt: number, completion: NodeExecutionCompletion & { expectedVersion: number }): Promise<boolean>;
  updateProgressMessage(flowId: string, nodeId: string, attempt: number, message: string): Promise<boolean>;
  findByFlowId(flowId: string, options?: FindNodeExecutionsOptions): Promise<NodeExecutionRow[]>;
  findByFlowAndNode(flowId: string, nodeId: string, limit?: number): Promise<NodeExecutionRow[]>;
  findLatestByFlowId(flowId: string): Promise<NodeExecutionRow[]>;
  /** Reconcile stale "running" node_executions when a flow reaches terminal state. */
  reconcileStaleRunning(flowId: string, flowStatus: string): Promise<number>;
}

// ── Flow Event ──

export type FlowEventRow = {
  id: number;
  event_id: string;
  flow_id: string;
  workflow_id: string;
  node_id: string | null;
  event_type: string;
  attempt: number | null;
  time: number;
  data_json: string | null;
  error_text: string | null;
  gmt_create: number;
};

export type FlowEventInsert = {
  id: string;
  time: number;
  type: string;
  flowId: string;
  workflowId: string;
  nodeId?: string | null;
  actionId?: string | null;
  attempt?: number;
  data?: Record<string, unknown>;
  error?: string | null;
};

export type FindEventOptions = {
  limit?: number;
  offset?: number;
};

export type TimeRangeOptions = {
  eventType?: string;
  limit?: number;
  offset?: number;
};

export interface IFlowEventRepository {
  insert(event: FlowEventInsert): Promise<boolean>;
  findByFlowId(flowId: string, options?: FindEventOptions): Promise<FlowEventRow[]>;
  findByWorkflowAndTimeRange(workflowId: string, startTime: number, endTime: number, options?: TimeRangeOptions): Promise<FlowEventRow[]>;
}

// ── Flow Metrics ──

export type FlowMetricsRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string;
  metric_name: string;
  metric_value: number;
  time: number;
  labels_json: string | null;
  gmt_create: number;
};

export type MetricsAggregateResult = {
  group_key: string;
  aggregate_value: number;
};

export type AggregateOptions = {
  metricName: string;
  aggregation: "avg" | "count" | "sum";
  groupBy?: string;
};

export interface IFlowMetricsRepository {
  record(flowId: string, workflowId: string, nodeId: string, metricName: string, metricValue: number, labels?: Record<string, string>): Promise<boolean>;
  aggregate(workflowId: string, startTime: number, endTime: number, options: AggregateOptions): Promise<MetricsAggregateResult[]>;
}

// ── Triggered Alert ──

export type TriggeredAlertRow = {
  id: number;
  flow_id: string;
  workflow_id: string;
  node_id: string | null;
  alert_rule: string;
  severity: string;
  message: string;
  time: number;
  acknowledged: number;
  gmt_create: number;
};

export type FindUnacknowledgedOptions = {
  severity?: string;
  limit?: number;
};

export interface ITriggeredAlertRepository {
  record(flowId: string, workflowId: string, nodeId: string | null, alertRule: string, severity: string, message: string): Promise<boolean>;
  findUnacknowledged(workflowId: string, options?: FindUnacknowledgedOptions): Promise<TriggeredAlertRow[]>;
  acknowledge(alertId: number): Promise<boolean>;
}

// ── Workflow Spec ──

export type WorkflowSpecRow = {
  id: number;
  workflow_id: string;
  pack_id: string | null;
  spec_json: string;
  gmt_create: number;
  gmt_modified: number | null;
};

export interface IWorkflowSpecRepository {
  findByWorkflowId(workflowId: string): Promise<WorkflowSpecRow | null>;
}

// ── Validation Template ──

export type ValidationTemplateRow = {
  id: number;
  template_id: string;
  name: string;
  description: string | null;
  content: string;
  enabled: number;
  gmt_create: number;
  gmt_modified: number | null;
};

export interface IValidationTemplateRepository {
  findByTemplateId(templateId: string): Promise<ValidationTemplateRow | null>;
  findEnabled(templateId: string): Promise<ValidationTemplateRow | null>;
  listAll(enabledOnly?: boolean): Promise<ValidationTemplateRow[]>;
}

// ── Facade Binding ──

export type FacadeBindingRow = {
  id: number;
  command: string;
  workflow_id: string;
  pack_id: string | null;
  remark: string | null;
  gmt_create: number;
  gmt_modified: number | null;
};

export type FacadeBindingInsert = {
  command: string;
  workflow_id: string;
  pack_id?: string;
  remark?: string;
};

export interface IFacadeBindingRepository {
  findByCommand(command: string): Promise<FacadeBindingRow | null>;
  findByWorkflowId(workflowId: string): Promise<FacadeBindingRow[]>;
  /** List all facade bindings, optionally filtered by bot permissions. */
  listAll(botId?: string, botOwnerId?: string): Promise<FacadeBindingRow[]>;
  upsert(insert: FacadeBindingInsert): Promise<FacadeBindingRow>;
  deleteByCommand(command: string): Promise<boolean>;
  deleteByWorkflowId(workflowId: string): Promise<number>;
}

// ── Flow Control ──

export type FlowControlSlotRow = {
  id: number;
  instance_id: string;
  scope_key: string;
  flow_id: string;
  node_id: string | null;
  acquired_at: number | null;
  session_id: string | null;
  /** 租约过期时间（Unix秒）。0=旧数据(永不过期)，>0=租约模式，过期后slot自动可用 */
  lease_expires_at: number;
  /** 续租次数，每次heartbeat续租+1，仅用于监控调测 */
  renew_count: number;
  gmt_create: number | null;
  gmt_modified: number | null;
};

export type FlowControlQueueRow = {
  id: number;
  instance_id: string;
  scope_key: string;
  flow_id: string;
  node_id: string | null;
  priority: number;
  status: string;
  enqueued_at: number | null;
  dispatch_after: number | null;
  expires_at: number | null;
  payload: string | null;
  gmt_create: number | null;
  gmt_modified: number | null;
};

export type FlowControlSlotInsert = {
  instanceId: string;
  scopeKey: string;
  flowId: string;
  nodeId: string | null;
  acquiredAt: number;
  /** 当前 session ID，用于调试追踪 */
  sessionId?: string | null;
  /** 租约过期时间（Unix秒）。0=旧数据(永不过期)，>0=租约模式 */
  leaseExpiresAt?: number;
};

export type FlowControlQueueInsert = {
  instanceId: string;
  scopeKey: string;
  flowId: string;
  nodeId: string | null;
  priority: number;
  status: string;
  enqueuedAt: number;
  dispatchAfter: number | null;
  expiresAt: number | null;
  payload: string | null;
};

export interface IFlowControlRepository {
  /** 原子性获取槽位：检查计数未超限后插入。返回 true 表示成功获取。 */
  acquireSlot(insert: FlowControlSlotInsert, maxConcurrent: number): Promise<boolean>;

  /** 释放指定槽位 */
  releaseSlot(instanceId: string, scopeKey: string, flowId: string, nodeId: string | null): Promise<boolean>;

  /** 释放某个流程持有的所有槽位 */
  releaseAllSlotsForFlow(instanceId: string, flowId: string): Promise<number>;

  /** 统计指定作用域当前持有的槽位数 */
  countSlots(instanceId: string, scopeKey: string): Promise<number>;

  /** 入队一个等待请求。返回值语义:
   *  > 0: 成功入队，返回队列条目的 DB 行 ID
   *  = 0: 入队失败（API 错误、队列满等），调用方应视为未入队
   *  < 0: 去重成功（DUPLICATE_ENQUEUE），该 flow 已在队列中，无需重试
   *       目前仅 API 模式的 FlowControlApiRepository 会返回 ENQUEUE_DUPLICATE_SENTINEL (-1)
   */
  enqueue(insert: FlowControlQueueInsert): Promise<number>;

  /** 标记为已派发。返回 true 表示更新成功（已弃用，保留向后兼容） */
  markDispatched(id: number): Promise<boolean>;

  /** 原子删除排队条目（DELETE WHERE id=? AND status='queued'）。返回 true 表示删除成功。 */
  deleteQueueEntryById(id: number): Promise<boolean>;

  /** 标记为已过期 */
  markExpired(id: number): Promise<boolean>;

  /** 批量标记过期条目（超过 expires_at 的 queued 条目） */
  expireStaleEntries(instanceId: string): Promise<number>;

  /** 获取即将过期的排队条目（超过 expires_at 的 queued 条目，未标记前查询） */
  fetchExpiringItems(instanceId: string): Promise<FlowControlQueueRow[]>;

  /** 获取指定作用域下最早排队条目，最多 limit 条 */
  fetchQueuedItems(instanceId: string, scopeKey: string, limit: number): Promise<FlowControlQueueRow[]>;

  /** 获取有排队条目的作用域列表（去重） */
  getScopesWithQueuedItems(instanceId: string): Promise<string[]>;

  /** 删除已派发或已过期的队列条目（清理） */
  deleteProcessedQueueEntries(instanceId: string, olderThan: number): Promise<number>;

  /** 释放孤儿槽位：工作流已完成但槽位未释放 */
  releaseOrphanedSlots(instanceId: string): Promise<number>;

  /** 释放过期孤儿槽位：工作流在 flow_runs 中仍为 active 状态但已停滞太久（会话已死亡） */
  releaseStaleOrphanedSlots(instanceId: string, staleSeconds: number): Promise<{ releasedSlots: number; failedFlows: number }>;

  /** 删除指定流程的排队条目 */
  deleteQueueEntriesForFlow(instanceId: string, flowId: string): Promise<number>;

  /** 查询指定作用域状态 */
  getScopeStatus(instanceId: string, scopeKey: string): Promise<{ running: number; queued: number }>;

  /** 查询当前实例所有有活动的（有 slot 的）作用域键 */
  getActiveScopeKeys(instanceId: string): Promise<string[]>;

  /** 查询排队条目（监控用） */
  getQueueItems(instanceId: string, scopeKey?: string, limit?: number): Promise<FlowControlQueueRow[]>;

  /** 查询活跃槽位（监控用） */
  getSlots(instanceId: string, scopeKey?: string): Promise<FlowControlSlotRow[]>;

  /** 强制释放指定 flow 的槽位、删除队列条目（僵尸流清理兜底）。注意：不再修改 flow_runs.status */
  forceReleaseSlotsForFlows(instanceId: string, flowIds: string[]): Promise<{ releasedSlots: number; deletedQueue: number }>;

  /** 按 session_id 分组查询槽位（调试用，不再用于僵尸检测） */
  findSlotsGroupedBySession(instanceId: string): Promise<Array<{ session_id: string; flow_ids: string[] }>>;

  /** 续租当前实例持有的所有有效租约。返回续租的行数。 */
  renewLeases(instanceId: string, newExpiryAt: number): Promise<number>;

  /** 释放已过期的租约（仅删除 slot 和 queue，不修改 flow_runs.status） */
  releaseExpiredLeases(instanceId: string): Promise<{ releasedSlots: number; deletedQueue: number }>;

  /** 计算未过期的活跃 slot 数（排除 lease_expires_at > 0 且已过期的行，保留 lease_expires_at = 0 的旧数据） */
  countActiveSlots(instanceId: string, scopeKey: string): Promise<number>;

  /** 删除旧版非 workflow scope 的遗留条目（global/executor:xxx），返回删除的 slot 和 queue 数量 */
  deleteLegacyScopeEntries(instanceId: string): Promise<{ deletedSlots: number; deletedQueue: number }>;
}

// ── Node Step Trace ──

export type NodeStepTraceRow = {
  id: number;
  flow_id: string;
  node_id: string;
  attempt: number;
  step_seq: number;
  step_type: string; // 'tool_call' | 'tool_result' | 'assistant_text' | 'progress'
  skill_name: string | null;
  tool_name: string | null;
  tool_use_id: string | null;
  tool_input_json: string | null;
  tool_output_text: string | null;
  is_error: number; // 0 | 1
  text_content: string | null;
  session_key: string | null;
  trace_id: string | null;
  observation_id: string | null;
  model: string | null;
  latency_ms: number | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  gmt_create: number;
};

export type NodeStepTraceInsert = {
  flowId: string;
  nodeId: string;
  attempt: number;
  stepSeq: number;
  stepType: string;
  skillName?: string | null;
  toolName?: string | null;
  toolUseId?: string | null;
  toolInputJson?: string | null;
  toolOutputText?: string | null;
  isError?: number;
  textContent?: string | null;
  sessionKey?: string | null;
  traceId?: string | null;
  observationId?: string | null;
  model?: string | null;
  latencyMs?: number | null;
  promptTokens?: number | null;
  completionTokens?: number | null;
};

export type NodeStepTraceSummary = {
  nodeId: string;
  attempt: number;
  skillName: string | null;
  toolCallCount: number;
  toolErrorCount: number;
  totalSteps: number;
};

export interface INodeStepTraceRepository {
  /** Batch insert step records for a node execution. */
  insertBatch(steps: NodeStepTraceInsert[]): Promise<number>;
  /** Insert a single step record (used for progress steps during execution). */
  insert(step: NodeStepTraceInsert): Promise<number>;
  /** Find all steps for a node execution, ordered by step_seq. */
  findByFlowNode(flowId: string, nodeId: string, attempt?: number): Promise<NodeStepTraceRow[]>;
  /** Find a single step by sequence number. */
  findBySeq(flowId: string, nodeId: string, attempt: number, stepSeq: number): Promise<NodeStepTraceRow | null>;
  /** Get step summary stats for all nodes in a flow run. */
  findSummaryByFlowId(flowId: string): Promise<NodeStepTraceSummary[]>;
  /** Delete steps for a flow run (cleanup). */
  deleteByFlowId(flowId: string): Promise<number>;
}

// ── Hallucination Check ──

export type HallucinationCheckRow = {
  id: number;
  flow_id: string;
  node_id: string;
  attempt: number;
  check_type: string;
  severity: string;
  passed: number; // 0 | 1
  description: string | null;
  evidence: string | null;
  risk_score: number;
  risk_level: string;
  gmt_create: number;
};

export type HallucinationCheckInsert = {
  flowId: string;
  nodeId: string;
  attempt: number;
  checkType: string;
  severity?: string;
  passed?: number;
  description?: string | null;
  evidence?: string | null;
  riskScore?: number;
  riskLevel?: string;
};

export type HallucinationCheckSummary = {
  node_id: string;
  attempt: number;
  totalChecks: number;
  failedChecks: number;
  riskScore: number;
  riskLevel: string;
};

export interface IHallucinationCheckRepository {
  /** Batch insert check results for a node execution. */
  insertChecks(checks: HallucinationCheckInsert[]): Promise<number>;
  /** Find all checks for a node execution. */
  findByFlowNode(flowId: string, nodeId: string, attempt?: number): Promise<HallucinationCheckRow[]>;
  /** Get check summary for all nodes in a flow run. */
  findSummaryByFlowId(flowId: string): Promise<HallucinationCheckSummary[]>;
  /** Delete checks for a flow run (cleanup). */
  deleteByFlowId(flowId: string): Promise<number>;
}

// ── Notification Config ──

export type NotificationConfigRow = {
  id: number;
  workflow_id: string;
  robot_code: string;
  app_secret: string;
  on_failure_users: string; // JSON array
  on_failure_groups: string; // JSON array
  on_failure_message_title: string | null;
  on_failure_message_include_run_link: number; // 0 | 1
  gmt_create: number;
  gmt_modified: number;
};

export interface INotificationConfigRepository {
  findByWorkflowId(workflowId: string): Promise<NotificationConfigRow | null>;
}

// ── Bot Workflow Permission ──

/** ClawWeb 权限检查 API 响应 */
export type BotPermissionCheckResult = {
  /** bot 是否拥有指定权限 */
  allowed: boolean;
  /** 该 workflow 是否存在任何权限记录（不限定 bot） */
  hasRecords: boolean;
};

/** bot-workflow 权限仓库接口 */
export interface IBotWorkflowPermissionRepository {
  /**
   * 检查 bot 对指定 workflow 的执行权限。
   * 返回 allowed + hasRecords，供调用方决定是否 fallback 到 YAML allowedBots。
   */
  checkExecutePermission(botId: string, botOwnerId: string, workflowId: string): Promise<BotPermissionCheckResult>;
}

// ── Execution Step Log ──

/** Step types for the execution_step_log table. */
export type ExecutionStepType =
  | "start"
  | "complete"
  | "fail"
  | "retry"
  | "skip"
  | "materialize"
  | "inject"
  | "llm_evaluate"
  | "goal_check"
  | "replan"
  | "budget_check"
  | "budget_warning"
  | "budget_exhausted"
  | "yaml_synthesized"
  | "synthesis_validated"
  | "synthesis_rejected"
  | "human_approval_requested"
  | "human_approval_granted"
  | "human_approval_denied";

export type ExecutionStepLogRow = {
  id: number;
  flow_id: string;
  node_id: string;
  step_type: string;
  timestamp: number;
  input_summary: string | null;
  output_summary: string | null;
  llm_evaluation: string | null;
  decision_path: string | null;
  duration_ms: number | null;
  token_usage: number | null;
  metadata: string | null;
  gmt_create: number;
  gmt_modified: number | null;
};

export type ExecutionStepLogInsert = {
  flowId: string;
  nodeId: string;
  stepType: ExecutionStepType;
  timestamp: number;
  inputSummary?: string | null;
  outputSummary?: string | null;
  llmEvaluation?: string | null;
  decisionPath?: string | null;
  durationMs?: number | null;
  tokenUsage?: number | null;
  metadata?: Record<string, unknown> | null;
};

export type FindExecutionStepLogOptions = {
  nodeId?: string;
  stepType?: string;
  limit?: number;
  offset?: number;
};

export interface IExecutionStepLogRepository {
  /** Insert a single execution step log entry. Best-effort: DB failure is logged but doesn't throw. */
  insertStep(step: ExecutionStepLogInsert): Promise<boolean>;
  /** Query step logs for a flow, with optional filters. Returns results sorted by timestamp ascending. */
  getStepsByFlow(flowId: string, options?: FindExecutionStepLogOptions): Promise<ExecutionStepLogRow[]>;
  /** Count step logs for a flow, with optional filters. */
  getStepCountByFlow(flowId: string, options?: FindExecutionStepLogOptions): Promise<number>;
  /** Delete step logs older than the given Unix timestamp (cleanup). Returns deleted count. */
  deleteOlderThan(olderThan: number): Promise<number>;
}

// ── Run Logs (console log capture) ──

export type RunLogInsert = {
  flow_id: string;
  /** Optional: not all log entries are tied to a specific node (e.g. engine-level errors). */
  node_id?: string | null;
  level: string;
  source: string | null;
  message: string;
  timestamp: number;
  /** Auto-assigned by the uploader; callers should not set this. */
  seq?: number;
};

export type RunLogRow = RunLogInsert & {
  id: number;
  gmt_create: number;
  gmt_modified: number | null;
};

export interface IRunLogRepository {
  /** Insert run log entries. Best-effort: DB failure is logged but doesn't throw. */
  insertBatch(entries: RunLogInsert[]): Promise<number>;
  /** Query all run logs for a flow, sorted by seq ascending. */
  findByFlowId(flowId: string): Promise<RunLogRow[]>;
  /** Delete run logs for a flow. Returns deleted count. */
  deleteByFlowId(flowId: string): Promise<number>;
  /** Delete run logs older than the given Unix timestamp (cleanup). Returns deleted count. */
  deleteOlderThan(olderThan: number): Promise<number>;
}

// ── Deploy History ──

export type DeployHistoryInsertInput = {
  packId: string;
  workflowId: string;
  deployNumber: number;
  version: number;
  tagName: string;
  action: string;
  fromDeployNumber?: number;
  specJson: string;
  note?: string;
  botId?: string | null;
  ownerId?: string | null;
};

export type DeployHistoryListRow = {
  deployNumber: number;
  version: number;
  tagName: string;
  action: string;
  fromDeployNumber?: number | null;
  note?: string | null;
  botId?: string | null;
  ownerId?: string | null;
  gmtCreate: number;
};

export type DeployHistoryDetailRow = DeployHistoryListRow & {
  specJson: string;
};

export type DeployHistoryLatestDeploy = {
  packId: string;
  workflowId: string;
  deployNumber: number;
  version: number;
  tagName: string;
  action: string;
  fromDeployNumber?: number;
};

export interface IDeployHistoryRepository {
  /** Insert a deploy history record. Returns true on success. */
  insert(input: DeployHistoryInsertInput): Promise<boolean>;
  /** Get the latest deploy record for a workflow. Returns null if no records. */
  getLatestDeploy(packId: string, workflowId: string): Promise<DeployHistoryLatestDeploy | null>;
  /** Get the latest version number from deploy history. Returns 0 if no records. */
  getLatestVersion(packId: string, workflowId: string): Promise<number>;
  /** Get the maximum deploy_number. Returns 0 if no records. */
  getMaxDeployNumber(packId: string, workflowId: string): Promise<number>;
  /** Find a deploy record by version (full snapshot incl. specJson for rollback). */
  findByVersion(packId: string, workflowId: string, version: number): Promise<DeployHistoryDetailRow | null>;
  /** Find a deploy record by deploy_number (full snapshot incl. specJson). */
  findByDeployNumber(packId: string, workflowId: string, deployNumber: number): Promise<DeployHistoryDetailRow | null>;
  /** List deploy history for a workflow, ordered by deploy_number DESC. */
  listHistory(workflowId: string, limit?: number): Promise<DeployHistoryListRow[]>;
}

// ── Scheduled Trigger ──

export interface IScheduledTriggerRepository {
  create(input: CreateTriggerInput): Promise<ScheduledTrigger>;
  getById(triggerId: string): Promise<ScheduledTrigger | null>;
  listByWorkflow(workflowId: string): Promise<ScheduledTrigger[]>;
  listEnabled(): Promise<ScheduledTrigger[]>;
  findDueTriggers(now: number): Promise<ScheduledTrigger[]>;
  update(triggerId: string, input: UpdateTriggerInput): Promise<ScheduledTrigger | null>;
  updateFireTimes(triggerId: string, lastFireTime: number, nextFireTime: number): Promise<ScheduledTrigger | null>;
  enable(triggerId: string): Promise<ScheduledTrigger | null>;
  disable(triggerId: string): Promise<ScheduledTrigger | null>;
  delete(triggerId: string): Promise<boolean>;
  countRunningFlows(workflowId: string): Promise<number>;
}

// ── Webhook Event ──

export type WebhookEventRecordInput = {
  eventId: string;
  triggerId: string;
  flowId?: string | null;
  status: string;
  requestMethod: string;
  requestHeaders?: Record<string, string> | null;
  requestBodyHash?: string | null;
  responseCode?: number | null;
  errorMessage?: string | null;
  ipAddress?: string | null;
};

export interface IWebhookEventRepository {
  record(input: WebhookEventRecordInput): Promise<WebhookEvent | null>;
  findDuplicate(eventId: string, windowHours: number): Promise<WebhookEvent | null>;
  findByTriggerId(triggerId: string, options?: { limit?: number; offset?: number }): Promise<WebhookEvent[]>;
  deleteOlderThan(retentionDays: number): Promise<number>;
}

// ── Webhook Trigger ──

export type CreateWebhookTriggerInput = {
  triggerId?: string;
  workflowId: string;
  packId?: string;
  secret?: string;
  payloadMapping?: Record<string, string> | null;
  allowedIps?: string[] | null;
  description?: string;
  enabled?: boolean;
};

export interface IWebhookTriggerRepository {
  create(input: CreateWebhookTriggerInput): Promise<WebhookTrigger>;
  getByTriggerId(triggerId: string): Promise<WebhookTrigger | null>;
  findByWorkflowId(workflowId: string): Promise<WebhookTrigger[]>;
  findAll(): Promise<WebhookTrigger[]>;
  update(triggerId: string, updates: Record<string, unknown>): Promise<WebhookTrigger | null>;
  delete(triggerId: string): Promise<boolean>;
}