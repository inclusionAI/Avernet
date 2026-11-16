/** Opt-in investigation only. Remove after the duplicate-recovery cause is verified. */
const storageKey = 'teamclaw:task-panel-debug';
let sequence = 0;
let identifier = 0;
type DebugWindow = Window & { __taskPanelDebugLog?: string[] };
function enabled(): boolean {
  try { return typeof window !== 'undefined' && window.localStorage.getItem(storageKey) === '1'; }
  catch { return false; }
}
export function taskPanelDiagnosticId(prefix: string): string { return `${prefix}-${++identifier}`; }
export function logTaskPanelDiagnostic(event: string, details: Record<string, unknown> = {}, withStack = false): void {
  if (!enabled()) return;
  try {
    const record = { sequence: ++sequence, time: new Date().toISOString(), event,
      origin: window.location.origin, pagePath: window.location.pathname, routeSessionId: new URL(window.location.href).searchParams.get('session'), ...details,
      ...(withStack ? { stack: new Error('task-panel-debug').stack } : {}) };
    const line = `[task-panel-debug] ${JSON.stringify(record)}`;
    const target = window as DebugWindow;
    const records = target.__taskPanelDebugLog ?? (target.__taskPanelDebugLog = []);
    records.push(line);
    if (records.length > 500) records.splice(0, records.length - 500);
    console.info(line);
  } catch { /* Diagnostics must never change task behavior. */ }
}
export function traceTaskPanelRequest(path: string, signal: AbortSignal) {
  if (!enabled()) return undefined;
  let url: URL;
  try { url = new URL(path, window.location.href); } catch { return undefined; }
  if (!/^\/(?:openapi|api)\/v1\/collaboration\/tasks\/(?:by_session|list)$/.test(url.pathname)) return undefined;
  // Do not retain the raw URL, user query, credentials, response body or messages.
  const context = { requestId: taskPanelDiagnosticId('request'), apiPath: url.pathname, sessionId: url.searchParams.get('session_id') };
  const started = Date.now();
  logTaskPanelDiagnostic('request.start', { ...context, aborted: signal.aborted }, true);
  const abort = () => logTaskPanelDiagnostic('request.abort', { ...context, elapsedMs: Date.now() - started }, true);
  if (signal.aborted) abort();
  else signal.addEventListener('abort', abort, { once: true });
  return {
    response(status: number) { logTaskPanelDiagnostic('request.response', { ...context, status }); },
    end(error?: unknown) {
      signal.removeEventListener('abort', abort);
      logTaskPanelDiagnostic('request.end', { ...context, elapsedMs: Date.now() - started, aborted: signal.aborted,
        errorName: error instanceof Error ? error.name : error === null || error === undefined ? null : 'unknown' });
    },
  };
}
