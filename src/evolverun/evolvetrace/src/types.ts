// 从 clawweb 迁移的核心类型，供 WorkflowWorkspace 模块使用

export interface ClientUser {
  userId: string
  nickName?: string
  userName?: string
  avatarUrl?: string
  displayName?: string
  isAdmin?: boolean
  isLogAdmin?: boolean
  isBenchAdmin?: boolean
  isClawEvolveAdmin?: boolean
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
}

export interface FlowRunsResponse {
  runs: FlowRun[]
  total: number
  statusCounts?: Record<string, number>
}

export interface DeployHistoryItem {
  deployNumber: number
  version: number
  tagName: string | null
  action: string
  fromDeployNumber: number | null
  note: string | null
  botId: string | null
  ownerId: string | null
  gmtCreate: number
}

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

export interface FacadeBinding {
  command: string
  workflowId: string
  packId: string | null
  remark: string | null
}

export interface NotificationConfig {
  workflowId: string
  robotCode: string
  appSecret: string
  onFailureUsers: { userId: string; name?: string }[]
  onFailureGroups: { openConversationId: string; name?: string }[]
  onFailureMessageTitle?: string | null
  onFailureMessageIncludeRunLink: boolean
}

export interface HttpCallbackConfig {
  id: number
  configId: string
  workflowId: string
  name: string
  url: string
  secret: string
  enabled: boolean
  notifyOn: string[]
  timeoutMs: number
  maxRetries: number
  retryDelayMs: number
  includeNodeOutput: boolean
  gmtCreate: number
  gmtModified: number
}

export interface WorkflowHealth {
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

export interface WorkflowNodeStats {
  workflowId: string
  totalRuns: number
  nodes: unknown[]
}


// ── Workflow Spec ──

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
  retry?: unknown
  outputContract?: unknown
  outputSchema?: unknown
  mock?: unknown
  knowledge?: unknown
  knowledgeBaseId?: string
  knowledgeQuery?: string
  validationTemplateId?: string
  validationMinScore?: number
  onSuccess?: unknown
  onFailure?: unknown
  onFeedback?: unknown
  onResult?: Array<{ value: string; target: string }>
  alerting?: unknown
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
  input?: unknown
  identity?: unknown
  outputs?: unknown
  debug?: unknown
  defaults?: unknown
  collaboration?: unknown
  workflow?: unknown
  messages?: unknown
  allowedBots?: string[]
  facade?: {
    command?: string
    remark?: string
  }
  [key: string]: unknown
}
// ── TCLog 迁移类型 ──

export type TraceDataSource = 'auto' | 'tc' | 'langfuse'

export interface TCLogBot {
  botId: string
  displayBotId: string
  ownerId: string
  status?: string
}

export interface TCLogBotsResponse {
  bots: TCLogBot[]
  total?: number
}

export interface TCLogObservation {
  observationId: string
  parentObservationId?: string | null
  type: string
  name?: string | null
  startTimeMs: number
  endTimeMs?: number | null
  latencyMs?: number | null
  status?: string | null
  model?: string | null
  input?: unknown
  output?: unknown
  metadata?: Record<string, unknown>
  promptTokens?: number
  completionTokens?: number
  totalTokens?: number
  inputTokens?: number
  outputTokens?: number
  cacheReadTokens?: number
  cacheWriteTokens?: number
}

export interface TCLogTrace {
  traceId: string
  ownerId?: string | null
  botId?: string | null
  sessionKey?: string | null
  sessionId?: string | null
  name?: string | null
  engine?: string | null
  status?: string | null
  startTimeMs: number
  endTimeMs?: number | null
  latencyMs?: number | null
  inputPreview?: string | null
  outputPreview?: string | null
  inputTokens?: number
  outputTokens?: number
  totalTokens?: number
  cacheReadTokens?: number
  cacheWriteTokens?: number
  totalCost?: number
  observations: TCLogObservation[]
}

/** /api/tclog/traces/:traceId 返回类型（含 dataSource / fallbackUsed） */
export interface TCLogTraceDetail {
  trace: TCLogTrace
  dataSource: string
  fallbackUsed: boolean
}

export interface TCLogSession {
  sessionKey?: string | null
  sessionId?: string | null
  ownerId?: string | null
  botId?: string | null
  engine?: string | null
  traceCount: number
  startTimeMs?: number | null
  endTimeMs?: number | null
  totalTokens?: number
  totalCost?: number
  latestStatus?: string | null
  traces: TCLogTrace[]
}

export interface TCLogWorkflowNode {
  id?: string | null
  nodeId: string
  nodeTitle?: string | null
  executorType?: string | null
  attempt: number
  status?: string | null
  startedAt?: number | null
  completedAt?: number | null
  durationMs?: number | null
  input?: unknown
  output?: unknown
  errorText?: string | null
  tokenUsage?: number | null
  systemContext?: unknown
  progressMessage?: string | null
  sessionKey?: string | null
  sessionId?: string | null
  embeddedSessionKey?: string | null
}

export interface TCLogWorkflowRun {
  flowId: string
  workflowId: string
  workflowTitle?: string | null
  ownerId?: string | null
  botId?: string | null
  status?: string | null
  originSessionKey?: string | null
  originSessionId?: string | null
  startedAt?: number | null
  completedAt?: number | null
  totalDurationMs?: number | null
  nodeCount?: number
  failedCount?: number
  totalTokenUsage?: number | null
  currentPhase?: string | null
  matchTypes: string[]
  params?: unknown
  input?: unknown
  output?: unknown
  nodes: TCLogWorkflowNode[]
}

export interface TCLogTaskSummary {
  bizScene: string
  taskId: string
  botId?: string | null
  ownerId?: string | null
  source?: string | null
  refCount?: number
  traceCount?: number
  workflowRunCount?: number
  lastEventTimeMs?: number | null
}

export interface TCLogTimelineItem {
  eventTimeMs: number
  type: string
  title: string
  detail?: string | null
}

export interface TCLogQueryParams {
  ownerId?: string
  embed?: boolean
  botId?: string
  traceId?: string
  sessionKey?: string
  sessionId?: string
  keyword?: string
  from: number
  to: number
  dataSource?: TraceDataSource
  limit?: number
  offset?: number
  groupBy?: 'trace' | 'session'
}

export interface TCLogTaskListParams {
  ownerId?: string
  botId?: string
  bizScene?: string
  taskId?: string
  from: number
  to: number
  limit?: number
}

export interface TCLogTaskSearchParams {
  ownerId?: string
  embed?: boolean
  botId?: string
  bizScene: string
  taskId: string
  dataSource?: TraceDataSource
}

export interface TCLogQueryResponse {
  dataSource: string
  fallbackUsed?: boolean
  summary: {
    sessionCount: number
    traceCount: number
    totalTokens: number
    totalCost: number
  }
  traces: TCLogTrace[]
  sessions: TCLogSession[]
}

export interface TCLogTaskListResponse {
  tasks: TCLogTaskSummary[]
}

export interface TCLogTaskSearchResponse {
  dataSource: string
  fallbackUsed?: boolean
  summary: {
    traceCount: number
    workflowRunCount: number
    totalCost: number
  }
  relations: TCLogTaskSummary[]
  traces: TCLogTrace[]
  sessions: TCLogSession[]
  workflowRuns: TCLogWorkflowRun[]
  timeline: TCLogTimelineItem[]
}
