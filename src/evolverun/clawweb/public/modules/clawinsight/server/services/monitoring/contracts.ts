/** Service API: CV reports immutable events; the UI reads projections. No transport dependencies. */
export const DIAGNOSIS_VERSION = "claw-monitoring/diagnosis-event/v1";
export const CHECK_VERSION = "claw-monitoring/bot-check/v1";
export type Engine = "OC" | "TE";
export type Decision = "ALERT" | "PASS" | "UNRESOLVED";
export type CheckStatus = "HEALTHY" | "ERROR" | "UNKNOWN" | "PAUSED";
export type MonitoringBot = { botId: string; engine: Engine };
export type DiagnosisEvent = {
  schemaVersion: typeof DIAGNOSIS_VERSION;
  eventId: string;
  diagnosisId: string;
  botId: string;
  engine: Engine;
  sessionKey: string | null;
  sessionId: string | null;
  traceId: string | null;
  occurredAt: string | null;
  diagnosedAt: string;
  decision: Decision;
  tcFaultLabel: string | null;
  confidence: number | null;
  businessProblemCategory: string | null;
  businessProblemSubtype: string | null;
  systemDiagnosis: string | null;
  businessDiagnosis: string | null;
  handlerName: string | null;
  humanIntervention: boolean;
};
export type BotCheck = {
  schemaVersion: typeof CHECK_VERSION;
  botId: string;
  engine: Engine;
  checkedAt: string;
  lastSuccessfulCheckAt: string | null;
  status: CheckStatus;
};
export type DiagnosisItem = Omit<DiagnosisEvent, "schemaVersion" | "eventId" | "engine">;
export type DiagnosisQuery = {
  startMs: number | null;
  endMs: number | null;
  decision: Decision | "ALL";
  keyword: string; businessProblemCategory?: string; businessProblemSubtype?: string;
  page: number;
  pageSize: number;
};
export type DiagnosisCounts = { all: number; alert: number; pass: number; unresolved: number };
export type DiagnosisPage = {
  botId: string; page: number; pageSize: number; total: number; totalPages: number;
  problemTypes: { category: string; subtypes: string[] }[];
  counts: DiagnosisCounts; items: DiagnosisItem[];
};
/** Latest persisted check status, not monitor liveness; UNKNOWN if no check exists. */
export type BotStatus = {
  botId: string; status: CheckStatus; lastSuccessfulCheckAt: string | null; diagnosisCount: number;
};
export type DiagnosisAck = { accepted: true; stored: true; eventId: string; duplicate: boolean };
export type CheckAck = { accepted: true; botId: string; applied: boolean };
export type MonitoringTarget = { botId: string; entityId: string; env: string };
export type ResolvedReport<T> = { wire: T; target: MonitoringTarget };
export type MonitoringWindow = { startMs: number | null; endMs: number | null };
export type MonitoringSummary = {
  status: CheckStatus; checkedAt: string; lastSuccessfulCheckAt: string | null;
  diagnosedSessionCount: number | null; unidentifiedSessionDiagnosisCount: number;
  diagnosisCount: number; alertCount: number;
};
/** Plugin API: durable storage. Successful writes mean committed, never merely queued. */
export interface MonitoringStore {
  /** Targets with a persisted bot-check; diagnosis-only targets are excluded. */
  listCheckedTargets(): Promise<MonitoringTarget[]>;
  /** Distinct persisted bot IDs from both checks and diagnoses, ordered by botId. */
  listTargets(): Promise<MonitoringTarget[]>;
  summaries(targets: readonly MonitoringTarget[], window: MonitoringWindow): Promise<(MonitoringSummary | null)[]>;
  insertDiagnosis(event: ResolvedReport<DiagnosisEvent>, receivedAt: number): Promise<boolean>;
  applyCheck(check: ResolvedReport<BotCheck>, receivedAt: number): Promise<boolean>;
  readStatus(target: MonitoringTarget): Promise<{ check: BotCheck | null; count: number }>;
  listDiagnoses(target: MonitoringTarget, query: DiagnosisQuery): Promise<DiagnosisPage>;
}
export interface MonitoringApi {
  bots(): Promise<{ items: { botId: string }[] }>;
  reportDiagnosis(input: unknown, key: string | undefined): Promise<DiagnosisAck>;
  reportCheck(input: unknown): Promise<CheckAck>;
  status(botId: string): Promise<BotStatus>;
  diagnoses(botId: string, query: Record<string, unknown>): Promise<DiagnosisPage>;
}
export type MonitoringErrorCode = "INVALID_EVENT"
  | "TARGET_UNRESOLVED" | "TARGET_AMBIGUOUS" | "TARGET_BINDING_CONFLICT"
  | "UNAUTHENTICATED" | "FORBIDDEN" | "NOT_ENROLLED" | "ALREADY_ENROLLED" | "ENROLLMENT_NOT_IMPLEMENTED"
  | "EVENT_CONFLICT" | "PAYLOAD_TOO_LARGE" | "NOT_READY" | "BOT_NOT_FOUND";
export class MonitoringError extends Error {
  constructor(readonly code: MonitoringErrorCode, message: string) { super(message); }
}
