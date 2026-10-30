import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Context } from '@deepseek-ai/cordis';
import type { Agent, AgentHandle, AgentOptions, ModelSelection } from '@deepseek-ai/dsh-agent';
import type { UserMessage } from '@deepseek-ai/dsh-llm';
import type { SessionEvent, SessionHeader, SessionId } from '@deepseek-ai/dsh-session';
import type { ToolDefinition, ToolRunContext } from '@deepseek-ai/dsh-tools';
import { BcnBridge } from '../src/bridge.js';
import type { EventFrame, RequestFrame, ResponseFrame } from '../src/protocol.js';
import type { BcnWsClient } from '../src/ws-client.js';
import { dshSessionIdForV2 } from '../src/session-identity.js';
import { testConfig } from './fixtures.js';

type Listener = (...args: unknown[]) => unknown;
type RequestHandler = (frame: RequestFrame) => Promise<void>;
type EventHandler = (frame: EventFrame) => Promise<void>;

class FakeClient {
  connected = true;
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
    if (method === 'task.dispatch') {
      return {
        type: 'res',
        id: 'task-dispatch-response',
        ok: true,
        payload: { task_id: 'task-1', status: 'dispatched' },
      };
    }
    if (method === 'task.message') {
      return { type: 'res', id: 'task-message-response', ok: true, payload: { status: 'sent' } };
    }
    if (method === 'task.complete') {
      return { type: 'res', id: 'task-complete-response', ok: true, payload: {} };
    }
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
  scopedEvents: string[];
  promptVariables: Map<string, () => string | undefined>;
}

interface FakePersistedSession {
  id: string;
  agentPreset?: string;
  selectedPreset?: string;
}

class FakeHarness {
  readonly listeners = new Map<string, Set<Listener>>();
  readonly agents = new Map<string, FakeAgentState>();
  readonly context: Context;
  readonly mountedPresets: string[] = [];
  readonly defaultSelection: ModelSelection = {
    provider: 'deepseek-official',
    model: 'deepseek-chat',
  };

  constructor(persistedSessions: Array<string | FakePersistedSession> = []) {
    const persisted = new Map(persistedSessions.map(value => {
      const entry = typeof value === 'string' ? { id: value } : value;
      const events = entry.selectedPreset
        ? [{
          type: 'agent-preset/selected',
          seq: 0,
          time: 1,
          data: { agentPreset: entry.selectedPreset },
        } as unknown as SessionEvent]
        : [];
      return [entry.id, {
        meta: {
          id: entry.id as SessionId,
          ...(entry.agentPreset ? { agentPreset: entry.agentPreset } : {}),
        } as SessionHeader,
        events,
      }];
    }));
    const registry = {
      get: (id: SessionId) => this.agents.get(String(id))?.agent,
      create: async (options: {
        sessionId: SessionId;
        meta?: { cwd?: string; agentPreset?: string };
        agentOptions?: AgentOptions;
        setup?: (ctx: Context, agent: Agent) => unknown;
      }): Promise<AgentHandle> => this.createAgent(
        options.sessionId,
        options.meta,
        options.agentOptions,
        options.setup,
      ),
      resume: async (options: {
        resumeSessionId: SessionId;
        agentOptions?: AgentOptions;
        setup?: (ctx: Context, agent: Agent) => unknown;
      }): Promise<AgentHandle> => this.createAgent(
        options.resumeSessionId,
        persisted.get(String(options.resumeSessionId))?.meta,
        options.agentOptions,
        options.setup,
      ),
    };
    this.context = {
      on: (event: string, listener: Listener) => {
        const listeners = this.listeners.get(event) ?? new Set<Listener>();
        listeners.add(listener);
        this.listeners.set(event, listeners);
        return () => listeners.delete(listener);
      },
      agentDefaultModel: { currentSelection: () => this.defaultSelection },
      agentPresets: {
        resolve: async (id?: string) => ({ id: id ?? 'standard' }),
        mount: async (_agentCtx: Context, id?: string) => {
          const resolved = id ?? 'standard';
          this.mountedPresets.push(resolved);
          return { id: resolved };
        },
      },
      agents: registry,
      sessionPersistence: {
        stat: async (id: SessionId) => {
          const item = persisted.get(String(id));
          return item ? { header: item.meta } : undefined;
        },
        open: async (id: SessionId) => {
          const inspection = persisted.get(String(id));
          if (!inspection) throw new Error(`missing persisted session ${String(id)}`);
          return { header: inspection.meta, read: async () => ({ events: inspection.events }), close: async () => {} };
        },
      },
    } as unknown as Context;
  }

  emit(event: string, ...args: unknown[]): void {
    for (const listener of this.listeners.get(event) ?? []) listener(...args);
  }

  state(sessionIdentity: string): FakeAgentState {
    const state = this.agents.get(String(dshSessionIdForV2(sessionIdentity)));
    if (!state) throw new Error(`missing agent for ${sessionIdentity}`);
    return state;
  }

  private async createAgent(
    sessionId: SessionId,
    meta: { cwd?: string; agentPreset?: string } | undefined,
    agentOptions: AgentOptions | undefined,
    setup: ((ctx: Context, agent: Agent) => unknown) | undefined,
  ): Promise<AgentHandle> {
    const followups: UserMessage[] = [];
    const injections: UserMessage[] = [];
    const cancels: string[] = [];
    const tools: ToolDefinition[] = [];
    const scopedEvents: string[] = [];
    const promptVariables = new Map<string, () => string | undefined>();
    const agentContext = {
      userQuestions: {
        attachWait: async function* () { yield { remainingMs: 1 }; },
      },
      on: (event: string, listener: Listener) => {
        scopedEvents.push(event);
        const listeners = this.listeners.get(event) ?? new Set<Listener>();
        listeners.add(listener);
        this.listeners.set(event, listeners);
        return () => listeners.delete(listener);
      },
      systemPrompt: {
        variable: (name: string, provider: () => string | undefined) => {
          promptVariables.set(name, provider);
          return () => promptVariables.delete(name);
        },
      },
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
      session: {
        id: sessionId,
        header: { cwd: meta?.cwd, agentPreset: meta?.agentPreset },
        requestHeader: () => undefined,
      },
      ctx: agentContext,
      status: 'idle',
      options: agentOptions ?? {},
      inbox: { remove: (id: unknown) => {
        const index = followups.findIndex(message => message.id === id);
        if (index < 0) return false;
        followups.splice(index, 1);
        return true;
      } },
      followup: (message: UserMessage) => followups.push(message),
      inject: (message: UserMessage) => injections.push(message),
      cancel: (cause: { kind: string }) => cancels.push(cause.kind),
      whenIdle: async () => {},
      runMaintenance: async <T>(task: (signal: AbortSignal) => Promise<T>) => task(new AbortController().signal),
      send: () => {},
      steer: () => {},
    } as unknown as Agent;
    Object.assign(agentContext, { agent });
    await setup?.(agentContext, agent);
    const state = { agent, followups, injections, cancels, tools, scopedEvents, promptVariables };
    this.agents.set(String(sessionId), state);
    return {
      agent,
      dispose: async () => { this.agents.delete(String(sessionId)); },
    };
  }
}

test('creates BCN agents with the DSH default model and scoped model selection', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();

  await client.deliverRequest('chat.send', 'request-model', chatParams('model-session', 'model-run'));

  const state = harness.state('model-session');
  assert.deepEqual(state.agent.options, {
    provider: harness.defaultSelection.provider,
    model: harness.defaultSelection.model,
  });
  assert.ok(state.scopedEvents.includes('session/event'));
  assert.ok(state.scopedEvents.includes('agent/request'));
  assert.equal(state.agent.session.header.cwd, process.cwd());
  assert.equal(state.agent.session.header.agentPreset, 'standard');
  assert.equal(state.promptVariables.get('cwd')?.(), process.cwd());
  assert.deepEqual(harness.mountedPresets, ['standard']);
  assert.equal(state.followups.length, 1);
  await bridge.dispose();
});

test('supplies cwd to legacy persisted BCN sessions created without session metadata', async () => {
  const sessionKey = 'legacy-no-cwd-session';
  const sessionId = dshSessionIdForV2(sessionKey);
  const harness = new FakeHarness([String(sessionId)]);
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();

  await client.deliverRequest('chat.send', 'request-legacy', chatParams(sessionKey, 'legacy-run'));

  const state = harness.state(sessionKey);
  assert.equal(state.agent.session.header.cwd, undefined);
  assert.equal(state.promptVariables.get('cwd')?.(), process.cwd());
  assert.deepEqual(harness.mountedPresets, ['standard']);
  assert.equal(state.followups.length, 1);
  await bridge.dispose();
});

test('restores the latest DSH agent preset recorded by a persisted BCN session', async () => {
  const sessionKey = 'recorded-preset-session';
  const sessionId = dshSessionIdForV2(sessionKey);
  const harness = new FakeHarness([{
    id: String(sessionId),
    agentPreset: 'standard',
    selectedPreset: 'custom-coding',
  }]);
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();

  await client.deliverRequest('chat.send', 'request-preset', chatParams(sessionKey, 'preset-run'));

  assert.deepEqual(harness.mountedPresets, ['custom-coding']);
  assert.equal(harness.state(sessionKey).followups.length, 1);
  await bridge.dispose();
});

test('isolates V3 sessions that share a legacy group-level session_key and reuses each session for inject', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  const sharedSessionKey = 'group:bcs_grp_59a251ab729a49cfbf8b7a183d80392d';
  const firstSessionId = 'bcs_grp_59a251ab729a49cfbf8b7a183d80392d:9e1f90a1';
  const secondSessionId = 'bcs_grp_59a251ab729a49cfbf8b7a183d80392d:28ab9c04';
  const first = chatParams(sharedSessionKey, 'run-first');
  first.bcs_group_id = 'bcs_grp_59a251ab729a49cfbf8b7a183d80392d';
  first.bcs_session_id = firstSessionId;
  first.session_context = {
    ...(first.session_context as Record<string, unknown>),
    session_id: firstSessionId,
  };
  const second = chatParams(sharedSessionKey, 'run-second');
  second.bcs_group_id = 'bcs_grp_59a251ab729a49cfbf8b7a183d80392d';
  second.bcs_session_id = secondSessionId;
  second.session_context = {
    ...(second.session_context as Record<string, unknown>),
    session_id: secondSessionId,
  };

  await client.deliverRequest('chat.send', 'request-first', first);
  await client.deliverRequest('chat.send', 'request-second', second);
  const firstState = harness.state(firstSessionId);
  const secondState = harness.state(secondSessionId);
  assert.notEqual(firstState.agent.id, secondState.agent.id);
  assert.equal(firstState.followups.length, 1);
  assert.equal(secondState.followups.length, 1);

  const firstInject = { ...first };
  delete firstInject.idempotency_key;
  await client.deliverRequest('chat.inject', 'inject-first', firstInject);
  assert.equal(firstState.injections.length, 1);
  assert.equal(secondState.injections.length, 0);
  await bridge.dispose();
});

test('registers chat.abort but not chat.history', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();

  assert.equal(client.requestHandlers.has('chat.abort'), true);
  assert.equal(client.eventHandlers.has('chat.abort'), false);
  assert.equal(client.requestHandlers.has('chat.history'), false);
  await bridge.dispose();
});

function chatParams(sessionKey = 'bcn-v2-session', idempotencyKey = 'run-1'): Record<string, unknown> {
  return {
    session_key: sessionKey,
    bcs_session_id: sessionKey,
    bcs_group_id: 'group-1',
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

function taskGroupParams(
  sessionKey: string,
  idempotencyKey: string,
  recipientRole: 'manager' | 'worker' | undefined,
): Record<string, unknown> {
  const bcsSessionId = `bcs_grp_test:${sessionKey}`;
  const params = chatParams(bcsSessionId, idempotencyKey);
  params.bcs_group_id = 'bcs_grp_test';
  params.session_context = {
    ...(params.session_context as Record<string, unknown>),
    group_type: 'manager_worker',
    participants: ['bot-manager', 'human-1'],
    ...(recipientRole ? { recipient_role: recipientRole } : {}),
  };
  return params;
}

test('state machine nodes retain the authenticated manager and worker profiles', async () => {
  for (const role of ['manager', 'worker'] as const) {
    const harness = new FakeHarness();
    const client = new FakeClient();
    const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
    bridge.start();
    await client.deliverRequest('chat.send', 'ordinary', taskGroupParams('same-session', 'ordinary-run', role));
    const node = taskGroupParams('same-session', 'node-run', role);
    (node.session_context as Record<string, unknown>).group_type = 'state_machine';
    await client.deliverRequest('chat.send', 'node', node);
    const state = harness.state('bcs_grp_test:same-session');
    assert.equal(state.followups.length, 2);
    assert.deepEqual(state.tools.map(tool => tool.name), role === 'manager'
      ? ['bcs_assign_task', 'bcs_task_complete'] : ['bcs_send_task_message']);
    await bridge.dispose();
  }
});

function toolRunContext(agent: Agent): ToolRunContext {
  return {
    agent,
    signal: new AbortController().signal,
  } as ToolRunContext;
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
  return client.events.filter(item => item.event === 'chat').map(item => item.payload);
}

function toolEvents(client: FakeClient): Array<Record<string, unknown>> {
  return client.events.filter(item => item.event === 'agent').map(item => item.payload);
}

test('chat.abort validates scope, removes only queued work, and is terminal-idempotent', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'send-a', chatParams('session-abort', 'a'));
  await client.deliverRequest('chat.send', 'send-b', chatParams('session-abort', 'b'));
  const state = harness.state('session-abort');
  emitClaim(harness, state, state.followups[0]!, 1);
  harness.emit('agent/assistant-stream', { agent: state.agent, frame: { type: 'start', attemptId: 'attempt', revision: 1, turn: 1, step: 1 } });
  harness.emit('agent/assistant-stream', { agent: state.agent, frame: { type: 'chunk', attemptId: 'attempt', revision: 1, index: 0, time: 1, chunk: { type: 'text-delta', text: 'partial' } } });
  await client.deliverRequest('chat.abort', 'scope', { session_key: 'other', run_id: 'a' });
  assert.equal(client.responses.at(-1)?.error?.code, 'SCOPE_MISMATCH');
  await client.deliverRequest('chat.abort', 'missing', { session_key: 'session-abort', run_id: 'unknown' });
  assert.equal(client.responses.at(-1)?.error?.code, 'RUN_NOT_FOUND');
  await client.deliverRequest('chat.abort', 'queued', { session_key: 'session-abort', run_id: 'b' });
  assert.equal(state.followups.length, 1);
  assert.deepEqual(state.cancels, []);
  await client.deliverRequest('chat.abort', 'active', { session_key: 'session-abort', run_id: 'a' });
  assert.deepEqual(state.cancels, ['user']);
  assert.equal(chatEvents(client).find(e => e.runId === 'a' && e.state === 'aborted')?.content, 'partial');
  await client.deliverRequest('chat.abort', 'repeat', { session_key: 'session-abort', run_id: 'a' });
  assert.deepEqual(client.responses.at(-1)?.payload, { aborted: false, aborted_run_ids: [] });
  assert.equal(chatEvents(client).filter(e => e.state === 'aborted').length, 2);
  await bridge.dispose();
});

test('chat.abort during asynchronous agent creation prevents later delivery', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  const stat = harness.context.sessionPersistence.stat;
  harness.context.sessionPersistence.stat = async (...args) => { await gate; return stat(...args); };
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  const delivery = client.deliverRequest('chat.send', 'send', chatParams('creating', 'creating-run'));
  await client.deliverRequest('chat.abort', 'abort', { session_key: 'creating', run_id: 'creating-run' });
  release();
  await delivery;
  assert.equal(harness.state('creating').followups.length, 0);
  assert.equal(chatEvents(client).filter(e => e.state === 'aborted').length, 1);
  await bridge.dispose();
});

test('V3 inject rejects missing canonical session identity', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  const params = chatParams();
  delete params.bcs_session_id;
  await client.deliverRequest('chat.inject', 'inject-invalid', params);
  assert.equal(client.responses.at(-1)?.error?.code, 'INVALID_REQUEST');
  assert.equal(harness.agents.size, 0);
  await bridge.dispose();
});

test('bridges single-use approval, rejects cross-run resolution, and handles duplicate acknowledgement', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'send', chatParams('approval', 'approval-run'));
  const state = harness.state('approval');
  emitClaim(harness, state, state.followups[0]!);
  emitSession(harness, state, event({
    type: 'tool/call', seq: 1, time: 1,
    data: { turn: 1, step: 1, callId: 'bash-call', name: 'bash', arguments: '{"command":"pwd"}' },
  }));
  const listener = [...harness.listeners.get('approval/request')!][0]!;
  const outcome = listener({ agent: state.agent, toolName: 'bash', callId: 'bash-call', reason: 'Needs permission' }, async () => 'unavailable');
  const request = client.events.find(e => e.event === 'interaction')!.payload;
  assert.equal(request.kind, 'exec');
  assert.equal(request.command, 'pwd');
  const params = { runId: 'wrong-run', interactionId: request.interactionId, kind: 'exec', idempotencyKey: 'key', decision: 'allow_once' };
  await client.deliverRequest('interaction.resolve', 'wrong', params);
  assert.equal(client.responses.at(-1)?.error?.code, 'SCOPE_MISMATCH');
  params.runId = 'approval-run';
  await client.deliverRequest('interaction.resolve', 'valid', params);
  assert.equal(await outcome, 'allowed-once');
  await client.deliverRequest('interaction.resolve', 'duplicate', params);
  assert.equal(client.responses.at(-1)?.ok, true);
  assert.equal(client.events.filter(e => e.event === 'interaction' && e.payload.phase === 'resolved').length, 1);
  await bridge.dispose();
});

test('maps structured questions and custom answers and cancels approval on chat.abort', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'send', chatParams('questions', 'question-run'));
  const state = harness.state('questions');
  emitClaim(harness, state, state.followups[0]!);
  const listener = [...harness.listeners.get('user-questions/request')!][0]!;
  const outcome = listener({ agent: state.agent, questions: [{ id: 'target', question: 'Where?', options: [{ label: 'local' }], multiSelect: true }] }, async () => ({ answers: [] }));
  const requested = client.events.find(e => e.event === 'interaction')!.payload;
  await client.deliverRequest('interaction.resolve', 'invalid', { runId: 'question-run', interactionId: requested.interactionId, kind: 'ask_user', idempotencyKey: 'key', action: 'submit', answers: {} });
  assert.equal(client.responses.at(-1)?.ok, false);
  await client.deliverRequest('interaction.resolve', 'answer', { runId: 'question-run', interactionId: requested.interactionId, kind: 'ask_user', idempotencyKey: 'key', action: 'submit', answers: { target: { values: ['local', 'staging'] } } });
  assert.deepEqual(await outcome, { answers: [{ id: 'target', selected: ['local'], custom: 'staging' }] });
  const approval = [...harness.listeners.get('approval/request')!][0]!({ agent: state.agent, toolName: 'bash' }, async () => 'unavailable');
  await client.deliverRequest('chat.abort', 'abort', { session_key: 'questions', run_id: 'question-run' });
  assert.equal(await approval, 'cancelled');
  assert.equal(client.events.at(-1)?.payload.state, 'aborted');
  await bridge.dispose();
});

test('timed questions expire using the DSH wait duration and reject late resolutions', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'send', chatParams('timed', 'timed-run'));
  const state = harness.state('timed');
  emitClaim(harness, state, state.followups[0]!);
  const listener = [...harness.listeners.get('user-questions/request')!][0]!;
  const answer = Promise.resolve(listener({
    agent: state.agent, questions: [{ id: 'target', question: 'Where?' }],
    wait: { callId: 'question-call', timed: true },
  }, async () => ({ answers: [] })));
  await assert.rejects(answer, { code: 'ASK_TIMED_OUT' });
  const requested = client.events.find(e => e.event === 'interaction' && e.payload.phase === 'requested')!.payload;
  await client.deliverRequest('interaction.resolve', 'late', {
    runId: 'timed-run', interactionId: requested.interactionId, kind: 'ask_user',
    idempotencyKey: 'late-key', action: 'submit', answers: { target: { values: ['local'] } },
  });
  assert.equal(client.responses.at(-1)?.error?.code, 'INTERACTION_NOT_FOUND');
  assert.equal(client.events.filter(e => e.event === 'interaction' && e.payload.phase === 'resolved').length, 1);
  await bridge.dispose();
});

test('unloading cancels approval waits and emits exactly one aborted terminal', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'send', chatParams('unload', 'unload-run'));
  const state = harness.state('unload');
  emitClaim(harness, state, state.followups[0]!);
  const outcome = [...harness.listeners.get('approval/request')!][0]!({ agent: state.agent, toolName: 'bash' }, async () => 'unavailable');
  await bridge.dispose();
  await bridge.dispose();
  assert.equal(await outcome, 'cancelled');
  assert.equal(chatEvents(client).filter(e => e.state === 'aborted').length, 1);
  assert.equal(harness.agents.size, 0);
  assert.equal(client.requestHandlers.size, 0);
});

test('maps committed DSH text and durable tool events, excluding failed attempts and reasoning', async () => {
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
    type: 'assistant/message', seq: 1, time: 1,
    data: { turn: 1, step: 1, message: { content: [{ type: 'text', text: 'Hello ' }] } },
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
        toolCallId: 'call-1', content: [{ type: 'text', text: 'ok' }], isError: false,
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
        toolCallId: 'call-1', content: [{ type: 'text', text: 'duplicate' }],
      },
    },
  }));
  emitSession(harness, state, event({
    type: 'assistant/attempt', seq: 6, time: 6,
    data: { turn: 1, step: 2, message: { content: [{ type: 'text', text: 'discarded attempt' }] } },
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
  assert.deepEqual(tools[0]?.args, { path: '/tmp/a' });
  assert.equal(tools[1]?.name, 'read_file');
  assert.deepEqual(tools[1]?.result, { content: [{ type: 'text', text: 'ok' }] });

  const chats = chatEvents(client);
  assert.deepEqual(chats.filter(item => item.state === 'delta').map(item => item.content), ['Hello ', 'world']);
  assert.equal(chats.some(item => JSON.stringify(item).includes('private reasoning')), false);
  const finals = chats.filter(item => item.state === 'final');
  assert.equal(finals.length, 1);
  assert.deepEqual(finals[0]?.usage, { input: 12, output: 3 });
  assert.equal(finals[0]?.content, 'Hello world');
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
          toolCallId: 'shared-call-id', isError: true,
          content: [{ type: 'text', text: `failed-${runId}` }],
        },
        error: { name: 'ToolCallError', code: 'FAILED' },
      },
    }));
  }
  const tools = toolEvents(client);
  assert.equal(tools.length, 4);
  assert.deepEqual(tools.filter(item => item.phase === 'start')
    .map(item => item.args), ['{unfinished', '{unfinished']);
  assert.deepEqual(tools.filter(item => item.phase === 'result')
    .map(item => item.isError), [true, true]);
  assert.deepEqual(new Set(tools.map(item => item.runId)), new Set(['run-a', 'run-b']));
  await bridge.dispose();
});

test('captures bcs_route once, emits its normal tool telemetry, and attaches routing only to final', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  const params = chatParams('route-session', 'run-route');
  params.bcs_session_id = 'session-layer-1';
  await client.deliverRequest('chat.send', 'request-route', params);
  const state = harness.state('session-layer-1');
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
        toolCallId: 'route-call',
        content: [{ type: 'text', text: JSON.stringify(result) }],
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

test('registers manager task tools instead of bcs_route and forwards task requests to BCN', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest(
    'chat.send',
    'request-manager',
    taskGroupParams('manager-session', 'manager-run', 'manager'),
  );
  const state = harness.state('bcs_grp_test:manager-session');
  assert.deepEqual(state.tools.map(tool => tool.name), ['bcs_assign_task', 'bcs_task_complete']);
  emitClaim(harness, state, state.followups[0] as UserMessage);

  const assign = state.tools.find(tool => tool.name === 'bcs_assign_task');
  const complete = state.tools.find(tool => tool.name === 'bcs_task_complete');
  assert.ok(assign);
  assert.ok(complete);
  assert.deepEqual(await assign.execute({
    target_bot: ' Worker ',
    message: ' Investigate the database ',
    response_mode: 'after-last-tool-call',
  }, toolRunContext(state.agent)), {
    ok: true,
    task_id: 'task-1',
    status: 'dispatched',
  });
  assert.deepEqual(await complete.execute({ summary: ' All work is done. ' }, toolRunContext(state.agent)), {
    ok: true,
  });
  assert.deepEqual(client.requests, [
    {
      method: 'task.dispatch',
      params: {
        group_id: 'bcs_grp_test:manager-session',
        target_bot: 'Worker',
        message: 'Investigate the database',
        response_mode: 'after-last-tool-call',
      },
    },
    {
      method: 'task.complete',
      params: { group_id: 'bcs_grp_test:manager-session', summary: 'All work is done.' },
    },
  ]);
  await bridge.dispose();
});

test('registers only the worker task-message tool for a manager-worker worker', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest(
    'chat.send',
    'request-worker',
    taskGroupParams('worker-session', 'worker-run', 'worker'),
  );
  const state = harness.state('bcs_grp_test:worker-session');
  assert.deepEqual(state.tools.map(tool => tool.name), ['bcs_send_task_message']);
  emitClaim(harness, state, state.followups[0] as UserMessage);

  const sendMessage = state.tools[0];
  assert.ok(sendMessage);
  assert.deepEqual(await sendMessage.execute({ message: ' Blocked on credentials. ' }, toolRunContext(state.agent)), {
    ok: true,
    status: 'sent',
  });
  assert.deepEqual(client.requests, [{
    method: 'task.message',
    params: { group_id: 'bcs_grp_test:worker-session', message: 'Blocked on credentials.' },
  }]);
  await bridge.dispose();
});

test('fails closed when manager-worker recipient_role is absent and hides route in mention mode', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest(
    'chat.send',
    'request-no-role',
    taskGroupParams('missing-role-session', 'missing-role-run', undefined),
  );
  assert.deepEqual(harness.state('bcs_grp_test:missing-role-session').tools, []);

  const mentionParams = chatParams('mention-session', 'mention-run');
  mentionParams.session_context = {
    ...(mentionParams.session_context as Record<string, unknown>),
    routing_mode: 'mention',
  };
  await client.deliverRequest('chat.send', 'request-mention', mentionParams);
  assert.deepEqual(harness.state('mention-session').tools, []);
  await bridge.dispose();
});

test('deduplicates inbound runs and emits exactly one DSH aborted or error terminal', async () => {
  const harness = new FakeHarness();
  const client = new FakeClient();
  const bridge = new BcnBridge(harness.context, client as unknown as BcnWsClient, testConfig());
  bridge.start();
  await client.deliverRequest('chat.send', 'request-a', chatParams('abort-session', 'same-run'));
  await client.deliverRequest('chat.send', 'request-b', chatParams('abort-session', 'same-run'));
  const state = harness.state('abort-session');
  assert.equal(state.followups.length, 1);
  emitClaim(harness, state, state.followups[0] as UserMessage);
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
  const error = chatEvents(client).find(item => item.runId === 'error-run' && item.state === 'error');
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
