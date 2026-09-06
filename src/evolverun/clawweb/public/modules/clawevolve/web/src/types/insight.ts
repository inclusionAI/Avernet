export type CompletionState = 0 | 1 | 2 | 3

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
