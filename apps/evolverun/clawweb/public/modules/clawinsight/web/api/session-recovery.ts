import { fetchJson } from '@avernet/clawweb-shared/web/api/client';
import type { SessionRecoverySummary, SessionRecoveryTask } from '../types/session-recovery';
const BASE = '/api/insight/v1/monitoring/session-recoveries';
export const sessionRecoveryApi = {
  toolCall: (taskId: string, toolCallId: string) => fetchJson<unknown>(`${BASE}/${encodeURIComponent(taskId)}/tool-calls/${encodeURIComponent(toolCallId)}`),
  list: (eventId: string, signal?: AbortSignal) => fetchJson<{ items: SessionRecoverySummary[] }>(`${BASE}?event_id=${encodeURIComponent(eventId)}`, { signal }),
  detail: (id: string, signal?: AbortSignal) => fetchJson<SessionRecoveryTask>(`${BASE}/${encodeURIComponent(id)}`, { signal }),
  setTaskShared: (id: string, shared: boolean) => fetchJson<SessionRecoveryTask>(`${BASE}/${encodeURIComponent(id)}/share`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ shared }),
  }),
  annotate: (id: string, input: { layer: 'delivery' | 'response' | 'effect'; correct: boolean | null; reason: string; evidence_ids: string[] }) => fetchJson<{ ok: true }>(`${BASE}/${encodeURIComponent(id)}/annotations`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(input),
  }),
  decide: (id: string, digest: string, approved: boolean, cooperationDone = false, feedback = "") => fetchJson<{ ok: true }>(`${BASE}/${encodeURIComponent(id)}/decision`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ plan_digest: digest, approved, cooperation_done: cooperationDone, feedback }),
  }),
};
