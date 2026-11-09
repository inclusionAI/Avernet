import type { WorkflowSpec } from './types.js'

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
  interventionReadOnly?: boolean
  sessionConfigured?: boolean
  originBotId: string | null
  originSessionKey: string | null
  originSessionId: string | null
  hasCredentials: boolean | null
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
  stepType: 'tool_call' | 'tool_result' | 'assistant_text' | 'progress' | 'script_progress'
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

export interface AutoHealSaveRequest {
  workflowId: string
  spec: WorkflowSpec
  packId?: string | null
  diagnosisId?: string | null
  note?: string | null
}

export interface AutoHealSaveResult {
  ok: boolean
  saved: boolean
  workflowId: string
  packId: string
}

export interface AutoHealDeployRequest {
  workflowId: string
  packId?: string | null
  diagnosisId?: string | null
  note?: string | null
  autoRun?: boolean
}

export interface AutoHealDeployResult {
  ok: boolean
  deployed: boolean
  workflowId: string
  packId: string
  deployNumber: number
  version: number
  tagName: string
  /** BaaS message id from the post-deploy re-run, not a ClawMind flow id. */
  messageId?: string | null
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
  /** BaaS message id for this delivery, not a ClawMind flow id. */
  messageId?: string | null
  message?: string | null
}

// ── Rerun ──────────────────────────────────────────────────

export interface RerunResult {
  ok: boolean
  flowId: string
  newFlowId: string | null
  sessionId: string | null
}

// ── Abort ──────────────────────────────────────────────────

export interface AbortResult {
  ok: boolean
  flowId: string
  status: string
  reconciledNodes: number
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

export type RunArchiveDiagnosis = {
  diagnosisId: string
  flowIds: string[]
  nodeId: string | null
  failureSignature: string
  failureMode: string
  severity: 'low' | 'medium' | 'high' | 'critical'
  reasoning: string
  evidenceEventIds: string[]
  suggestedFixSpec: string | null
  proposal: {
    schemaVersion: string
    workflowId: string
    baseSpecDigest: string | null
    summary: string
    operations: Array<{
      op: 'replace' | 'add' | 'remove'
      path: string
      value?: unknown
    }>
  } | null
}

export type RunArchiveAnalysis = {
  source: 'reused' | 'preview' | 'failed'
  status: 'completed' | 'failed' | 'skipped'
  analysisId: string | null
  error: string | null
  diagnoses: RunArchiveDiagnosis[]
  summary: string | null
}

export type RunArchiveSuggestedYaml = {
  yamlContent: string
  patchProposal: {
    schemaVersion: string
    workflowId: string
    operations: Array<{ op: string; path: string; value?: unknown }>
  } | null
  summary: string
  confidence: 'low' | 'medium' | 'high'
}

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
  // Part 3: AI diagnosis (optional — present when analysis is available)
  analysis?: RunArchiveAnalysis | null
  // Part 4: Suggested YAML (optional — derived from analysis proposals)
  suggestedYaml?: RunArchiveSuggestedYaml | null
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
  | 'hardening'
  | 'optimize'
  | 'apply'
  | 'full'
  | 'bench'
  | 'bench_optimize'
  | 'pack'
  | 'pack_restore'
  | 'runtime_cleanup'
  | 'repair'
