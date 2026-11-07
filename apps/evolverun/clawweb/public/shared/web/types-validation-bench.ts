import type { WorkflowSpec } from './types.js'

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
