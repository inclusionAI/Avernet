import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Context } from '@deepseek-ai/cordis';
import type { Agent, AgentHandle } from '@deepseek-ai/dsh-agent';
import type { UserMessage } from '@deepseek-ai/dsh-llm';
import type { SessionEvent, SessionId } from '@deepseek-ai/dsh-session';
import type { ToolDefinition } from '@deepseek-ai/dsh-tools';
import { BcnBridge } from '../src/bridge.js';
import type { EventFrame, RequestFrame, ResponseFrame } from '../src/protocol.js';
import type { BcnWsClient } from '../src/ws-client.js';
import { dshSessionIdForV2 } from '../src/session-identity.js';
import { testConfig } from './fixtures.js';

type Listener = (...args: unknown[]) => void;
type RequestHandler = (frame: RequestFrame) => Promise<void>;
type EventHandler = (frame: EventFrame) => Promise<void>;

class FakeClient {
  requestHandlers = new Map<string, RequestHandler>();
  eventHandlers = new Map<string, EventHandler>();
  responses: ResponseFrame[] = [];
  events: Array<{ event: string; payload: Record<string, unknown> }> = [];
  requests: Array<{ method: string; params: Record<string, unknown> }> = [];

  onRequest(method: string, handler: RequestHandler): () => void {
    this.requestHandlers.set(method, handler);
    return () => this.requestHandlers.delete(method);
  }

  onEvent(event: string, handler: EventHandler): () => void {
    this.eventHandlers.set(event, handler);
    return () => this.eventHandlers.delete(event);
  }

  sendResponse(id: string, ok: boolean, payload?: Record<string, unknown>, error?: ResponseFrame['error']): void {
    this.responses.push({ type: 'res', id, ok, ...(payload ? { payload } : {}), ...(error ? { error } : {}) });
  }

  sendEvent(event: string, payload: Record<string, unknown>): void {
    this.events.push({ event, payload });
  }

  async sendRequest(method: string, params: Record<string, unknown>): Promise<ResponseFrame> {
    this.requests.push({ method, params });
    return {
      type: 'res',
      id: 'route-response',
      ok: true,
      payload: { ok: true, resolved: [{ type: 'bot', value: 'bot-target', bot_name: 'Target' }] },
    };
  }

  async deliverRequest(method: string, id: string, params: Record<string, unknown>): Promise<void> {
    const handler = this.requestHandlers.get(method);
    if (!handler) throw new Error(`no handler for ${method}`);
    await handler({ type: 'req', id, method, params });
  }

  async deliverEvent(event: string, payload: Record<string, unknown>): Promise<void> {
    const handler = this.eventHandlers.get(event);
    if (!handler) throw new Error(`no handler for ${event}`);
    await handler({ type: 'event', event, payload, seq: 1 });
  }
}

interface FakeAgentState {
  agent: Agent;
  followups: UserMessage[];
  injections: UserMessage[];
  cancels: string[];
  tools: ToolDefinition[];
}

class FakeHarness {
  readonly listeners = new Map<string, Set<Listener>>();
  readonly agents = new Map<string, FakeAgentState>();
  readonly context: Context;

  constructor() {
    const registry = {
      get: (id: SessionId) => this.agents.get(String(id))?.agent,
      create: async (options: { sessionId: SessionId; setup?: (ctx: Context) => unknown }): Promise<AgentHandle> =>
        this.createAgent(options.sessionId, options.setup),
      resume: async (options: { resumeSessionId: SessionId; setup?: (ctx: Context) => unknown }): Promise<AgentHandle> =>
        this.createAgent(options.resumeSessionId, options.setup),
    };
    this.context = {
      on: (event: string, listener: Listener) => {
        const listeners = this.listeners.get(event) ?? new Set<Listener>();
        listeners.add(listener);
        this.listeners.set(event, listeners);
        return () => listeners.delete(listener);
      },
      agents: registry,
      sessionPersistence: { list: async () => [] },
    } as unknown as Context;
  }

  emit(event: string, ...args: unknown[]): void {
    for (const listener of this.listeners.get(event) ?? []) listener(...args);
  }

  state(sessionKey: string): FakeAgentState {
    const state = this.agents.get(String(dshSessionIdForV2(sessionKey)));
    if (!state) throw new Error(`missing agent for ${sessionKey}`);
    return state;
  }

  private async createAgent(
    sessionId: SessionId,
    setup: ((ctx: Context) => unknown) | undefined,
  ): Promise<AgentHandle> {
    const followups: UserMessage[] = [];
    const injections: UserMessage[] = [];
    const cancels: string[] = [];
    const tools: ToolDefinition[] = [];
    const agentContext = {
      tools: {
        register: (definition: ToolDefinition) => {
          tools.push(definition);
          return () => {
            const index = tools.indexOf(definition);
            if (index >= 0) tools.splice(index, 1);
          };
        },
      },
    } as unknown as Context;
    const agent = {
      id: sessionId,
      session: { id: sessionId },
      ctx: agentContext,
      status: 'idle',
      options: {},
      inbox: {},
      followup: (message: UserMessage) => followups.push(message),
      inject: (message: UserMessage) => injections.push(message),
      cancel: (cause: { kind: string }) => cancels.push(cause.kind),
      whenIdle: async () => {},
      runMaintenance: async <T>(task: (signal: AbortSignal) => Promise<T>) => task(new AbortController().signal),
      send: () => {},
      steer: () => {},
    } as unknown as Agent;
    await setup?.(agentContext);
    const state = { agent, followups, injections, cancels, tools };
    this.agents.set(String(sessionId), state);
    return {
      agent,
      dispose: async () => { this.agents.delete(String(sessionId)); },
    };
  }
}

function chatParams(sessionKey = 'bcn-v2-session', idempotencyKey = 'run-1'): Record<string, unknown> {
  return {
    session_key: sessionKey,
    bcs_group_id: 'group-1',
    bcs_session_id: 'session-layer-1',
    idempotency_key: idempotencyKey,
    message: { role: 'user', content: [{ type: 'text', text: 'Please investigate.' }], timestamp: 1 },
    channel: { source: 'api', actor_id: 'human-1' },
    session_context: {
      session_id: 'group-1',
      originator: 'human-1',
      from: 'human-1',
      participants: ['bot-123'],
      mentions: [],
      you_are_mentioned: true,
      is_sender: false,
      response_directive: { action: 'respond', request_source: 'default_policy' },
    },
  };
}

function event(value: unknown): SessionEvent {
  return value as SessionEvent;
}

function emitClaim(harness: FakeHarness, state: FakeAgentState, message: UserMessage, turn = 1): void {
  harness.emit('agent/inbox/claimed', { agent: state.agent, message, turn });
}

function emitSession(harness: FakeHarness, state: FakeAgentState, value: SessionEvent): void {
  harness.emit('session/event', state.agent.session, value);
}

function chatEvents(client: FakeClient): Array<Record<string, unknown>> {
  return client.events.filter(item => item.event === 'chat.event').map(item => item.payload);
}

function toolEvents(client: FakeClient): Array<Record<string, unknown>> {
  return client.events.filter(item => item.event === 'agent').map(item => item.payload);
}

test('maps live DSH text and durable tool events to BCN with one terminal event', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'request-1', chatParams());
  const state = harness.state('bcn-v2-session');
  assert.equal(state.followups.length, 1);
  assert.match((state.followups[0]?.content[0] as { text: string }).text, /<bcn_context>/);
  emitClaim(harness, state, state.followups[0] as UserMessage);

  emitSession(harness, state, event({
    type: 'assistant/chunk', seq: 1, time: 1,
    data: { turn: 1, step: 1, chunk: { type: 'text-delta', index: 0, text: 'Hello ' } },
  }));
  emitSession(harness, state, event({
    type: 'assistant/chunk', seq: 2, time: 2,
    data: { turn: 1, step: 1, chunk: { type: 'reasoning-delta', index: 1, text: 'private reasoning' } },
  }));
  emitSession(harness, state, event({
    type: 'tool/result', seq: 3, time: 3, surfaceOp: { op: 'append' },
    data: {
      turn: 1,
      step: 1,
      message: {
        id: 'message-result', role: 'user', source: { kind: 'tool', callId: 'call-1' },
        content: [{ type: 'tool-result', toolCallId: 'call-1', content: [{ type: 'text', text: 'ok' }], isError: false }],
      },
    },
  }));
  emitSession(harness, state, event({
    type: 'tool/call', seq: 4, time: 4,
    data: { turn: 1, step: 1, callId: 'call-1', name: 'read_file', arguments: '{"path":"/tmp/a"}' },
  }));
  emitSession(harness, state, event({
    type: 'tool/result', seq: 5, time: 5, surfaceOp: { op: 'append' },
    data: {
      turn: 1,
      step: 1,
      message: {
        id: 'duplicate-result', role: 'user', source: { kind: 'tool', callId: 'call-1' },
        content: [{ type: 'tool-result', toolCallId: 'call-1', content: [{ type: 'text', text: 'duplicate' }] }],
      },
    },
  }));
  emitSession(harness, state, event({
    type: 'assistant/chunk', seq: 6, time: 6,
    data: { turn: 1, step: 2, chunk: { type: 'text-delta', index: 0, text: 'world' } },
  }));
  emitSession(harness, state, event({
    type: 'assistant/message', seq: 7, time: 7, surfaceOp: { op: 'append' },
    data: {
      turn: 1,
      step: 2,
      message: {
        id: 'assistant-message', role: 'assistant', source: { kind: 'model', provider: 'deepseek', model: 'chat' },
        content: [{ type: 'text', text: 'world' }],
      },
      usage: { inputTokens: 10, cacheReadTokens: 2, outputTokens: 3 },
    },
  }));
  emitSession(harness, state, event({
    type: 'turn/end', seq: 8, time: 8, data: { turn: 1, reason: { kind: 'completed' } },
  }));
  emitSession(harness, state, event({
    type: 'turn/end', seq: 9, time: 9, data: { turn: 1, reason: { kind: 'completed' } },
  }));

  const tools = toolEvents(client);
  assert.equal(tools.length, 2);
  assert.deepEqual((tools[0]?.data as Record<string, unknown>).args, { path: '/tmp/a' });
  assert.equal((tools[1]?.data as Record<string, unknown>).name, 'read_file');
  assert.deepEqual((tools[1]?.data as Record<string, unknown>).result, { content: [{ type: 'text', text: 'ok' }] });

  const chats = chatEvents(client);
  assert.deepEqual(chats.filter(item => item.state === 'delta').map(item => item.delta_text), ['Hello ', 'world']);
  assert.equal(chats.some(item => JSON.stringify(item).includes('private reasoning')), false);
  const finals = chats.filter(item => item.state === 'final');
  assert.equal(finals.length, 1);
  assert.deepEqual(finals[0]?.usage, { input: 12, output: 3 });
  assert.equal((((finals[0]?.message as Record<string, unknown>).content as Array<{ text: string }>)[0]?.text), 'Hello world');
  assert.equal(chats.some(item => item.state === 'tool_call_start' || item.state === 'tool_call_end'), false);
  await bridge.dispose();
});

test('preserves raw tool arguments, parallel call identity, error results, and cross-run isolation', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  for (const [sessionKey, runId] of [['session-a', 'run-a'], ['session-b', 'run-b']] as const) {
    await client.deliverRequest('chat.send', `request-${runId}`, chatParams(sessionKey, runId));
    const state = harness.state(sessionKey);
    emitClaim(harness, state, state.followups[0] as UserMessage);
    emitSession(harness, state, event({
      type: 'tool/call', seq: 1, time: 1,
      data: { turn: 1, step: 1, callId: 'shared-call-id', name: 'shell', arguments: '{unfinished' },
    }));
    emitSession(harness, state, event({
      type: 'tool/result', seq: 2, time: 2, surfaceOp: { op: 'append' },
      data: {
        turn: 1,
        step: 1,
        message: {
          id: `result-${runId}`, role: 'user', source: { kind: 'tool', callId: 'shared-call-id' },
          content: [{
            type: 'tool-result', toolCallId: 'shared-call-id', isError: true,
            content: [{ type: 'text', text: `failed-${runId}` }],
          }],
        },
        error: { name: 'ToolCallError', code: 'FAILED' },
      },
    }));
  }
  const tools = toolEvents(client);
  assert.equal(tools.length, 4);
  assert.deepEqual(tools.filter(item => (item.data as Record<string, unknown>).phase === 'start')
    .map(item => (item.data as Record<string, unknown>).args), ['{unfinished', '{unfinished']);
  assert.deepEqual(tools.filter(item => (item.data as Record<string, unknown>).phase === 'result')
    .map(item => (item.data as Record<string, unknown>).isError), [true, true]);
  assert.deepEqual(new Set(tools.map(item => item.run_id)), new Set(['run-a', 'run-b']));
  await bridge.dispose();
});

test('captures bcs_route once, emits its normal tool telemetry, and attaches routing only to final', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'request-route', chatParams('route-session', 'run-route'));
  const state = harness.state('route-session');
  emitClaim(harness, state, state.followups[0] as UserMessage);
  const result = await bridge.captureRoute(
    state.agent,
    [{ type: 'name', value: 'Target' }],
    'Needs database expertise',
    new AbortController().signal,
  );
  assert.deepEqual(result, {
    ok: true,
    captured: true,
    display_to_user: false,
    resolved: [{ type: 'bot', value: 'bot-target' }],
  });
  assert.deepEqual(client.requests, [{
    method: 'route.resolve',
    params: { group_id: 'group-1', session_id: 'session-layer-1', selectors: [{ type: 'name', value: 'Target' }] },
  }]);
  assert.equal(state.tools[0]?.name, 'bcs_route', 'the route tool is scoped to the BCN agent');

  emitSession(harness, state, event({
    type: 'tool/call', seq: 1, time: 1,
    data: {
      turn: 1,
      step: 1,
      callId: 'route-call',
      name: 'bcs_route',
      arguments: '{"to":[{"type":"name","value":"Target"}],"reason":"Needs database expertise"}',
    },
  }));
  emitSession(harness, state, event({
    type: 'tool/result', seq: 2, time: 2, surfaceOp: { op: 'append' },
    data: {
      turn: 1,
      step: 1,
      message: {
        id: 'route-result', role: 'user', source: { kind: 'tool', callId: 'route-call' },
        content: [{
          type: 'tool-result', toolCallId: 'route-call',
          content: [{ type: 'text', text: JSON.stringify(result) }],
        }],
      },
    },
  }));
  emitSession(harness, state, event({
    type: 'turn/end', seq: 3, time: 3, data: { turn: 1, reason: { kind: 'completed' } },
  }));
  assert.equal(toolEvents(client).length, 2);
  const final = chatEvents(client).find(item => item.state === 'final');
  assert.deepEqual(final?.routing, {
    responders: [{ type: 'bot', value: 'bot-target' }],
    mode: 'required',
    reason: 'Needs database expertise',
    include_self: false,
  });
  await bridge.dispose();
});

test('deduplicates inbound runs and emits exactly one aborted or error terminal', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'request-a', chatParams('abort-session', 'same-run'));
  await client.deliverRequest('chat.send', 'request-b', chatParams('abort-session', 'same-run'));
  const state = harness.state('abort-session');
  assert.equal(state.followups.length, 1);
  emitClaim(harness, state, state.followups[0] as UserMessage);
  await client.deliverRequest('chat.abort', 'abort-request', { session_key: 'abort-session', run_id: 'same-run' });
  assert.deepEqual(state.cancels, ['user']);
  emitSession(harness, state, event({
    type: 'turn/end', seq: 1, time: 1,
    data: { turn: 1, reason: { kind: 'aborted', reason: { kind: 'user' } } },
  }));
  emitSession(harness, state, event({
    type: 'turn/end', seq: 2, time: 2,
    data: { turn: 1, reason: { kind: 'error', error: { message: 'private stack', code: 'E_FAIL' } } },
  }));
  const terminals = chatEvents(client).filter(item => item.state === 'aborted' || item.state === 'error');
  assert.deepEqual(terminals.map(item => item.state), ['aborted']);

  await client.deliverRequest('chat.send', 'request-error', chatParams('error-session', 'error-run'));
  const errorState = harness.state('error-session');
  emitClaim(harness, errorState, errorState.followups[0] as UserMessage);
  emitSession(harness, errorState, event({
    type: 'turn/end', seq: 1, time: 1,
    data: { turn: 1, reason: { kind: 'error', error: { message: 'private stack', code: 'E_SAFE' } } },
  }));
  const error = chatEvents(client).find(item => item.run_id === 'error-run' && item.state === 'error');
  assert.equal(error?.errorMessage, 'DeepSeek Harness run failed');
  assert.equal(error?.errorCode, 'E_SAFE');
  assert.equal(JSON.stringify(error).includes('private stack'), false);
  await bridge.dispose();
});

test('injects observe-only messages without creating a BCN response run', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  const params = chatParams('inject-session', 'ignored-run');
  delete params.idempotency_key;
  await client.deliverRequest('chat.inject', 'inject-request', params);
  const state = harness.state('inject-session');
  assert.equal(state.injections.length, 1);
  assert.equal(client.responses.at(-1)?.ok, true);
  assert.equal(client.events.length, 0);
  await bridge.dispose();
});
