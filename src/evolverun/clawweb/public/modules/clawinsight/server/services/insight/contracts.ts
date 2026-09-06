export const INSIGHT_CONTRACT_VERSION = "insight-serving/v1";
export const EVIDENCE_SCHEMA_VERSION = "session-evidence/v1";

export type CompletionState = 0 | 1 | 2 | 3;

export type InsightQueryScope = {
  userId: string;
  botId?: string;
  from?: string;
  to?: string;
  isCron?: boolean;
};

export type InsightMetricCounts = {
  totalTaskCount: number;
  validTaskCount: number;
  completeTaskCount: number;
  capabilityTaskCount: number;
  capabilityCompleteTaskCount: number;
  autoCompleteTaskCount: number;
};

export type InsightMetricRates = {
  completionRate: number | null;
  capabilityCompletionRate: number | null;
  autoCompletionRate: number | null;
};

export type InsightVerificationStatus =
  | "NOT_STARTED"
  | "PENDING"
  | "STILL_PRESENT"
  | "VERIFIED"
  | "INSUFFICIENT_DATA";

export type InsightGovernanceEvent = {
  improvementId: number;
  ownerUserId: string;
  botId: string;
  title: string;
  sourceType: string;
  sourceRuleId: string | null;
  actionType: "DIRECT_EVOLUTION" | "ASSIGN_OWNER" | null;
  status: string;
  verificationStatus: InsightVerificationStatus;
  effectiveAt: string;
  observationEndAt: string | null;
  observationDays: number;
  appliedAt: string | null;
  handledAt: string | null;
  verificationLastCheckedAt: string | null;
  resolvedSource: string | null;
  rootCauseSummary: string | null;
  suggestedAction: string | null;
};

export type InsightFailureDistribution = {
  failureClass: string;
  taskCount: number;
  ratio: number;
};

export type InsightBotComparison = InsightMetricCounts & InsightMetricRates & {
  ownerUserId?: string;
  botId: string;
  botName: string;
};

export type InsightOverview = {
  contractVersion: typeof INSIGHT_CONTRACT_VERSION;
  dataAsOf: string;
  sourceBatchId: string;
  scope: { userId: string; botId: string | null };
  counts: InsightMetricCounts;
  rates: InsightMetricRates;
  failureDistribution: InsightFailureDistribution[];
  botComparison: InsightBotComparison[];
};

export type InsightTrendPoint = InsightMetricCounts & InsightMetricRates & {
  date: string;
  /** 管理员专属：全站所有用户/Bot 的任务总量。普通用户响应不返回。 */
  overallTaskCount?: number;
  /** 管理员专属：近 30 天进入过改进项的 Bot 的能力失败数量。普通用户响应不返回。 */
  repairBotCapabilityFailureTaskCount?: number;
  /** 当前用户或管理员当前筛选范围内，按验收完成日期计算的自动闭环解决率。 */
  autoClosureRate?: number | null;
};

export type InsightAdminTrendMetrics = {
  overallTaskCountByDate: Record<string, number>;
  repairBotCapabilityFailureTaskCountByDate: Record<string, number>;
};

export type InsightTrend = {
  contractVersion: typeof INSIGHT_CONTRACT_VERSION;
  dataAsOf: string;
  sourceBatchId: string;
  scope: { userId: string; botId: string | null };
  points: InsightTrendPoint[];
  governanceEvents: InsightGovernanceEvent[];
};

export type FailureTaskIndex = {
  sourceDt: string;
  ownerUserId: string;
  botId: string;
  botName: string;
  sessionId: string;
  taskIndex: number;
  taskDescription: string;
  isComplete: CompletionState;
  failureClass: string;
  judgeReasonSummary: string | null;
  sessionStartTime: string | null;
  sessionEndTime: string | null;
  sessionDurationSeconds: number | null;
  isCron: boolean;
  payloadRef: string;
  payloadEtag: string;
  payloadVersionId: string | null;
  judgedAt: string | null;
  batchId: string;
  dataAsOf: string;
};

export type FailureTaskQuery = InsightQueryScope & {
  failureClass?: string;
  completionStates?: CompletionState[];
  cursor?: string;
  pageSize: number;
};

export type FailureTaskPage = {
  contractVersion: typeof INSIGHT_CONTRACT_VERSION;
  dataAsOf: string;
  sourceBatchId: string;
  items: FailureTaskIndex[];
  nextCursor: string | null;
};

export type EvidenceMessage = Record<string, unknown> & {
  message_index: number;
  role: string;
  timestamp: string | number | null;
  visibility: "visible" | "internal";
  content: unknown;
  raw: Record<string, unknown>;
};

export type EvidenceTask = Record<string, unknown> & {
  task_index: number;
  task_description: string;
  message_range: [number, number];
  is_complete: CompletionState;
  reasoning?: string;
  task_failure_class?: string;
};

export type SessionEvidence = Record<string, unknown> & {
  schema_version: typeof EVIDENCE_SCHEMA_VERSION;
  batch_id: string;
  dt: string;
  user_id: string;
  bot_id: string;
  session_id: string;
  session: Record<string, unknown>;
  messages: EvidenceMessage[];
  tasks: EvidenceTask[];
  judge_meta: Record<string, unknown>;
  generated_at: string;
};

export type TimelineBlockSummary = {
  blockId: string;
  kind: "user_message" | "assistant_message" | "agent_execution" | "judge_result";
  messageIndex: number | null;
  role: string;
  timestamp: string | number | null;
  visibility: "visible" | "internal";
  title: string;
  preview: string;
  charCount: number;
  expandable: boolean;
};

export type TimelineBlockDetail = TimelineBlockSummary & {
  content: unknown;
  raw: Record<string, unknown> | null;
};

export type FailureSessionSummary = {
  sessionId: string;
  userId: string;
  botId: string;
  botName: string;
  startTime: string | null;
  endTime: string | null;
  durationSeconds: number | null;
  isCron: boolean;
  messageCount: number;
};

export type FailureSessionTaskSummary = {
  taskIndex: number;
  taskDescription: string;
  messageRange: [number, number];
  isComplete: CompletionState;
  failureClass: string;
};

export type FailureTaskDetail = {
  contractVersion: typeof INSIGHT_CONTRACT_VERSION;
  dataAsOf: string;
  sourceBatchId: string;
  task: FailureTaskIndex;
  session: FailureSessionSummary;
  sessionTasks: FailureSessionTaskSummary[];
  judge: EvidenceTask;
  evidence: {
    schemaVersion: string;
    batchId: string;
    generatedAt: string;
    etag: string;
    versionId: string | null;
  };
  timeline: {
    totalBlocks: number;
    blocks: TimelineBlockSummary[];
  };
};

export type TimelinePage = {
  contractVersion: typeof INSIGHT_CONTRACT_VERSION;
  dataAsOf: string;
  sourceBatchId: string;
  task: { sessionId: string; taskIndex: number; messageRange: [number, number] };
  items: TimelineBlockSummary[] | TimelineBlockDetail[];
  nextCursor: string | null;
};

export type ImprovementEvidenceSnapshot = {
  sessionId: string;
  taskIndex: number;
  ordinal: number;
  taskDescription: string;
  failureClass: string;
  reasoningSummary: string | null;
  payloadRef: string;
  payloadEtag: string;
  payloadVersionId: string | null;
};

export type ImprovementView = {
  improvementId: number;
  ownerUserId: string;
  botOwnerUserId: string;
  botId: string;
  title: string;
  userGuidance: string | null;
  sourceType: string;
  sourceRuleId: string | null;
  evidenceCount: number;
  sessionCount: number;
  dataStartTime: string | null;
  dataEndTime: string | null;
  dataAsOf: string;
  batchId: string;
  status: string;
  actionType: "DIRECT_EVOLUTION" | "ASSIGN_OWNER" | null;
  assignmentReason: string | null;
  rootCauseSummary: string | null;
  suggestedAction: string | null;
  adminReviewStatus: "PENDING" | "APPROVED" | "REJECTED" | "TRUSTED";
  adminReviewedBy: string | null;
  adminReviewedAt: number | string | null;
  adminReviewComment: string | null;
  rejectReasonCode: string | null;
  rejectComment: string | null;
  rejectedAt: number | string | null;
  handledAt: number | string | null;
  verificationStatus: "NOT_STARTED" | "PENDING" | "STILL_PRESENT" | "VERIFIED" | "INSUFFICIENT_DATA";
  verificationLastCheckedAt: number | string | null;
  verificationNewSessionCount: number;
  verificationLastRecurrenceAt: string | null;
  resolvedSource: string | null;
  latestEvolveTaskId: string | null;
  latestEvolveTaskStatus: string | null;
  appliedEvolveTaskId: string | null;
  appliedBy: string | null;
  appliedAt: number | string | null;
  version: number;
  createdBy: string;
  gmtCreate: number | string;
  gmtModified: number | string;
};

export type ImprovementEvolveLinkView = {
  evolveTaskId: string;
  requestId: string;
  taskStatus: string | null;
  taskName: string | null;
  gmtCreate: number | string;
};

export type ImprovementDetail = ImprovementView & {
  evidence: ImprovementEvidenceSnapshot[];
  evolveLinks: ImprovementEvolveLinkView[];
};

export type GovernanceRule = {
  ruleId: string;
  version: number;
  enabled: boolean;
  scope: Record<string, unknown>;
  matcher: Record<string, unknown>;
  actionType: "DIRECT_EVOLUTION" | "ASSIGN_OWNER";
  allowedTargets: string[];
  risk: "low" | "medium" | "high";
  adminPolicy: {
    mode: "REVIEW" | "TRUSTED";
    trustedAfterApprovals?: number;
  };
  verification?: Record<string, unknown>;
  learnedFixes?: Array<{
    summary: string;
    sourceImprovementId?: number;
    verifiedAt?: string;
  }>;
};

export type GovernanceRuleDocument = {
  schemaVersion: "insight-governance-rules/v1";
  environment: string;
  version: number;
  updatedAt: string;
  rules: GovernanceRule[];
};
