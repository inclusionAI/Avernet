import { randomUUID } from 'node:crypto';
import { hostname } from 'node:os';
import { performance } from 'node:perf_hooks';
import { getCurrentEnv } from '../env.js';
import { AsyncLocalStorage } from 'node:async_hooks';
import { redactText } from './redaction.js';

export type WorkflowBackgroundFields = {
  enabled?: boolean; intervalMs?: number; trigger?: string; outcome?: string; elapsedMs?: number;
  runId?: string; workflowId?: string; flowId?: string; taskId?: string; count?: number;
  policyCount?: number; enabledPolicyCount?: number; op?: string; reason?: string; environment?: string;
  operation?: string; revision?: number; attempt?: number; executionId?: string; jobId?: string;
  phase?: string; status?: string; errorCode?: string; spanId?: string; suppressed?: number; queueWaitMs?: number;
  eventId?: string; botId?: string; targetEnvironment?: string; stepId?: string; toolCallId?: string; retryAtMs?: number; stateVersion?: number;
};
const allowed = new Set(['enabled', 'intervalMs', 'trigger', 'outcome', 'elapsedMs', 'runId', 'workflowId',
  'flowId', 'taskId', 'count', 'policyCount', 'enabledPolicyCount', 'op', 'reason', 'environment',
  'operation', 'revision', 'attempt', 'executionId', 'jobId', 'phase', 'status', 'errorCode', 'spanId', 'suppressed', 'queueWaitMs',
  'eventId', 'botId', 'targetEnvironment', 'stepId', 'toolCallId', 'retryAtMs', 'stateVersion']);
const context = new AsyncLocalStorage<WorkflowBackgroundFields>();
/** Retain a useful cause without serializing SQL, parameters, provider payloads or credentials. */
export function workflowBackgroundError(error: unknown): Pick<WorkflowBackgroundFields, 'errorCode' | 'reason'> {
  const code = error && typeof error === 'object' && 'code' in error ? error.code : undefined;
  return {
    errorCode: typeof code === 'string' && /^[A-Za-z][A-Za-z0-9_]{0,63}$/.test(code) ? code
      : error instanceof Error ? error.name : 'UNKNOWN_ERROR',
    reason: redactText(error instanceof Error ? error.message : String(error), 1024),
  };
}
export function withWorkflowBackgroundContext<T>(fields: WorkflowBackgroundFields, action: () => T): T {
  return context.run({ runId: context.getStore()?.runId ?? randomUUID(), trigger: 'manual', ...context.getStore(), ...fields }, action);
}

/** Metadata only: never include raw errors, Git arguments/URLs, credentials or business payloads. */
export function logWorkflowBackground(job: string, event: string, fields: WorkflowBackgroundFields = {}): void {
  try {
    const safe = Object.fromEntries(Object.entries({ ...context.getStore(), ...fields }).filter(([key, value]) => allowed.has(key)
      && (typeof value === 'string' || typeof value === 'boolean' || typeof value === 'number' && Number.isFinite(value))));
    console.info('[workflow-background] ' + JSON.stringify({ ...safe, job, event,
      environment: fields.environment ?? context.getStore()?.environment ?? getCurrentEnv(), instance: hostname(), pid: process.pid, ts: new Date().toISOString() }));
  } catch { /* Observability must never change task execution or settlement. */ }
}

/** Log before work starts, including synchronous Git; a missing end can reveal a stuck operation. */
export function startWorkflowBackgroundRun(job: string, trigger: string, fields: WorkflowBackgroundFields = {}) {
  const inherited = context.getStore();
  const runId = fields.runId ?? inherited?.runId ?? randomUUID(), started = performance.now();
  const span = { ...fields, runId, trigger: inherited?.trigger ?? trigger, spanId: randomUUID() };
  logWorkflowBackground(job, 'start', span);
  let ended = false;
  return (outcome: 'succeeded' | 'failed' | 'skipped', result: WorkflowBackgroundFields = {}) => {
    if (ended) return;
    ended = true;
    logWorkflowBackground(job, 'end', { ...span, ...result, outcome,
      elapsedMs: Math.round((performance.now() - started) * 10) / 10 });
  };
}
