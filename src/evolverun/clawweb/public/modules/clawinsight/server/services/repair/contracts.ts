import type { AisArtifactSpec } from "../ais-task-runner.js";

export const REPAIR_CONTRACT_VERSION = "ce-repair/v1";
export const LEGACY_REPAIR_PLAN_VERSION = "ce-repair-plan/v1";
export const REPAIR_PLAN_VERSION = "ce-repair-plan/v2";

export type RepairTargetEnvironment = "pre" | "prod";
export type RepairExecutionMode = "OWNER" | "ADMIN_ONCE";
export type RepairControlPlaneEnvironment = "pre" | "prod";
export type RepairPhase = "repair_plan" | "repair_apply";
export type RepairAgentMode = "openclaw" | "cfuse";
export type RepairCfuseEngine = "cfuse" | "claude-code";
export type RepairDiagnosticMode = "observe" | "deep";
/** Read compatibility only. New Repair executions must use RepairCfuseEngine. */
export type RepairPersistedCfuseEngine = RepairCfuseEngine | "codex";

export type RepairAuthorizationScope = {
  /** The authenticated subject that initiated this Repair run. */
  actorUserId: string;
  /** The Bot owner whose runtime is the target of the run. */
  ownerId: string;
  botId: string;
  environment: RepairTargetEnvironment;
  /**
   * OWNER keeps the existing owner-only path. ADMIN_ONCE is an explicit,
   * non-persistent administrator delegation for this one Repair task.
   * Optional for backwards-compatible reads of pre-existing task configs.
   */
  executionMode?: RepairExecutionMode;
};

export type RepairTarget = {
  environment: RepairTargetEnvironment;
  ownerId: string;
  botId: string;
  botType: "personal";
  botStatus: string | null;
  bindingId: string;
  bindingStatus: string | null;
  provider: string;
  deviceId: string;
  /** Present only for a directly reachable legacy ARCA binding. */
  sandboxId?: string;
  /** Raw ARCA PaaS instance id used to sign the proxypass target. */
  arcaInstanceId?: string;
  observedAt: string;
  source: "clawweb_runtime_catalog" | "ocb_backend_current";
};

export type RepairRuntimeTargetSnapshot = {
  version: number;
  fingerprint: string;
  target: RepairTarget;
  /** `ocb_context` is retained only for previously persisted snapshots. */
  reason: "task_created" | "browser_relay" | "ocb_context" | "before_action" | "after_action" | "resume";
};

export type RepairTimeRange = { from: number; to: number };

export type RepairInsightSource = {
  sourceType: "insight_improvement";
  improvementId: number;
  requestId: string;
  version: number;
  title: string;
  sourceBatchId: string;
  evidenceCount: number;
  sessionIds: string[];
  evidenceTaskRefs: Array<{ sessionId: string; taskIndex: number; ordinal: number }>;
  repairDirection: string | null;
  authorizationMode: "ONCE" | "PERSISTENT";
  authorizationGrantId?: number;
  adminOverride?: {
    operatorUserId: string;
    reason: string;
  };
};

export type RepairIssueInput = {
  symptom: string;
  traceId: string | null;
  relatedTaskId: string | null;
  errorText: string | null;
  timeRange: RepairTimeRange;
};

export type RepairStepArtifacts = Record<string, AisArtifactSpec>;

export type RepairStepContext = {
  stepId: string;
  stepNo: number;
  attempt: number;
  phase: RepairPhase;
  artifacts: RepairStepArtifacts;
};

export type RepairHistoryItem = {
  stepId: string;
  stepNo: number;
  attempt: number;
  phase: RepairPhase;
  status: string;
  artifactDigest: string | null;
  feedback: string | null;
};

/** A user-authored question or constraint that the current Plan must address. */
export type RepairInvestigationRequirement = {
  requirementId: string;
  source: "user_feedback";
  text: string;
  introducedBy: {
    stepId: string;
    stepNo: number;
    attempt: number;
    phase: RepairPhase;
  };
};

export type ApprovedRepairPlan = {
  stepId: string;
  artifactDigest: string;
  objectKey: string;
  approvedAt: string;
};

export type RepairPendingDecision = {
  kind: "approve_plan" | "reject_plan" | "accept_result" | "retry_result";
  requestedBy: string;
  requestedAt: string;
  artifactDigest: string | null;
  feedback: string | null;
};

export type RepairExecutionState = {
  executionId: string;
  ticketDigest: string;
  jobId: string | null;
  ccSessionId: string | null;
  state: "dispatching" | "running" | "waiting_decision" | "ended";
  stepId: string;
  phase: RepairPhase;
  leaseExpiresAt: number;
  decisionDeadlineAt: number | null;
  lastHeartbeatAt: number | null;
  invalidatedAt: number | null;
};

/** Persisted in ce_tasks.config_json. It must never contain credentials or Cookie. */
export type RepairTaskConfig = {
  schemaVersion: typeof REPAIR_CONTRACT_VERSION;
  taskId: string;
  /**
   * ClawWeb environment that created this task. Missing on legacy tasks and
   * recovered from their persisted publicBaseUrl.
   */
  controlPlaneEnvironment?: RepairControlPlaneEnvironment;
  /** Missing on tasks created before sharing support; absence means private. */
  shared?: boolean;
  issue: RepairIssueInput;
  authorizationScope: RepairAuthorizationScope;
  authorizationScopeDigest: string;
  target: RepairTarget;
  targetFingerprint: string;
  runtimeTarget: RepairRuntimeTargetSnapshot;
  runtimeTargetHistory: RepairRuntimeTargetSnapshot[];
  current: RepairStepContext;
  history: RepairHistoryItem[];
  /** Optional source handoff from Insight Center. Kept in the immutable task config. */
  insightSource?: RepairInsightSource;
  approvedPlan: ApprovedRepairPlan | null;
  pendingDecision: RepairPendingDecision | null;
  /** Missing on tasks created before diagnostic modes; absence means observe. */
  diagnosticMode?: RepairDiagnosticMode;
  agentMode: RepairAgentMode;
  llmUseDefault: boolean;
  llmModel: string | null;
  openclawUsesCustomApiKey: boolean;
  cfuseEngine: RepairPersistedCfuseEngine | null;
  cfuseModel: string | null;
  execution: RepairExecutionState;
  publicBaseUrl: string;
  artifacts: RepairStepArtifacts;
};

/** Process-local dispatch config. Never serialize this object to Task/Step/OSS. */
export type RepairDispatchConfig = RepairTaskConfig & {
  executionTicket: string;
  /** Process-local only. Never serialize to Task/Step/OSS or dispatch metadata. */
  llmApiKey?: string;
};

export type RepairAgentExecutionInput = {
  agentMode?: unknown;
  llmUseDefault?: unknown;
  llmModel?: unknown;
  llmApiKey?: unknown;
  cfuseEngine?: unknown;
  cfuseModel?: unknown;
};

export type RepairCreateTaskInput = RepairAgentExecutionInput & {
  ownerId?: unknown;
  diagnosticMode?: unknown;
  targetEnvironment?: unknown;
  botId?: unknown;
  taskName?: unknown;
  targetUserId?: unknown;
  adminOverrideReason?: unknown;
  crossBotConfirmed?: unknown;
  persistAutoRepairGrant?: unknown;
  authorizationGrantId?: unknown;
  adminConsentToken?: unknown;
  symptom?: unknown;
  repairDirection?: unknown;
  insightImprovementId?: unknown;
  insightRequestId?: unknown;
  traceId?: unknown;
  relatedTaskId?: unknown;
  errorText?: unknown;
  timeRange?: { from?: unknown; to?: unknown };
};

export type RepairDecisionInput = RepairAgentExecutionInput & {
  decision?: unknown;
  artifactDigest?: unknown;
  reason?: unknown;
};

export type RepairResumeInput = RepairAgentExecutionInput;

export type RepairTerminateInput = {
  reason?: unknown;
};

export type RepairCfuseLoginInput = {
  clientRequestId?: unknown;
  loginUrl?: unknown;
};

export type RepairCfuseAuthCodeInput = {
  authCode?: unknown;
};

export type RepairCfuseLoginReportInput = {
  status?: unknown;
  errorCode?: unknown;
  errorMessage?: unknown;
};

export type RepairPlanAction = {
  actionId: string;
  type: "container_command" | "ocb_operation";
  summary: string;
  risk: string;
  verification: string;
  rollback: string | null;
  dependsOn?: string[];
  rollbackActionId?: string | null;
  command?: string;
  operation?: { type: string; params?: Record<string, unknown> };
};

export type RepairPlanQuality = "verified" | "partially_verified" | "blocked" | "unknown";

export type RepairPlanRecommendation = {
  disposition: "execute_actions" | "no_change" | "insufficient_evidence";
  summary: string;
  reason: string;
  nextSteps?: string[];
};

type RepairPlanArtifactBase = {
  taskId: string;
  stepId: string;
  attempt: number;
  authorizationScopeDigest: string;
  runtimeTargetVersion: number;
  diagnosis: {
    facts: string[];
    inferences: string[];
    unknowns: string[];
  };
  actions: RepairPlanAction[];
};

export type LegacyRepairPlanArtifact = RepairPlanArtifactBase & {
  schemaVersion: typeof LEGACY_REPAIR_PLAN_VERSION;
};

export type RepairPlanArtifactV2 = RepairPlanArtifactBase & {
  schemaVersion: typeof REPAIR_PLAN_VERSION;
  recommendation: RepairPlanRecommendation;
  quality: RepairPlanQuality;
};

export type RepairPlanArtifact = LegacyRepairPlanArtifact | RepairPlanArtifactV2;

export type RepairExecutorOutput = {
  schemaVersion?: unknown;
  taskId?: unknown;
  stepId?: unknown;
  attempt?: unknown;
  phase?: unknown;
  artifactDigest?: unknown;
  artifacts?: unknown;
  summary?: unknown;
};

export type RepairWorkloadIdentity = {
  taskId: string;
  stepId: string;
  executionId: string;
  /**
   * The Step id present in the request path when decision/claim recovers a
   * response lost after the control-plane CAS already advanced the Step.
   * All authorization and service work remains scoped to stepId.
   */
  requestedStepId?: string;
};

export type RepairTaskContext = {
  schemaVersion: typeof REPAIR_CONTRACT_VERSION;
  taskId: string;
  stepId: string;
  attempt: number;
  phase: RepairPhase;
  issue: RepairIssueInput;
  authorizationScope: RepairAuthorizationScope;
  authorizationScopeDigest: string;
  target: RepairTarget;
  targetFingerprint: string;
  runtimeTargetVersion: number;
};

export type RepairLogIdentifier = "botId" | "ownerId" | "traceId" | "relatedTaskId" | "errorText";
export type RepairDiscoveredIdentifierKind = "bindingId" | "publishId" | "deviceUuid" | "sessionId" | "traceId" | "taskId";
export type RepairDiscoveredIdentifierCandidate = { kind: RepairDiscoveredIdentifierKind; value: string };
export type RepairDiscoveredIdentifier = RepairDiscoveredIdentifierCandidate & { evidenceId: string };

export type RepairLogSearchInput = {
  clientRequestId?: unknown;
  purpose?: unknown;
  identifiers?: RepairLogIdentifier[];
  discoveredIdentifiers?: RepairDiscoveredIdentifier[];
  sources?: string[];
  from?: number;
  to?: number;
  limit?: number;
};

export type RepairRuntimeInspectInput = ({ clientRequestId?: unknown; purpose?: unknown } & (
  | { operation: "fs_list"; path: string; maxEntries?: number }
  | { operation: "fs_find"; path: string; name: string; maxDepth?: number; maxEntries?: number }
  | { operation: "fs_stat"; path: string }
  | { operation: "fs_read"; path: string; startLine?: number; lines?: number }
  | { operation: "fs_search"; path: string; pattern: string; maxMatches?: number }
  | { operation: "process_list"; pattern?: string }
  | { operation: "port_list" }
  | { operation: "http_get"; port: number; path: string }
  | { operation: "shell_exec"; command: string }
));

export type RepairApplyActionInput = {
  clientRequestId?: unknown;
  purpose?: unknown;
  actionId?: unknown;
  retry?: unknown;
};

export type RepairOcbContextInput = {
  clientRequestId?: unknown;
  purpose?: unknown;
  actionId?: unknown;
  retry?: unknown;
};

export type RepairSemanticConclusionInput = {
  sourceToolCallId?: unknown;
  evidenceToolCallIds?: unknown;
  conclusionZh?: unknown;
  nextAction?: unknown;
};

export type RepairToolCallStatus =
  | "pending"
  | "executing"
  | "succeeded"
  | "failed"
  | "unknown"
  | "canceled";

export type RepairExecutionHeartbeatInput = {
  ccSessionId?: unknown;
};

export type RepairArtifactRefreshInput = {
  artifactName?: unknown;
};
