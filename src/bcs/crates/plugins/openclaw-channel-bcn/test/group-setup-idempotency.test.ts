import { strict as assert } from 'node:assert';
import {
  abortAllStreams,
  handleCreateManagerWorkerGroup,
  rememberTaskToolSession,
} from '../src/inbound-handler.js';

describe('bcs_create_manager_worker_group idempotency', () => {
  it('reuses an exact concurrent and recent creation receipt', async () => {
    abortAllStreams();
    const calls: Array<Record<string, unknown>> = [];
    let resolveFirst: ((value: Record<string, unknown>) => void) | undefined;
    const client = {
      connected: true,
      async createManagerWorkerGroup(params: Record<string, unknown>) {
        calls.push(params);
        if (calls.length === 1) {
          return new Promise<Record<string, unknown>>(resolve => {
            resolveFirst = resolve;
          });
        }
        return { id: `group-${calls.length}`, session_id: `session-${calls.length}` };
      },
    };

    rememberTaskToolSession('session-create-dedupe', client as any, 'private-group:abcdef12', {
      session_id: 'private-group:abcdef12',
      participants: [ 'Manager(bot-manager)' ],
      originator: 'User',
      from: 'User',
      you_are_mentioned: true,
      is_sender: false,
      mentions: [ 'Manager' ],
      message: 'create group',
      group_type: 'chat',
      recipient_role: 'driver',
    });

    const params = {
      worker_bot_uuids: [ 'worker-1', 'worker-2' ],
      topic: 'anniversary',
      context: 'public brief',
    };
    const first = handleCreateManagerWorkerGroup('session-create-dedupe', params);
    const concurrent = handleCreateManagerWorkerGroup('session-create-dedupe', params);
    assert.equal(calls.length, 1);
    resolveFirst?.({ id: 'group-1', session_id: 'session-1' });
    assert.deepEqual(await Promise.all([ first, concurrent ]), [
      {
        ok: true,
        id: 'group-1',
        session_id: 'session-1',
        chat_url: undefined,
        driver_bot: undefined,
        participants: [],
      },
      {
        ok: true,
        id: 'group-1',
        session_id: 'session-1',
        chat_url: undefined,
        driver_bot: undefined,
        participants: [],
      },
    ]);

    assert.equal((await handleCreateManagerWorkerGroup('session-create-dedupe', params)).id, 'group-1');
    assert.equal(calls.length, 1);

    const changed = await handleCreateManagerWorkerGroup('session-create-dedupe', {
      ...params,
      context: 'revised public brief',
    });
    assert.equal(changed.id, 'group-2');
    assert.equal(calls.length, 2);
  });
});
