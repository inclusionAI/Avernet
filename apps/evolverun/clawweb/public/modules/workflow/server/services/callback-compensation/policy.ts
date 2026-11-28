import type { HttpCallbackConfigRow } from '../../repositories/http-callback-config-repository.js';
import type { RunSnapshot } from '../../repositories/callback-compensation-source.js';
import type { CompensationEvidence, EvidenceQuery, ReceiverDto } from './types.js';
import { hash } from './protection.js';

export function receiverDto(config: HttpCallbackConfigRow): ReceiverDto {
  let targetOrigin = 'invalid';
  try { targetOrigin = new URL(config.url).origin; } catch { /* Invalid targets remain non-executable. */ }
  return { configId: config.config_id, name: config.name, targetOrigin };
}
export function configHash(config: HttpCallbackConfigRow): string { return hash(config); }
export function configReasons(config: HttpCallbackConfigRow): string[] {
  const reasons: string[] = [];
  if (config.enabled !== 1) reasons.push('CONFIG_DISABLED');
  try {
    const events = JSON.parse(config.notify_on);
    if (!Array.isArray(events) || !events.includes('workflow_succeeded')) reasons.push('SUCCESS_EVENT_NOT_SUBSCRIBED');
  } catch { reasons.push('INVALID_SUBSCRIPTION'); }
  try {
    const url = new URL(config.url);
    if (url.protocol !== 'https:' || url.username || url.password || url.hash) reasons.push('UNSAFE_TARGET');
  } catch { reasons.push('UNSAFE_TARGET'); }
  return reasons;
}
export function runReasons(snapshot: RunSnapshot, environment: string, completedAt: number | null): string[] {
  const { run, nodes } = snapshot;
  const reasons: string[] = [];
  if (run.status !== 'succeeded') reasons.push('NOT_SUCCEEDED');
  if (snapshot.trial) reasons.push('TRIAL_RUN');
  if (run.env !== environment) reasons.push('RUN_ENVIRONMENT_UNVERIFIED');
  if (completedAt === null || !Number.isFinite(completedAt)) reasons.push('COMPLETION_TIME_MISSING');
  if (!Number.isSafeInteger(run.retry_epoch) || run.retry_epoch < 0) reasons.push('RETRY_EPOCH_UNVERIFIED');
  if (nodes.some(n => n.workflow_id !== run.workflow_id || n.flow_id !== run.flow_id)) reasons.push('NODE_OWNERSHIP_MISMATCH');
  for (const field of ['result_json', 'params_json', 'input_json']) {
    const value = run[field];
    if (field === 'result_json' && value === null) reasons.push('RESULT_MISSING');
    if (value !== null) {
      try { if (typeof value !== 'string') throw new Error('Not JSON text'); JSON.parse(value); }
      catch { reasons.push('INVALID_RUN_JSON'); }
    }
  }
  try {
    const state = run.state_json ? JSON.parse(run.state_json) : null;
    if (state && typeof state === 'object' && !Array.isArray(state) && state.flowId !== undefined && state.flowId !== run.flow_id) reasons.push('STATE_IDENTITY_MISMATCH');
    if (state && (typeof state !== 'object' || Array.isArray(state))) reasons.push('INVALID_STATE_JSON');
    if (state && (Object.hasOwn(state, 'fanoutOrigin') || Object.hasOwn(state, 'fanoutMap')
      || Object.hasOwn(state, '_fanout') || Object.hasOwn(state, 'subworkflowFanouts'))) reasons.push('FANOUT_REQUIRES_REVIEW');
  } catch { reasons.push('INVALID_STATE_JSON'); }
  return reasons;
}
function ref(value: unknown): boolean { return typeof value === 'string' && value.trim().length > 0 && value.length <= 2048; }
function time(value: unknown): number {
  if (typeof value !== 'string' || !/^\d{4}-\d\d-\d\dT.*(?:Z|[+-]\d\d:\d\d)$/.test(value)) return NaN;
  return Date.parse(value);
}
/** All proof fields are validated server-side; none can be supplied by create/preview inputs. */
export function evidenceReasons(e: CompensationEvidence | null, q: EvidenceQuery, config: HttpCallbackConfigRow,
  phase: unknown): string[] {
  if (!e) return ['TRUSTED_EVIDENCE_MISSING'];
  const reasons: string[] = [];
  const now = time(q.now); const completed = time(q.completedAt);
  if (e.environment !== q.environment || e.workflowId !== q.workflowId || e.flowId !== q.flowId
    || e.snapshotHash !== q.snapshotHash || e.retryEpoch !== q.retryEpoch) reasons.push('ATTESTATION_MISMATCH');
  if (!ref(e.receiverId) || !ref(e.generation) || !ref(e.generationEvidenceRef) || !ref(e.evidenceRef))
    reasons.push('GENERATION_OR_RECEIVER_UNVERIFIED');
  if (!(time(e.validFrom) <= now && time(e.validUntil) > now)) reasons.push('ATTESTATION_EXPIRED');
  if (e.url !== config.url || !Array.isArray(e.configIds) || !e.configIds.includes(config.config_id)
    || !Array.isArray(e.urls) || !e.urls.includes(e.url) || !e.configIds.length || !e.urls.length
    || e.configIds.length > 1000 || e.urls.length > 1000
    || !e.configIds.every(ref) || !e.urls.every(ref)) reasons.push('HISTORICAL_RECEIVER_UNVERIFIED');
  const route = e.historicalRoute;
  if (!route || !ref(route.evidenceRef) || !(time(route.validFrom) <= completed && time(route.validUntil) >= now))
    reasons.push('HISTORICAL_ROUTE_UNVERIFIED');
  const i = e.legacyIdempotency;
  if (!i || ![i.evidenceRef, i.keyContract, i.legacySenderContract, i.generationContract, i.lateArrivalContract].every(ref)
    || !(time(i.earliestCompletion) <= completed && completed <= time(i.latestCompletion)
      && time(i.lateArrivalUntil) >= time(e.validUntil) && time(i.retentionUntil) >= time(i.lateArrivalUntil)))
    reasons.push('LEGACY_LATE_RETENTION_IDEMPOTENCY_UNVERIFIED');
  if (typeof e.phaseNullable !== 'boolean' || (phase == null && e.phaseNullable !== true)) reasons.push('NULL_PHASE_NOT_APPROVED');
  return reasons;
}

/** Explicit callback projection; raw JSON strings remain byte-for-byte strings, no credentials. */
export function buildPayload(snapshot: RunSnapshot, includeOutput: boolean): unknown {
  const { env, retry_epoch, state_json, ...run } = snapshot.run;
  const nodes = snapshot.nodes.map(({ output_json, ...node }) => includeOutput ? { ...node, output_json } : node);
  return { workflow_id: run.workflow_id, flow_id: run.flow_id, status: 'succeeded',
    ext_info: { flow_runs: { ...run, credentials_json: null }, node_executions: nodes } };
}
/** Preview only structural information. Never display sensitive values, arbitrary keys or raw text. */
function shape(value: unknown, depth = 0): unknown {
  if (value === null) return null;
  if (depth > 4) return '[redacted]';
  if (typeof value === 'string') {
    try { return { type: 'json-string', structure: shape(JSON.parse(value), depth + 1) }; }
    catch { return '[redacted]'; }
  }
  if (Array.isArray(value)) return { type: 'array', length: value.length, elements: value.slice(0, 3).map(v => shape(v, depth + 1)) };
  if (typeof value === 'object') return { type: 'object', fieldCount: Object.keys(value).length,
    values: Object.values(value).slice(0, 10).map(v => shape(v, depth + 1)) };
  return `[${typeof value}]`;
}
export function redactedPayload(snapshot: RunSnapshot, includeOutput: boolean): unknown {
  return { workflow_id: snapshot.run.workflow_id, flow_id: snapshot.run.flow_id, status: snapshot.run.status,
    ext_info: { flow_runs: { current_phase: snapshot.run.current_phase == null ? null : '[redacted]',
      result_json: shape(snapshot.run.result_json), params_json: shape(snapshot.run.params_json),
      input_json: shape(snapshot.run.input_json), credentials_json: null },
    node_executions: snapshot.nodes.map(() => ({ input_json: '[redacted]', ...(includeOutput ? { output_json: '[redacted]' } : {}) })) } };
}
