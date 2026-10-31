import { strict as assert } from 'node:assert';
import {
  abortAllStreams,
  BCS_ASSIGN_TASK_TOOL_SCHEMA,
  handleAssignTask,
  handleTaskControl,
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
        return { ok: true, payload: { task_id: '1234abcd-0000-0000-0000-000000000000', status: 'dispatched' } };
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
      timeout_s: 60,
    });

    assert.deepEqual(result, { ok: true, task_ref: 'T-1234abcd', status: 'dispatched' });
    assert.deepEqual(calls, [
      {
        method: 'task.dispatch',
        params: {
          group_id: 'group-1:abcdef12',
          target_bot: 'Worker',
          message: 'do work',
          response_mode: 'full',
          timeout_s: 60,
        },
      },
    ]);
    assert.deepEqual(
      (BCS_ASSIGN_TASK_TOOL_SCHEMA.parameters.properties as any).response_mode.enum,
      [ 'after-last-tool-call', 'full' ],
    );
  });
});

describe('task controls', () => {
  it('reports unknown on disconnected cancel, but never fabricates status', async () => {
    abortAllStreams();
    assert.deepEqual(await handleTaskControl('missing-session', 'task.cancel', {
      task_ref: 'T-1234abcd', reason: 'stop',
    }), { ref: 'T-1234abcd', status: 'unknown' });
    await assert.rejects(handleTaskControl('missing-session', 'task.status', {
      task_refs: [ 'T-1234abcd' ],
    }), /not connected/);
  });
});
