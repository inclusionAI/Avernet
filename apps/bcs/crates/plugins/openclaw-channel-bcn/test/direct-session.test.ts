import { strict as assert } from 'node:assert';
import { setBcsRuntime } from '../src/runtime.js';
import { createBcsPlugin } from '../src/channel.js';
import {
  abortAllStreams,
  handleChatSend,
  resolveGroupIdFromSessionKey,
  resolveBcsSessionIdFromSessionKey,
  withBcsDirectSessionScope,
} from '../src/inbound-handler.js';

describe('BCS direct-chat sessions', () => {
  afterEach(() => abortAllStreams());

  it('advertises direct chats to OpenClaw', () => {
    assert.deepEqual(createBcsPlugin().capabilities.chatTypes, [ 'direct' ]);
  });

  it('scopes direct sessions locally without changing host configuration', () => {
    const cfg = Object.freeze({
      session: Object.freeze({ dmScope: 'main', store: '/tmp/bcn-sessions.json' }),
      agents: { default: 'bot' },
    });
    const scoped = withBcsDirectSessionScope(cfg);
    assert.equal(scoped.session.dmScope, 'per-account-channel-peer');
    assert.equal(scoped.session.store, cfg.session.store);
    assert.equal(scoped.agents, cfg.agents);
    assert.equal(cfg.session.dmScope, 'main');
    assert.equal(withBcsDirectSessionScope({}).session.dmScope, 'per-account-channel-peer');
  });

  it('dispatches direct turns with unchanged BCS context and distinct session peers', async () => {
    const hostCfg = Object.freeze({ session: Object.freeze({ dmScope: 'main' }) });
    const routes: any[] = [];
    const contexts: any[] = [];
    const recorded: any[] = [];
    const dispatched: any[] = [];
    const responses: any[] = [];
    setBcsRuntime({
      config: { async loadConfig() { return hostCfg; } },
      channel: {
        routing: {
          resolveAgentRoute(input: any) {
            routes.push(input);
            assert.equal(input.peer.kind, 'direct');
            assert.equal(input.cfg.session.dmScope, 'per-account-channel-peer');
            // Model the SDK's account/channel/peer scoped direct session key.
            return {
              agentId: 'agent-1',
              sessionKey: `agent:agent-1:bcs:${input.accountId}:direct:${input.peer.id}`,
            };
          },
        },
        reply: {
          finalizeInboundContext(ctx: any) { contexts.push(ctx); return ctx; },
          async dispatchReplyWithBufferedBlockDispatcher(input: any) { dispatched.push(input); },
        },
        session: {
          resolveStorePath() { return '/tmp/bcn-direct-session-test.json'; },
          async recordInboundSession(input: any) { recorded.push(input); },
        },
      },
    } as any);
    const client = {
      sendResponse(...args: any[]) { responses.push(args); },
      sendEvent() {},
    };
    const body = '[GroupContext] participants: Alice, Worker\n[from:Alice] Please review this';
    for (const [ accountId, groupId, sessionId ] of [
      [ 'first', 'group-1', 'group-1:session-1' ],
      [ 'first', 'group-1', 'group-1:session-2' ],
      [ 'first', 'group-2', 'group-2:session-1' ],
      [ 'second', 'group-1', 'group-1:session-1' ],
    ]) {
      await handleChatSend({
        type: 'req', id: `${accountId}-${sessionId}`, method: 'chat.send',
        params: {
          bcs_group_id: groupId, bcs_session_id: sessionId, session_key: sessionId,
          message: { role: 'user', content: [{ type: 'text', text: body }] },
          session_context: { from: 'Alice', participants: [ 'Alice', 'Worker' ] },
        },
      } as any, client as any, { accountId, botId: 'bot-1' } as any);
    }
    assert.equal(responses.length, 4);
    assert.ok(responses.every(response => response[1] === true));
    assert.equal(dispatched.length, 4);
    assert.equal(new Set(contexts.map(ctx => ctx.SessionKey)).size, 4);
    for (let index = 0; index < contexts.length; index++) {
      const ctx = contexts[index];
      assert.equal(ctx.ChatType, 'direct');
      assert.equal(ctx.Body, body);
      assert.equal(ctx.RawBody, body);
      assert.equal(ctx.SenderName, 'Alice');
      assert.equal(ctx.To, index === 2 ? 'group-2' : 'group-1');
      assert.equal(ctx.OriginatingTo, ctx.To);
      assert.equal(recorded[index].updateLastRoute.to, ctx.To);
      assert.equal(dispatched[index].ctx, ctx);
      assert.equal(resolveGroupIdFromSessionKey(ctx.SessionKey), ctx.To);
      assert.equal(resolveBcsSessionIdFromSessionKey(ctx.SessionKey), routes[index].peer.id);
    }
    assert.equal(hostCfg.session.dmScope, 'main');
  });
});
