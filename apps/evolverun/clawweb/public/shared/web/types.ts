import type { InterventionAction } from './types-workflow-support.js'

export type * from './types-validation-bench.js'
export type * from './types-workflow-support.js'

export interface ClientUser {
  nickName: string
  userId: string
  userName: string
  avatarUrl: string
  displayName: string
  isAdmin?: boolean
  isLogAdmin?: boolean
  isBenchAdmin?: boolean
  isClawEvolveAdmin?: boolean
  isClawInsightAdmin?: boolean
  isSuperAdmin?: boolean
}

export type BotPermission = {
  id: number
  botId: string | null
  botOwnerId: string
  canView: number
  canExecute: number
  canEdit: number
}

export type BotPermissionUpsert = {
  botId: string | null
  botOwnerId: string
  canView: number
  canExecute: number
  canEdit: number
}

export type { DirectoryBot } from "../server/services/bot-directory.js"
export type TCLogBot = import("../server/services/bot-directory.js").DirectoryBot

export type TCLogTrace = {
  traceId: string
  sessionId: string | null
  sessionKey: string | null
  botId: string | null
  ownerId: string | null
  engine: string | null
  evolution_analysis_status?: string | null
  status: string | null
  name: string | null
  startTimeMs: number | null
  endTimeMs: number | null
  latencyMs: number | null
  inputPreview: string | null
  outputPreview: string | null
  inputTokens: number | null
  outputTokens: number | null
  cacheReadTokens: number | null
  cacheWriteTokens: number | null
  totalTokens: number | null
  totalCost: number | null
  matchTypes: string[]
  matchSource?: 'direct' | 'biz_ref' | 'both'
  observations?: TCLogObservation[]
}

export type TCLogObservation = {
  observationId: string
  traceId: string
  parentObservationId: string | null
  type: string | null
  name: string | null
  model: string | null
  status: string | null
  startTimeMs: number | null
  endTimeMs: number | null
  latencyMs: number | null
  input: unknown
  output: unknown
  promptTokens: number | null
  completionTokens: number | null
  totalTokens: number | null
  metadata?: unknown
}

export type TCLogSession = {
  sessionKey: string | null
  sessionId: string | null
  ownerId: string | null
  botId: string | null
  engine: string | null
  traceCount: number
  startTimeMs: number | null
  endTimeMs: number | null
  totalTokens: number | null
  totalCost: number | null
  latestStatus: string | null
  traces: TCLogTrace[]
}

export type TCLogWorkflowRun = {
  flowId: string
  workflowId: string
  workflowTitle: string | null
  status: string
  botId: string | null
  ownerId: string | null
  originSessionId: string | null
  originSessionKey: string | null
  startedAt: number
  completedAt: number | null
  nodeCount: number
  failedCount: number
  currentPhase: string | null
  params: unknown
  input: unknown
  output: unknown
  totalDurationMs: number | null
  totalTokenUsage: number | null
  nodes: TCLogWorkflowNode[]
  matchTypes: string[]
}

export type TCLogWorkflowNode = {
  id: number | null
  flowId: string
  workflowId: string
  nodeId: string
  nodeTitle: string | null
  executorType: string | null
  status: string | null
  attempt: number
  sessionKey: string | null
  sessionId: string | null
  embeddedSessionKey: string | null
  input: unknown
  output: unknown
  errorText: string | null
  durationMs: number | null
  tokenUsage: unknown
  systemContext: unknown
  progressMessage: string | null
  startedAt: number | null
  completedAt: number | null
}

export type TCLogTimelineItem = {
  id: string
  source: 'ocb_trace' | 'ocb_observation' | 'clawmind_workflow' | 'clawmind_node_step' | string
  eventTimeMs: number
  title: string
  status: string | null
  traceId?: string
  flowId?: string
  nodeId?: string
  sessionId?: string | null
  sessionKey?: string | null
  payload?: unknown
}

export type TCLogQueryParams = {
  ownerId?: string
  embed?: boolean
  botId?: string
  traceId?: string
  sessionKey?: string
  sessionId?: string
  keyword?: string
  engine?: string
  status?: string
  from?: number
  to?: number
  dataSource?: 'auto' | 'tc' | 'langfuse'
  limit?: number
  offset?: number
  groupBy?: 'session' | 'trace'
}

export type TCLogTaskSearchParams = {
  ownerId?: string
  embed?: boolean
  botId?: string
  bizScene?: string
  taskId?: string
  from?: number
  to?: number
  dataSource?: 'auto' | 'tc' | 'langfuse'
  limit?: number
  offset?: number
  includeRaw?: boolean
}

export type TCLogTaskListParams = {
  ownerId?: string
  botId?: string
  bizScene?: string
  taskId?: string
  from?: number
  to?: number
  limit?: number
}

export type TCLogQueryResponse = {
  query: Record<string, unknown>
  sessions: TCLogSession[]
  traces: TCLogTrace[]
  summary: {
    sessionCount: number
    traceCount: number
    totalTokens: number
    totalCost: number
  }
  dataSource: string
  fallbackUsed: boolean
}

export type TCLogTaskSummary = {
  bizScene: string
  taskId: string
  botId: string | null
  ownerId: string | null
  source: string
  refCount: number
  traceCount: number
  workflowRunCount: number
  lastEventTimeMs: number | null
}

export type TCLogTaskListResponse = {
  query: Record<string, unknown>
  tasks: TCLogTaskSummary[]
  dataSource: string
}

export type TCLogTaskSearchResponse = {
  query: Record<string, unknown>
  summary: {
    botCount: number
    traceCount: number
    workflowRunCount: number
    nodeStepCount: number
    errorCount: number
    totalTokens: number
    totalCost: number
  }
  relations: Array<{ type: string; value: string; source: string }>
  traces: TCLogTrace[]
  workflowRuns: TCLogWorkflowRun[]
  timeline: TCLogTimelineItem[]
  dataSource: string
  fallbackUsed: boolean
}

export type DingTalkUserTarget = {
  userId: string
  name?: string
}

export type DingTalkGroupTarget = {
  openConversationId: string
  name?: string
}

export type DingTalkEventOptions = {
  title?: string
  includeRunLink?: boolean
  includeNodeOutput?: boolean
  includeFinalOutput?: boolean
  maxOutputFields?: number
  maxOutputValueLen?: number
}

/** Supported workflow lifecycle events for DingTalk notifications. */
export type DingTalkNotifyEvent =
  | "workflow_started"
  | "node_started"
  | "node_succeeded"
  | "node_failed"
  | "node_rejected"
  | "node_skipped"
  | "workflow_succeeded"
  | "workflow_failed"
  | "workflow_cancelled"

export type NotificationConfig = {
  workflowId: string
  robotCode: string
  appSecret: string
  onFailureUsers: DingTalkUserTarget[]
  onFailureGroups: DingTalkGroupTarget[]
  onFailureMessageTitle?: string | null
  onFailureMessageIncludeRunLink: boolean
  /** Lifecycle events that trigger DingTalk notifications.
   *  If omitted, the legacy behavior (only node_failed + workflow_failed) applies. */
  notifyOn?: DingTalkNotifyEvent[]
  /** Per-event message customization keyed by event name. */
  eventOptions?: Partial<Record<DingTalkNotifyEvent, DingTalkEventOptions>>
}

interface TERNUser {
  outUserNo?: string
  nickName?: string
  userName?: string
  avatarUrl?: string
  displayName?: string
  clientUser?: Partial<ClientUser>
}

interface TERNWindow {
  user: TERNUser
}

interface MUSEWindow {
  yfdAppId: string
  appVersionId: string
}

declare global {
  interface Window {
    __TERN__?: TERNWindow
    __MUSE__?: MUSEWindow
  }
}

export type NodeStatus =
  | 'pending'
  | 'running'
  | 'postActionsRunning'
  | 'waiting'
  | 'succeeded'
  | 'failed'
  | 'blocked'
  | 'skipped'
  | 'cancelled'

export interface FlowRun {
  flow_id: string
  workflow_id: string
  workflow_title: string
  status: NodeStatus
  triggered_by: string | null
  node_count: number
  succeeded_count: number
  failed_count: number
  total_duration_ms: number | null
  total_token_usage: number | null
  started_at: string
  completed_at: string | null
  user_id: string | null
  origin_bot_id: string | null
  plugin_version: string | null
  engine: string | null
  evolution_analysis_status?: 'analyzing' | 'completed' | 'failed' | null
  evolution_analyzed_at?: number | null
  workflow_version: number | null
  workflow_deploy_number: number | null
}

export interface NodeExecution {
  flow_id: string
  node_id: string
  node_title: string | null
  executor_type: string
  triggered_by: string | null
  phase: string | null
  branch_id: string | null
  session_key: string | null
  session_id: string | null
  embedded_session_key: string | null
  status: NodeStatus
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  input_json: string | null
  output_json: string | null
  error_text: string | null
  token_usage_json: string | null
  system_context_json: string | null
  progress_message: string | null
  resolved_prompt: string | null
  attempt: number
}

export interface FlowEvent {
  event_id: string
  flow_id: string
  event_type: string
  node_id: string | null
  time: string
  data_json: string | null
  error_text: string | null
}

export interface RetryConfig {
  maxAttempts?: number
  delayMs?: number
  backoff?: 'fixed' | 'exponential' | 'linear'
}

export interface OutputContract {
  type?: string
  required?: boolean
  properties?: Record<string, unknown>
  schema?: Record<string, unknown>
  [key: string]: unknown
}

export interface MockConfig {
  output?: unknown
  [key: string]: unknown
}

export interface KnowledgeItem {
  type: string
  content: string
  [key: string]: unknown
}

export interface PostAction {
  id?: string
  action?: string
  required?: boolean
  args?: Record<string, unknown>
  saveAs?: Record<string, string>
}

export interface OnFeedbackConfig {
  target?: string
  feedbackPath?: string
  feedbackMode?: string
  feedbackTemplate?: string
  reset?: string
}

export interface NodeAlerting {
  dingtalk?: boolean
  severity?: string
  keywords?: string[]
}

export interface OnResultBranch {
  value: string
  target: string
}

export interface WorkflowNode {
  id: string
  title: string
  phase?: string
  businessStatus?: string
  executor: {
    type: string
    [key: string]: unknown
  }
  dependsOn?: string[]
  branchId?: string
  join?: 'all' | 'any'
  triggerRule?: 'all_success' | 'one_success' | 'all_done'
  retry?: RetryConfig
  outputContract?: OutputContract
  outputSchema?: Record<string, unknown>
  mock?: MockConfig
  knowledge?: KnowledgeItem[] | boolean
  knowledgeBaseId?: string
  knowledgeQuery?: string
  validationTemplateId?: string
  validationMinScore?: number
  onSuccess?: PostAction[]
  onFailure?: PostAction[]
  onFeedback?: OnFeedbackConfig
  onResult?: OnResultBranch[]
  alerting?: NodeAlerting
  progressMessage?: string
  [key: string]: unknown
}

export interface WorkflowSpec {
  id: string
  version: string
  title: string
  nodes: WorkflowNode[]
  config?: Record<string, unknown>
  params?: Record<string, unknown>
  tests?: unknown[]
  requiredParams?: string[]
  input?: {
    mode?: string
    requiredParams?: string[]
    schema?: Record<string, unknown>
  }
  identity?: {
    key?: string
    label?: string
    duplicatePolicy?: string
  }
  outputs?: Record<string, unknown>
  debug?: {
    summaryKeys?: string[]
  }
  defaults?: {
    progress?: string
    user?: string
    contextPolicy?: string
    [key: string]: unknown
  }
  collaboration?: Record<string, unknown>
  workflow?: {
    preflight?: PostAction[]
    onStart?: PostAction[]
    onFinish?: PostAction[]
  }
  messages?: {
    onCreated?: string
    onFinished?: string
    variants?: Array<{ condition?: string; message: string }>
  }
  facade?: {
    command?: string
    remark?: string
  }
  allowedBots?: string[]
  [key: string]: unknown
}

// --- Workflow Deploy History (read-only version management) ---

/** One row of workflow_deploy_history (list payload, no spec_json). */
export interface DeployHistoryItem {
  /** DB pack identity is included only on the separately returned active record. */
  packId?: string
  /** Opaque baseline token included only on the separately returned active record. */
  activeToken?: string
  deployNumber: number
  version: number
  tagName: string | null
  action: string // 'deploy' | 'rollback' | 'pull' | 'migration' | 'edit'
  fromDeployNumber: number | null
  note: string | null
  botId: string | null
  ownerId: string | null
  isActive: boolean
  gmtCreate: number // epoch seconds
}

/** Full snapshot of a single version (incl. spec_json). */
export interface VersionSnapshot {
  workflowId: string
  version: number
  deployNumber: number
  tagName: string | null
  action: string
  specJson: string
  note: string | null
  gmtCreate: number
}

/** One side of a version diff. */
export interface VersionDiffSide {
  version: number
  deployNumber: number
  action: string
  specJson: string
  gmtCreate: number
}

/** Result of GET /api/workflows/:wf/history/diff. */
export interface VersionDiffResult {
  workflowId: string
  from: VersionDiffSide
  to: VersionDiffSide
}

/** A deployed version entry in the version list. */
export interface VersionListItem {
  version: number
  deployNumber: number
  tagName: string | null
  isActive: boolean
  gmtCreate: number
}

/** Result of GET /api/workflows/:wf/versions. */
export interface VersionListResponse {
  workflowId: string
  versions: VersionListItem[]
}

/** Result of POST /api/workflows/:wf/versions/:v/activate. */
export interface VersionActivateResponse {
  workflowId: string
  version: number
  activated: boolean
}

export interface FacadeBinding {
  command: string
  workflowId: string
  packId: string | null
  remark: string | null
}

export interface FlowRunsResponse {
  runs: FlowRun[]
  total: number
  /** Server-side status→count breakdown for accurate success-rate (avoids pagination skew) */
  statusCounts?: Record<string, number>
}

export type WorkflowTypeRow = {
  workflow_id: string
  workflow_title: string | null
  run_count: number
  last_status: string | null
  last_run_at: number | null
  updated_at: number | null
  owner_id?: string | null
}

export interface WorkflowTypesResponse {
  workflows: WorkflowTypeRow[]
  total: number
}

export interface FlowRunDetail {
  run: FlowRun
  nodes: NodeExecution[]
  availableInterventions?: InterventionAction[]
}

export interface DryRunRequest {
  spec: WorkflowSpec
  params?: Record<string, unknown>
  mocks?: Record<string, unknown>
}

export interface DryRunResult {
  nodeStates: Record<string, NodeState>
  nodeReports?: NodeReport[]
  assertionResults?: AssertionResult[]
}

export interface NodeReport {
  nodeId: string
  nodeStatus: string
  mockSource: string
}

export interface NodeState {
  nodeId: string
  status: NodeStatus
  output?: unknown
  error?: string
  durationMs?: number
}

export interface AssertionResult {
  description: string
  passed: boolean
  expected?: unknown
  actual?: unknown
}

export interface KnowledgeBase {
  id: number
  kbId: string
  name: string
  description: string | null
  instanceName: string
  interfaceName: string
  token: string
  userName: string
  userId: string
  topK: number
  rankingThreshold: number
  vectorThreshold: number
  rankingModel: string
  env: string
  enabled: boolean
  gmtCreate: number
  gmtModified: number
}

export interface KnowledgeBaseCreateInput {
  kb_id: string
  name: string
  instance_name: string
  interface_name: string
  token: string
  description?: string
  user_name?: string
  user_id?: string
  top_k?: number
  ranking_threshold?: number
  vector_threshold?: number
  ranking_model?: string
  env?: string
}

export interface KnowledgeBaseUpdateInput {
  name?: string
  description?: string
  instance_name?: string
  interface_name?: string
  token?: string
  user_name?: string
  user_id?: string
  top_k?: number
  ranking_threshold?: number
  vector_threshold?: number
  ranking_model?: string
  env?: string
  enabled?: number
}

export interface AppConfigEntry {
  id: number
  configKey: string
  configYaml: string
  version: number
  enabled: boolean
  description: string | null
  updatedBy: string | null
  gmtCreate: number | string
  gmtModified: number | string
}

export interface AppConfigCreateInput {
  config_key: string
  config_yaml: string
  description?: string
  updated_by?: string
}

export interface AppConfigUpdateInput {
  config_yaml?: string
  enabled?: number
  description?: string
  updated_by?: string
}

export type AdminRole = 'admin' | 'log_admin' | 'bench_admin' | 'claw_evolve_admin' | 'claw_insight_admin'

export interface AdminUserEntry {
  id: number
  userId: string
  role: AdminRole
  roleLabel: string
  source: string
  enabled: boolean
  createdBy: string | null
  gmtCreate: number | string
  gmtModified: number | string
}

export interface AdminRoleOption {
  value: AdminRole
  label: string
}

export interface AdminUsersListResponse {
  items: AdminUserEntry[]
  roles: AdminRoleOption[]
}

export interface AdminUserCreateInput {
  userId: string
  role: AdminRole
}

export interface KnowledgeBaseTestResult {
  query: string
  items: Array<{
    content: string
    score: number
    title: string
    source: string
    kbName?: string
  }>
  total: number
}

// --- YuQue Knowledge Base ---

export interface YuQueBookInfo {
  bookId: number
  name: string
}

// --- System Log Search ---

export interface SystemLogEntry {
  timestamp: string
  level: 'ERROR' | 'WARN' | 'INFO' | 'DEBUG'
  message: string
  source: string
  metadata?: Record<string, unknown>
}

export interface SystemLogSourceResult {
  source: string
  status: 'success' | 'partial' | 'failed'
  entriesCount: number
  errorEntriesCount: number
  durationMs: number
  error?: string
}

export interface SystemLogSearchResult {
  entries: SystemLogEntry[]
  sourceResults: SystemLogSourceResult[]
  totalEntries: number
  totalErrors: number
  durationMs: number
  collectorType: string
}
