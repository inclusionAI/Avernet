import { afterEach, expect, it, vi } from 'vitest';
import { performance } from 'node:perf_hooks';
import { logWorkflowBackground, startWorkflowBackgroundRun, withWorkflowBackgroundContext, workflowBackgroundError } from '../workflow-background-log.js';

afterEach(() => vi.restoreAllMocks());

it('retains the actual failure code and bounded redacted cause, not SQL or exception objects', () => {
  const error = Object.assign(new Error('Expected finite JSON data; password=example'), {
    code: 'INVALID_INPUT', sql: 'PRIVATE_SQL', params: ['PRIVATE_PARAMETER'],
  });
  const fields = workflowBackgroundError(error);
  expect(fields.errorCode).toBe('INVALID_INPUT');
  expect(fields.reason).toContain('Expected finite JSON data');
  expect(JSON.stringify(fields)).not.toContain('password=example');
  expect(JSON.stringify(fields)).not.toContain('PRIVATE_');
  expect(workflowBackgroundError(new TypeError('bad cursor'))).toEqual({ errorCode: 'TypeError', reason: 'bad cursor' });
  expect(workflowBackgroundError(new Error('x'.repeat(4096))).reason!.length).toBeLessThan(1100);
});

it('keeps concurrent task and trigger contexts separate across asynchronous Git calls', async () => {
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  await Promise.all(['a', 'b'].map(taskId => withWorkflowBackgroundContext({ taskId, runId: `run-${taskId}`, trigger: 'timer',
    revision: 2, executionId: `exec-${taskId}`, operation: 'result' }, async () => {
    await new Promise(resolve => setTimeout(resolve, 1));
    const end = startWorkflowBackgroundRun('workflow_repair_git', 'git_command', { op: 'fetch' });
    end('succeeded');
  })));
  const events = output.mock.calls.map(([line]) => JSON.parse(String(line).slice('[workflow-background] '.length)));
  expect(events).toHaveLength(4);
  for (const event of events) expect(event).toMatchObject({ runId: `run-${event.taskId}`, trigger: 'timer',
    revision: 2, executionId: `exec-${event.taskId}`, operation: 'result', op: 'fetch' });
  logWorkflowBackground('outside', 'configured');
  expect(JSON.parse(String(output.mock.calls.at(-1)![0]).slice('[workflow-background] '.length))).not.toHaveProperty('taskId');
});

it('correlates start and end with measured duration and instance identity', () => {
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  vi.spyOn(performance, 'now').mockReturnValueOnce(100).mockReturnValueOnce(2350);
  const end = startWorkflowBackgroundRun('repair_git', 'result_read', { op: 'fetch', workflowId: 'wf' });
  end('succeeded', { count: 1 });
  end('failed');
  const events = output.mock.calls.map(([line]) => JSON.parse(String(line).slice('[workflow-background] '.length)));
  expect(events).toHaveLength(2);
  expect(events[0]).toMatchObject({ event: 'start', job: 'repair_git', trigger: 'result_read', op: 'fetch', workflowId: 'wf' });
  expect(events[1]).toMatchObject({ event: 'end', runId: events[0].runId, elapsedMs: 2250, outcome: 'succeeded', count: 1 });
  expect(events[0].instance).toBeTruthy();
  expect(events[0].pid).toBe(process.pid);
});

it('does not serialize arbitrary errors, command arguments, tokens or repository URLs', () => {
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  logWorkflowBackground('repair_git', 'configured', {
    enabled: false, error: new Error('PRIVATE_ERROR'), token: 'test-token', args: ['PRIVATE_ARGUMENT'],
    remoteUrl: 'PRIVATE_REMOTE',
  } as never);
  const line = String(output.mock.calls[0][0]);
  expect(line).not.toContain('PRIVATE_');
  expect(line).not.toContain('test-token');
  expect(JSON.parse(line.slice('[workflow-background] '.length))).toMatchObject({ event: 'configured', enabled: false });
});

it('never changes business execution when the logging sink throws', () => {
  vi.spyOn(console, 'info').mockImplementation(() => { throw new Error('sink unavailable'); });
  expect(() => {
    const end = startWorkflowBackgroundRun('workflow_automation', 'timer');
    end('failed');
  }).not.toThrow();
});

it('retains recovery correlation metadata without introducing another logging format', () => {
  const output = vi.spyOn(console, 'info').mockImplementation(() => {});
  logWorkflowBackground('session_recovery_task', 'CREATE_FAILED', {
    environment: 'pre', targetEnvironment: 'prod', eventId: 'diagnosis-1', botId: 'default', count: 1,
  });
  expect(JSON.parse(String(output.mock.calls[0][0]).slice('[workflow-background] '.length))).toMatchObject({
    job: 'session_recovery_task', event: 'CREATE_FAILED', eventId: 'diagnosis-1', botId: 'default',
    environment: 'pre', targetEnvironment: 'prod', count: 1,
  });
});
