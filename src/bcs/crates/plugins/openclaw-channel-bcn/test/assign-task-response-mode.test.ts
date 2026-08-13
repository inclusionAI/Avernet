import { strict as assert } from 'node:assert';
import {
  abortAllStreams,
  BCS_ASSIGN_TASK_TOOL_SCHEMA,
  handleAssignTask,
  rememberTaskToolSession,
} from '../src/inbound-handler.js';

describe('bcs_assign_task response_mode', () => {
  it('forwards response_mode to task.dispatch', async () => {
    abortAllStreams();
    const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
    const client = {
      connected: true,
      async sendRequest(method: string, params: Record<string, unknown>) {
        calls.push({ method, params });
        return { ok: true, payload: { task_id: 'task-1', status: 'dispatched' } };
      },
    };

    rememberTaskToolSession('session-manager', client as any, 'group-1:abcdef12', {
      session_id: 'group-1:abcdef12',
      participants: [ 'Manager(bot-manager)', 'Worker(bot-worker)' ],
      originator: 'Manager(bot-manager)',
      from: 'User',
      you_are_mentioned: true,
      is_sender: false,
      mentions: [ 'Manager' ],
      message: 'task',
      group_type: 'manager_worker',
      recipient_role: 'manager',
    });

    const result = await handleAssignTask('session-manager', {
      target_bot: 'Worker',
      message: 'do work',
      response_mode: 'full',
    });

    assert.deepEqual(result, { ok: true, task_id: 'task-1', status: 'dispatched' });
    assert.deepEqual(calls, [
      {
        method: 'task.dispatch',
        params: {
          group_id: 'group-1:abcdef12',
          target_bot: 'Worker',
          message: 'do work',
          response_mode: 'full',
        },
      },
    ]);
    assert.deepEqual(
      (BCS_ASSIGN_TASK_TOOL_SCHEMA.parameters.properties as any).response_mode.enum,
      [ 'after-last-tool-call', 'full' ],
    );
  });

  it('reuses an exact concurrent and recent dispatch receipt', async () => {
    abortAllStreams();
    const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
    let resolveFirst: ((value: { ok: boolean; payload: { task_id: string; status: string } }) => void) | undefined;
    const client = {
      connected: true,
      async sendRequest(method: string, params: Record<string, unknown>) {
        calls.push({ method, params });
        if (calls.length === 1) {
          return new Promise<{ ok: boolean; payload: { task_id: string; status: string } }>(resolve => {
            resolveFirst = resolve;
          });
        }
        return { ok: true, payload: { task_id: `task-${calls.length}`, status: 'dispatched' } };
      },
    };

    rememberTaskToolSession('session-dedupe', client as any, 'group-1:abcdef12', {
      session_id: 'group-1:abcdef12',
      participants: [ 'Manager(bot-manager)', 'Worker(bot-worker)' ],
      originator: 'Manager(bot-manager)',
      from: 'User',
      you_are_mentioned: true,
      is_sender: false,
      mentions: [ 'Manager' ],
      message: 'task',
      group_type: 'manager_worker',
      recipient_role: 'manager',
    });

    const params = { target_bot: 'Worker', message: 'do exact work', response_mode: 'full' };
    const first = handleAssignTask('session-dedupe', params);
    const concurrent = handleAssignTask('session-dedupe', params);
    assert.equal(calls.length, 1);
    resolveFirst?.({ ok: true, payload: { task_id: 'task-1', status: 'dispatched' } });
    assert.deepEqual(await Promise.all([ first, concurrent ]), [
      { ok: true, task_id: 'task-1', status: 'dispatched' },
      { ok: true, task_id: 'task-1', status: 'dispatched' },
    ]);

    assert.deepEqual(await handleAssignTask('session-dedupe', params), {
      ok: true,
      task_id: 'task-1',
      status: 'dispatched',
    });
    assert.equal(calls.length, 1);

    assert.deepEqual(await handleAssignTask('session-dedupe', {
      ...params,
      message: 'do revised work',
    }), {
      ok: true,
      task_id: 'task-2',
      status: 'dispatched',
    });
    assert.equal(calls.length, 2);
  });
});
