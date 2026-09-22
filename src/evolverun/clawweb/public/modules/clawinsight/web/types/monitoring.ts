/** Read-only browser contract. Never import server runtime or reporting credentials here. */
export type Decision = 'ALERT' | 'PASS' | 'UNRESOLVED';
export type DiagnosisItem = {
  diagnosisId: string; botId: string; decision: Decision;
  occurredAt: string | null; diagnosedAt: string;
  sessionKey: string | null; sessionId: string | null; traceId: string | null;
  tcFaultLabel: string | null; confidence: number | null;
  businessProblemCategory: string | null; businessProblemSubtype: string | null;
  systemDiagnosis: string | null; businessDiagnosis: string | null;
  handlerName: string | null; humanIntervention: boolean;
};
export type DiagnosisPage = {
  botId: string; page: number; pageSize: number; total: number; totalPages: number;
  problemTypes: { category: string; subtypes: string[] }[];
  counts: { all: number; alert: number; pass: number; unresolved: number };
  items: DiagnosisItem[];
};
export type BotStatus = {
  botId: string; status: 'HEALTHY' | 'ERROR' | 'UNKNOWN' | 'PAUSED';
  lastSuccessfulCheckAt: string | null; diagnosisCount: number;
};
export type MonitoringQuery = {
  startDate: string; endDate: string; decision: Decision | 'ALL'; keyword: string; businessProblemCategory?: string; businessProblemSubtype?: string;
  page: number; pageSize: number;
};
export type BotScope = 'mine' | 'monitored' | 'all';
export type BotOption = {
  botRef: string; botName: string; botId: string; ownerId: string; env: string;
  enrollmentState: 'ENROLLED' | 'NOT_ENROLLED';
  monitoring: null | {
    status: BotStatus['status']; checkedAt: string; lastSuccessfulCheckAt: string | null;
    diagnosedSessionCount: number | null; unidentifiedSessionDiagnosisCount: number;
    diagnosisCount: number; alertCount: number;
  };
  capabilities: { canView: true; canRequestEnrollment: boolean };
};
export type BotOptionsPage = { items: BotOption[]; nextCursor: string | null };
