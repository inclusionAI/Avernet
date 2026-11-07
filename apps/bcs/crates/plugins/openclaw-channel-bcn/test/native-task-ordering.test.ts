// Pins plugin forwarding, NOT SDK event scheduling across all engine versions.
import { strict as assert } from 'node:assert';
import { BcsWsClient } from '../src/bcs-ws-client.js';
import { registerBcsCore } from '../src/core.js';
import { abortAllStreams, handleChatSend, initAgentEventsSubscription } from '../src/inbound-handler.js';
import type { ResolvedBcsAccount } from '../src/types.js';

const account: ResolvedBcsAccount = {
  accountId: 'default', enabled: true, bcsUrl: 'ws://bcs.test/ws/bot',
  botId: 'bot-manager', botName: 'Manager',
  capabilities: { summary: '', domains: [], skills: [], scopes: [] },
  heartbeatIntervalMs: 60000, reconnectIntervalMs: 5000, connectionTimeoutMs: 10000,
};

describe('native task legacy wire ordering', () => {
  afterEach(() => abortAllStreams());

  it('forwards an SDK start synchronously before the unchanged task RPC', async () => {
    const requests: any[] = [];
    const client = new BcsWsClient({ account });
    // Replace only socket I/O. Keep real sendRequest wire construction.
    Object.defineProperty(client, 'connected', { get: () => true });
    (client as any)._send = (frame: any) => requests.push(frame);
    (client as any)._waitResponse = async () => ({ ok: true, payload: { task_id: 'abcd1234', status: 'queued' } });
    let onAgentEvent: (event: any) => void;
    const factories = new Map<string, any>();
    registerBcsCore({
      registerChannel() {},
      registerTool(factory: any, options: any) { factories.set(options.name, factory); },
      runtime: {
        events: { onAgentEvent(callback: any) { onAgentEvent = callback; return () => true; } },
        config: { async loadConfig() { return {}; } },
        channel: {
          routing: { resolveAgentRoute() { return { agentId: 'agent-1', sessionKey: 'native-manager-session' }; } },
          session: { resolveStorePath() { return '/tmp/native-task-correlation-sessions.json'; }, async recordInboundSession() {} },
          reply: {
            finalizeInboundContext(ctx: any) { return ctx; },
            async dispatchReplyWithBufferedBlockDispatcher() {
              const tool = factories.get('bcs_assign_task')({ sessionKey: 'native-manager-session', messageChannel: 'bcs' });
              assert.ok(tool);
              onAgentEvent({ runId: 'run-manager', sessionKey: 'native-manager-session',
                stream: 'tool', seq: 1, ts: Date.now(), data: { phase: 'start',
                  name: 'bcs_assign_task', toolCallId: 'sdk-tool-call',
                  args: { target_bot: 'Worker', message: 'work' } } });
              await tool.execute('sdk-tool-call', {
                target_bot: 'Worker', message: 'work',
                parent_run_id: 'model-run', invocation_id: 'model-call', parent_attempt: 999,
              });
            },
          },
        },
      },
    } as any, { warnWhenMissingBcsUrl: false });
    initAgentEventsSubscription();
    await handleChatSend({
      type: 'req', id: 'run-manager', method: 'chat.send', params: {
        bcs_group_id: 'group-1', bcs_session_id: 'group-1:session',
        channel: { source: 'web', user_id: 'human-1' },
        session_context: {
          session_id: 'group-1:session', group_type: 'manager_worker', recipient_role: 'manager',
          participants: [ 'Manager(bot-manager)', 'Worker(bot-worker)' ],
          originator: 'Manager(bot-manager)', from: 'User', mentions: [], message: 'delegate',
          you_are_mentioned: true, is_sender: false,
        },
        message: { role: 'user', content: [{ type: 'text', text: 'delegate' }], timestamp: Date.now() },
      },
    }, client, account);
    const task = requests.find(frame => frame.method === 'task.dispatch');
    assert.ok(task, 'the native tool must send task.dispatch');
    const start = requests.find(frame => frame.event === 'agent' && frame.payload?.stream === 'tool');
    assert.ok(start, 'SDK start was forwarded');
    assert.equal(start.payload.runId, 'run-manager');
    assert.equal(start.payload.toolCallId, 'sdk-tool-call');
    assert.ok(requests.indexOf(start) < requests.indexOf(task));
    assert.equal(task.params.group_id, 'group-1:session');
    assert.equal(task.params.parent_run_id, undefined);
    assert.equal(task.params.invocation_id, undefined);
    assert.equal(task.params.parent_attempt, undefined);
  });
});
