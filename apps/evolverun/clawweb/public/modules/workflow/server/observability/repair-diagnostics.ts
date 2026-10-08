import { AsyncLocalStorage } from 'node:async_hooks';
import { randomUUID } from 'node:crypto';
import { hostname } from 'node:os';

type Access = {
  workflowId: string; mode: 'view' | 'edit'; actorId: string | null;
  reason: 'MISSING_IDENTITY' | 'INSUFFICIENT_EDIT_PERMISSION' | 'INSUFFICIENT_VIEW_SCOPE' | 'ALLOWED' | 'HOST_DENIED';
  viewScope?: 'all' | 'deny' | 'limited';
};
type Diagnostic = {
  requestId: string; instance: string; startedAt: number; stagesMs: Record<string, number>; access?: Access;
};
const requests = new AsyncLocalStorage<Diagnostic>();

/** Server-generated correlation only. Never capture headers, query strings or payloads. */
export function repairRequestDiagnostic<T>(run: (context: Diagnostic) => T): T {
  const context: Diagnostic = { requestId: randomUUID(), instance: hostname(), startedAt: performance.now(), stagesMs: {} };
  return requests.run(context, () => run(context));
}
export function recordRepairAccess(access: Access, fallback = false): void {
  const context = requests.getStore();
  if (context && !(fallback && context.access)) context.access = access;
}
export async function repairStage<T>(name: string, run: () => Promise<T>): Promise<T> {
  const context = requests.getStore();
  if (!context) return run();
  const start = performance.now();
  try { return await run(); }
  finally { context.stagesMs[name] = (context.stagesMs[name] ?? 0) + Math.round(performance.now() - start); }
}
export function finishRepairDiagnostic(context: Diagnostic, method: string, route: string, status: number): void {
  const record = { requestId: context.requestId, instance: context.instance, method, route, status,
    ...context.access, elapsedMs: Math.round(performance.now() - context.startedAt), stagesMs: context.stagesMs };
  if (status >= 400) console.warn('[workflow-repair] request diagnostic', JSON.stringify(record));
  else console.info('[workflow-repair] request diagnostic', JSON.stringify(record));
}
