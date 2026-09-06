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

export type TCLogBot = {
  botId: string
  botName?: string | null
  env?: string | null
  deviceProvider?: 'baas' | 'arca' | string | null
  activeEngine?: string | null
  botType?: string | null
  hasServiceBot?: boolean
  displayBotId: string
  status: 'active' | 'all' | string
  source: string
  ownerId?: string | null
  accessType?: 'owner' | 'collaborator'
}

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

export type NotificationConfig = {
  workflowId: string
  robotCode: string
  appSecret: string
  onFailureUsers: DingTalkUserTarget[]
  onFailureGroups: DingTalkGroupTarget[]
  onFailureMessageTitle?: string | null
  onFailureMessageIncludeRunLink: boolean
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

export type AdminRole = 'admin' | 'log_admin' | 'bench_admin' | 'claw_evolve_admin'

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

// --- Validation Templates ---

export interface GradingWeights {
  automated?: number
  llm_judge?: number
}

export interface ValidationTemplateContent {
  prompt: string
  expectedBehavior?: string
  gradingCriteria?: string
  automatedChecks?: string
  /** Frontmatter field: task category (e.g. "complex") */
  category?: string
  /** Frontmatter field: grading type (e.g. "hybrid") */
  gradingType?: string
  /** Frontmatter field: timeout in seconds */
  timeoutSeconds?: number
  /** Frontmatter field: workspace file paths */
  workspaceFiles?: string[]
  /** Frontmatter field: weights for automated vs LLM judge scoring */
  gradingWeights?: GradingWeights
  /** Body section: applicability include/exclude criteria */
  applicability?: string
  /** Body section: LLM judge rubric (separate from gradingCriteria) */
  llmJudgeRubric?: string
}

export interface ValidationTemplate {
  id: number
  templateId: string
  name: string
  description: string | null
  content: ValidationTemplateContent
  enabled: boolean
  gmtCreate: number
  gmtModified: number
}

export interface ValidationTemplateCreateInput {
  template_id: string
  name: string
  description?: string
  content: ValidationTemplateContent
}

export interface ValidationTemplateUpdateInput {
  name?: string
  description?: string
  content?: ValidationTemplateContent
  enabled?: number
}

export interface ValidationResult {
  passed: boolean
  score: number
  feedback: string
  details: Record<string, number>
}

// --- Workflow Validation ---

export interface WorkflowValidationIssue {
  path: string
  message: string
  severity: 'error' | 'warning'
}

export interface WorkflowValidationResult {
  valid: boolean
  issues: WorkflowValidationIssue[]
  normalizedSpec: WorkflowSpec | null
}

// --- Flow Control ---

export interface FlowControlSlot {
  id: number
  instance_id: string
  scope_key: string
  flow_id: string
  node_id: string | null
  acquired_at: number
  gmt_create: number
  gmt_modified: number
}

export interface FlowControlQueueItem {
  id: number
  instance_id: string
  scope_key: string
  flow_id: string
  node_id: string | null
  priority: number
  status: string
  enqueued_at: number
  dispatch_after: number | null
  expires_at: number | null
  payload: string | null
  gmt_create: number
  gmt_modified: number
}

export interface FlowControlListResponse<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface FlowControlDeleteResponse {
  deleted: boolean
  id: number
  queueDeleted?: number
  slotsReleased?: number
}

export interface FlowControlFlowDeleteResponse {
  flowId: string
  slotsReleased: number
  queueDeleted: number
}

export interface FlowControlBatchDeleteResponse {
  deleted: number
}

export interface FlowControlDeleteAllResponse {
  deleted: number
}

// --- Langfuse Trace Analysis ---

export interface LangfuseObservation {
  id: string
  type: 'span' | 'generation' | 'event'
  name: string | null
  startTime: string
  endTime: string | null
  latencyMs: number | null
  input: unknown
  output: unknown
  model: string | null
  promptTokens: number | null
  completionTokens: number | null
  totalTokens: number | null
  statusMessage: string | null
}

export interface LangfuseTrace {
  id: string
  name: string | null
  sessionId: string | null
  startTime: string
  endTime: string | null
  latencyMs: number | null
  input: unknown
  output: unknown
  metadata: Record<string, unknown> | null
  observations: LangfuseObservation[]
  scores: Array<{ id: string; name: string; value: number; comment: string | null }>
}

export interface LangfuseTracesResponse {
  data: LangfuseTrace[]
  meta: { page: number; limit: number; totalItems: number }
}

export interface AnalysisResult {
  summary: string
  analysis: string
  suggestions: string[]
  nodeSlice?: {
    inputCompleteness: number
    outputFormatCompliant: boolean
    anomalies: string[]
  }
  performance?: {
    bottleneckObservation: string
    latencyBreakdown: string
    tokenEfficiency: string
  }
  llmQuality?: {
    formatCompliant: boolean
    hallucinationRisk: boolean
    toolCallCount: number
  }
}

// --- Bench Domains ---

export interface BenchDomain {
  id: number
  domainId: string
  name: string
  description: string | null
  status: 'active' | 'archived'
  templateCount: number
  createdBy: string | null
  ownerUserId: string
  gmtCreate: number
  gmtModified: number
}

export interface BenchDomainCreateInput {
  domainId: string
  name: string
  description?: string | null
}

export interface BenchDomainUpdateInput {
  name?: string
  description?: string | null
}

// --- Bench Templates ---

export interface BenchTemplate {
  id: number
  domainId: string
  templateName: string
  displayName: string | null
  description: string | null
  category: string | null
  targetType: string
  gradingType: string
  source: string
  sourcePath: string | null
  sourceHash: string | null
  latestVersion: number
  publishedVersion: number | null
  status: 'draft' | 'published' | 'archived'
  createdBy: string | null
  ownerUserId: string
  gmtCreate: number
  gmtModified: number
  versions: Array<{ version: number; status: string; contentMd?: string; gmtCreate: number }>
}

export interface BenchTemplateVersion {
  id: number
  domainId: string
  templateName: string
  version: number
  displayName: string | null
  description: string | null
  contentMd: string
  parsedMeta: Record<string, unknown> | null
  sourcePath: string | null
  sourceHash: string | null
  status: string
  createdBy: string | null
  gmtCreate: number
  gmtModified: number
}

export interface BenchTemplateCreateInput {
  domainId: string
  templateName: string
  displayName?: string | null
  description?: string | null
  category?: string | null
  targetType?: string
  gradingType?: string
  contentMd: string
  sourcePath?: string | null
  sourceHash?: string | null
  status?: string
}

export interface BenchTemplateUpdateInput {
  displayName?: string | null
  description?: string | null
  category?: string | null
  targetType?: string
  gradingType?: string
  contentMd?: string
  sourcePath?: string | null
  sourceHash?: string | null
  status?: string
}

export interface BenchUploadScanItem {
  action: 'new' | 'update' | 'skip' | 'conflict'
  templateName: string
  displayName: string
  originalFilename: string
  entryPath: string
  fileKey: string
  currentVersion: number | null
  nextVersion: number | null
  sourceHash: string
  reason: string
  imported?: boolean
}

export interface BenchUploadScanResult {
  domainId: string
  summary: { new: number; update: number; skip: number; conflict: number; imported?: number }
  items: BenchUploadScanItem[]
}

// --- Bench Runs ---

export interface BenchRun {
  id: number
  benchRunId: string
  domainId: string
  templateName: string
  templateVersion: number
  runScope: 'template' | 'domain'
  templateCount: number | null
  targetType: string
  status: 'pending' | 'running' | 'succeeded' | 'failed' | 'cancelled'
  score: number | null
  maxScore: number | null
  passRate: number | null
  model: string | null
  suite: string | null
  scene: string | null
  triggeredBy: string | null
  clawmindFlowId: string | null
  sessionId: string | null
  sessionKey: string | null
  runConfig: Record<string, unknown> | null
  summary: Record<string, unknown> | null
  tokenUsage?: BenchTokenUsage | null
  errorText: string | null
  startedAt: number | null
  completedAt: number | null
  ownerUserId: string
  gmtCreate: number
  gmtModified: number
}

export interface BenchRunCreateInput {
  domainId: string
  templateName: string
  templateVersion: number
  targetType?: string
  model?: string | null
  suite?: string | null
  scene?: string | null
  triggeredBy?: string | null
  clawmindFlowId?: string | null
  sessionId?: string | null
  sessionKey?: string | null
  runConfig?: Record<string, unknown> | null
}

export interface BenchRunUpdateInput {
  status?: string
  score?: number | null
  maxScore?: number | null
  passRate?: number | null
  summary?: Record<string, unknown> | null
  errorText?: string | null
  startedAt?: number | null
  completedAt?: number | null
}

export interface BenchRunCreateResponse {
  benchRunId: string
  detailUrl: string
}

export interface BenchRunListResponse {
  runs: BenchRun[]
  total: number
  limit: number
  offset: number
}

export interface BenchDomainSummary {
  domainId: string
  ownerUserId: string
  templateCount: number
  runCount: number
  latestRun: BenchRun | null
  latestScore: number | null
  latestPassRate: number | null
}

// --- Bench Admin ---

export interface BenchTag {
  id: number
  tagId: string
  name: string
  description: string | null
  status: string
  createdBy: string | null
  gmtCreate: number | string
  gmtModified: number | string
}

export interface BenchAdminSummary {
  totalRunCount: number
  succeededCount: number
  failedCount: number
  runningCount: number
  avgPassRate: number | null
  avgScore: number | null
  ownerCount: number
  domainCount: number
  templateCount: number
}

export interface BenchAdminDailyItem {
  date: string
  runCount: number
  succeededCount: number
  failedCount: number
  runningCount: number
  avgPassRate: number | null
}

export interface BenchAdminDailyResponse {
  from: number
  to: number
  days: BenchAdminDailyItem[]
}

export interface BenchAdminTemplateTag {
  tagId: string
  name: string
  status: string
}

export interface BenchAdminSample {
  ownerUserId: string
  domainId: string
  templateName: string
  targetType: string | null
  tags: BenchAdminTemplateTag[]
  runCount: number
  latestRunAt: number | null
  latestStatus: string | null
  latestPassRate: number | null
  avgPassRate: number | null
  failedRunCount: number
}

export interface BenchAdminDomain {
  ownerUserId: string
  domainId: string
  name: string
  status: string
  templateCount: number
  tags: BenchAdminTemplateTag[]
}

export interface BenchAdminDomainsResponse {
  domains: BenchAdminDomain[]
  total: number
}

export interface BenchAdminSamplesResponse {
  samples: BenchAdminSample[]
  total: number
  limit: number
  offset: number
}

export interface BenchAdminFilters {
  ownerUserId?: string
  domainId?: string
  templateName?: string
  status?: string
  tagId?: string
  from?: number
  to?: number
  limit?: number
  offset?: number
}

// --- Bench Task Results ---

export interface BenchTaskResult {
  id: number
  resultId: string
  benchRunId: string
  taskId: string
  taskName: string | null
  status: string
  score: number | null
  maxScore: number | null
  gradingType: string | null
  executionTimeMs: number | null
  transcriptPath: string | null
  workspacePath: string | null
  resultJson: Record<string, unknown> | null
  breakdown: Record<string, unknown> | null
  tokenUsage?: BenchTokenUsage | null
  notes: string | null
  errorText: string | null
  gmtCreate: number
  gmtModified: number
}

export interface BenchTaskResultBatchInput {
  results: Array<{
    resultId?: string
    taskId: string
    taskName?: string | null
    status: string
    score?: number | null
    maxScore?: number | null
    gradingType?: string | null
    executionTimeMs?: number | null
    transcriptPath?: string | null
    workspacePath?: string | null
    resultJson?: Record<string, unknown> | null
    breakdown?: Record<string, unknown> | null
    notes?: string | null
    errorText?: string | null
  }>
}

export interface BenchTaskResultBatchResponse {
  created: number
  results: BenchTaskResult[]
}

export interface BenchTokenUsage {
  inputTokens?: number
  outputTokens?: number
  cacheReadTokens?: number
  cacheWriteTokens?: number
  totalTokens?: number
  requestCount?: number
  costUsd?: number
  raw?: unknown
}

export interface BenchArtifact {
  id: number
  artifactId: string
  benchRunId: string
  resultId: string | null
  taskId: string | null
  artifactType: string
  filename: string | null
  contentType: string | null
  sizeBytes: number | null
  storageType: string
  storagePath: string | null
  summary: Record<string, unknown> | null
  sha256: string | null
  createdBy: string | null
  ownerUserId: string
  gmtCreate: number
  gmtModified: number
  contentText?: string | null
  contentJson?: unknown
}

export interface BenchArtifactCreateInput {
  artifactType: string
  taskId?: string | null
  resultId?: string | null
  filename?: string | null
  contentType?: string | null
  contentText?: string | null
  contentJson?: unknown
  summary?: Record<string, unknown> | null
  createdBy?: string | null
}

export interface BenchSessionSummary {
  artifactId: string
  benchRunId: string
  taskId: string | null
  artifactType: string
  filename: string | null
  contentType: string | null
  sizeBytes: number | null
  eventCount: number | null
  messageCount: number | null
  toolCallCount: number | null
  totalTokens: number | null
  firstTimestamp: string | null
  lastTimestamp: string | null
  summary: Record<string, unknown>
  gmtCreate: number
}

export interface BenchSessionsResponse {
  benchRunId: string
  sessions: BenchSessionSummary[]
}

export interface BenchSessionDetail extends BenchSessionSummary {
  contentText: string | null
  contentJson: unknown
  events: unknown[]
}

export interface BenchBatchPublishInput {
  templates: Array<{ templateName: string; version?: number | null }>
}

export interface BenchBatchPublishResponse {
  published: number
  failed: number
  items: Array<{
    templateName: string
    version: number | null
    success: boolean
    skipped?: boolean
    reason: string
  }>
}

// --- Session Diagnosis ---

export interface SessionDiagnosisResult {
  summary: string
  sessionOverview: {
    totalTraces: number
    totalObservations: number
    totalTokens: { prompt: number; completion: number; total: number }
    models: string[]
    timeRange: { earliest: string | null; latest: string | null }
    duration: string
  }
  systemPromptAnalysis: {
    promptsFound: string[]
    issues: Array<{
      severity: string
      category: string
      description: string
      evidence: string
      impact: string
    }>
    overallAssessment: string
  }
  outputCausality: Array<{
    observationId: string
    observationName: string
    model: string
    inputSummary: string
    systemPromptInfluence: string
    outputSummary: string
    whyThisOutput: string
    qualityIssue: string | null
  }>
  toolUsageAnalysis: {
    totalToolCalls: number
    toolCallSequence: string[]
    issues: string[]
    efficiency: string
  }
  performanceAnalysis: {
    bottleneckObservation: string
    latencyBreakdown: string
    tokenEfficiency: string
    totalDuration: string
  }
  errorPatterns: string[]
  suggestions: Array<{
    priority: string
    category: string
    suggestion: string
    rationale: string
  }>
}

// --- Reasoning Log (Theta) ---

export interface ThetaTraceLog {
  traceId: string
  respId: string | null
  model: string
  startTime: number
  endTime: number
  appKeyId: number
  costTime: number
  resultState: string
  resultCode: string
  errorMsg: string | null
  promptTokens: number
  cachedPromptTokens: number
  completionTokens: number
  ttft: number | null
  tpot: number | null
  originRequest: string | null
  originResponse: string | null
}

export interface ThetaTraceQueryParams {
  pageNo: number
  pageSize: number
  appKeyId?: number
  startTime?: number
  endTime?: number
  traceId?: string
  resultState?: string
  resultCode?: string
  serviceName?: string
  content?: string
  respId?: string
  type?: string // 'online' | 'async'
}

export interface ThetaApiKey {
  apiKeyId: number
  appName: string
  creatorName: string | null
  level: string | null
  status: string
  isEnabled: string
  apiKeyTokensUsage: number
  totalTokenQuota: number
  remainingQuota: number
  haKeys?: Array<{ serviceName: string }> | null
}

// --- Human Intervention ---

export type InterventionAction = 'retry' | 'skip' | 'revise' | 'confirm'

export interface InterventionInfo {
  flowId: string
  status: string
  canIntervene: boolean
  availableInterventions: InterventionAction[]
  interventionReady: boolean
  originBotId: string | null
  originSessionKey: string | null
  originSessionId: string | null
  hasCredentials: boolean
}

export interface InterveneRequest {
  action: InterventionAction
  nodeId?: string
  nodeTitle?: string
  reason?: string
}

export interface InterveneResult {
  ok: boolean
  flowId: string
  action: InterventionAction
  messageId: string | null
  sessionId: string | null
}

export interface SessionInfoUpdate {
  originBotId?: string | null
  originSessionKey?: string | null
  originSessionId?: string | null
}

export interface SessionInfoUpdateResult {
  ok: boolean
  flowId: string
  originBotId: string | null
  originSessionKey: string | null
  originSessionId: string | null
}

// --- Chat (Multi-turn Intervention) ---

export interface ChatSendResult {
  ok: boolean
  flowId: string
  messageId: string | null
  sessionId: string | null
}

export interface ChatPollResult {
  ok: boolean
  status: number
  data: {
    messageId: string
    sessionId: string | null
    messageStatus: 'PENDING' | 'RUNNING' | 'COMPLETED' | 'FAILED' | 'UNKNOWN'
    result: { content?: string } | null
  } | null
  errorCode: string | number | null
  errorMessage: string | null
}

export interface ChatMessage {
  id: string
  role: 'user' | 'bot'
  content: string
  timestamp: number
  /** BaaS message_id for polling bot responses */
  messageId?: string
  /** Pending = waiting for bot response */
  pending?: boolean
  /** Action label (e.g. "重试", "修正") if this was an action button click */
  actionLabel?: string
}

// ── Node Step Trace ──────────────────────────────────────────────────

export interface NodeStepTraceStep {
  stepSeq: number
  stepType: 'tool_call' | 'tool_result' | 'assistant_text' | 'progress'
  toolName: string | null
  toolUseId: string | null
  toolInputJson: string | null
  toolOutputText: string | null
  isError: boolean
  textContent: string | null
  sessionKey: string | null
}

export interface NodeStepTraceData {
  flowId: string
  nodeId: string
  attempt: number
  skillName: string | null
  totalSteps: number
  toolCallCount: number
  toolErrorCount: number
  steps: NodeStepTraceStep[]
}

// ── Hallucination Check ──────────────────────────────────────────────────

export interface HallucinationCheckItem {
  checkType: string
  severity: 'low' | 'medium' | 'high'
  passed: boolean
  description: string | null
  evidence: string | null
}

export interface HallucinationCheckData {
  flowId: string
  nodeId: string
  attempt: number
  riskScore: number
  riskLevel: 'none' | 'low' | 'medium' | 'high'
  failedChecks: number
  totalChecks: number
  checks: HallucinationCheckItem[]
}

// ── Auto-Heal ──────────────────────────────────────────────────

export interface AutoHealDiagnosisRequest {
  flowId: string
  useBaas?: boolean
  customPrompt?: string
}

/** Response from POST /diagnose — async submit returns immediately */
export interface AutoHealDiagnosisSubmitResult {
  ok: boolean
  diagnosisId: string
  messageId: string
  sessionId: string
  workflowId: string
  channel: 'baas' | 'llm'
  status: 'pending'
}

/** Response from GET /diagnoses/:diagnosisId — poll result */
export interface AutoHealDiagnosisPollResult {
  status: 'pending' | 'running' | 'completed' | 'failed'
  result?: AutoHealDiagnosisResult
  error?: string
}

export interface AutoHealApplyRequest {
  workflowId: string
  spec: WorkflowSpec
  packId?: string
  diagnosisId?: string
  autoRun?: boolean
}

export interface AutoHealRunRequest {
  workflowId: string
  params?: Record<string, unknown>
}

export interface AutoHealErrorChainItem {
  nodeId: string
  nodeTitle: string | null
  executorType: string
  errorText: string | null
  analysis: string | null
}

export interface AutoHealFixSuggestion {
  nodeId: string
  field: string
  oldValue: string | null
  newValue: string | null
  reason: string
}

export interface AutoHealDiffItem {
  type: 'add' | 'change' | 'remove'
  path: string
  value?: string | null
  oldValue?: string | null
  newValue?: string | null
}

export interface AutoHealDiagnosisResult {
  ok: boolean
  diagnosisId: string
  workflowId: string
  summary: string
  errorChain: AutoHealErrorChainItem[]
  fixSuggestions: AutoHealFixSuggestion[]
  fixedYaml: string | null
  fixedSpec: WorkflowSpec | null
  diff: AutoHealDiffItem[]
  rawResponse: string
}

export interface AutoHealApplyResult {
  ok: boolean
  workflowId: string
  updatedSpec: WorkflowSpec | null
  flowId?: string | null
}

export interface AutoHealRunResult {
  ok: boolean
  workflowId: string
  flowId?: string | null
  message?: string | null
}

// ── Rerun ──────────────────────────────────────────────────

export interface RerunResult {
  ok: boolean
  flowId: string
  newFlowId: string | null
  sessionId: string | null
}

// ── Smart Onboarding ──────────────────────────────────────

export type SmartOnboardingPhase =
  | 'idle'
  | 'generating'
  | 'generated'
  | 'saving'
  | 'saved'
  | 'testing'
  | 'test_succeeded'
  | 'test_failed'
  | 'healing'
  | 'healed'

export interface SmartOnboardingMessage {
  id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  timestamp: number
  yamlBlock?: string
}

export interface SmartOnboardingGenerateRequest {
  prompt: string
  context?: {
    packId?: string
    existingYaml?: string
  }
  /** BaaS session ID for multi-turn conversation — passed from previous generate response */
  baasSessionId?: string
}

export interface SmartOnboardingGenerateResult {
  ok: boolean
  generationId: string
  messageId: string
  sessionId: string
  status: 'pending'
}

export interface SmartOnboardingGenerationStatus {
  status: 'pending' | 'running' | 'completed' | 'failed'
  result?: {
    yaml: string
    summary: string
    rawResponse?: string
  }
  error?: string
}

export interface SmartOnboardingTestRunRequest {
  yaml: string
  workflowId: string
  botId: string
}

export interface SmartOnboardingTestRunResult {
  ok: boolean
  flowId: string
  sessionId: string
}

export interface SmartOnboardingValidateRequest {
  yaml: string
}

export interface SmartOnboardingValidateResult {
  valid: boolean
  errors?: Array<{ path: string; message: string }>
  warnings?: Array<{ path: string; message: string }>
}

// ── HTTP Callback Notification ──────────────────────────────────

export type NotifyEvent =
  | 'workflow_started'
  | 'node_started'
  | 'node_succeeded'
  | 'node_failed'
  | 'node_skipped'
  | 'workflow_succeeded'
  | 'workflow_failed'

export interface HttpCallbackConfig {
  id: number
  configId: string
  workflowId: string
  name: string
  url: string
  secret: string
  enabled: boolean
  notifyOn: NotifyEvent[]
  timeoutMs: number
  maxRetries: number
  retryDelayMs: number
  includeNodeOutput: boolean
  gmtCreate: number
  gmtModified: number
}

export interface HttpCallbackConfigCreateInput {
  configId: string
  workflowId: string
  name: string
  url: string
  secret?: string
  enabled?: boolean
  notifyOn: NotifyEvent[]
  timeoutMs?: number
  maxRetries?: number
  retryDelayMs?: number
  includeNodeOutput?: boolean
}

export type HttpCallbackConfigUpdateInput = Partial<Omit<HttpCallbackConfigCreateInput, 'configId' | 'workflowId'>>

// ── Run Archive ──

export type RunArchiveData = {
  archive: {
    flowId: string
    archiveId: string
    archiveVersion: string
    createdAt: string
    status: 'completed' | 'partial'
    errors: string[]
  }
  flowRun: Record<string, unknown> | null
  nodeExecutions: Record<string, unknown>[]
  flowEvents: Record<string, unknown>[]
  nodeStepTraces: Record<string, unknown>[]
  executionStepLogs: Record<string, unknown>[]
  runLogs: Array<{
    id: number
    flow_id: string
    node_id: string | null
    level: string
    source: string | null
    message: string
    timestamp: number
    seq: number
  }>
  langfuseTraces: Record<string, unknown>[]
  langfuseObservations: Record<string, unknown>[]
  failureSummary: {
    failedNodeCount: number
    failedNodes: Array<{
      nodeId: string
      nodeTitle: string | null
      executorType: string | null
      error: string | null
      attempt: number
      embeddedSessionKey: string | null
    }>
    rootCauseHints: string[]
    errorTimeline: Array<{
      timestamp: string
      event: string
      detail: string
    }>
  }
}


// ── Unified Run Timeline ──

export type TimelineEventSeverity = "info" | "warning" | "error" | "success"
export type TimelineEventSource = "flow_event" | "node_step_trace" | "execution_step_log" | "run_log" | "langfuse" | "synthetic"

export type TimelineEventType =
  | "WORKFLOW_START"
  | "WORKFLOW_FINISH"
  | "WORKFLOW_REOPENED"
  | "WORKFLOW_BLOCKED"
  | "WORKFLOW_REPAIRED"
  | "NODE_START"
  | "NODE_END"
  | "NODE_WAITING"
  | "NODE_SKIPPED"
  | "NODE_READY"
  | "LOOP_STARTED"
  | "LOOP_COMPLETED"
  | "LOOP_FAILED"
  | "LOOP_ITERATION_STARTED"
  | "LOOP_ITERATION_COMPLETED"
  | "BUDGET_EXHAUSTED"
  | "ACTION_STARTED"
  | "ACTION_FAILED"
  | "ACTION_SUCCEEDED"
  | "VALIDATION_FAILED"
  | "TOOL_CALL"
  | "TOOL_RESULT"
  | "ASSISTANT_TEXT"
  | "PROGRESS"
  | "ERROR"
  | "LOG"
  | "UNKNOWN"

export type TimelineEvent = {
  id: string
  eventType: TimelineEventType
  displayType: string
  timestamp: number | null
  relativeMs: number | null
  nodeId: string | null
  attempt: number | null
  title: string
  detail: string | null
  payload: Record<string, unknown> | null
  severity: TimelineEventSeverity
  source: TimelineEventSource
  traceId?: string | null
  observationId?: string | null
}

export type RunTimelineSummary = {
  total: number
  errors: number
  warnings: number
  toolCalls: number
  assistantTurns: number
  nodesStarted: number
  nodesFinished: number
  failedNodes: string[]
  skippedNodes: string[]
}

export type RunTimeline = {
  ok: boolean
  flowId: string
  startedAt: number | null
  finishedAt: number | null
  durationMs: number | null
  events: TimelineEvent[]
  summary: RunTimelineSummary
}

// ── Workflow Node Analytics ──

export type ErrorCategory = {
  category: string
  count: number
  sampleError: string | null
}

export type NodeStat = {
  nodeId: string
  nodeTitle: string | null
  executorType: string | null
  totalExecutions: number
  succeededCount: number
  failedCount: number
  failureRate: number
  avgRetryCount: number
  avgDurationMs: number
  p50DurationMs: number
  p95DurationMs: number
  maxDurationMs: number
  minDurationMs: number
  avgTokens: number | null
  totalTokens: number | null
  lastErrorText: string | null
  lastErrorAt: number | null
  errorCategories: ErrorCategory[]
}

export type WorkflowNodeStats = {
  workflowId: string
  totalRuns: number
  nodes: NodeStat[]
}

export type WorkflowHealth = {
  overallScore: number
  successRate: number
  nodeFailureRate: number
  p95DurationMs: number
  retryRate: number
  totalTokens: number | null
  bottleneckNode: string | null
  fragileNode: string | null
  recommendation: string
}
export type EvolveTaskType =
  | 'diagnose'
  | 'optimize'
  | 'apply'
  | 'full'
  | 'bench'
  | 'bench_optimize'
  | 'pack'
  | 'pack_restore'
  | 'runtime_cleanup'
  | 'repair'
