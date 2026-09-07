import type { EvolveStepRow, EvolveTaskRow } from "../repositories/evolve-repository.js";

// These process-local ports intentionally describe only the methods Clawevolve
// calls. Their implementations remain owned by ClawInsight.
export type InsightEvolveLink = {
  improvement_id: number;
  request_id: string;
  evolve_task_id: string;
};

export type InsightImprovementItem = {
  owner_user_id: string;
  bot_owner_user_id: string | null;
  bot_id: string;
  status: string;
};

export type InsightTaskSourceView = {
  sourceType: string;
  sourceId: string;
  schemaVersion: string;
  adapterVersion: string | null;
  status: string;
  digest: string | null;
  evidenceCount: number | null;
  error: { code: string | null; message: string | null; stage: string | null } | null;
  resolvedAt: number | string | null;
};

export type InsightApplyResult = {
  outcome: "APPLIED" | "IDEMPOTENT" | "NOT_FOUND" | "STATE_CONFLICT";
  currentStatus?: string;
};

export type InsightImprovementPort = {
  findEvolveLinkByRequest(improvementId: number, requestId: string): Promise<InsightEvolveLink | null>;
  findEvolveLinkByTaskId(taskId: string): Promise<InsightEvolveLink | null>;
  findItem(ownerUserId: string, improvementId: number): Promise<InsightImprovementItem | null>;
  linkEvolveTask(input: {
    improvementId: number;
    ownerUserId: string;
    evolveTaskId: string;
    requestId: string;
    createdBy: string;
  }): Promise<unknown>;
  resolveFromApply(input: {
    improvementId: number;
    applyTaskId: string;
    requestId: string;
    appliedBy: string;
  }): Promise<InsightApplyResult>;
};

export type InsightTaskSourcePort = {
  findView(taskId: string): Promise<InsightTaskSourceView | null>;
  markRuntimeFailure(taskId: string, code: string, message: string): Promise<void>;
  resolvePlanSource(taskId: string): Promise<unknown>;
};

export type CreateInsightTaskInput = {
  taskType: unknown;
  taskName: unknown;
  remark: unknown;
  userId: unknown;
  botId: unknown;
  botEnv?: unknown;
  improvementId: unknown;
  crossBotConfirmed: unknown;
  maxRounds: unknown;
  nodeCommandYamls: unknown;
  forceMessage: unknown;
  runtimeMaintenance?: unknown;
  openclawExecutionMode?: unknown;
  idempotencyKey: string;
  actorUserId: string | null;
  persistAutoRepairGrant?: unknown;
  authorizationGrantId?: unknown;
  createdByOverride?: string;
  adminOverrideOnce?: {
    operatorUserId: string;
    reason: string;
    repairDirection?: string | null;
  };
  autoExecuteAfterConsent?: unknown;
  adminConsentToken?: unknown;
  callbackUrl: (taskId: string, stepId: string) => string;
};

export type InsightTaskCreationResult = {
  task: EvolveTaskRow;
  steps: EvolveStepRow[];
  source: InsightTaskSourceView | null;
  idempotent: boolean;
  created: boolean;
};

export type InsightTaskCreatorPort = {
  create(input: CreateInsightTaskInput): Promise<InsightTaskCreationResult>;
};

export type ClawInsightInternalApi = {
  improvementRepository: InsightImprovementPort;
};

export type ClawEvolveInternalApi = {
  createInsightTask(input: CreateInsightTaskInput): Promise<InsightTaskCreationResult>;
};
