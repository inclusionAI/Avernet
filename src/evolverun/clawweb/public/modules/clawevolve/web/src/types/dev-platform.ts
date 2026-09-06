// ── Dev Platform Types ──────────────────────────────────────────────

/** Default DIMA workspace ID — used when no workspace is explicitly specified */
export const DIMA_DEFAULT_WORKSPACE_ID = 'W26001113566'

/** ClawFix log-analysis workspace ID — bugs created by log analysis go here */
export const DIMA_CLAWFIX_WORKSPACE_ID = 'W26001124452'

/** DIMA workspace tab definitions for the Task/Bug panel */
export const DIMA_WORKSPACE_TABS = [
  { key: 'default', label: '工作项', workspaceId: DIMA_DEFAULT_WORKSPACE_ID },
  { key: 'clawfix', label: 'ClawFix Bug', workspaceId: DIMA_CLAWFIX_WORKSPACE_ID },
] as const

// --- Dima Work Items ---

export interface DimaWorkItem {
  id?: number
  workItemId: string
  workspaceId: string | null
  projectId: string | null
  subject: string
  itemType: 'Req' | 'Bug' | 'Task'
  priority: 'urgent' | 'high' | 'medium' | 'low' | null
  status: string
  processor: string | null
  creator: string | null
  content: string | null
  relatedWorkflowId: string | null
  syncedAt: number | null
  gmtCreate: number | null
  gmtModified: number | null
}

export interface DimaWorkItemListParams {
  itemType?: string
  status?: string
  processor?: string
  workspaceId?: string
  projectId?: string
  limit?: number
  offset?: number
}

export interface DimaWorkItemListResponse {
  items: DimaWorkItem[]
  total: number
}

export interface DimaCreateItemInput {
  itemType: 'Req' | 'Bug' | 'Task'
  subject: string
  content: string
  priority?: 'urgent' | 'high' | 'medium' | 'low'
  processor?: string
  workspaceId?: string
  projectId?: string
  triggerWorkflow?: boolean
  workflowTemplateId?: string
}

// --- Dev Workflow Templates ---

export interface DevWorkflowTemplate {
  id: number
  templateId: string
  name: string
  description: string | null
  phasesJson: DevWorkflowPhaseDef[]
  isBuiltin: boolean
  enabled: boolean
  gmtCreate: number
  gmtModified: number
}

export interface DevWorkflowPhaseDef {
  phaseId: string
  name: string
  order: number
  isRequired: boolean
  executorType: 'embedded-agent' | 'human-wait'
  botPromptTemplate?: string
  botId?: string | null
  promptTemplate?: string | null
  approvalPolicy?: 'any' | 'all' | 'majority' | null
  approvers?: Array<{ empId: string; name?: string; role?: string }>
  gatePosition?: 'pre-bot' | 'post-bot' | null
  humanGateConfig?: {
    prompt: string
    confirmLabel: string
    rejectLabel: string
    rejectToPhaseId?: string
  }
}

export interface DevWorkflowTemplateCreateInput {
  templateId?: string
  name: string
  description?: string
  phases: DevWorkflowPhaseDef[]
}

export interface DevWorkflowTemplateUpdateInput {
  name?: string
  description?: string
  phases?: DevWorkflowPhaseDef[]
  enabled?: boolean
}

// --- Dev Workflows ---

export type DevWorkflowStatus =
  | 'created'
  | 'running'
  | 'completed'
  | 'cancelled'
  | 'failed'

export type DevPhaseStatus =
  | 'pending'
  | 'running'
  | 'waiting_confirm'
  | 'confirmed'
  | 'rejected'
  | 'skipped'
  | 'failed'

/** Project constraints stored in workflow configJson.projectConstraints */
export interface ProjectConstraints {
  techStack?: string
  codingStyle?: string
  apiDesign?: string
  testingRequirements?: string
  architecture?: string
  otherConstraints?: string
}

export interface DevWorkflow {
  id: number
  workflowId: string
  templateId: string
  templateName: string
  status: DevWorkflowStatus
  source: 'dima' | 'manual' | 'log_analysis'
  dimaWorkItemId: string | null
  dimaSubject: string | null
  enabledPhasesJson: string[]
  configJson: Record<string, unknown> | null
  projectConstraints: ProjectConstraints | null
  currentPhaseId: string | null
  resultSummary: string | null
  gitRepoUrl: string | null
  gitBranch: string | null
  gmtCreate: number
  gmtModified: number
}

export interface DevWorkflowPhase {
  id: number
  phaseId: string
  workflowId: string
  phaseName: string
  phaseOrder: number
  status: DevPhaseStatus
  isRequired: boolean
  resultSummary: string | null
  documentUrl: string | null
  documentTitle: string | null
  botSessionId: string | null
  humanComment: string | null
  rejectedFromPhaseId: string | null
  rejectReason: string | null
  /** Bound Bot ID for dispatching */
  botId: string | null
  /** Prompt template with {{variable}} syntax */
  promptTemplate: string | null
  /** Resolved prompt with variables replaced */
  promptResolved: string | null
  /** Approval policy: any | all | majority */
  approvalPolicy: string | null
  /** Approvers JSON string: [{empId, name, role}] */
  approversJson: string | null
  /** Confirmed-by JSON string: [{empId, name, confirmedAt}] */
  confirmedByJson: string | null
  /** Gate position: pre-bot (approve before Bot) or post-bot (approve after Bot) */
  gatePosition: string | null
  /** Computed: can user edit botId/prompt at runtime */
  canEdit: boolean
  /** Computed: can user manually advance to next phase */
  canAdvance: boolean
  startedAt: number | null
  completedAt: number | null
  gmtCreate: number
  gmtModified: number
}

export interface DevWorkflowDetail {
  workflow: DevWorkflow
  phases: DevWorkflowPhase[]
}

export interface DevWorkflowListParams {
  status?: string
  templateId?: string
  source?: string
  dimaWorkItemId?: string
  limit?: number
  offset?: number
}

export interface DevWorkflowListResponse {
  items: DevWorkflow[]
  total: number
}

export interface CreateDevWorkflowInput {
  templateId: string
  title?: string
  dimaWorkItemType?: string
  source?: 'dima' | 'manual' | 'log_analysis'
  dimaWorkItemId?: string
  enabledPhases?: string[]
  config?: Record<string, unknown>
}

export interface DevWorkflowPhaseActionInput {
  comment?: string
  rejectToPhaseId?: string
}

/** Input for updating a phase's runtime config (botId, promptTemplate) */
export interface DevWorkflowPhaseConfigInput {
  botId?: string
  promptTemplate?: string
}

// --- Log Analysis ---

export type LogAnalysisSeverity = 'critical' | 'high' | 'medium' | 'low'
export type LogAnalysisStatus = 'new' | 'analyzed' | 'bug_created' | 'bug_creating' | 'ignored'

export interface LogAnalysisResult {
  id: number
  analysisId: string
  errorPattern: string
  errorCount: number
  rootCause: string | null
  severity: LogAnalysisSeverity
  fixSuggestion: string | null
  estimatedChangedFiles: number | null
  isKnownIssue: boolean
  relatedBugId: string | null
  dimaBugId: string | null
  linkedWorkflowId: string | null
  status: LogAnalysisStatus
  logSource: string | null
  cooldownUntil: number | null
  gmtCreate: number
  gmtModified: number
}

export interface LogAnalysisListParams {
  status?: string
  severity?: string
  limit?: number
  offset?: number
}

export interface LogAnalysisListResponse {
  items: LogAnalysisResult[]
  total: number
}

export interface LogAnalysisTriggerInput {
  lookbackMinutes?: number
  minSeverity?: string
  dryRun?: boolean
  autoCreateBugs?: boolean
}

export interface LogAnalysisTriggerResult {
  /** Async trigger response: runId + status */
  runId: string
  status: string
  message?: string
  /** Populated after polling completes */
  patternsAnalyzed?: number
  bugsCreated?: number
  prsCreated?: number
  issuesSkipped?: number
  issuesPendingAction?: number
  errors?: string[]
  dryRun?: boolean
  /** True when MCP was unavailable and demo/mock data was used */
  demoMode?: boolean
  startedAt?: number | null
  completedAt?: number | null
}

export interface LogAnalysisStepProgress {
  key: string
  status: 'pending' | 'running' | 'completed' | 'failed'
  startedAt?: number
  completedAt?: number
  data?: Record<string, unknown> | null
}

export interface LogAnalysisRunStatus {
  runId: string
  status: 'running' | 'completed' | 'failed' | 'partial'
  startedAt: number | null
  completedAt: number | null
  issuesFound: number
  dimaCreated: number
  prsCreated: number
  skipped: number
  issuesPendingAction: number
  errors: string[]
  steps: LogAnalysisStepProgress[]
}

/** Log analysis source info (from GET /api/log-analysis/sources) */
export interface LogAnalysisSource {
  name: string
  region: string
  app: string
  defaultEnabled: boolean
}

/** Log analysis sources response */
export interface LogAnalysisSourcesResponse {
  collectorType: string
  sources: LogAnalysisSource[]
}

// --- Conversation Messages (for Bot conversation panel) ---

export interface ConversationMessage {
  id: string
  role: 'user' | 'bot' | 'system'
  content: string
  timestamp: number
  /** BaaS message_id for polling bot responses */
  messageId?: string
  /** Pending = waiting for bot response */
  pending?: boolean
  /** Phase ID this message relates to */
  phaseId?: string
}