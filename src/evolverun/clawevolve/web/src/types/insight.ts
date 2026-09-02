export type CompletionState = 0 | 1 | 2 | 3

export type InsightMetricCounts = {
  totalTaskCount: number
  validTaskCount: number
  completeTaskCount: number
  capabilityTaskCount: number
  capabilityCompleteTaskCount: number
  autoCompleteTaskCount: number
}

export type InsightMetricRates = {
  completionRate: number | null
  capabilityCompletionRate: number | null
  autoCompletionRate: number | null
}

export type InsightVerificationStatus = 'NOT_STARTED' | 'PENDING' | 'STILL_PRESENT' | 'VERIFIED' | 'INSUFFICIENT_DATA'

export type InsightGovernanceEvent = {
  improvementId: number
  ownerUserId: string
  botId: string
  title: string
  sourceType: string
  sourceRuleId: string | null
  actionType: 'DIRECT_EVOLUTION' | 'ASSIGN_OWNER' | null
  status: string
  verificationStatus: InsightVerificationStatus
  effectiveAt: string
  observationEndAt: string | null
  observationDays: number
  appliedAt: string | null
  handledAt: string | null
  verificationLastCheckedAt: string | null
  resolvedSource: string | null
  rootCauseSummary: string | null
  suggestedAction: string | null
}

export type InsightFailureDistribution = {
  failureClass: string
  taskCount: number
  ratio: number
}

export type InsightBotComparison = InsightMetricCounts & InsightMetricRates & {
  ownerUserId?: string
  botId: string
  botName: string
}

export type InsightOverview = {
  contractVersion: string
  dataAsOf: string
  sourceBatchId: string
  scope: { userId: string; botId: string | null }
  counts: InsightMetricCounts
  rates: InsightMetricRates
  failureDistribution: InsightFailureDistribution[]
  botComparison: InsightBotComparison[]
}

export type InsightTrendPoint = InsightMetricCounts & InsightMetricRates & {
  date: string
  /** 管理员专属：全站所有用户/Bot 的失败任务数量。普通用户响应不返回。 */
  overallTaskCount?: number
  /** 管理员专属：近 30 天进入过改进项的 Bot 的能力失败数量。普通用户响应不返回。 */
  repairBotCapabilityFailureTaskCount?: number
  /** 当前用户或管理员当前筛选范围内，按验收完成日期计算的自动闭环解决率。 */
  autoClosureRate?: number | null
}

export type InsightTrend = {
  contractVersion: string
  dataAsOf: string
  sourceBatchId: string
  scope: { userId: string; botId: string | null }
  points: InsightTrendPoint[]
  governanceEvents: InsightGovernanceEvent[]
}

export type FailureTaskIndex = {
  sourceDt: string
  ownerUserId: string
  botId: string
  botName: string
  sessionId: string
  taskIndex: number
  taskDescription: string
  isComplete: CompletionState
  failureClass: string
  judgeReasonSummary: string | null
  sessionStartTime: string | null
  sessionEndTime: string | null
  sessionDurationSeconds: number | null
  isCron: boolean
  dataAsOf: string
}

export type FailureTaskPage = {
  contractVersion: string
  dataAsOf: string
  sourceBatchId: string
  items: FailureTaskIndex[]
  nextCursor: string | null
}

export type TimelineBlockSummary = {
  blockId: string
  kind: 'user_message' | 'assistant_message' | 'agent_execution' | 'judge_result'
  messageIndex: number | null
  role: string
  timestamp: string | number | null
  visibility: 'visible' | 'internal'
  title: string
  preview: string
  charCount: number
  expandable: boolean
}

export type TimelineBlockDetail = TimelineBlockSummary & {
  content: unknown
  raw: Record<string, unknown> | null
}

export type FailureSessionSummary = {
  sessionId: string
  userId: string
  botId: string
  botName: string
  startTime: string | null
  endTime: string | null
  durationSeconds: number | null
  isCron: boolean
  messageCount: number
}

export type FailureSessionTaskSummary = {
  taskIndex: number
  taskDescription: string
  messageRange: [number, number]
  isComplete: CompletionState
  failureClass: string
}

export type FailureTaskDetail = {
  contractVersion: string
  dataAsOf: string
  sourceBatchId: string
  task: FailureTaskIndex
  session: FailureSessionSummary
  sessionTasks: FailureSessionTaskSummary[]
  judge: Record<string, unknown> & {
    task_index: number
    task_description: string
    message_range: [number, number]
    is_complete: CompletionState
    reasoning?: string
    task_failure_class?: string
  }
  evidence: {
    schemaVersion: string
    batchId: string
    generatedAt: string
    etag: string
    versionId: string | null
  }
  timeline: {
    totalBlocks: number
    blocks: TimelineBlockSummary[]
  }
}

export type TimelinePage = {
  contractVersion: string
  dataAsOf: string
  sourceBatchId: string
  task: { sessionId: string; taskIndex: number; messageRange: [number, number] }
  items: Array<TimelineBlockSummary | TimelineBlockDetail>
  nextCursor: string | null
}

export type ImprovementEvidenceSnapshot = {
  sessionId: string
  taskIndex: number
  ordinal: number
  taskDescription: string
  failureClass: string
  reasoningSummary: string | null
  payloadRef: string
  payloadEtag: string
  payloadVersionId: string | null
}

export type ImprovementView = {
  improvementId: number
  ownerUserId: string
  botOwnerUserId: string
  botId: string
  title: string
  userGuidance: string | null
  sourceType: string
  sourceRuleId: string | null
  evidenceCount: number
  sessionCount: number
  dataStartTime: string | null
  dataEndTime: string | null
  dataAsOf: string
  batchId: string
  status: string
  actionType: 'DIRECT_EVOLUTION' | 'ASSIGN_OWNER' | null
  assignmentReason: string | null
  rootCauseSummary: string | null
  suggestedAction: string | null
  adminReviewStatus: 'PENDING' | 'APPROVED' | 'REJECTED' | 'TRUSTED'
  adminReviewedBy: string | null
  adminReviewedAt: number | string | null
  adminReviewComment: string | null
  rejectReasonCode: string | null
  rejectComment: string | null
  rejectedAt: number | string | null
  handledAt: number | string | null
  verificationStatus: 'NOT_STARTED' | 'PENDING' | 'STILL_PRESENT' | 'VERIFIED' | 'INSUFFICIENT_DATA'
  verificationLastCheckedAt: number | string | null
  verificationNewSessionCount: number
  verificationLastRecurrenceAt: string | null
  resolvedSource: string | null
  latestEvolveTaskId: string | null
  latestEvolveTaskStatus: string | null
  appliedEvolveTaskId: string | null
  appliedBy: string | null
  appliedAt: number | string | null
  version: number
  createdBy: string
  gmtCreate: number | string
  gmtModified: number | string
}

export type ImprovementEvolveLinkView = {
  evolveTaskId: string
  requestId: string
  taskStatus: string | null
  taskName: string | null
  gmtCreate: number | string
}

export type ImprovementDetail = ImprovementView & {
  evidence: ImprovementEvidenceSnapshot[]
  evolveLinks: ImprovementEvolveLinkView[]
}

export type ImprovementPage = {
  items: ImprovementView[]
  nextCursor: string | null
  statusCounts: {
    active: number
    inProgress: number
    resolved: number
    archived: number
  }
}

export type AdminImprovementPage = {
  items: ImprovementView[]
  nextCursor: string | null
  reviewCounts: {
    pending: number
    approved: number
    rejected: number
  }
}

export type AdminExecuteOnceResult = {
  taskId: string
  taskName: string
  status: string
  improvementId: number
  executionMode: 'ADMIN_ONCE'
  operatorUserId: string
  targetUserId: string
  targetBotId: string
  persistentAuthorization: false
  source: Record<string, unknown> | null
  steps: Array<Record<string, unknown>>
  idempotent?: boolean
}

export type ImprovementHandoff = {
  contractVersion: string
  improvement: {
    improvementId: number
    ownerUserId: string
    botOwnerUserId: string
    botId: string
    title: string
    userGuidance: string | null
    actionType: 'DIRECT_EVOLUTION' | 'ASSIGN_OWNER' | null
    adminReviewStatus: 'PENDING' | 'APPROVED' | 'REJECTED' | 'TRUSTED'
    sourceRuleId: string | null
    dataAsOf: string
    batchId: string
    evidenceCount: number
  }
  evidence: Array<ImprovementEvidenceSnapshot & { evidenceAccessUrl: string | null }>
  agentMarkdown: string
}

export type AutoRepairGrantView = {
  grantId: number
  ownerUserId: string
  botId: string
  environment: string
  sourceRuleId: string
  ruleVersion: number
  actionType: string
  allowedTargets: string[]
  risk: string
  status: 'ACTIVE' | 'REVOKED'
  sourceImprovementId: number
  grantedBy: string
  grantedAt: number | string
  revokedBy: string | null
  revokedAt: number | string | null
  version: number
  gmtCreate: number | string
  gmtModified: number | string
}

export type InsightScopeParams = {
  ownerUserId?: string
  botId?: string
  from?: string
  to?: string
  isCron?: boolean
}
