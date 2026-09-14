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

export type OcbRequestIdentity = {
  authorization?: string;
  cookie?: string;
  userId: string;
};

/** OCB remains authoritative for space identity and live membership. */
export type OcbSpace = {
  id: string;
  name: string;
  type: "PERSONAL" | "TEAM";
  role: "ADMIN" | "MEMBER";
};

export type OcbSpacePort = {
  listAccessibleSpaces(input: { identity: OcbRequestIdentity }): Promise<OcbSpace[]>;
};

export type OcbLocalSkillSummary = {
  skillId: string;
  displayName: string;
  description?: string | null;
};

/** OCB owns live Local Skills and Bot metadata. Package snapshots are frozen;
 * display metadata is live and never changes the registering user's permissions.
 * Metadata fields/method remain optional for older injected providers (unknown => null).
 */
export type OcbLocalSkillPort = {
  /** Live display metadata only; never substitutes for the registering user's authorization. */
  getBotMetadata?(input: {
    botId: string;
    identity: OcbRequestIdentity;
  }): Promise<{ ownerId: string | null }>;
  listLocalSkills(input: {
    botId: string;
    identity: OcbRequestIdentity;
  }): Promise<OcbLocalSkillSummary[]>;
  exportLocalSkill(input: {
    botId: string;
    skillId: string;
    identity: OcbRequestIdentity;
  }): Promise<{ packageBytes: Buffer; sha256: string; displayName: string; description?: string | null }>;
  replaceLocalSkill(input: {
    botId: string;
    skillId: string;
    expectedSha256: string;
    packageBytes: Buffer;
    identity: OcbRequestIdentity;
  }): Promise<{ sha256: string }>;
};

export type ClawEvolveInternalApi = {
  createInsightTask(input: CreateInsightTaskInput): Promise<InsightTaskCreationResult>;
};
