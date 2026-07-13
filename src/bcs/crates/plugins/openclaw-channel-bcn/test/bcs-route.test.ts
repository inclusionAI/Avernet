import { strict as assert } from 'node:assert';
import {
  abortAllStreams,
  BCS_ASSIGN_TASK_TOOL_SCHEMA,
  handleAssignTask,
  handleTaskMessage,
  rememberTaskToolSession,
  resolveActiveRunId,
} from '../src/inbound-handler.js';

describe('bcs tool integration', () => {
  // resolveActiveRunId (session→runId mapping for tool factory)

  it('resolveActiveRunId returns undefined for unknown session', () => {
    assert.equal(resolveActiveRunId('unknown-session'), undefined);
  });

  it('resolveActiveRunId is cleared by abortAllStreams', () => {
    abortAllStreams();
    assert.equal(resolveActiveRunId('any-session'), undefined);
  });

  it('abortAllStreams completes without error', () => {
    abortAllStreams();
    assert.ok(true, 'abortAllStreams completed without error');
  });

  it('handleTaskMessage sends task.message through the BCS client', async () => {
    abortAllStreams();
    const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
    const client = {
      connected: true,
      async sendRequest(method: string, params: Record<string, unknown>) {
        calls.push({ method, params });
        return { ok: true, payload: { status: 'sent' } };
      },
    };

    rememberTaskToolSession('session-worker', client as any, 'group-1:abcdef12', {
      session_id: 'group-1:abcdef12',
      participants: [ 'Manager(bot-manager)', 'Worker(bot-worker)' ],
      originator: 'Manager(bot-manager)',
      from: 'Manager(bot-manager)',
      you_are_mentioned: true,
      is_sender: false,
      mentions: [ 'Worker' ],
      message: 'task',
      group_type: 'manager_worker',
      recipient_role: 'worker',
    });

    const result = await handleTaskMessage('session-worker', {
      message: 'blocked on missing schema',
    });

    assert.deepEqual(result, { ok: true, status: 'sent' });
    assert.deepEqual(calls, [
      {
        method: 'task.message',
        params: {
          group_id: 'group-1:abcdef12',
          message: 'blocked on missing schema',
        },
      },
    ]);
  });

  it('handleAssignTask forwards response_mode to task.dispatch', async () => {
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
});
