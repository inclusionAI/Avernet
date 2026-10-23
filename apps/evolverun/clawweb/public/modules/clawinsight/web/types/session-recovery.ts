export type SessionRecoveryTermination = { status: 'remote_stopped' | 'remote_stop_failed' | 'stop_pending'; aisJobId: string | null };
export type SessionRecoveryTask = {
  taskId: string;
  shared: boolean;
  canOperate: boolean;
  canManageShare: boolean;
  level: 'debug' | 'approval' | 'automatic';
  status: 'running' | 'waiting_owner' | 'waiting_effect' | 'completed' | 'failed' | 'canceled';
  completionReason?: 'user_continued';
  phase: 'assess' | 'observe' | 'plan' | 'apply' | 'verify';
  request: { diagnosis: string; error_location?: string; target: { bot_id: string; entity_id: string; env: 'pre' | 'prod'; engine: 'OC' | 'CC' | 'TE' }; session_key: string; session_id?: string; event_id: string; delivery_key: string; tc_fault_label: string | null };
  plan: null | {
    digest: string; approvedAt: number | null;
    summary: string; basis: string; scope: string; verification: string;
    actions: Array<{ actionId: string; summary: string; risk: string; verification: string; command?: string; engineOperation?: { method: string; path: string; body?: unknown; upload?: { targetPath: string; filename?: string; content: string } }; rollback?: string | null; operation?: { type: string; params?: Record<string, unknown> } }>;
    owner_cooperation: Array<{ request: string; basis: string }>;
  };
  judgment: null | { delivery?: 'confirmed' | 'unconfirmed'; response?: 'acted' | 'replied' | 'none' | 'unknown'; effect: 'recovered' | 'not_recovered' | 'unknown' | 'not_intervened'; summary: string; evidence_ids: string[] };
  evidence: Array<{ evidence_id: string; tool_call_id: string; source: string; location: string; excerpt: string }>;
  timeline: Array<{ event_id: string; at_ms: number; kind: string; actor: 'service' | 'agent' | 'owner'; summary: string; payload: Record<string, unknown> }>;
  executions?: Array<{ executionId: string; attempt: number; status: string; jobId: string | null; jobUrl: string | null; termination?: SessionRecoveryTermination }>;
  termination?: SessionRecoveryTermination;
};
export type SessionRecoverySummary = { task_id: string; status: SessionRecoveryTask['status']; phase: SessionRecoveryTask['phase']; effect: string; completion_reason?: 'user_continued' };
