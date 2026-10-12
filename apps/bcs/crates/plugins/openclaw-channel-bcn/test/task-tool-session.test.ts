import { strict as assert } from 'node:assert';
import {
  abortAllStreams,
  handleAssignTask,
  handleTaskMessage,
  handleTaskComplete,
  rememberTaskToolSession,
} from '../src/inbound-handler.js';

describe('task tools canonical session scope', () => {
  afterEach(() => abortAllStreams());

  for (const explicitSession of [ false, true ]) {
    it(`preserves separate V3 group/session IDs (explicit=${explicitSession})`, async () => {
      const calls: Array<{ method: string; params: Record<string, unknown> }> = [];
      const client = {
        connected: true,
        async sendRequest(method: string, params: Record<string, unknown>) {
          calls.push({ method, params });
          return { ok: true, payload: { task_id: 'task-1', status: 'dispatched' } };
        },
      };
      for (const suffix of [ 'first', 'second' ]) {
        rememberTaskToolSession(suffix, client as any, 'group-1', {
          session_id: explicitSession ? 'group-1:stale' : `group-1:${suffix}`,
          participants: [ 'Manager(bot-manager)', 'Worker(bot-worker)' ],
          originator: 'Manager(bot-manager)',
          from: 'User',
          you_are_mentioned: true,
          is_sender: false,
          mentions: [ 'Manager' ],
          message: 'task',
          group_type: 'manager_worker',
          recipient_role: 'manager',
        }, explicitSession ? `group-1:${suffix}` : undefined);
      }

      assert.equal((await handleAssignTask('first', {
        target_bot: 'Worker', message: 'do work', response_mode: 'full',
      })).ok, true);
      assert.equal((await handleTaskMessage('second', { message: 'progress' })).ok, true);
      assert.equal((await handleTaskComplete('first', { summary: 'done' })).ok, true);
      assert.deepEqual(calls, [
        { method: 'task.dispatch', params: {
          group_id: 'group-1:first', target_bot: 'Worker', message: 'do work', response_mode: 'full',
        } },
        { method: 'task.message', params: { group_id: 'group-1:second', message: 'progress' } },
        { method: 'task.complete', params: { group_id: 'group-1:first', summary: 'done' } },
      ]);
    });
  }
});
