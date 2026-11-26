export type RecoveryStatus = 'running' | 'waiting_owner' | 'waiting_effect' | 'completed' | 'failed' | 'canceled';
export type RecoveryEffect = 'recovered' | 'not_recovered' | 'unknown' | 'not_intervened';
export type RecoveryPhase = 'assess' | 'observe' | 'plan' | 'apply' | 'verify';
export type RecoveryCompletionReason =
  | 'user_continued'
  | 'target_unavailable'
  | 'owner_handoff'
  | 'mcp_permission_required'
  | 'no_recovery_needed';

export interface RecoveryListItem {
  taskId: string;
  botId: string;
  entityId: string;
  env: string;
  sessionKey: string;
  status: RecoveryStatus;
  phase: RecoveryPhase;
  effect: RecoveryEffect;
  createdAt: number | string;
  startAtMs?: number;
  endAtMs?: number;
  overview?: string;
  completionReason?: RecoveryCompletionReason;
}

export interface RecoveryListPage {
  items: RecoveryListItem[];
  page: number;
  pageSize: number;
  total: number;
}

export interface RecoveryBotOption {
  botRef: string;
  botName: string;
  botId: string;
  ownerId: string;
  env: string;
  activeEngine?: string;
  botType?: string | null;
  canConfigure?: boolean;
  configuration?: { diagnosisEnabled: boolean; recoveryEnabled: boolean; version: number };
  configurationDisabledReason?: string;
}

export interface RecoveryBotOptionsPage {
  items: RecoveryBotOption[];
  nextCursor: string | null;
}

export interface RecoveryDiagnosis {
  diagnosisId: string;
  diagnosedAt?: string | null;
  decision?: 'ALERT' | 'PASS' | 'UNRESOLVED';
  businessProblemCategory?: string | null;
  handlerName?: string | null;
  businessDiagnosis?: string | null;
  systemDiagnosis?: string | null;
}

export interface RecoveryEvent {
  event_id: string;
  at_ms: number;
  kind: string;
  actor: 'service' | 'agent' | 'owner';
  summary: string;
  payload: Record<string, unknown>;
}

export interface RecoveryOwnerRequest {
  requestId: string;
  status: 'pending' | 'confirmed';
  summary: string;
  instructions: string;
  createdAtMs: number;
  feedback?: { text: string; atMs: number };
}

export interface RecoveryPlan {
  digest: string;
  approvedAt: number | null;
  summary: string;
  basis: string;
  scope: string;
  verification: string;
  actions: Array<{
    actionId: string;
    summary: string;
    risk: string;
    verification: string;
    rollback?: string | null;
    command?: string;
    operation?: { type: string; params?: Record<string, unknown> };
    engineOperation?: { method: string; path: string; body?: unknown };
  }>;
  owner_cooperation: Array<{ request: string; basis: string }>;
}

export interface RecoveryTask {
  taskId: string;
  status: RecoveryStatus;
  phase: RecoveryPhase;
  completionReason?: RecoveryCompletionReason;
  canOperate: boolean;
  simulation?: unknown;
  final_summary?: string;
  request: {
    target: { bot_id: string; entity_id: string; env: 'pre' | 'prod'; engine: 'OC' | 'CC' | 'TE' };
    session_key: string;
    event_id: string;
    diagnosis: string;
    tc_fault_label?: string | null;
  };
  diagnosis?: RecoveryDiagnosis | null;
  ownerRequest?: RecoveryOwnerRequest;
  plan: RecoveryPlan | null;
  judgment: {
    effect: RecoveryEffect;
    delivery?: string;
    response?: string;
    summary: string;
  } | null;
  termination?: { status: 'remote_stopped' | 'remote_stop_failed' | 'stop_pending' };
  timeline: RecoveryEvent[];
}
