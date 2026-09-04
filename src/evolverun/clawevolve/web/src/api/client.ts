import type {
  FlowRunsResponse,
  FlowRunDetail,
  NodeExecution,
  FlowEvent,
  WorkflowSpec,
  DryRunRequest,
  DryRunResult,
  KnowledgeBase,
  KnowledgeBaseCreateInput,
  KnowledgeBaseUpdateInput,
  AppConfigEntry,
  AppConfigCreateInput,
  AppConfigUpdateInput,
  KnowledgeBaseTestResult,
  YuQueBookInfo,
  ValidationTemplate,
  ValidationTemplateCreateInput,
  ValidationTemplateUpdateInput,
  ValidationResult,
  FacadeBinding,
  LangfuseTracesResponse,
  AnalysisResult,
  SessionDiagnosisResult,
  WorkflowTypesResponse,
  FlowControlSlot,
  FlowControlQueueItem,
  FlowControlListResponse,
  FlowControlDeleteResponse,
  FlowControlFlowDeleteResponse,
  FlowControlBatchDeleteResponse,
  FlowControlDeleteAllResponse,
  ThetaTraceLog,
  ThetaTraceQueryParams,
  ThetaApiKey,
  InterventionInfo,
  InterveneRequest,
  InterveneResult,
  SessionInfoUpdate,
  SessionInfoUpdateResult,
  ChatSendResult,
  ChatPollResult,
  BenchDomain,
  BenchDomainCreateInput,
  BenchDomainUpdateInput,
  BenchDomainSummary,
  BenchTemplate,
  BenchTemplateCreateInput,
  BenchTemplateUpdateInput,
  BenchUploadScanResult,
  BenchRun,
  BenchRunCreateInput,
  BenchRunUpdateInput,
  BenchRunCreateResponse,
  BenchRunListResponse,
  BenchTaskResult,
  BenchTaskResultBatchInput,
  BenchTaskResultBatchResponse,
  BenchArtifact,
  BenchArtifactCreateInput,
  BenchSessionsResponse,
  BenchSessionDetail,
  BenchBatchPublishInput,
  BenchBatchPublishResponse,
  BenchAdminDailyResponse,
  BenchAdminDomainsResponse,
  BenchAdminFilters,
  BenchAdminSamplesResponse,
  BenchAdminSummary,
  BenchTag,
  NodeStepTraceData,
  HallucinationCheckData,
  AutoHealDiagnosisRequest,
  AutoHealDiagnosisSubmitResult,
  AutoHealDiagnosisPollResult,
  AutoHealApplyRequest,
  AutoHealApplyResult,
  AutoHealRunRequest,
  AutoHealRunResult,
  BotPermission,
  BotPermissionUpsert,
  NotificationConfig,
  HttpCallbackConfig,
  HttpCallbackConfigCreateInput,
  HttpCallbackConfigUpdateInput,
  RerunResult,
  SmartOnboardingGenerateRequest,
  SmartOnboardingGenerateResult,
  SmartOnboardingGenerationStatus,
  SmartOnboardingTestRunRequest,
  SmartOnboardingTestRunResult,
  SmartOnboardingValidateRequest,
  SmartOnboardingValidateResult,
  TCLogBot,
  TCLogQueryParams,
  TCLogQueryResponse,
  TCLogTaskListParams,
  TCLogTaskListResponse,
  TCLogTaskSearchParams,
  TCLogTaskSearchResponse,
  TCLogTrace,
  TCLogWorkflowRun,
  WorkflowValidationResult,
  DeployHistoryItem,
  SystemLogSearchResult,
  VersionSnapshot,
  VersionDiffResult,
  VersionListResponse,
  VersionActivateResponse,
  RunArchiveData,
  RunTimeline,
  WorkflowNodeStats,
  WorkflowHealth,
  AdminUsersListResponse,
  AdminUserEntry,
  AdminUserCreateInput,
} from '../types'
import { getClientUser } from '../hooks/useClientUser'
import type { EvolveTaskType } from '../features/evolve/task-registry'

const BASE = '/api'

export type EvolveStep = {
  stepId: string
  taskId: string
  stepType: string
  stepNo: number
  roundNo: number | null
  command: string
  status: string
  botRunId: string | null
  botSessionId: string | null
  botResponse: Record<string, unknown> | null
  startedAt: number | null
  completedAt: number | null
  gmtCreate: number | string
  gmtModified: number | string
  summary: string | null
  output?: Record<string, unknown> | null
  error: { code: string | null; message: string | null; retryable: boolean | null } | null
}

export type EvolveLesson = {
  id: number
  lesson_id: string
  workflow_id: string | null
  node_id: string | null
  failure_signature: string
  failure_mode: string
  executor_type: string | null
  fix_kind: 'kb_hint' | 'prompt_patch' | 'arg_template_fix' | 'node_patch' | 'alert' | string
  fix_spec: string
  status: 'draft' | 'verified' | 'published' | 'retired' | string
  confidence: number
  hit_count: number
  rescued_count: number
  successRate: number
  note: string | null
  created_by: string | null
  updated_by: string | null
  gmt_create: number | string
  gmt_modified: number | string
  source?: string | null
}

export type EvolveLessonInput = {
  workflowId?: string
  nodeId?: string
  failureSignature: string
  failureMode: string
  executorType?: string
  fixKind: string
  fixSpec: string
  status?: string
  confidence?: number
  source?: string
  note?: string
}

export type EvolveRunDiagnosis = {
  id: number | string
  diagnosis_id: string
  flow_id: string
  workflow_id: string
  run_id: string | null
  node_id: string | null
  failure_signature: string
  failure_mode: string
  executor_type: string | null
  weak_node_id: string | null
  suggested_fix_kind: string | null
  lesson_id_hit: string | null
  error_text: string | null
  reasoning?: string | null
  analysis_id?: string
  flow_ids?: string[]
  evidence_event_ids?: string[]
  created_by: string | null
  gmt_create: number | string
  gmt_modified: number | string
}

export type EvolutionEvidenceSummary = {
  eventId: string
  flowId: string
  nodeId: string | null
  eventType: string
  producer: string
  occurredAtMs: number
  summary: string
  missing?: boolean
}

export type RunEvolutionDiagnosis = {
  diagnosisId: string
  flowIds: string[]
  nodeId: string | null
  failureSignature: string
  failureMode: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  reasoning: string
  evidenceEventIds: string[]
  sourceEvidence: EvolutionEvidenceSummary[]
  proposal?: Record<string, unknown>
}

export type RunEvolutionAnalysisResponse = {
  analysisId: string
  flowId: string
  workflowId: string
  status: string
  evidenceStatus: 'complete' | 'partial' | 'missing' | null
  requestedAtMs: number
  completedAtMs: number | null
  errorCode: string | null
  facts: string[]
  inferences: string[]
  unknowns: string[]
  diagnoses: RunEvolutionDiagnosis[]
}

export type EvolveLessonOutcome = {
  outcome_id: string
  lesson_id: string
  workflow_id: string | null
  node_id: string | null
  action: string
  applied: number
  succeeded: number
  verdict: string
  note: string | null
  created_by: string | null
  gmt_create: number | string
  gmt_modified: number | string
}
export type SuggestionApplyTask = {
  taskId: string
  stepId: string
  suggestionId: string
  status: 'created' | 'pending' | 'dispatching' | 'dispatched' | 'running' | 'succeeded' | 'completed' | 'canceled' | 'adopted' | 'applying' | 'applied_unverified' | 'failed' | 'verified' | 'ineffective' | 'rejected' | 'benched'
  summary: string | null
  botId: string | null
  botName: string | null
  botEnv: string | null
  errorMessage: string | null
  retryable: boolean
  proposalDigest: string | null
  proposal: Record<string, unknown> | null
  applicationSpec: string | null
  progress?: {
    phase: 'task_received' | 'reading_workflow' | 'planning_change' | 'editing_workflow' | 'deploying'
    message: string
    elapsedMs: number
    updatedAtMs: number
    stalled: boolean
    history?: Array<{
      phase: 'task_received' | 'reading_workflow' | 'planning_change' | 'editing_workflow' | 'deploying'
      message: string
      updatedAtMs: number
    }>
  } | null
  appliedAt: number | string | null
  createdAt: number | string
  updatedAt: number | string
}

export type WorkflowAnalysisInputSummary = {
  evidenceStatus: 'complete' | 'partial' | 'missing'
  evidenceTotal: number
  evidenceIncluded: number
  nodeCount: number
  failedNodeCount: number
  traceCount: number
  flowEventCount?: number
  warnErrorLogCount: number
  truncated: boolean
}

export type WorkflowAnalysisProgressResponse = {
  analysisId: string | null
  status: 'queued' | 'collecting' | 'analyzing' | 'completed' | 'insufficient_evidence' | 'failed' | null
  taskId?: string
  stepId?: string
  progress: {
    phase: 'loading_input' | 'input_ready' | 'agent_analyzing' | 'validating' | 'persisting' | 'completed' | 'failed'
    message: string
    elapsedMs: number
    updatedAtMs: number
    inputSummary?: WorkflowAnalysisInputSummary
  } | null
}

export type SuggestionStatus = 'pending' | 'adopted' | 'applying' | 'applied_unverified' | 'verified' | 'ineffective' | 'failed' | 'benched' | 'rejected'

export type EvolveSuggestion = {
  id: string
  diagnosisId: string
  weakNode: string
  signature: string
  failureMode: string
  kind: string
  impactRuns: number
  evidenceRuns: string[]
  description: string
  status: SuggestionStatus
  proposalDigest?: string | null
  proposal?: Record<string, unknown> | null
  applyTaskId?: string | null
  appliedAt?: number | null
  verificationStatus?: 'not_started' | 'observing' | 'recurrence_detected' | 'verified' | 'ineffective'
  verificationCheckedAt?: number | null
  recurrenceCount?: number
  lastRecurrenceAt?: number | null
}

export type EvolveSuggestionAction = {
  id: number
  signature: string
  workflow_id: string
  node_id: string | null
  action: string
  fix_kind: string | null
  note: string | null
  created_by: string | null
  gmt_create: number | string
  gmt_modified: number | string
}

export type WorkflowAutoAnalysisSetting = {
  workflowId: string
  enabled: boolean
  source: 'database' | 'environment' | 'default'
}


export type EvolveTask = {
  task_id: string
  task_type: EvolveTaskType | 'session_analysis' | 'session_export'
  task_name: string | null
  remark: string | null
  user_id: string
  bot_id: string
  bot_name?: string | null
  status: string
  config: Record<string, unknown>
  error_message: string | null
  created_by: string
  gmt_create: number | string
  gmt_modified: number | string
  steps?: EvolveStep[]
  source?: EvolveTaskSource | null
  initialPack?: {
    packId: string
    taskId: string
    stepId: string
    sourceKind: 'baseline'
    status: string
    artifact: { ref: string; size: number; sha256: string; contentType: string }
  } | null
}

export type EvolveTaskLogArchive = {
  archiveId: string
  taskId: string
  status: 'dispatching' | 'running' | 'succeeded' | 'failed'
  requestedBy: string
  transport: string | null
  artifact: { ref: string; size: number; sha256: string | null; contentType: string | null } | null
  metadata: Record<string, unknown> | null
  error: { code: string | null; message: string | null } | null
  startedAt: number | string | null
  completedAt: number | string | null
  gmtCreate: number | string
  gmtModified: number | string
}

export type EvolveTaskSource = {
  sourceType: string
  sourceId: string
  schemaVersion: string
  adapterVersion: string | null
  status: string
  digest: string | null
  evidenceCount: number | null
  error: { code: string | null; message: string | null; stage: string | null } | null
  resolvedAt: number | string | null
}

export type EvolveVersion = {
  versionId: string
  kind: 'initial' | 'round' | 'snapshot'
  acceptanceStatus: 'accepted' | 'accepted_unregistered' | 'promotion_failed' | 'passed_not_promoted' | 'rejected' | 'unregistered' | 'unassessed' | 'unknown'
  userId: string
  botId: string
  taskId: string
  taskName: string | null
  taskType: string | null
  stepId: string
  round: number | null
  createdAt: number | string | null
  benchDecision: string | null
  accepted: boolean | null
  promotionStatus: string | null
  stateSource: 'skill_output' | 'legacy_inferred' | null
  reviewStatus: string | null
  scoreComparison: { name: string | null; baseline: number | null; candidate: number | null; delta: number | null } | null
  specVersion: string | null
  diff: { summary: string | null; files: Array<Record<string, unknown>>; available: boolean; artifactAvailable: boolean } | null
  reportedPack: {
    status: string | null
    artifact: { ref?: string; size?: number; sha256?: string } | null
  } | null
  pack: {
    packId: string
    status: string
    artifact: { ref?: string; size?: number; sha256?: string }
  } | null
}
export type SessionAnalysisTask = {
  analysisId: string; taskType: string; taskName: string | null; botId: string;
  botName?: string | null; userId: string; createdBy: string; remark: string | null;
  mode: 'ANALYZE_SINGLE' | 'EXPORT_ALL'; stage: 'all' | 'draft' | 'service'; engineType: string;
  shared: boolean;
  sessionIdentifier: string | null; sessionId: string | null; sessionKey: string | null; question: string | null;
  sessionLookbackDays: number | null;
  llmAnalysis: boolean; llmUseDefault: boolean; llmModel: string | null;
  status: string; phase: string; aisJobId: string | null; aisJobUrl?: string | null;
  stepId?: string | null; stepCommand?: string | null; errorCode?: string | null; summary: string | null;
  stepResponse?: Record<string, unknown> | null; stepOutput?: Record<string, unknown> | null;
  stepCreatedAt?: number | string | null; stepStartedAt?: number | string | null;
  stepCompletedAt?: number | string | null;
  result: Record<string, unknown> | null; reportMarkdown?: string | null;
  sessionPreview?: { events: Record<string, unknown>[]; eventCount: number; parseErrorCount: number; truncated: boolean } | null;
  artifacts?: string[]; error: string | null; gmtCreate: number | string; gmtModified: number | string;
}

export type RepairTargetEnvironment = 'pre' | 'prod'
export type RepairAgentMode = 'openclaw' | 'cfuse'
export type RepairCfuseEngine = 'cfuse' | 'claude-code'
export type RepairPersistedCfuseEngine = RepairCfuseEngine | 'codex'
export type RepairDiagnosticMode = 'observe' | 'deep'

export type RepairBot = {
  botId: string
  botName: string | null
  env: string | null
  activeEngine: string | null
  botType: string | null
  ownerId?: string | null
  accessType?: 'owner' | 'collaborator'
}

export type RepairIssue = {
  symptom: string
  traceId: string | null
  relatedTaskId: string | null
  errorText: string | null
  timeRange: { from: number; to: number }
}

export type RepairPlanAction = {
  actionId: string
  type: 'container_command' | 'ocb_operation'
  summary: string
  risk: string
  verification: string
  rollback: string | null
  dependsOn?: string[]
  rollbackActionId?: string | null
  command?: string
  operation?: { type: string; params?: Record<string, unknown> }
}

export type RepairPlan = {
  schemaVersion?: string
  taskId?: string
  stepId?: string
  attempt?: number
  legacySemantics?: boolean
  quality?: 'verified' | 'partially_verified' | 'blocked' | 'unknown'
  recommendation?: {
    disposition: 'execute_actions' | 'no_change' | 'insufficient_evidence'
    summary: string
    reason: string
    nextSteps?: string[]
  }
  diagnosis: { facts: string[]; inferences: string[]; unknowns: string[] }
  actions: RepairPlanAction[]
}

export type RepairHistoricalPlan = {
  taskId: string
  step: {
    stepId: string
    stepNo: number
    attempt: number
    status: 'succeeded'
    artifactDigest: string
  }
  source: 'history'
  readOnly: true
  approvable: false
  plan: RepairPlan
}

export type RepairToolCallStatus = 'pending' | 'executing' | 'succeeded' | 'failed' | 'unknown' | 'canceled'

export type RepairToolCall = {
  toolCallId: string
  toolName: string
  operation: string
  purpose?: string
  executionTarget?: string
  safeInvocation?:
    | { kind: 'readonly_command'; command: string }
    | { kind: 'diagnostic_command'; command: string }
    | { kind: 'typed_operation'; operation: string; params: Record<string, unknown> }
  resultSummary?: string
  conclusion?: {
    text: string
    nextAction: string | null
    evidenceToolCallIds: string[]
  }
  status: RepairToolCallStatus
  requiresBrowserRelay?: boolean
  cfuseLoginUrl?: string | null
  error: { code?: string | null; message?: string | null } | string | null
  actionId?: string | null
  stepId?: string
  phase?: 'repair_plan' | 'repair_apply'
  attempt?: number
  createdAt: number | string
  updatedAt: number | string
  startedAt?: number | string | null
  completedAt?: number | string | null
}

export type RepairExecution = {
  state: string
  phase: 'repair_plan' | 'repair_apply'
  leaseExpiresAt: number | string | null
  decisionDeadlineAt: number | string | null
  decisionWindowExpired?: boolean
}

export type RepairStepFailure = {
  code?: string
  stage?: string
  reason?: string
  artifactName?: string
  exitCode?: number
  httpStatus?: number
  providerCode?: string
  providerRequestId?: string
  retryCount?: number
  field?: string
  rule?: 'han_required' | 'chinese_dominance'
  retryBranch?: 'not_allowed' | 'already_consumed' | 'session_missing' | 'session_mismatch'
    | 'output_invalid' | 'semantic_mismatch' | 'contract_invalid' | 'still_non_chinese'
  retryable: boolean | null
}

export type RepairTaskStep = {
  stepId: string
  stepNo: number
  attempt?: number
  phase: 'repair_plan' | 'repair_apply'
  status: string
  aisJobId: string | null
  summary?: string | null
  output?: Record<string, unknown>
  error?: string | null
  failure?: RepairStepFailure | null
}

export type RepairTask = {
  taskId: string
  taskType: 'repair'
  taskName: string | null
  status: string
  shared: boolean
  canOperate: boolean
  /** Admin may progress an owner's historical Repair, but does not receive owner browser credentials. */
  canAdminOperate?: boolean
  canManageShare: boolean
  canTerminate: boolean
  executionSupported: boolean
  executionBlock: { code: string; message: string } | null
  botId: string
  targetEnvironment: RepairTargetEnvironment
  diagnosticMode: RepairDiagnosticMode
  agentMode: RepairAgentMode
  llmUseDefault: boolean
  llmModel: string | null
  openclawUsesCustomApiKey: boolean
  cfuseEngine: RepairPersistedCfuseEngine | null
  cfuseModel: string | null
  issue?: RepairIssue
  insightSource?: {
    sourceType: 'insight_improvement'
    improvementId: number
    requestId: string
    version: number
    title: string
    sourceBatchId: string
    evidenceCount: number
    sessionIds: string[]
    evidenceTaskRefs: Array<{ sessionId: string; taskIndex: number; ordinal: number }>
    repairDirection: string | null
    authorizationMode: 'ONCE' | 'PERSISTENT'
    authorizationGrantId?: number
    adminOverride?: { operatorUserId: string; reason: string }
  }
  target: Record<string, unknown>
  currentStep: (RepairTaskStep & { attempt: number }) | null
  steps?: RepairTaskStep[]
  history: Array<{
    stepId: string
    stepNo: number
    attempt: number
    phase: 'repair_plan' | 'repair_apply'
    status: string
    artifactDigest: string | null
    feedback?: string | null
  }>
  approvedPlan: { stepId: string; artifactDigest: string; approvedAt: string } | null
  execution?: RepairExecution | null
  toolCalls?: RepairToolCall[]
  toolCallAuditTruncated?: boolean
  resumeAvailable?: boolean
  canResume?: boolean
  plan?: RepairPlan | null
  applyResult?: Record<string, unknown> | null
  error?: string | null
  termination?: {
    status: 'remote_stopped' | 'remote_stop_failed' | 'stop_pending'
    aisJobId: string | null
  }
  createdAt: number | string
  updatedAt: number | string
}

export type RepairAgentInput =
  | {
      agentMode: 'openclaw'
      llmUseDefault: boolean
      llmModel?: string
      llmApiKey?: string
      cfuseEngine?: never
      cfuseModel?: never
    }
  | {
      agentMode: 'cfuse'
      cfuseEngine: RepairCfuseEngine
      cfuseModel: string
      llmUseDefault?: never
      llmModel?: never
      llmApiKey?: never
    }

export type RepairCreateTaskInput = RepairAgentInput & {
  diagnosticMode?: RepairDiagnosticMode
  /** Compatibility echo only; the server derives the authoritative environment from the selected Bot runtime. */
  targetEnvironment?: RepairTargetEnvironment
  taskName?: string
  targetUserId?: string
  adminOverrideReason?: string
  crossBotConfirmed?: boolean
  persistAutoRepairGrant?: boolean
  authorizationGrantId?: number
  adminConsentToken?: string
  botId: string
  symptom: string
  repairDirection?: string
  insightImprovementId?: number
  insightRequestId?: string
  traceId?: string
  relatedTaskId?: string
  errorText?: string
  timeRange?: { from: number; to: number }
}

export type RepairPlanDecisionInput =
  | ({ decision: 'approve'; artifactDigest: string } & Partial<RepairAgentInput>)
  | ({ decision: 'reject'; reason: string } & Partial<RepairAgentInput>)

export type RepairResultDecisionInput =
  | { decision: 'accept' }
  | ({ decision: 'retry'; reason: string } & Partial<RepairAgentInput>)

export type RepairResumeInput = Partial<RepairAgentInput>

/** Gzip threshold: ACE/Tengine gateway rejects POST bodies > ~16KB */
const GZIP_THRESHOLD = 14 * 1024 // 14KB — conservative limit below the 16KB ACE cutoff

class ApiError extends Error {
  public readonly status: number
  public readonly body: string

  constructor(status: number, body: string) {
    super(`API ${status}: ${body}`)
    this.status = status
    this.body = body
  }
}

async function gzipCompress(data: string): Promise<Uint8Array> {
  const encoder = new TextEncoder()
  const bytes = encoder.encode(data)
  const cs = new CompressionStream('gzip')
  const writer = cs.writable.getWriter()
  const reader = cs.readable.getReader()
  writer.write(bytes)
  writer.close()
  const chunks: Uint8Array[] = []
  while (true) {
    const { done, value } = await reader.read()
    if (done) break
    chunks.push(value)
  }
  const totalLen = chunks.reduce((s, c) => s + c.length, 0)
  const compressed = new Uint8Array(totalLen)
  let offset = 0
  for (const chunk of chunks) {
    compressed.set(chunk, offset)
    offset += chunk.length
  }
  return compressed
}

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const user = getClientUser()
  const isFormData = init?.body instanceof FormData
  const headers: Record<string, string> = {
    ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
    ...((init?.headers as Record<string, string>) ?? {}),
  }
  if (user) {
    headers['X-User-Id'] = user.userId
    headers['X-User-Name'] = encodeURIComponent(user.nickName)
  }

  // Auto-gzip large JSON bodies to bypass ACE gateway POST size limit (~16KB)
  let body = init?.body
  if (typeof body === 'string' && body.length > GZIP_THRESHOLD) {
    const compressed = await gzipCompress(body)
    body = compressed as BodyInit
    headers['Content-Encoding'] = 'gzip'
  }

  const res = await fetch(url, {
    cache: 'no-store',
    ...init,
    body,
    headers,
  })
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText)
    throw new ApiError(res.status, body)
  }
  return res.json() as Promise<T>
}

async function fetchText(url: string, init?: RequestInit): Promise<string> {
  const user = getClientUser()
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string>) ?? {}),
  }
  if (user) {
    headers['X-User-Id'] = user.userId
    headers['X-User-Name'] = encodeURIComponent(user.nickName)
  }
  const res = await fetch(url, { ...init, headers })
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${body}`)
  }
  return res.text()
}

async function fetchBlob(url: string, init?: RequestInit): Promise<Blob> {
  const user = getClientUser()
  const headers: Record<string, string> = {
    ...((init?.headers as Record<string, string>) ?? {}),
  }
  if (user) {
    headers['X-User-Id'] = user.userId
    headers['X-User-Name'] = encodeURIComponent(user.nickName)
  }
  const res = await fetch(url, { cache: 'no-store', ...init, headers })
  if (!res.ok) {
    const body = await res.text().catch(() => res.statusText)
    throw new Error(`API ${res.status}: ${body}`)
  }
  return res.blob()
}

function appendBenchAdminFilters(sp: URLSearchParams, params?: BenchAdminFilters) {
  if (!params) return
  if (params.ownerUserId) sp.set('ownerUserId', params.ownerUserId)
  if (params.domainId) sp.set('domainId', params.domainId)
  if (params.templateName) sp.set('templateName', params.templateName)
  if (params.status) sp.set('status', params.status)
  if (params.tagId) sp.set('tagId', params.tagId)
  if (params.from !== undefined) sp.set('from', String(params.from))
  if (params.to !== undefined) sp.set('to', String(params.to))
  if (params.limit !== undefined) sp.set('limit', String(params.limit))
  if (params.offset !== undefined) sp.set('offset', String(params.offset))
}

export const api = {
  repair: {
    bots(ownerId?: string): Promise<{ userId: string; bots: RepairBot[] }> {
      const query = ownerId?.trim()
        ? `?ownerId=${encodeURIComponent(ownerId.trim())}`
        : ''
      return fetchJson(`${BASE}/repair/v1/bots${query}`)
    },
    create(input: RepairCreateTaskInput): Promise<RepairTask> {
      return fetchJson(`${BASE}/repair/v1/tasks`, { method: 'POST', body: JSON.stringify(input) })
    },
    get(taskId: string): Promise<RepairTask> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}`)
    },
    getStepPlan(taskId: string, stepId: string): Promise<RepairHistoricalPlan> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/plan`)
    },
    setTaskShared(taskId: string, shared: boolean): Promise<RepairTask> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}/share`, {
        method: 'PATCH', body: JSON.stringify({ shared }),
      })
    },
    terminate(taskId: string, reason = '用户终止本次 Repair 实验'): Promise<RepairTask> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}/terminate`, {
        method: 'POST', body: JSON.stringify({ reason }),
      })
    },
    decidePlan(taskId: string, input: RepairPlanDecisionInput): Promise<RepairTask> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}/plan-decision`, {
        method: 'POST', body: JSON.stringify(input),
      })
    },
    decideResult(taskId: string, input: RepairResultDecisionInput): Promise<RepairTask> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}/result-decision`, {
        method: 'POST', body: JSON.stringify(input),
      })
    },
    resume(taskId: string, input: RepairResumeInput = {}): Promise<RepairTask> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}/resume`, {
        method: 'POST', body: JSON.stringify(input),
      })
    },
    fulfillToolCall(taskId: string, toolCallId: string): Promise<unknown> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}/tool-calls/${encodeURIComponent(toolCallId)}/fulfill`, {
        method: 'POST',
        credentials: 'same-origin',
      })
    },
    submitCfuseAuthCode(taskId: string, toolCallId: string, authCode: string): Promise<RepairToolCall> {
      return fetchJson(`${BASE}/repair/v1/tasks/${encodeURIComponent(taskId)}/tool-calls/${encodeURIComponent(toolCallId)}/cfuse-auth-code`, {
        method: 'POST',
        credentials: 'same-origin',
        body: JSON.stringify({ authCode }),
      })
    },
  },
  sessionAnalysis: {
    bots(userId?: string): Promise<{ userId: string; bots: Array<{ botId: string; botName: string | null; env: string | null; activeEngine: string | null; botType: string | null; ownerId?: string | null; accessType?: 'owner' | 'collaborator' }> }> {
      const suffix = userId ? `?userId=${encodeURIComponent(userId)}` : ''
      return fetchJson(`${BASE}/session-analyses/bots${suffix}`)
    },
    create(input: { taskName: string; remark?: string; mode: 'ANALYZE_SINGLE' | 'EXPORT_ALL'; botId: string; botEnv?: string; targetUserId?: string; stage: 'all' | 'draft' | 'service'; sessionIdentifier?: string; sessionId?: string; sessionKey?: string; question?: string; sessionLookbackDays?: number | null; llmAnalysis?: boolean; llmUseDefault?: boolean; llmModel?: string; llmApiKey?: string }): Promise<{ analysisId: string; status: string; aisJobId: string }> {
      return fetchJson(`${BASE}/session-analyses`, { method: 'POST', body: JSON.stringify(input) })
    },
    list(): Promise<{ items: SessionAnalysisTask[] }> { return fetchJson(`${BASE}/session-analyses`) },
    get(id: string): Promise<SessionAnalysisTask> { return fetchJson(`${BASE}/session-analyses/${encodeURIComponent(id)}`) },
    retry(id: string): Promise<{ analysisId: string; attempt: number; aisJobId: string }> { return fetchJson(`${BASE}/session-analyses/${encodeURIComponent(id)}/retry`, { method: 'POST', body: '{}' }) },
    downloadUrl(id: string, name: string): Promise<{ url: string; filename: string; expiresInSeconds: number }> {
      return fetchJson(`${BASE}/session-analyses/${encodeURIComponent(id)}/artifacts/${encodeURIComponent(name)}/download-url`)
    },
  },
  evolve: {
    taskDefinitions(): Promise<{
      tasks: Array<{ type: string; label: string; nodes: Array<{ key: string; label: string; defaultCommand: string }> }>;
      variants: { insight_improvement: Array<{ key: string; label: string; defaultCommand: string }> };
    }> {
      return fetchJson(`${BASE}/evolve/task-definitions`)
    },
    listTasks(params: { page?: number; pageSize?: number; scope?: 'mine' | 'all'; ownerUserId?: string; category?: string; status?: string; query?: string } = {}): Promise<{ tasks: EvolveTask[]; page: number; pageSize: number; total: number; totalPages: number; scope: 'mine' | 'all'; canViewAll: boolean }> {
      const sp = new URLSearchParams()
      if (params.page) sp.set('page', String(params.page))
      if (params.pageSize) sp.set('pageSize', String(params.pageSize))
      if (params.scope) sp.set('scope', params.scope)
      if (params.ownerUserId?.trim()) sp.set('ownerUserId', params.ownerUserId.trim())
      if (params.category && params.category !== 'all') sp.set('category', params.category)
      if (params.status && params.status !== 'all') sp.set('status', params.status)
      if (params.query?.trim()) sp.set('query', params.query.trim())
      const suffix = sp.size ? `?${sp.toString()}` : ''
      return fetchJson(`${BASE}/evolve/tasks${suffix}`)
    },
    adminOwners(): Promise<{ ownerUserIds: string[] }> {
      return fetchJson(`${BASE}/evolve/admin/owners`)
    },
    getTask(taskId: string): Promise<EvolveTask> {
      return fetchJson(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}`)
    },
    createTaskLogArchive(taskId: string): Promise<{ archive: EvolveTaskLogArchive; reused: boolean }> {
      return fetchJson(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}/log-archives`, {
        method: 'POST', body: '{}',
      })
    },
    listTaskLogArchives(taskId: string): Promise<{ items: EvolveTaskLogArchive[] }> {
      return fetchJson(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}/log-archives`)
    },
    getTaskLogArchiveDownloadUrl(taskId: string, archiveId: string): Promise<{ url: string; filename: string }> {
      return fetchJson(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}/log-archives/${encodeURIComponent(archiveId)}/download-url`)
    },
    setTaskShared(taskId: string, shared: boolean): Promise<{ taskId: string; shared: boolean }> {
      return fetchJson(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}/share`, {
        method: 'PATCH', body: JSON.stringify({ shared }),
      })
    },
    getStepDiff(taskId: string, stepId: string): Promise<string> {
      return fetch(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/diff`)
        .then(async (response) => {
          const text = await response.text()
          if (!response.ok) throw new Error(`API ${response.status}: ${text}`)
          return text
        })
    },
    getPackDownloadUrl(taskId: string, stepId: string, sourceKind: 'baseline' | 'snapshot' | 'round'): Promise<{ url: string; filename: string }> {
      return fetchJson(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/pack-download-url?sourceKind=${sourceKind}`)
    },
    retryStep(taskId: string, stepId: string, apiKey?: string): Promise<{ step: EvolveStep }> {
      return fetchJson(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/retry`, {
        method: 'POST', body: JSON.stringify(apiKey ? { apiKey } : {}),
      })
    },
    cancelStep(taskId: string, stepId: string, reason?: string): Promise<{ step: EvolveStep }> {
      return fetchJson(`${BASE}/evolve/tasks/${encodeURIComponent(taskId)}/steps/${encodeURIComponent(stepId)}/cancel`, {
        method: 'POST', body: JSON.stringify(reason ? { reason } : {}),
      })
    },
    createDiagnosis(input: {
      taskName: string; remark?: string;
      userId: string; botId: string; botEnv?: string; apiKey?: string; judgeBackend: 'subagent' | 'api'; model: string;
      diagnoseIntent: string; maxSessions: number; sessionSource?: 'local' | 'service_export';
      startDate?: string; endDate?: string; nodeCommandYamls?: Record<string, string>;
      forceMessage?: boolean;
      runtimeMaintenance?: boolean;
      openclawExecutionMode?: 'local' | 'gateway';
      improvementId?: number; improvementRequestId?: string;
    }): Promise<{ task_id: string; status: string }> {
      return fetchJson(`${BASE}/evolve/diagnoses`, { method: 'POST', body: JSON.stringify(input) })
    },
    createTask(input: ({
      taskType: 'full';
      taskName: string; remark?: string;
      userId: string; botId: string; botEnv?: string; apiKey?: string; judgeBackend: 'subagent' | 'api'; model: string;
      diagnoseIntent: string; maxSessions: number; maxRounds: number; sessionSource?: 'local' | 'service_export';
      inputMode?: 'diagnose_goal';
      goal?: string;
      startDate: string; endDate: string; nodeCommandYamls?: Record<string, string>;
      forceMessage?: boolean;
      runtimeMaintenance?: boolean;
      openclawExecutionMode?: 'local' | 'gateway';
    } | {
      taskType: 'full';
      inputMode: 'direct_goal';
      taskName: string; remark?: string;
      userId: string; botId: string; botEnv?: string; maxRounds: number;
      goal: string; nodeCommandYamls?: Record<string, string>;
      forceMessage?: boolean;
      runtimeMaintenance?: boolean;
      openclawExecutionMode?: 'local' | 'gateway';
    } | {
      taskType: 'full';
      taskName: string; remark?: string;
      userId: string; botId: string; botEnv?: string; maxRounds: number; nodeCommandYamls?: Record<string, string>;
      forceMessage?: boolean;
      runtimeMaintenance?: boolean;
      openclawExecutionMode?: 'local' | 'gateway';
      input: {
        type: 'insight_improvement';
        improvementId: number;
        crossBotConfirmed: boolean;
        persistAutoRepairGrant?: boolean;
        adminAutoExecute?: boolean;
        adminConsentToken?: string;
      };
    }), idempotencyKey?: string): Promise<{ task_id: string; status: string }> {
      return fetchJson(`${BASE}/evolve/tasks`, {
        method: 'POST',
        ...(idempotencyKey ? { headers: { 'Idempotency-Key': idempotencyKey } } : {}),
        body: JSON.stringify(input),
      })
    },
    createOptimization(input: {
      taskName: string; remark?: string;
      userId: string; botId: string; botEnv?: string; sourceDiagnosisTaskIds: string[];
      maxRounds: number; nodeCommandYamls?: Record<string, string>;
      forceMessage?: boolean;
      runtimeMaintenance?: boolean;
      openclawExecutionMode?: 'local' | 'gateway';
    }): Promise<{ task_id: string; status: string }> {
      return fetchJson(`${BASE}/evolve/optimizations`, { method: 'POST', body: JSON.stringify(input) })
    },
    createPack(input: { taskName: string; remark?: string; userId: string; botId: string; botEnv?: string; forceMessage?: boolean; runtimeMaintenance?: boolean }): Promise<{ task_id: string; status: string }> {
      return fetchJson(`${BASE}/evolve/packs`, { method: 'POST', body: JSON.stringify(input) })
    },
    restorePack(input: {
      taskName: string; remark?: string; userId: string; botId: string; botEnv?: string;
      forceMessage?: boolean; runtimeMaintenance?: boolean;
    } & (
      { packId: string; sourceTaskId?: string; sourceKind?: 'baseline' | 'snapshot' | 'round'; sourceRound?: number }
      | { packId?: undefined; sourceTaskId: string; sourceKind: 'baseline' | 'snapshot' | 'round'; sourceRound?: number }
    )): Promise<{ task_id: string; status: string }> {
      return fetchJson(`${BASE}/evolve/pack-restores`, { method: 'POST', body: JSON.stringify(input) })
    },
    createRuntimeCleanup(input: {
      taskName: string; remark?: string; userId: string; botId: string; botEnv?: string;
      forceCleanup?: boolean;
    }): Promise<{ task_id: string; status: string }> {
      return fetchJson(`${BASE}/evolve/runtime-cleanups`, { method: 'POST', body: JSON.stringify(input) })
    },
    listPacks(botId?: string, params: { scope?: 'mine' | 'all'; ownerUserId?: string } = {}): Promise<{ items: Array<{ packId: string; userId: string; botId: string; taskId: string; stepId: string; sourceKind: 'baseline' | 'snapshot' | 'round'; sourceRound?: number | null; createdAt?: number | string; status?: string; applicationCount: number; artifact: { ref?: string; size?: number; sha256?: string } }> }> {
      const sp = new URLSearchParams()
      if (botId) sp.set('botId', botId)
      if (params.scope) sp.set('scope', params.scope)
      if (params.ownerUserId?.trim()) sp.set('ownerUserId', params.ownerUserId.trim())
      return fetchJson(`${BASE}/evolve/packs${sp.size ? `?${sp.toString()}` : ''}`)
    },
    listVersions(botId?: string, params: { scope?: 'mine' | 'all'; ownerUserId?: string } = {}): Promise<{ items: EvolveVersion[] }> {
      const sp = new URLSearchParams()
      if (botId) sp.set('botId', botId)
      if (params.scope) sp.set('scope', params.scope)
      if (params.ownerUserId?.trim()) sp.set('ownerUserId', params.ownerUserId.trim())
      return fetchJson(`${BASE}/evolve/versions${sp.size ? `?${sp.toString()}` : ''}`)
    },
    getPack(packId: string): Promise<{ pack: { packId: string; userId: string; botId: string; taskId: string; stepId: string; sourceKind: 'baseline' | 'snapshot' | 'round'; sourceRound?: number | null; createdAt?: number | string; status: string; artifact: { ref: string; size: number; sha256: string; contentType: string } }; sourceTask: EvolveTask | null; applications: EvolveTask[] }> {
      return fetchJson(`${BASE}/evolve/packs/${encodeURIComponent(packId)}`)
    },
    createBench(input: {
      taskName: string; remark?: string;
      userId: string; botId: string; botEnv?: string; benchDomainId: string;
      templateName?: string; templateVersion?: number | null;
      model: string; suite: string; scene: string;
      nodeCommandYamls?: Record<string, string>; forceMessage?: boolean; runtimeMaintenance?: boolean;
      openclawExecutionMode?: 'local' | 'gateway';
    }): Promise<{ task_id: string; status: string }> {
      return fetchJson(`${BASE}/evolve/benches`, { method: 'POST', body: JSON.stringify(input) })
    },
    createBenchOptimization(input: {
      taskName: string; remark?: string;
      userId: string; botId: string; botEnv?: string;
      objective: string;
      trainBenchDomainId: string; testBenchDomainId: string;
      maxRounds: number;
      nodeCommandYamls?: Record<string, string>; forceMessage?: boolean; runtimeMaintenance?: boolean;
      openclawExecutionMode?: 'local' | 'gateway';
    }): Promise<{ task_id: string; status: string }> {
      return fetchJson(`${BASE}/evolve/bench-optimizations`, { method: 'POST', body: JSON.stringify(input) })
    },
    listLessons(params: { workflowId?: string; status?: string; query?: string; limit?: number; offset?: number } = {}): Promise<{ lessons: EvolveLesson[]; total: number; limit: number; offset: number }> {
      const sp = new URLSearchParams()
      if (params.workflowId) sp.set('workflowId', params.workflowId)
      if (params.status) sp.set('status', params.status)
      if (params.query) sp.set('query', params.query)
      if (params.limit) sp.set('limit', String(params.limit))
      if (params.offset !== undefined) sp.set('offset', String(params.offset))
      const suffix = sp.size ? `?${sp.toString()}` : ''
      return fetchJson(`${BASE}/evolve/lessons${suffix}`)
    },
    getLesson(lessonId: string): Promise<{ lesson: EvolveLesson }> {
      return fetchJson(`${BASE}/evolve/lessons/${encodeURIComponent(lessonId)}`)
    },
    createLesson(input: EvolveLessonInput): Promise<{ lesson: EvolveLesson }> {
      return fetchJson(`${BASE}/evolve/lessons`, { method: 'POST', body: JSON.stringify(input) })
    },
    updateLesson(lessonId: string, input: Partial<EvolveLessonInput>): Promise<{ lesson: EvolveLesson }> {
      return fetchJson(`${BASE}/evolve/lessons/${encodeURIComponent(lessonId)}`, { method: 'PATCH', body: JSON.stringify(input) })
    },
    recordLessonOutcome(lessonId: string, input: { workflowId?: string; nodeId?: string; action?: string; applied?: boolean; succeeded?: boolean; verdict?: string; note?: string }): Promise<{ outcome: EvolveLessonOutcome; lesson: EvolveLesson | null }> {
      return fetchJson(`${BASE}/evolve/lessons/${encodeURIComponent(lessonId)}/outcomes`, { method: 'POST', body: JSON.stringify(input) })
    },
    listDiagnoses(params: { workflowId?: string; flowId?: string; analysisId?: string; query?: string; limit?: number; offset?: number } = {}): Promise<{ diagnoses: EvolveRunDiagnosis[]; total: number; limit: number; offset: number }> {
      const sp = new URLSearchParams()
      if (params.workflowId) sp.set('workflowId', params.workflowId)
      if (params.flowId) sp.set('flowId', params.flowId)
      if (params.analysisId) sp.set('analysisId', params.analysisId)
      if (params.query) sp.set('query', params.query)
      if (params.limit) sp.set('limit', String(params.limit))
      if (params.offset !== undefined) sp.set('offset', String(params.offset))
      const suffix = sp.size ? `?${sp.toString()}` : ''
      return fetchJson(`${BASE}/evolve/diagnoses${suffix}`)
    },
    getDiagnosis(diagnosisId: string): Promise<{ diagnosis: EvolveRunDiagnosis }> {
      return fetchJson(`${BASE}/evolve/diagnoses/${encodeURIComponent(diagnosisId)}`)
    },
    createDiagnosisRecord(input: Omit<EvolveRunDiagnosis, 'id' | 'diagnosis_id' | 'gmt_create' | 'gmt_modified'> & { diagnosisId?: string }): Promise<{ diagnosis: EvolveRunDiagnosis }> {
      return fetchJson(`${BASE}/evolve/run-diagnoses`, { method: 'POST', body: JSON.stringify(input) })
    },
    promoteDiagnosisToLesson(diagnosisId: string, input?: { fixSpec?: string; fixKind?: string; status?: string; note?: string }): Promise<{ lesson: EvolveLesson }> {
      return fetchJson(`${BASE}/evolve/diagnoses/${encodeURIComponent(diagnosisId)}/promote`, { method: 'POST', body: JSON.stringify(input ?? {}) })
    },
    listSuggestions(params: { workflowId: string; status?: string; limit?: number; offset?: number }): Promise<{ suggestions: EvolveSuggestion[]; total: number }> {
      const sp = new URLSearchParams()
      sp.set('workflowId', params.workflowId)
      if (params.status) sp.set('status', params.status)
      if (params.limit !== undefined) sp.set('limit', String(params.limit))
      if (params.offset !== undefined) sp.set('offset', String(params.offset))
      return fetchJson(`${BASE}/evolve/suggestions?${sp.toString()}`)
    },

    listEligibleBotsForSuggestion(params: { suggestionId: string }): Promise<{ bots: { botId: string; botName: string | null; env: string | null; accessType: string; ownerId: string | null }[] }> {
      return fetchJson(`${BASE}/evolve/suggestions/${encodeURIComponent(params.suggestionId)}/eligible-bots`)
    },
    applySuggestion(input: { suggestionId: string; botId?: string; botEnv?: string; applicationSpec?: string }): Promise<{ ok: boolean; taskId: string; stepId: string; status: string; reportUrl: string; suggestionIds?: string[]; appliedSpecDigest?: string }> {
      return fetchJson(`${BASE}/evolve/suggestions/${encodeURIComponent(input.suggestionId)}/apply`, { method: 'POST', body: JSON.stringify(input) })
    },
    applySuggestionsBatch(input: { suggestionIds: string[]; botId?: string; botEnv?: string; applicationSpec?: string }): Promise<{ ok: boolean; taskId: string; stepId: string; status: string; reportUrl: string; suggestionIds: string[]; appliedSpecDigest?: string }> {
      return fetchJson(`${BASE}/evolve/suggestions/apply-batch`, { method: 'POST', body: JSON.stringify(input) })
    },
    recordSuggestionAction(input: { suggestionId: string; workflowId: string; signature: string; nodeId?: string; action: string; fixKind?: string; note?: string }): Promise<{ action: EvolveSuggestionAction }> {
      return fetchJson(`${BASE}/evolve/suggestions/${encodeURIComponent(input.suggestionId)}/action`, { method: 'POST', body: JSON.stringify(input) })
    },
    analyzeWorkflowLogs(input: { workflowId: string; lookbackDays?: number; force?: boolean }): Promise<{ ok: boolean; workflowId: string; analyzedFlows: number; newDiagnoses: number; skippedDiagnoses: number; firstError?: string }> {
      return fetchJson(`${BASE}/evolve/analyze`, { method: 'POST', body: JSON.stringify(input) })
    },
    analyzeFlow(params: { flowId: string; workflowId: string }): Promise<{ ok: boolean; flowId: string; workflowId: string; diagnosisCount: number; lessonCount: number }> {
      return fetchJson(`${BASE}/evolve/diagnoses/analyze-flow`, { method: 'POST', body: JSON.stringify(params) })
    },
    analyzeRun(flowId: string, botId?: string, botEnv?: string): Promise<{ ok: boolean; analysisId?: string; flowId: string; status: 'analyzing' | 'completed' | 'failed' | null; diagnosisCount: number; suggestionCount: number }> {
      return fetchJson(`${BASE}/evolve/runs/${encodeURIComponent(flowId)}/analyze`, { method: 'POST', body: JSON.stringify({ botId, botEnv }) })
    },
    getAnalysisProgress(flowId: string): Promise<WorkflowAnalysisProgressResponse> {
      return fetchJson(`${BASE}/evolve/runs/${encodeURIComponent(flowId)}/analysis-progress`)
    },
    getRunAnalysisResult(flowId: string, analysisId?: string): Promise<{ analysis: RunEvolutionAnalysisResponse | null }> {
      const query = analysisId ? `?analysisId=${encodeURIComponent(analysisId)}` : ''
      return fetchJson(`${BASE}/evolve/runs/${encodeURIComponent(flowId)}/analysis-result${query}`)
    },
    listEligibleBotsForAnalyze(workflowId: string): Promise<{ bots: { botId: string; botName: string | null; env: string | null; accessType: string; ownerId: string | null }[] }> {
      return fetchJson(`${BASE}/evolve/runs/eligible-bots?workflowId=${encodeURIComponent(workflowId)}`)
    },
    resetAnalysisRun(flowId: string): Promise<{ ok: boolean; flowId: string; canceled: number }> {
      return fetchJson(`${BASE}/evolve/runs/${encodeURIComponent(flowId)}/reset-analysis`, { method: 'POST' })
    },
    getSuggestionApplyTasks(suggestionIds: string[]): Promise<{ tasks: SuggestionApplyTask[] }> {
      const sp = new URLSearchParams()
      for (const id of suggestionIds) sp.append('suggestionIds', id)
      return fetchJson(`${BASE}/evolve/suggestions/apply-tasks?${sp.toString()}`)
    },

  },
  tclog: {
    bots(params?: { ownerId?: string; status?: 'active' | 'all' }): Promise<{ ownerId: string; bots: TCLogBot[] }> {
      const sp = new URLSearchParams()
      if (params?.ownerId) sp.set('ownerId', params.ownerId)
      if (params?.status) sp.set('status', params.status)
      const qs = sp.toString()
      return fetchJson<{ ownerId: string; bots: TCLogBot[] }>(`${BASE}/tclog/bots${qs ? `?${qs}` : ''}`)
    },

    query(params: TCLogQueryParams): Promise<TCLogQueryResponse> {
      const sp = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== '') sp.set(key, String(value))
      })
      const qs = sp.toString()
      return fetchJson<TCLogQueryResponse>(`${BASE}/tclog/query${qs ? `?${qs}` : ''}`)
    },

    tasks(params: TCLogTaskListParams): Promise<TCLogTaskListResponse> {
      const sp = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== '') sp.set(key, String(value))
      })
      const qs = sp.toString()
      return fetchJson<TCLogTaskListResponse>(`${BASE}/tclog/tasks${qs ? `?${qs}` : ''}`)
    },

    taskSearch(params: TCLogTaskSearchParams): Promise<TCLogTaskSearchResponse> {
      const sp = new URLSearchParams()
      Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== '') sp.set(key, String(value))
      })
      const qs = sp.toString()
      return fetchJson<TCLogTaskSearchResponse>(`${BASE}/tclog/task-search${qs ? `?${qs}` : ''}`)
    },

    trace(traceId: string, ownerId?: string, dataSource?: 'auto' | 'tc' | 'langfuse', botId?: string, embed?: boolean): Promise<{ trace: TCLogTrace; dataSource: string; fallbackUsed: boolean }> {
      const sp = new URLSearchParams()
      if (ownerId) sp.set('ownerId', ownerId)
      if (dataSource && dataSource !== 'auto') sp.set('dataSource', dataSource)
      if (botId) sp.set('botId', botId)
      if (embed) sp.set('embed', '1')
      const qs = sp.toString()
      return fetchJson<{ trace: TCLogTrace; dataSource: string; fallbackUsed: boolean }>(`${BASE}/tclog/traces/${encodeURIComponent(traceId)}${qs ? `?${qs}` : ''}`)
    },

    workflow(flowId: string, ownerId?: string): Promise<{ workflowRun: TCLogWorkflowRun }> {
      const sp = new URLSearchParams()
      if (ownerId) sp.set('ownerId', ownerId)
      const qs = sp.toString()
      return fetchJson<{ workflowRun: TCLogWorkflowRun }>(`${BASE}/tclog/workflows/${encodeURIComponent(flowId)}${qs ? `?${qs}` : ''}`)
    },
  },

  sandboxQuery: {
    query(botId: string, entityId: number): Promise<{ success: boolean; data: { arca: string[]; baas: string[] } }> {
      return fetchJson(`${BASE}/sandbox-query?bot_id=${encodeURIComponent(botId)}&entity_id=${entityId}`)
    },
  },

  runs: {
    list(params?: {
      status?: string
      statuses?: string[]
      workflowId?: string
      limit?: number
      offset?: number
      from?: string
      to?: string
      botOwnerId?: string
      botId?: string
      inputQuery?: string
    }): Promise<FlowRunsResponse> {
      const sp = new URLSearchParams()
      if (params?.status) sp.set('status', params.status)
      if (params?.statuses?.length) sp.set('statuses', params.statuses.join(','))
      if (params?.workflowId) sp.set('workflowId', params.workflowId)
      if (params?.limit) sp.set('limit', String(params.limit))
      if (params?.offset) sp.set('offset', String(params.offset))
      if (params?.from) sp.set('from', params.from)
      if (params?.to) sp.set('to', params.to)
      if (params?.botOwnerId) sp.set('botOwnerId', params.botOwnerId)
      if (params?.botId) sp.set('botId', params.botId)
      if (params?.inputQuery) sp.set('inputQuery', params.inputQuery)
      const qs = sp.toString()
      return fetchJson<FlowRunsResponse>(`${BASE}/runs${qs ? `?${qs}` : ''}`)
    },

    workflowTypes(botOwnerId?: string, botId?: string, limit?: number, offset?: number, status?: string): Promise<WorkflowTypesResponse> {
      const sp = new URLSearchParams()
      if (botOwnerId) sp.set('botOwnerId', botOwnerId)
      if (botId) sp.set('botId', botId)
      if (limit != null) sp.set('limit', String(limit))
      if (offset != null) sp.set('offset', String(offset))
      if (status) sp.set('status', status)
      const qs = sp.toString()
      return fetchJson<WorkflowTypesResponse>(`${BASE}/runs/workflow-types${qs ? `?${qs}` : ''}`)
    },

    delete(flowId: string): Promise<{ deleted: boolean; details: { nodes: number; events: number; metrics: number; alerts: number } }> {
      return fetchJson<{ deleted: boolean; details: { nodes: number; events: number; metrics: number; alerts: number } }>(`${BASE}/runs/${flowId}`, {
        method: 'DELETE',
      })
    },

    get(flowId: string): Promise<FlowRunDetail> {
      return fetchJson<FlowRunDetail>(`${BASE}/runs/${flowId}`)
    },

    nodes(flowId: string, full = false): Promise<NodeExecution[]> {
      const qs = full ? '?full=true' : ''
      return fetchJson<NodeExecution[]>(`${BASE}/runs/${flowId}/nodes${qs}`)
    },

    events(flowId: string): Promise<FlowEvent[]> {
      return fetchJson<FlowEvent[]>(`${BASE}/runs/${flowId}/events`)
    },

    interventions(flowId: string): Promise<InterventionInfo> {
      return fetchJson<InterventionInfo>(`${BASE}/runs/${flowId}/interventions`)
    },

    intervene(flowId: string, req: InterveneRequest): Promise<InterveneResult> {
      return fetchJson<InterveneResult>(`${BASE}/runs/${flowId}/intervene`, {
        method: 'POST',
        body: JSON.stringify(req),
      })
    },

    updateSession(flowId: string, patch: SessionInfoUpdate): Promise<SessionInfoUpdateResult> {
      return fetchJson<SessionInfoUpdateResult>(`${BASE}/runs/${flowId}/session`, {
        method: 'PATCH',
        body: JSON.stringify(patch),
      })
    },

    chat(flowId: string, message: string): Promise<ChatSendResult> {
      return fetchJson<ChatSendResult>(`${BASE}/runs/${flowId}/chat`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      })
    },

    pollMessage(flowId: string, messageId: string): Promise<ChatPollResult> {
      return fetchJson<ChatPollResult>(`${BASE}/runs/${flowId}/poll-message`, {
        method: 'POST',
        body: JSON.stringify({ messageId }),
      })
    },

    rerun(flowId: string): Promise<RerunResult> {
      return fetchJson<RerunResult>(`${BASE}/runs/${encodeURIComponent(flowId)}/rerun`, {
        method: 'POST',
      })
    },
  },

  stepTraces: {
    get(flowId: string, nodeId: string, attempt = 1): Promise<NodeStepTraceData> {
      const sp = new URLSearchParams({ attempt: String(attempt) })
      return fetchJson<{ success: boolean; data: NodeStepTraceData }>(`${BASE}/runs/${flowId}/nodes/${nodeId}/steps?${sp.toString()}`)
        .then(r => r.data)
    },
  },

  hallucinationChecks: {
    get(flowId: string, nodeId: string, attempt = 1): Promise<HallucinationCheckData> {
      const sp = new URLSearchParams({ attempt: String(attempt) })
      return fetchJson<{ success: boolean; data: HallucinationCheckData }>(`${BASE}/runs/${flowId}/nodes/${nodeId}/hallucination-checks?${sp.toString()}`)
        .then(r => r.data)
    },
  },

  runArchives: {
    get(flowId: string): Promise<RunArchiveData> {
      return fetchJson<{ data: RunArchiveData }>(`${BASE}/run-archives/${encodeURIComponent(flowId)}`)
        .then(r => r.data)
    },
    getTimeline(flowId: string): Promise<RunTimeline> {
      return fetchJson<{ data: RunTimeline }>(`${BASE}/run-archives/${encodeURIComponent(flowId)}/timeline`)
        .then(r => r.data)
    },
  },

  workflowNodeStats: {
    get(workflowId: string, days?: number): Promise<WorkflowNodeStats> {
      const qs = days ? `?days=${days}` : ''
      return fetchJson<{ data: WorkflowNodeStats }>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/node-stats${qs}`)
        .then(r => r.data)
    },
    getHealth(workflowId: string, days?: number): Promise<WorkflowHealth> {
      const qs = days ? `?days=${days}` : ''
      return fetchJson<{ data: WorkflowHealth }>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/health${qs}`)
        .then(r => r.data)
    },
  },

  taskGuard: {
    getAutoAnalysis(workflowId: string): Promise<WorkflowAutoAnalysisSetting> {
      return fetchJson(`${BASE}/task-guard/workflows/${encodeURIComponent(workflowId)}/auto-analysis`)
    },
    updateAutoAnalysis(workflowId: string, enabled: boolean): Promise<WorkflowAutoAnalysisSetting> {
      return fetchJson(`${BASE}/task-guard/workflows/${encodeURIComponent(workflowId)}/auto-analysis`, {
        method: 'PUT',
        body: JSON.stringify({ enabled }),
      })
    },
  },

  workflows: {
    list(botOwnerId?: string, botId?: string): Promise<Array<{ workflowId: string; title: string; packId: string | null; updatedAt: number }>> {
      const params = new URLSearchParams()
      if (botOwnerId) params.set('botOwnerId', botOwnerId)
      if (botId) params.set('botId', botId)
      const qs = params.toString()
      return fetchJson<unknown>(`${BASE}/workflows${qs ? `?${qs}` : ''}`).then((res) => {
        // Backward compat: server may return {data: [...], pagination: {...}} or plain array
        if (Array.isArray(res)) return res as Array<{ workflowId: string; title: string; packId: string | null; updatedAt: number }>
        if (res && typeof res === 'object' && 'data' in res && Array.isArray((res as { data: unknown }).data)) {
          return (res as { data: Array<{ workflowId: string; title: string; packId: string | null; updatedAt: number }> }).data
        }
        return res as Array<{ workflowId: string; title: string; packId: string | null; updatedAt: number }>
      })
    },

    listPage(params: { page: number; pageSize: number; search?: string }): Promise<{
      data: Array<{ workflowId: string; title: string; packId: string | null; updatedAt: number }>;
      pagination: { page: number; pageSize: number; total: number; totalPages: number };
    }> {
      const sp = new URLSearchParams()
      sp.set('page', String(params.page))
      sp.set('pageSize', String(params.pageSize))
      if (params.search) sp.set('search', params.search)
      return fetchJson(`${BASE}/workflows/list?${sp.toString()}`)
    },

    get(workflowId: string): Promise<WorkflowSpec> {
      return fetchJson<WorkflowSpec>(`${BASE}/workflows/${workflowId}`)
    },

    save(
      workflowId: string,
      spec: WorkflowSpec,
      options?: {
        packId?: string
        facade?: { command?: string; remark?: string }
        originalWorkflowId?: string
        botOwnerId?: string
        botId?: string
      },
    ): Promise<WorkflowSpec> {
      return fetchJson<WorkflowSpec>(`${BASE}/workflows/save`, {
        method: 'POST',
        body: JSON.stringify({
          workflowId,
          packId: options?.packId ?? null,
          spec,
          facade: options?.facade,
          originalWorkflowId: options?.originalWorkflowId,
          botOwnerId: options?.botOwnerId,
          botId: options?.botId,
        }),
      })
    },

    validate(spec: unknown): Promise<WorkflowValidationResult> {
      return fetchJson<WorkflowValidationResult>(`${BASE}/workflows/validate`, {
        method: 'POST',
        body: JSON.stringify({ spec }),
      })
    },

    /** GET /api/workflows/:wf/history — deploy history list (no spec_json). */
    getHistory(workflowId: string, limit = 50): Promise<{ workflowId: string; history: DeployHistoryItem[] }> {
      return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}/history?limit=${limit}`)
    },

    getAccess(workflowId: string): Promise<{ workflowId: string; canView: boolean; canEdit: boolean }> {
      return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}/access`)
    },

    /** GET /api/workflows/:wf/history/:version — full snapshot at a specific version. */
    getVersion(workflowId: string, version: number): Promise<VersionSnapshot> {
      return fetchJson<VersionSnapshot>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/history/${version}`)
    },

    /** GET /api/workflows/:wf/history/by-deploy/:deployNumber — snapshot of a specific deploy record. */
    getDeploySnapshot(workflowId: string, deployNumber: number): Promise<VersionSnapshot> {
      return fetchJson<VersionSnapshot>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/history/by-deploy/${deployNumber}`)
    },

    /** GET /api/workflows/:wf/history/diff?fromDeploy=&toDeploy= — both spec_json for frontend diffing. */
    diffHistory(workflowId: string, fromDeploy: number, toDeploy: number): Promise<VersionDiffResult> {
      return fetchJson<VersionDiffResult>(
        `${BASE}/workflows/${encodeURIComponent(workflowId)}/history/diff?fromDeploy=${fromDeploy}&toDeploy=${toDeploy}`,
      )
    },

    /** GET /api/workflows/:wf/versions — list deployed versions (action='deploy' only). */
    listVersions(workflowId: string, limit = 50): Promise<VersionListResponse> {
      return fetchJson<VersionListResponse>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/versions?limit=${limit}`)
    },

    /** POST /api/workflows/:wf/versions/:v/activate — set version v as the active (default) version. */
    activateVersion(workflowId: string, version: number): Promise<VersionActivateResponse> {
      return fetchJson<VersionActivateResponse>(
        `${BASE}/workflows/${encodeURIComponent(workflowId)}/versions/${version}/activate`,
        { method: 'POST' },
      )
    },

    delete(workflowId: string, botOwnerId?: string, botId?: string): Promise<{ ok: boolean }> {
      const params = new URLSearchParams()
      if (botOwnerId) params.set('botOwnerId', botOwnerId)
      if (botId) params.set('botId', botId)
      const qs = params.toString()
      return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}${qs ? `?${qs}` : ''}`, {
        method: 'DELETE',
      })
    },

    botPermissions: {
      list(workflowId: string): Promise<BotPermission[]> {
        return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}/bot-permissions`)
      },

      upsert(workflowId: string, data: BotPermissionUpsert): Promise<{ ok: boolean }> {
        return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}/bot-permissions`, {
          method: 'PUT',
          body: JSON.stringify(data),
        })
      },

      delete(workflowId: string, permissionId: number): Promise<{ ok: boolean }> {
        const params = new URLSearchParams()
        params.set('permissionId', String(permissionId))
        return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}/bot-permissions?${params.toString()}`, {
          method: 'DELETE',
        })
      },
    },

    notificationConfig: {
      get(workflowId: string): Promise<NotificationConfig | null> {
        return fetchJson<NotificationConfig | null>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/notification-config`)
      },

      upsert(workflowId: string, data: Omit<NotificationConfig, 'workflowId'>): Promise<{ ok: boolean }> {
        return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}/notification-config`, {
          method: 'PUT',
          body: JSON.stringify(data),
        })
      },

      delete(workflowId: string): Promise<{ ok: boolean }> {
        return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}/notification-config`, {
          method: 'DELETE',
        })
      },
    },

    callbackConfigs: {
      list(workflowId: string): Promise<HttpCallbackConfig[]> {
        return fetchJson<HttpCallbackConfig[]>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/callback-configs`)
      },

      create(workflowId: string, data: HttpCallbackConfigCreateInput): Promise<HttpCallbackConfig> {
        return fetchJson<HttpCallbackConfig>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/callback-configs`, {
          method: 'POST',
          body: JSON.stringify(data),
        })
      },

      update(workflowId: string, configId: string, data: HttpCallbackConfigUpdateInput): Promise<HttpCallbackConfig> {
        return fetchJson<HttpCallbackConfig>(`${BASE}/workflows/${encodeURIComponent(workflowId)}/callback-configs/${encodeURIComponent(configId)}`, {
          method: 'PUT',
          body: JSON.stringify(data),
        })
      },

      delete(workflowId: string, configId: string): Promise<{ ok: boolean }> {
        return fetchJson(`${BASE}/workflows/${encodeURIComponent(workflowId)}/callback-configs/${encodeURIComponent(configId)}`, {
          method: 'DELETE',
        })
      },
    },
  },

  dryRun(req: DryRunRequest): Promise<DryRunResult> {
    return fetchJson<DryRunResult>(`${BASE}/dry-run`, {
      method: 'POST',
      body: JSON.stringify(req),
    })
  },

  knowledgeBases: {
    list(enabledOnly = false): Promise<KnowledgeBase[]> {
      const qs = enabledOnly ? '?enabled=true' : ''
      return fetchJson<KnowledgeBase[]>(`${BASE}/knowledge-bases${qs}`)
    },

    get(kbId: string): Promise<KnowledgeBase> {
      return fetchJson<KnowledgeBase>(`${BASE}/knowledge-bases/${encodeURIComponent(kbId)}`)
    },

    create(input: KnowledgeBaseCreateInput): Promise<KnowledgeBase> {
      return fetchJson<KnowledgeBase>(`${BASE}/knowledge-bases`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    update(kbId: string, input: KnowledgeBaseUpdateInput): Promise<KnowledgeBase> {
      return fetchJson<KnowledgeBase>(`${BASE}/knowledge-bases/${encodeURIComponent(kbId)}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    delete(kbId: string): Promise<{ affected: boolean }> {
      return fetchJson<{ affected: boolean }>(`${BASE}/knowledge-bases/${encodeURIComponent(kbId)}`, {
        method: 'DELETE',
      })
    },

    test(kbId: string, query: string): Promise<KnowledgeBaseTestResult> {
      return fetchJson<KnowledgeBaseTestResult>(`${BASE}/knowledge-bases/${encodeURIComponent(kbId)}/test`, {
        method: 'POST',
        body: JSON.stringify({ query }),
      })
    },

    yuqueSearch(query: string, bookId?: number, bookIds?: number[]): Promise<KnowledgeBaseTestResult> {
      return fetchJson<KnowledgeBaseTestResult>(`${BASE}/knowledge-bases/yuque/search`, {
        method: 'POST',
        body: JSON.stringify({ query, bookId, bookIds }),
      })
    },

    yuqueBooks(): Promise<YuQueBookInfo[]> {
      return fetchJson<YuQueBookInfo[]>(`${BASE}/knowledge-bases/yuque/books`)
    },
  },

  appConfig: {
    list(enabledOnly = false): Promise<AppConfigEntry[]> {
      const qs = enabledOnly ? '?enabled=true' : ''
      return fetchJson<AppConfigEntry[]>(`${BASE}/app-config${qs}`)
    },

    get(configKey: string): Promise<AppConfigEntry> {
      return fetchJson<AppConfigEntry>(`${BASE}/app-config/${encodeURIComponent(configKey)}`)
    },

    create(input: AppConfigCreateInput): Promise<AppConfigEntry> {
      return fetchJson<AppConfigEntry>(`${BASE}/app-config`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    update(configKey: string, input: AppConfigUpdateInput): Promise<AppConfigEntry> {
      return fetchJson<AppConfigEntry>(`${BASE}/app-config/${encodeURIComponent(configKey)}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    delete(configKey: string): Promise<{ affected: boolean }> {
      return fetchJson<{ affected: boolean }>(`${BASE}/app-config/${encodeURIComponent(configKey)}`, {
        method: 'DELETE',
      })
    },
  },

  adminUsers: {
    list(): Promise<AdminUsersListResponse> {
      return fetchJson<AdminUsersListResponse>(`${BASE}/admin-users`)
    },
    create(input: AdminUserCreateInput): Promise<AdminUserEntry> {
      return fetchJson<AdminUserEntry>(`${BASE}/admin-users`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },
    delete(id: number): Promise<{ affected: boolean }> {
      return fetchJson<{ affected: boolean }>(`${BASE}/admin-users/${id}`, {
        method: 'DELETE',
      })
    },
  },

  systemLogs: {
    search(params: { keyword: string; sources?: string[]; from: number; to: number; limit?: number }): Promise<SystemLogSearchResult> {
      return fetchJson<SystemLogSearchResult>(`${BASE}/log-analysis/search`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },
  },

  validationTemplates: {
    list(enabledOnly = false): Promise<ValidationTemplate[]> {
      const qs = enabledOnly ? '?enabled=true' : ''
      return fetchJson<ValidationTemplate[]>(`${BASE}/validation-templates${qs}`)
    },

    get(templateId: string): Promise<ValidationTemplate> {
      return fetchJson<ValidationTemplate>(`${BASE}/validation-templates/${encodeURIComponent(templateId)}`)
    },

    create(input: ValidationTemplateCreateInput): Promise<ValidationTemplate> {
      return fetchJson<ValidationTemplate>(`${BASE}/validation-templates`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    update(templateId: string, input: ValidationTemplateUpdateInput): Promise<ValidationTemplate> {
      return fetchJson<ValidationTemplate>(`${BASE}/validation-templates/${encodeURIComponent(templateId)}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    delete(templateId: string): Promise<void> {
      return fetch(`${BASE}/validation-templates/${encodeURIComponent(templateId)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
      }).then(res => {
        if (!res.ok) throw new Error(`API ${res.status}`)
      })
    },

    test(templateId: string, sampleOutput: string): Promise<ValidationResult> {
      return fetchJson<ValidationResult>(`${BASE}/validation-templates/${encodeURIComponent(templateId)}/test`, {
        method: 'POST',
        body: JSON.stringify({ sampleOutput }),
      })
    },
  },

  facades: {
    list(): Promise<FacadeBinding[]> {
      return fetchJson<unknown>(`${BASE}/facades`).then((res) => {
        // Backward compat: server may return {data: [...], pagination: {...}} or plain array
        if (Array.isArray(res)) return res as FacadeBinding[]
        if (res && typeof res === 'object' && 'data' in res && Array.isArray((res as { data: unknown }).data)) {
          return (res as { data: FacadeBinding[] }).data
        }
        return res as FacadeBinding[]
      })
    },

    listPage(params: { page: number; pageSize: number; search?: string }): Promise<{
      data: FacadeBinding[];
      pagination: { page: number; pageSize: number; total: number; totalPages: number };
    }> {
      const sp = new URLSearchParams()
      sp.set('page', String(params.page))
      sp.set('pageSize', String(params.pageSize))
      if (params.search) sp.set('search', params.search)
      return fetchJson(`${BASE}/facades/list?${sp.toString()}`)
    },

    create(input: { command: string; workflowId: string; packId?: string; remark?: string }): Promise<FacadeBinding> {
      return fetchJson<FacadeBinding>(`${BASE}/facades`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    update(command: string, input: { workflowId?: string; packId?: string; remark?: string }): Promise<FacadeBinding> {
      return fetchJson<FacadeBinding>(`${BASE}/facades/${encodeURIComponent(command)}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    delete(command: string): Promise<void> {
      return fetch(`${BASE}/facades/${encodeURIComponent(command)}`, {
        method: 'DELETE',
        headers: { 'Content-Type': 'application/json' },
      }).then(res => {
        if (!res.ok) throw new Error(`API ${res.status}`)
      })
    },
  },

  baas: {
    pollStatus(params: {
      baseUrl: string
      mode: string
      runId: string
      iamToken?: string
    }): Promise<{
      ok: boolean
      status: number
      data: {
        run_id?: string
        message_id?: string
        session_id?: string
        status?: string
        result?: { content?: string }
      } | null
      errorCode: string | number | null
      errorMessage: string | null
    }> {
      return fetchJson(`${BASE}/baas/poll-status`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },
  },

  langfuse: {
    traces(params: { sessionId: string; from?: string; to?: string }): Promise<LangfuseTracesResponse> {
      const sp = new URLSearchParams({ sessionId: params.sessionId })
      if (params.from) sp.set('from', params.from)
      if (params.to) sp.set('to', params.to)
      return fetchJson<LangfuseTracesResponse>(`${BASE}/langfuse/traces?${sp.toString()}`)
    },
  },

  analysis: {
    analyze(params: { traceData: unknown; nodeTitle: string; nodeId: string; nodeInput?: string; nodeOutput?: string; nodeError?: string }): Promise<AnalysisResult> {
      return fetchJson<AnalysisResult>(`${BASE}/analysis/analyze`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },
  },

  sessionDiagnosis: {
    diagnose(params: { url?: string; sessionId?: string }): Promise<SessionDiagnosisResult> {
      return fetchJson<SessionDiagnosisResult>(`${BASE}/session-diagnosis/diagnose`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },
  },

  flowControl: {
    listSlots(params?: {
      instance_id?: string
      scope_key?: string
      flow_id?: string
      limit?: number
      offset?: number
    }): Promise<FlowControlListResponse<FlowControlSlot>> {
      const sp = new URLSearchParams()
      if (params?.instance_id) sp.set('instance_id', params.instance_id)
      if (params?.scope_key) sp.set('scope_key', params.scope_key)
      if (params?.flow_id) sp.set('flow_id', params.flow_id)
      if (params?.limit) sp.set('limit', String(params.limit))
      if (params?.offset) sp.set('offset', String(params.offset))
      const qs = sp.toString()
      return fetchJson(`${BASE}/flow-control/slots${qs ? `?${qs}` : ''}`)
    },

    slotInstances(): Promise<string[]> {
      return fetchJson(`${BASE}/flow-control/slots/instances`)
    },

    slotCount(instanceId?: string): Promise<{ count: number }> {
      const qs = instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : ''
      return fetchJson(`${BASE}/flow-control/slots/count${qs}`)
    },

    deleteSlot(id: number, cascade = false): Promise<FlowControlDeleteResponse> {
      const qs = cascade ? '?cascade=true' : ''
      return fetchJson(`${BASE}/flow-control/slots/${id}${qs}`, { method: 'DELETE' })
    },

    batchDeleteSlots(ids: number[]): Promise<FlowControlBatchDeleteResponse> {
      return fetchJson(`${BASE}/flow-control/slots/batch-delete`, {
        method: 'POST',
        body: JSON.stringify({ ids }),
      })
    },

    deleteAllSlots(instanceId?: string): Promise<FlowControlDeleteAllResponse> {
      const qs = instanceId ? `?instance_id=${encodeURIComponent(instanceId)}` : ''
      return fetchJson(`${BASE}/flow-control/slots/all${qs}`, { method: 'DELETE' })
    },

    listQueue(params?: {
      instance_id?: string
      scope_key?: string
      flow_id?: string
      status?: string
      limit?: number
      offset?: number
    }): Promise<FlowControlListResponse<FlowControlQueueItem>> {
      const sp = new URLSearchParams()
      if (params?.instance_id) sp.set('instance_id', params.instance_id)
      if (params?.scope_key) sp.set('scope_key', params.scope_key)
      if (params?.flow_id) sp.set('flow_id', params.flow_id)
      if (params?.status) sp.set('status', params.status)
      if (params?.limit) sp.set('limit', String(params.limit))
      if (params?.offset) sp.set('offset', String(params.offset))
      const qs = sp.toString()
      return fetchJson(`${BASE}/flow-control/queue${qs ? `?${qs}` : ''}`)
    },

    queueInstances(): Promise<string[]> {
      return fetchJson(`${BASE}/flow-control/queue/instances`)
    },

    queueCount(instanceId?: string, status?: string): Promise<{ count: number }> {
      const sp = new URLSearchParams()
      if (instanceId) sp.set('instance_id', instanceId)
      if (status) sp.set('status', status)
      const qs = sp.toString()
      return fetchJson(`${BASE}/flow-control/queue/count${qs ? `?${qs}` : ''}`)
    },

    deleteQueueItem(id: number, cascade = false): Promise<FlowControlDeleteResponse> {
      const qs = cascade ? '?cascade=true' : ''
      return fetchJson(`${BASE}/flow-control/queue/${id}${qs}`, { method: 'DELETE' })
    },

    batchDeleteQueueItems(ids: number[]): Promise<FlowControlBatchDeleteResponse> {
      return fetchJson(`${BASE}/flow-control/queue/batch-delete`, {
        method: 'POST',
        body: JSON.stringify({ ids }),
      })
    },

    deleteAllQueueItems(instanceId?: string, status?: string): Promise<FlowControlDeleteAllResponse> {
      const sp = new URLSearchParams()
      if (instanceId) sp.set('instance_id', instanceId)
      if (status) sp.set('status', status)
      const qs = sp.toString()
      return fetchJson(`${BASE}/flow-control/queue/all${qs ? `?${qs}` : ''}`, { method: 'DELETE' })
    },

    deleteFlow(flowId: string): Promise<FlowControlFlowDeleteResponse> {
      return fetchJson(`${BASE}/flow-control/flow/${encodeURIComponent(flowId)}`, { method: 'DELETE' })
    },
  },

  theta: {
    traceQuery(params: ThetaTraceQueryParams & { _ctoken?: string; _iamToken?: string; _cookies?: string }): Promise<{ success: boolean; code?: string; msg?: string; data: ThetaTraceLog[]; total?: number; pageNum?: number; pageSize?: number; pages?: number }> {
      return fetchJson(`${BASE}/theta/trace-query`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },

    asyncTraceQuery(params: ThetaTraceQueryParams & { _ctoken?: string; _iamToken?: string; _cookies?: string }): Promise<{ success: boolean; code?: string; msg?: string; data: ThetaTraceLog[]; total?: number; pageNum?: number; pageSize?: number; pages?: number }> {
      return fetchJson(`${BASE}/theta/async-trace-query`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },

    tokenList(_ctoken?: string, _iamToken?: string, _cookies?: string): Promise<{ success: boolean; code?: string; data: { keyList: ThetaApiKey[] } }> {
      return fetchJson(`${BASE}/theta/token-list`, {
        method: 'POST',
        body: JSON.stringify({ simpleMode: true, _ctoken, _iamToken, _cookies }),
      })
    },
  },

  bench: {
    domains(): Promise<BenchDomain[]> {
      return fetchJson<BenchDomain[]>(`${BASE}/bench/domains`)
    },

    createDomain(input: BenchDomainCreateInput): Promise<BenchDomain> {
      return fetchJson<BenchDomain>(`${BASE}/bench/domains`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    domain(ownerUserId: string, domainId: string): Promise<BenchDomain> {
      return fetchJson<BenchDomain>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}`)
    },

    updateDomain(ownerUserId: string, domainId: string, input: BenchDomainUpdateInput): Promise<BenchDomain> {
      return fetchJson<BenchDomain>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    archiveDomain(ownerUserId: string, domainId: string): Promise<BenchDomain> {
      return fetchJson<BenchDomain>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}`, {
        method: 'DELETE',
      })
    },

    templates(domainId?: string, params?: { ownerUserId?: string; status?: string }): Promise<BenchTemplate[]> {
      const sp = new URLSearchParams()
      if (params?.status) sp.set('status', params.status)
      const qs = sp.toString()
      const base = domainId
        ? `${BASE}/bench/domains/${encodeURIComponent(params?.ownerUserId ?? '')}/${encodeURIComponent(domainId)}/templates`
        : `${BASE}/bench/templates`
      return fetchJson<BenchTemplate[]>(`${base}${qs ? `?${qs}` : ''}`)
    },

    template(ownerUserId: string, domainId: string, templateName: string): Promise<BenchTemplate> {
      return fetchJson<BenchTemplate>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/templates/${encodeURIComponent(templateName)}`)
    },

    createTemplate(ownerUserId: string, domainId: string, input: BenchTemplateCreateInput): Promise<BenchTemplate> {
      return fetchJson<BenchTemplate>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/templates`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    updateTemplate(ownerUserId: string, domainId: string, templateName: string, input: BenchTemplateUpdateInput): Promise<BenchTemplate> {
      return fetchJson<BenchTemplate>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/templates/${encodeURIComponent(templateName)}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    archiveTemplate(ownerUserId: string, domainId: string, templateName: string): Promise<BenchTemplate> {
      return fetchJson<BenchTemplate>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/templates/${encodeURIComponent(templateName)}`, {
        method: 'DELETE',
      })
    },

    publishTemplate(ownerUserId: string, domainId: string, templateName: string, version?: number): Promise<BenchTemplate> {
      const qs = version !== undefined ? `?version=${version}` : ''
      return fetchJson<BenchTemplate>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/templates/${encodeURIComponent(templateName)}/publish${qs}`, {
        method: 'POST',
      })
    },

    batchPublishTemplates(ownerUserId: string, domainId: string, input: BenchBatchPublishInput): Promise<BenchBatchPublishResponse> {
      return fetchJson<BenchBatchPublishResponse>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/templates/batch-publish`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    uploadsScan(ownerUserId: string, domainId: string, files: File[]): Promise<BenchUploadScanResult> {
      const formData = new FormData()
      files.forEach((f) => formData.append('files', f))
      return fetchJson<BenchUploadScanResult>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/uploads/scan`, {
        method: 'POST',
        body: formData,
      })
    },

    createRun(input: BenchRunCreateInput): Promise<BenchRunCreateResponse> {
      return fetchJson<BenchRunCreateResponse>(`${BASE}/bench/runs`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    updateRun(benchRunId: string, input: BenchRunUpdateInput): Promise<BenchRun> {
      return fetchJson<BenchRun>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    run(benchRunId: string): Promise<BenchRun> {
      return fetchJson<BenchRun>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}`)
    },

    runs(params?: {
      ownerUserId?: string
      domainId?: string
      templateName?: string
      status?: string
      model?: string
      suite?: string
      scene?: string
      startedFrom?: number
      startedTo?: number
      limit?: number
      offset?: number
    }): Promise<BenchRunListResponse> {
      const sp = new URLSearchParams()
      if (params?.ownerUserId) sp.set('ownerUserId', params.ownerUserId)
      if (params?.domainId) sp.set('domainId', params.domainId)
      if (params?.templateName) sp.set('templateName', params.templateName)
      if (params?.status) sp.set('status', params.status)
      if (params?.model) sp.set('model', params.model)
      if (params?.suite) sp.set('suite', params.suite)
      if (params?.scene) sp.set('scene', params.scene)
      if (params?.startedFrom !== undefined) sp.set('startedFrom', String(params.startedFrom))
      if (params?.startedTo !== undefined) sp.set('startedTo', String(params.startedTo))
      if (params?.limit !== undefined) sp.set('limit', String(params.limit))
      if (params?.offset !== undefined) sp.set('offset', String(params.offset))
      const qs = sp.toString()
      return fetchJson<BenchRunListResponse>(`${BASE}/bench/runs${qs ? `?${qs}` : ''}`)
    },

    adminRuns(params?: {
      ownerUserId?: string
      domainId?: string
      templateName?: string
      status?: string
      model?: string
      suite?: string
      scene?: string
      startedFrom?: number
      startedTo?: number
      limit?: number
      offset?: number
    }): Promise<BenchRunListResponse> {
      const sp = new URLSearchParams()
      if (params?.ownerUserId) sp.set('ownerUserId', params.ownerUserId)
      if (params?.domainId) sp.set('domainId', params.domainId)
      if (params?.templateName) sp.set('templateName', params.templateName)
      if (params?.status) sp.set('status', params.status)
      if (params?.model) sp.set('model', params.model)
      if (params?.suite) sp.set('suite', params.suite)
      if (params?.scene) sp.set('scene', params.scene)
      if (params?.startedFrom !== undefined) sp.set('startedFrom', String(params.startedFrom))
      if (params?.startedTo !== undefined) sp.set('startedTo', String(params.startedTo))
      if (params?.limit !== undefined) sp.set('limit', String(params.limit))
      if (params?.offset !== undefined) sp.set('offset', String(params.offset))
      const qs = sp.toString()
      return fetchJson<BenchRunListResponse>(`${BASE}/bench/admin/runs${qs ? `?${qs}` : ''}`)
    },

    domainSummary(ownerUserId: string, domainId: string): Promise<BenchDomainSummary> {
      return fetchJson<BenchDomainSummary>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/summary`)
    },

    runsByTemplate(ownerUserId: string, domainId: string, templateName: string): Promise<BenchRun[]> {
      return fetchJson<BenchRun[]>(`${BASE}/bench/domains/${encodeURIComponent(ownerUserId)}/${encodeURIComponent(domainId)}/templates/${encodeURIComponent(templateName)}/runs`)
    },

    createResults(benchRunId: string, input: BenchTaskResultBatchInput): Promise<BenchTaskResultBatchResponse> {
      return fetchJson<BenchTaskResultBatchResponse>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}/results`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    results(benchRunId: string): Promise<BenchTaskResult[]> {
      return fetchJson<BenchTaskResult[]>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}/results`)
    },

    createArtifact(benchRunId: string, input: BenchArtifactCreateInput): Promise<BenchArtifact> {
      return fetchJson<BenchArtifact>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}/artifacts`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    artifacts(benchRunId: string, params?: { artifactType?: string; taskId?: string; includeContent?: boolean }): Promise<BenchArtifact[]> {
      const sp = new URLSearchParams()
      if (params?.artifactType) sp.set('artifactType', params.artifactType)
      if (params?.taskId) sp.set('taskId', params.taskId)
      if (params?.includeContent) sp.set('includeContent', 'true')
      const qs = sp.toString()
      return fetchJson<BenchArtifact[]>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}/artifacts${qs ? `?${qs}` : ''}`)
    },

    artifact(artifactId: string): Promise<BenchArtifact> {
      return fetchJson<BenchArtifact>(`${BASE}/bench/artifacts/${encodeURIComponent(artifactId)}`)
    },

    artifactContent(artifactId: string): Promise<string> {
      return fetchText(`${BASE}/bench/artifacts/${encodeURIComponent(artifactId)}/content`)
    },

    sessions(benchRunId: string): Promise<BenchSessionsResponse> {
      return fetchJson<BenchSessionsResponse>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}/sessions`).catch((err: unknown) => {
        if (err instanceof ApiError && (err.status === 404 || err.status === 503)) {
          return { benchRunId, sessions: [] }
        }
        throw err
      })
    },

    session(benchRunId: string, taskId: string): Promise<BenchSessionDetail> {
      return fetchJson<BenchSessionDetail>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}/sessions/${encodeURIComponent(taskId)}`)
    },

    sessionByArtifact(benchRunId: string, artifactId: string): Promise<BenchSessionDetail> {
      return fetchJson<BenchSessionDetail>(`${BASE}/bench/runs/${encodeURIComponent(benchRunId)}/sessions/artifacts/${encodeURIComponent(artifactId)}`)
    },

    admin: {
      summary(params?: BenchAdminFilters): Promise<BenchAdminSummary> {
        const sp = new URLSearchParams()
        appendBenchAdminFilters(sp, params)
        const qs = sp.toString()
        return fetchJson<BenchAdminSummary>(`${BASE}/bench/admin/summary${qs ? `?${qs}` : ''}`)
      },

      daily(params?: BenchAdminFilters): Promise<BenchAdminDailyResponse> {
        const sp = new URLSearchParams()
        appendBenchAdminFilters(sp, params)
        const qs = sp.toString()
        return fetchJson<BenchAdminDailyResponse>(`${BASE}/bench/admin/daily${qs ? `?${qs}` : ''}`)
      },

      samples(params?: BenchAdminFilters): Promise<BenchAdminSamplesResponse> {
        const sp = new URLSearchParams()
        appendBenchAdminFilters(sp, params)
        const qs = sp.toString()
        return fetchJson<BenchAdminSamplesResponse>(`${BASE}/bench/admin/samples${qs ? `?${qs}` : ''}`)
      },

      domains(params?: Pick<BenchAdminFilters, 'ownerUserId' | 'domainId' | 'tagId'>): Promise<BenchAdminDomainsResponse> {
        const sp = new URLSearchParams()
        appendBenchAdminFilters(sp, params)
        const qs = sp.toString()
        return fetchJson<BenchAdminDomainsResponse>(`${BASE}/bench/admin/domains${qs ? `?${qs}` : ''}`)
      },

      tags(includeArchived = false): Promise<BenchTag[]> {
        const qs = includeArchived ? '?includeArchived=true' : ''
        return fetchJson<BenchTag[]>(`${BASE}/bench/admin/tags${qs}`)
      },

      createTag(input: { tagId: string; name: string; description?: string | null }): Promise<BenchTag> {
        return fetchJson<BenchTag>(`${BASE}/bench/admin/tags`, {
          method: 'POST',
          body: JSON.stringify(input),
        })
      },

      updateTag(tagId: string, input: { name?: string; description?: string | null; status?: string }): Promise<BenchTag> {
        return fetchJson<BenchTag>(`${BASE}/bench/admin/tags/${encodeURIComponent(tagId)}`, {
          method: 'PUT',
          body: JSON.stringify(input),
        })
      },

      addDomainTags(input: { tagIds: string[]; domains: Array<{ ownerUserId: string; domainId: string }> }): Promise<{ affected: number }> {
        return fetchJson<{ affected: number }>(`${BASE}/bench/admin/domains/tags/batch`, {
          method: 'POST',
          body: JSON.stringify(input),
        })
      },

      removeDomainTags(input: { tagIds: string[]; domains: Array<{ ownerUserId: string; domainId: string }> }): Promise<{ affected: number }> {
        return fetchJson<{ affected: number }>(`${BASE}/bench/admin/domains/tags/batch`, {
          method: 'DELETE',
          body: JSON.stringify(input),
        })
      },

      exportTemplates(params?: BenchAdminFilters & { versionMode?: 'published' | 'latest' | 'all_versions' }): Promise<Blob> {
        const sp = new URLSearchParams()
        appendBenchAdminFilters(sp, params)
        if (params?.versionMode) sp.set('versionMode', params.versionMode)
        const qs = sp.toString()
        return fetchBlob(`${BASE}/bench/admin/templates/export${qs ? `?${qs}` : ''}`)
      },
    },
  },

  autoHeal: {
    /** Submit diagnosis request — returns immediately with diagnosisId for polling */
    diagnose(params: AutoHealDiagnosisRequest): Promise<AutoHealDiagnosisSubmitResult> {
      return fetchJson<AutoHealDiagnosisSubmitResult>(`${BASE}/auto-heal/diagnose`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },

    /** Poll diagnosis status — call repeatedly until status is 'completed' or 'failed' */
    pollDiagnosis(diagnosisId: string): Promise<AutoHealDiagnosisPollResult> {
      return fetchJson<AutoHealDiagnosisPollResult>(`${BASE}/auto-heal/diagnoses/${encodeURIComponent(diagnosisId)}`)
    },

    apply(params: AutoHealApplyRequest): Promise<AutoHealApplyResult> {
      return fetchJson<AutoHealApplyResult>(`${BASE}/auto-heal/apply`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },

    run(params: AutoHealRunRequest): Promise<AutoHealRunResult> {
      return fetchJson<AutoHealRunResult>(`${BASE}/auto-heal/run`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },
  },

  smartOnboarding: {
    /** Submit YAML generation request — returns immediately with generationId for polling */
    generate(params: SmartOnboardingGenerateRequest): Promise<SmartOnboardingGenerateResult> {
      return fetchJson<SmartOnboardingGenerateResult>(`${BASE}/smart-onboarding/generate`, {
        method: 'POST',
        body: JSON.stringify(params),
        // botId resolved server-side from smartOnboarding.defaultBotId / autoHeal.botId
      })
    },

    /** Poll generation status — call repeatedly until status is 'completed' or 'failed' */
    pollGeneration(generationId: string): Promise<SmartOnboardingGenerationStatus> {
      return fetchJson<SmartOnboardingGenerationStatus>(`${BASE}/smart-onboarding/generations/${encodeURIComponent(generationId)}`)
    },

    /** Trigger test run — sends YAML to bot for step */
    testRun(params: SmartOnboardingTestRunRequest): Promise<SmartOnboardingTestRunResult> {
      return fetchJson<SmartOnboardingTestRunResult>(`${BASE}/smart-onboarding/test-run`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },

    /** Server-side YAML structural validation */
    validate(params: SmartOnboardingValidateRequest): Promise<SmartOnboardingValidateResult> {
      return fetchJson<SmartOnboardingValidateResult>(`${BASE}/smart-onboarding/validate`, {
        method: 'POST',
        body: JSON.stringify(params),
      })
    },
  },

  // ── Dev Platform ──

  dima: {
    listWorkItems(params?: {
      itemType?: string
      status?: string
      processor?: string
      workspaceId?: string
      projectId?: string
      limit?: number
      offset?: number
    }): Promise<{ items: import('../types/dev-platform').DimaWorkItem[]; total: number }> {
      const sp = new URLSearchParams()
      if (params?.itemType) sp.set('itemType', params.itemType)
      if (params?.status) sp.set('status', params.status)
      if (params?.processor) sp.set('processor', params.processor)
      if (params?.workspaceId) sp.set('workspaceId', params.workspaceId)
      if (params?.projectId) sp.set('projectId', params.projectId)
      if (params?.limit) sp.set('limit', String(params.limit))
      if (params?.offset) sp.set('offset', String(params.offset))
      const qs = sp.toString()
      return fetchJson(`${BASE}/dima/work-items${qs ? `?${qs}` : ''}`)
    },

    createWorkItem(input: {
      itemType: string
      subject: string
      content: string
      priority?: string
      processor?: string
      workspaceId?: string
      projectId?: string
      triggerWorkflow?: boolean
      workflowTemplateId?: string
    }): Promise<{ dimaWorkItemId: string; workflowId: string | null }> {
      // Map frontend itemType ("Req"|"Bug"|"Task") to server type ("issue"|"bug"|"task")
      const typeMap: Record<string, string> = { Req: 'issue', Bug: 'bug', Task: 'task' }
      const body = {
        type: typeMap[input.itemType] ?? input.itemType.toLowerCase(),
        subject: input.subject,
        content: input.content,
        priority: input.priority,
        processor: input.processor,
        workspaceId: input.workspaceId,
        projectId: input.projectId,
        triggerWorkflow: input.triggerWorkflow,
        workflowTemplateId: input.workflowTemplateId,
      }
      return fetchJson(`${BASE}/dima/work-items`, {
        method: 'POST',
        body: JSON.stringify(body),
      })
    },

    sync(): Promise<{ synced: number }> {
      return fetchJson(`${BASE}/dima/sync`, { method: 'POST' })
    },

    linkWorkflow(workItemId: string, workflowId: string): Promise<{ ok: boolean }> {
      return fetchJson(`${BASE}/dima/work-items/${encodeURIComponent(workItemId)}/link-workflow`, {
        method: 'POST',
        body: JSON.stringify({ workflowId }),
      })
    },

    /** Fetch a single Dima work item by dima_id (includes content) */
    getWorkItem(workItemId: string): Promise<import('../types/dev-platform').DimaWorkItem> {
      return fetchJson(`${BASE}/dima/work-items/${encodeURIComponent(workItemId)}`)
    },
  },

  devWorkflowTemplates: {
    list(): Promise<import('../types/dev-platform').DevWorkflowTemplate[]> {
      return fetchJson(`${BASE}/dev-workflow-templates`)
    },

    get(templateId: string): Promise<import('../types/dev-platform').DevWorkflowTemplate> {
      return fetchJson(`${BASE}/dev-workflow-templates/${encodeURIComponent(templateId)}`)
    },

    create(input: import('../types/dev-platform').DevWorkflowTemplateCreateInput): Promise<import('../types/dev-platform').DevWorkflowTemplate> {
      return fetchJson(`${BASE}/dev-workflow-templates`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    update(templateId: string, input: import('../types/dev-platform').DevWorkflowTemplateUpdateInput): Promise<import('../types/dev-platform').DevWorkflowTemplate> {
      return fetchJson(`${BASE}/dev-workflow-templates/${encodeURIComponent(templateId)}`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    delete(templateId: string): Promise<{ ok: boolean }> {
      return fetchJson(`${BASE}/dev-workflow-templates/${encodeURIComponent(templateId)}`, {
        method: 'DELETE',
      })
    },
  },

  devWorkflows: {
    list(params?: {
      status?: string
      templateId?: string
      source?: string
      dimaWorkItemId?: string
      limit?: number
      offset?: number
    }): Promise<import('../types/dev-platform').DevWorkflowListResponse> {
      const sp = new URLSearchParams()
      if (params?.status) sp.set('status', params.status)
      if (params?.templateId) sp.set('templateId', params.templateId)
      if (params?.source) sp.set('source', params.source)
      if (params?.dimaWorkItemId) sp.set('dimaWorkItemId', params.dimaWorkItemId)
      if (params?.limit) sp.set('limit', String(params.limit))
      if (params?.offset) sp.set('offset', String(params.offset))
      const qs = sp.toString()
      return fetchJson(`${BASE}/dev-workflows${qs ? `?${qs}` : ''}`)
    },

    get(workflowId: string): Promise<import('../types/dev-platform').DevWorkflowDetail> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}`)
    },

    create(input: import('../types/dev-platform').CreateDevWorkflowInput): Promise<import('../types/dev-platform').DevWorkflow> {
      return fetchJson(`${BASE}/dev-workflows`, {
        method: 'POST',
        body: JSON.stringify(input),
      })
    },

    /** Update workflow fields (gitRepoUrl, gitBranch, projectConstraints, etc.) */
    update(workflowId: string, input: { gitRepoUrl?: string; gitBranch?: string; projectConstraints?: import('../types/dev-platform').ProjectConstraints }): Promise<{ workflowId: string; gitRepoUrl: string | null; gitBranch: string | null; projectConstraints: import('../types/dev-platform').ProjectConstraints | null }> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}`, {
        method: 'PATCH',
        body: JSON.stringify(input),
      })
    },

    start(workflowId: string): Promise<import('../types/dev-platform').DevWorkflow> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/start`, {
        method: 'POST',
      })
    },

    confirmPhase(workflowId: string, phaseId: string, comment?: string): Promise<import('../types/dev-platform').DevWorkflowPhase> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/confirm`, {
        method: 'POST',
        body: JSON.stringify({ comment }),
      })
    },

    rejectPhase(workflowId: string, phaseId: string, rejectToPhaseId?: string, reason?: string): Promise<import('../types/dev-platform').DevWorkflowPhase> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/reject`, {
        method: 'POST',
        body: JSON.stringify({ rejectToPhaseId, reason }),
      })
    },

    retryPhase(workflowId: string, phaseId: string): Promise<import('../types/dev-platform').DevWorkflowPhase> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/retry`, {
        method: 'POST',
      })
    },

    skipPhase(workflowId: string, phaseId: string): Promise<import('../types/dev-platform').DevWorkflowPhase> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/skip`, {
        method: 'POST',
      })
    },

    cancel(workflowId: string): Promise<import('../types/dev-platform').DevWorkflow> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/cancel`, {
        method: 'POST',
      })
    },

    /** Update a phase's runtime config (botId, promptTemplate) — P4 */
    updatePhaseConfig(workflowId: string, phaseId: string, input: import('../types/dev-platform').DevWorkflowPhaseConfigInput): Promise<import('../types/dev-platform').DevWorkflowDetail> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/config`, {
        method: 'PUT',
        body: JSON.stringify(input),
      })
    },

    /** Manually advance to the next phase — P5 explicit "进入下一阶段" */
    advancePhase(workflowId: string, phaseId: string, options?: { skipDeliverableCheck?: boolean; skipApprovalCheck?: boolean }): Promise<import('../types/dev-platform').DevWorkflowDetail> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/advance`, {
        method: 'POST',
        body: options ? JSON.stringify(options) : undefined,
      })
    },

    archive(workflowId: string, phaseId: string, title: string, content: string): Promise<{ documentUrl: string | null }> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/archive`, {
        method: 'POST',
        body: JSON.stringify({ title, content }),
      })
    },

    createPR(workflowId: string, files: Array<{ path: string; content: string }>, dimaWorkItemId?: string): Promise<{ prUrl: string; branchName: string }> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/create-pr`, {
        method: 'POST',
        body: JSON.stringify({ files, dimaWorkItemId }),
      })
    },

    /** Send a chat message to the bot for a specific dev-workflow phase via BaaS */
    chat(workflowId: string, phaseId: string, message: string): Promise<{ ok: boolean; messageId: string | null; sessionId: string | null; botId: string | null }> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/chat`, {
        method: 'POST',
        body: JSON.stringify({ message }),
      })
    },

    /** Poll BaaS for a bot response by messageId */
    pollMessage(workflowId: string, phaseId: string, messageId: string, botId?: string, sessionId?: string): Promise<{
      ok: boolean
      status: number
      data: {
        messageId: string
        sessionId: string | null
        messageStatus: string
        result: { content?: string } | null
      } | null
      errorCode: string | number | null
      errorMessage: string | null
    }> {
      return fetchJson(`${BASE}/dev-workflows/${encodeURIComponent(workflowId)}/phases/${encodeURIComponent(phaseId)}/poll-message`, {
        method: 'POST',
        body: JSON.stringify({ messageId, botId, sessionId }),
      })
    },
  },

  logAnalysis: {
    list(params?: {
      status?: string
      severity?: string
      limit?: number
      offset?: number
    }): Promise<import('../types/dev-platform').LogAnalysisListResponse> {
      const sp = new URLSearchParams()
      if (params?.status) sp.set('status', params.status)
      if (params?.severity) sp.set('severity', params.severity)
      if (params?.limit) sp.set('limit', String(params.limit))
      if (params?.offset) sp.set('offset', String(params.offset))
      const qs = sp.toString()
      return fetchJson(`${BASE}/log-analysis/results${qs ? `?${qs}` : ''}`)
    },

    trigger(params?: {
      lookbackMinutes?: number
      minSeverity?: string
      dryRun?: boolean
      autoCreateBugs?: boolean
      sources?: string[]
    }): Promise<import('../types/dev-platform').LogAnalysisTriggerResult> {
      return fetchJson(`${BASE}/log-analysis/trigger`, {
        method: 'POST',
        body: JSON.stringify(params ?? {}),
      })
    },

    getSources(): Promise<import('../types/dev-platform').LogAnalysisSourcesResponse> {
      return fetchJson(`${BASE}/log-analysis/sources`)
    },

    getRunStatus(runId: string): Promise<import('../types/dev-platform').LogAnalysisRunStatus> {
      return fetchJson(`${BASE}/log-analysis/runs/${encodeURIComponent(runId)}`)
    },

    createBug(analysisId: string): Promise<{ analysisId: string; dimaBugId: string; message: string }> {
      return fetchJson(`${BASE}/log-analysis/results/${encodeURIComponent(analysisId)}/create-bug`, {
        method: 'POST',
      })
    },

    ignore(analysisId: string): Promise<{ ok: boolean }> {
      return fetchJson(`${BASE}/log-analysis/results/${encodeURIComponent(analysisId)}/ignore`, {
        method: 'POST',
      })
    },
  },
}
