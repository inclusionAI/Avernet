/** @jest-environment jsdom */
import { logTaskPanelDiagnostic, traceTaskPanelRequest } from '@/services/tasks/panel/taskPanelDiagnostics';
const key = 'teamclaw:task-panel-debug';
const debugWindow = window as Window & { __taskPanelDebugLog?: string[] };
beforeEach(() => { localStorage.removeItem(key); delete debugWindow.__taskPanelDebugLog; jest.spyOn(console, 'info').mockImplementation(() => {}); });
afterEach(() => { localStorage.removeItem(key); delete debugWindow.__taskPanelDebugLog; jest.restoreAllMocks(); });
it('is silent and installs no request listener when diagnostics are disabled', () => {
  const signal = new AbortController().signal;
  const listener = jest.spyOn(signal, 'addEventListener');
  logTaskPanelDiagnostic('disabled');
  expect(traceTaskPanelRequest('/openapi/v1/collaboration/tasks/by_session?session_id=s1', signal)).toBeUndefined();
  expect(listener).not.toHaveBeenCalled(); expect(console.info).not.toHaveBeenCalled();
  expect(debugWindow.__taskPanelDebugLog).toBeUndefined();
});
it('correlates request cancellation with a stack while excluding unrelated query parameters', () => {
  localStorage.setItem(key, '1');
  const controller = new AbortController();
  const trace = traceTaskPanelRequest('/openapi/v1/collaboration/tasks/by_session?session_id=s1&extra=discarded-value&user_id=private-user', controller.signal)!;
  controller.abort(); trace.end(new DOMException('aborted', 'AbortError'));
  const records = debugWindow.__taskPanelDebugLog!.map(line => JSON.parse(line.replace('[task-panel-debug] ', '')));
  expect(records.map(record => record.event)).toEqual(['request.start', 'request.abort', 'request.end']);
  expect(new Set(records.map(record => record.requestId)).size).toBe(1);
  expect(records[1]).toEqual(expect.objectContaining({ sessionId: 's1', stack: expect.any(String) }));
  expect(records[2]).toEqual(expect.objectContaining({ aborted: true, errorName: 'AbortError' }));
  expect(debugWindow.__taskPanelDebugLog!.join('')).not.toMatch(/discarded-value|private-user/);
});
it('records HTTP completion and removes the abort listener after a successful request', () => {
  localStorage.setItem(key, '1');
  const controller = new AbortController();
  const trace = traceTaskPanelRequest('/api/v1/collaboration/tasks/list?user_id=private-user', controller.signal)!;
  trace.response(200); trace.end(); controller.abort();
  const lines = debugWindow.__taskPanelDebugLog!.join('');
  expect(lines).toContain('request.response'); expect(lines).toContain('"status":200');
  expect(lines).not.toContain('request.abort'); expect(lines).not.toContain('private-user');
});
it('ignores non-task endpoints and stays non-throwing when storage is unavailable', () => {
  localStorage.setItem(key, '1');
  expect(traceTaskPanelRequest('/openapi/v1/collaboration/messages/ws?extra=discarded-value', new AbortController().signal)).toBeUndefined();
  jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
  expect(() => logTaskPanelDiagnostic('blocked')).not.toThrow(); expect(console.info).not.toHaveBeenCalled();
});
it('keeps only the most recent 500 diagnostic records', () => {
  localStorage.setItem(key, '1');
  for (let index = 0; index < 505; index++) logTaskPanelDiagnostic('bounded', { index });
  expect(debugWindow.__taskPanelDebugLog).toHaveLength(500);
  expect(debugWindow.__taskPanelDebugLog![0]).toContain('"index":5');
});
