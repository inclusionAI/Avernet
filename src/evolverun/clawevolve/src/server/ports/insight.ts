export const EVIDENCE_SCHEMA_VERSION = "session-evidence/v1" as const;

export type CompletionState = 0 | 1 | 2 | 3;

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

/** Minimal handoff contract consumed by Clawevolve, not the full Insight view. */
export type ImprovementDetail = {
  improvementId: number;
  ownerUserId: string;
  botOwnerUserId: string;
  botId: string;
  title: string;
  userGuidance: string | null;
  sourceType: string;
  sourceRuleId: string | null;
  evidenceCount: number;
  dataStartTime: string | null;
  dataEndTime: string | null;
  dataAsOf: string;
  batchId: string;
  version: number;
  createdBy: string;
  gmtCreate: number | string;
  gmtModified: number | string;
  evidence: ImprovementEvidenceSnapshot[];
};
