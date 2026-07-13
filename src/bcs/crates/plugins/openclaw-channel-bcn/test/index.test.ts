import { strict as assert } from 'node:assert';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import plugin, { bcsPlugin, registerBcsCore, setBcsRuntime } from '../src/index.js';
import { getDefaultBcsUrl, listAccountIds, resolveAccount } from '../src/accounts.js';
import {
  abortAllStreams,
  cleanupAgentEventsSubscription,
  combineDeliveredReplyParts,
  handleChatSend,
  handleBcsRouteTool,
  initAgentEventsSubscription,
  rememberTaskToolSession,
  resolveGroupIdFromSessionKey,
} from '../src/inbound-handler.js';
import type { RequestFrame, ResolvedBcsAccount } from '../src/types.js';

const __dirname = dirname(fileURLToPath(import.meta.url));
const noop = () => undefined;
type RegisteredToolFactory = (ctx: Record<string, unknown>) => unknown;

function listSourceFiles(dir: string): string[] {
  const files: string[] = [];
  for (const entry of readdirSync(dir)) {
    const fullPath = join(dir, entry);
    if (statSync(fullPath).isDirectory()) {
      files.push(...listSourceFiles(fullPath));
    } else if (fullPath.endsWith('.ts')) {
      files.push(fullPath);
    }
  }
  return files;
}

describe('openclaw-channel-bcn', () => {
  it('resolves default and named BCS accounts', () => {
    const cfg = {
      channels: {
        bcs: {
          bcsUrl: 'ws://localhost:21000/ws/bot',
          botId: 'primary-bot',
          botName: 'Primary Bot',
          accounts: {
            secondary: {
              botId: 'secondary-bot',
            },
          },
        },
      },
    };

    assert.deepEqual(listAccountIds(cfg), [ 'default', 'secondary' ]);

    const primary = resolveAccount(cfg);
    assert.equal(primary.accountId, 'default');
    assert.equal(primary.bcsUrl, 'ws://localhost:21000/ws/bot');
    assert.equal(primary.botId, 'primary-bot');
    assert.equal(primary.botName, 'Primary Bot');

    const secondary = resolveAccount(cfg, 'secondary');
    assert.equal(secondary.accountId, 'secondary');
    assert.equal(secondary.botId, 'secondary-bot');
    assert.equal(secondary.botName, 'Primary Bot');
  });

  it('does not fall back to internal BCS endpoints in the public package', () => {
    const originalBcsUrl = process.env.BCS_URL;
    const originalAgentClawEnv = process.env.AGENTCLAW_ENV;
    const originalEnv = process.env.env;

    delete process.env.BCS_URL;
    process.env.AGENTCLAW_ENV = 'pre';
    process.env.env = 'pre';

    try {
      assert.equal(getDefaultBcsUrl(), '');
      assert.equal(resolveAccount({}).bcsUrl, '');
    } finally {
      if (originalBcsUrl === undefined) {
        delete process.env.BCS_URL;
      } else {
        process.env.BCS_URL = originalBcsUrl;
      }
      if (originalAgentClawEnv === undefined) {
        delete process.env.AGENTCLAW_ENV;
      } else {
        process.env.AGENTCLAW_ENV = originalAgentClawEnv;
      }
      if (originalEnv === undefined) {
        delete process.env.env;
      } else {
        process.env.env = originalEnv;
      }
    }
  });

  it('does not resolve Cookie authentication from public config or environment', () => {
    const originalBcsCookie = process.env.BCS_COOKIE;
    process.env.BCS_COOKIE = 'session=env-secret';

    try {
      const cfg = {
        channels: {
          bcs: {
            bcsUrl: 'ws://localhost:21000/ws/bot',
            cookie: 'session=cfg-secret',
            accounts: {
              secondary: {
                cookie: 'session=account-secret',
              },
            },
          },
        },
      };

      assert.equal('cookie' in resolveAccount(cfg), false);
      assert.equal('cookie' in resolveAccount(cfg, 'secondary'), false);
    } finally {
      if (originalBcsCookie === undefined) {
        delete process.env.BCS_COOKIE;
      } else {
        process.env.BCS_COOKIE = originalBcsCookie;
      }
    }
  });

  it('resolves BCS DM policy and allowlist from channel config', () => {
    const cfg = {
      channels: {
        bcs: {
          bcsUrl: 'ws://localhost:21000/ws/bot',
          dmPolicy: 'allowlist',
          allowFrom: [ 'alice', 'Bob' ],
        },
      },
    };
    const account = resolveAccount(cfg) as any;

    assert.equal(account.dmPolicy, 'allowlist');
    assert.deepEqual(account.allowFrom, [ 'alice', 'Bob' ]);
    assert.deepEqual((bcsPlugin.config as any).resolveAllowFrom({ cfg }), [ 'alice', 'Bob' ]);
    assert.deepEqual((bcsPlugin.security as any).resolveDmPolicy(account), {
      policy: 'allowlist',
      allowFrom: [ 'alice', 'Bob' ],
    });
  });

  it('registers the BCS channel plugin and stores runtime', () => {
    const runtime = {
      marker: 'runtime',
    };
    let registeredChannel: unknown;
    let registeredTool: unknown;

    plugin.register({
      runtime,
      registerChannel(payload: unknown) {
        registeredChannel = payload;
      },
      registerTool(tool: unknown) {
        registeredTool = tool;
      },
      on: noop,
    } as any);

    // registerChannel receives bcsPlugin directly (ChannelPlugin type)
    assert.equal((registeredChannel as any)?.id, 'bcs');
    assert.ok(typeof registeredTool === 'function', 'bcs_route tool factory should be registered');
    assert.equal(resolveGroupIdFromSessionKey('missing-session'), undefined);
    setBcsRuntime(runtime as any);
  });

  it('declares every registered agent tool in the OpenClaw manifest contract', () => {
    const registeredToolNames: string[] = [];
    const manifest = JSON.parse(
      readFileSync(join(__dirname, '..', 'openclaw.plugin.json'), 'utf8'),
    ) as {
      contracts?: {
        tools?: string[];
      };
    };

    plugin.register({
      runtime: {},
      registerChannel: noop,
      registerTool(tool: unknown, opts?: { name?: string; names?: string[] }) {
        registeredToolNames.push(
          ...(opts?.names ?? []),
          ...(opts?.name ? [ opts.name ] : []),
        );
        if (typeof tool === 'object' && tool && 'name' in tool && typeof tool.name === 'string') {
          registeredToolNames.push(tool.name);
        }
      },
      on: noop,
    } as any);

    const declaredTools = new Set(manifest.contracts?.tools ?? []);
    const missingTools = [ ...new Set(registeredToolNames) ]
      .filter(name => !declaredTools.has(name))
      .sort();

    assert.deepEqual(missingTools, []);
    assert.equal(declaredTools.has('hitl_request'), false);
  });

  it('requires a ws version with the known close reason fix', () => {
    const pkg = JSON.parse(
      readFileSync(join(__dirname, '..', 'package.json'), 'utf8'),
    ) as {
      dependencies?: Record<string, string>;
    };

    assert.equal(pkg.dependencies?.ws, '^8.18.3');
  });

  it('does not ship internal-only implementation details in public source files', () => {
    const forbidden = [
      'hitl',
      'SECBAAS',
      'humanloop',
      'function.alipay.com',
      'AGENTCLAW_SANDBOX',
      'tc_sdb_nenv',
      'BOT_DATA_DIR',
      '~/.credentials',
      'bcs.example.com',
      'service-bot-session',
      'env-detect',
    ];
    const matches: string[] = [];

    for (const file of listSourceFiles(join(__dirname, '..', 'src'))) {
      const content = readFileSync(file, 'utf-8');
      for (const token of forbidden) {
        if (content.includes(token)) {
          matches.push(`${file.slice(join(__dirname, '..').length + 1)}:${token}`);
        }
      }
    }

    assert.deepEqual(matches, []);
  });

  it('warns once when plugin loads without channels.bcs.bcsUrl configured', async () => {
    const warnings: string[] = [];
    const runtime = {
      config: {
        async loadConfig() {
          return {
            channels: {
              bcs: {
                enabled: true,
              },
            },
          };
        },
      },
    };

    plugin.register({
      runtime,
      logging: {
        getLogger() {
          return {
            warn(message: string) {
              warnings.push(message);
            },
          };
        },
      },
      registerChannel: noop,
      registerTool: noop,
      on: noop,
    } as any);

    await new Promise(resolve => setTimeout(resolve, 0));

    assert.equal(warnings.length, 1);
    assert.match(warnings[0], /plugin loaded, but BCS channel runtime did not start/i);
    assert.match(warnings[0], /channels\.bcs\.bcsUrl/);

    plugin.register({
      runtime,
      logging: {
        getLogger() {
          return {
            warn(message: string) {
              warnings.push(message);
            },
          };
        },
      },
      registerChannel: noop,
      registerTool: noop,
      on: noop,
    } as any);

    await new Promise(resolve => setTimeout(resolve, 0));
    assert.equal(warnings.length, 1, 'warning should only be emitted once');
  });

  it('does not register internal HITL tools or before_tool_call hooks in the public package', () => {
    const registeredToolNames: string[] = [];
    const registeredEvents: string[] = [];

    plugin.register({
      runtime: {},
      registerChannel: noop,
      registerTool(tool: unknown, opts?: { name?: string; names?: string[] }) {
        registeredToolNames.push(
          ...(opts?.names ?? []),
          ...(opts?.name ? [ opts.name ] : []),
        );
        if (typeof tool === 'object' && tool && 'name' in tool && typeof tool.name === 'string') {
          registeredToolNames.push(tool.name);
        }
      },
      on(eventName: string) {
        registeredEvents.push(eventName);
      },
    } as any);

    assert.deepEqual(
      [ ...new Set(registeredToolNames) ].sort(),
      [ 'bcs_assign_task', 'bcs_route', 'bcs_send_task_message', 'bcs_task_complete' ].sort(),
    );
    assert.equal(registeredEvents.includes('before_tool_call'), false);
  });

  it('does not expose unused chat.abort request handling in public source files', () => {
    const matches: string[] = [];

    for (const file of listSourceFiles(join(__dirname, '..', 'src'))) {
      const content = readFileSync(file, 'utf-8');
      if (content.includes('chat.abort') || content.includes('ChatAbortParams')) {
        matches.push(file.slice(join(__dirname, '..').length + 1));
      }
    }

    assert.deepEqual(matches, []);
  });

  it('does not activate manager task tools for legacy task groups without a local bot uuid', () => {
    const originalBotUuid = process.env.BCN_BOT_UUID;
    delete process.env.BCN_BOT_UUID;
    abortAllStreams();

    try {
      rememberTaskToolSession('legacy-task-session', {} as any, 'group-1', {
        session_id: 'group-1',
        participants: [ 'Manager(bot-manager)', 'Worker(bot-worker)' ],
        originator: 'Manager(bot-manager)',
        from: 'Manager(bot-manager)',
        you_are_mentioned: true,
        is_sender: false,
        mentions: [ 'Worker' ],
        message: 'task',
        group_type: 'manager_worker',
      });

      const factories = new Map<string, RegisteredToolFactory>();
      registerBcsCore({
        runtime: {},
        registerChannel: noop,
        registerTool(tool: unknown, opts?: { name?: string }) {
          if (typeof tool === 'function' && opts?.name) {
            factories.set(opts.name, tool as RegisteredToolFactory);
          }
        },
        on: noop,
      } as any, { warnWhenMissingBcsUrl: false });

      const ctx = { messageChannel: 'bcs', sessionKey: 'legacy-task-session' };
      assert.equal(factories.get('bcs_assign_task')?.(ctx), null);
      assert.equal(factories.get('bcs_task_complete')?.(ctx), null);
    } finally {
      abortAllStreams();
      if (originalBotUuid === undefined) {
        delete process.env.BCN_BOT_UUID;
      } else {
        process.env.BCN_BOT_UUID = originalBotUuid;
      }
    }
  });

  it('exposes BCS channel metadata', () => {
    assert.equal(plugin.id, 'openclaw-channel-bcn');
    assert.equal(bcsPlugin.id, 'bcs');
    assert.equal(bcsPlugin.meta.label, 'BCS');
    assert.equal(bcsPlugin.capabilities.media, false);
  });

  it('does not synthesize a fallback reply when OpenClaw returns no text', () => {
    assert.equal(combineDeliveredReplyParts([]), undefined);
    assert.equal(combineDeliveredReplyParts([ '  ', '' ]), undefined);
    assert.equal(combineDeliveredReplyParts([ 'first', 'second' ]), 'first\n\nsecond');
  });

  it('builds chat deltas and final from assistant agent events', async () => {
    const responses: Array<{ id: string; ok: boolean; payload?: Record<string, unknown> }> = [];
    const events: Array<{ event: string; payload: Record<string, unknown>; seq: number }> = [];
    let agentEventHandler: ((evt: Record<string, unknown>) => boolean) | undefined;
    let capturedReplyOptions: Record<string, unknown> | undefined;
    const client = {
      sendResponse(id: string, ok: boolean, payload?: Record<string, unknown>) {
        responses.push({ id, ok, payload });
      },
      sendEvent(event: string, payload: Record<string, unknown>, seq: number) {
        events.push({ event, payload, seq });
      },
    };
    const account: ResolvedBcsAccount = {
      accountId: 'default',
      enabled: true,
      bcsUrl: 'ws://bcs.test/ws/bot',
      botId: 'bot-1',
      botName: 'Bot 1',
      capabilities: {
        summary: 'test bot',
        domains: [],
        skills: [],
        scopes: [],
      },
      heartbeatIntervalMs: 60000,
      reconnectIntervalMs: 5000,
      connectionTimeoutMs: 10000,
    };
    const runtime = {
      config: {
        async loadConfig() {
          return {};
        },
      },
      events: {
        onAgentEvent(handler: (evt: Record<string, unknown>) => boolean) {
          agentEventHandler = handler;
          return () => {
            agentEventHandler = undefined;
          };
        },
      },
      channel: {
        routing: {
          resolveAgentRoute() {
            return { agentId: 'agent-1', sessionKey: 'bcs:group-1' };
          },
        },
        reply: {
          finalizeInboundContext(ctx: Record<string, unknown>) {
            return ctx;
          },
          async dispatchReplyWithBufferedBlockDispatcher({ dispatcherOptions, replyOptions }: any) {
            capturedReplyOptions = replyOptions;
            const runId = replyOptions.runId;
            agentEventHandler?.({
              runId,
              stream: 'assistant',
              ts: 1,
              data: { text: 'snapshot: before tool', delta: 'before tool' },
            });
            agentEventHandler?.({
              runId,
              stream: 'tool',
              ts: 2,
              data: { phase: 'start', toolCallId: 'tool-1' },
            });
            agentEventHandler?.({
              runId,
              stream: 'assistant',
              ts: 3,
              data: { text: 'snapshot: before tool after first tool', delta: '\nafter first tool' },
            });
            agentEventHandler?.({
              runId,
              stream: 'tool',
              ts: 4,
              data: { phase: 'result', toolCallId: 'tool-1' },
            });
            agentEventHandler?.({
              runId,
              stream: 'assistant',
              ts: 5,
              data: { text: 'snapshot: before tool after first tool final answer', delta: '\nfinal answer' },
            });
            agentEventHandler?.({
              runId,
              stream: 'lifecycle',
              ts: 6,
              data: { phase: 'end' },
            });
            await dispatcherOptions.deliver({ text: 'stale dispatcher block' }, { kind: 'block' });
            await dispatcherOptions.deliver({ text: 'stale dispatcher final' }, { kind: 'final' });
          },
        },
        session: {
          resolveStorePath() {
            return '/tmp/openclaw-bcn-test';
          },
          async recordInboundSession() {
            return undefined;
          },
        },
      },
    };
    setBcsRuntime(runtime as any);
    initAgentEventsSubscription();

    const request: RequestFrame = {
      type: 'req',
      id: 'chat-1',
      method: 'chat.send',
      params: {
        bcs_group_id: 'group-1',
        channel: { source: 'api', user_id: 'user-1' },
        session_context: {},
        message: {
          role: 'user',
          content: [{ type: 'text', text: 'run with tools' }],
          timestamp: Date.now(),
        },
      },
    };

    try {
      await handleChatSend(request, client as any, account);

      assert.equal(responses.length, 1);
      assert.equal(responses[0].ok, true);
      const runId = responses[0].payload?.run_id;
      assert.equal(typeof runId, 'string');
      assert.equal(capturedReplyOptions?.disableBlockStreaming, false);
      assert.equal(capturedReplyOptions?.sourceReplyDeliveryMode, 'automatic');
      assert.equal(events.filter(item => item.event === 'agent').length, 6);
      const chatEvents = events.filter(item => item.event === 'chat.event');
      assert.deepEqual(chatEvents.map(item => item.payload.state), [ 'delta', 'delta', 'delta', 'final' ]);
      assert.deepEqual(
        chatEvents.map(item => (item.payload.message as any).content[0].text),
        [
          'before tool',
          '\nafter first tool',
          '\nfinal answer',
          'before tool\nafter first tool\nfinal answer',
        ],
      );
      assert.deepEqual(chatEvents.map(item => item.payload.run_id), [ runId, runId, runId, runId ]);
    } finally {
      cleanupAgentEventsSubscription();
      abortAllStreams();
    }
  });

  it('sends lifecycle final before dispatcher settles without duplicating final', async () => {
    const responses: Array<{ id: string; ok: boolean; payload?: Record<string, unknown> }> = [];
    const events: Array<{ event: string; payload: Record<string, unknown>; seq: number }> = [];
    let agentEventHandler: ((evt: Record<string, unknown>) => boolean) | undefined;
    let releaseDispatch: (() => void) | undefined;
    const client = {
      sendResponse(id: string, ok: boolean, payload?: Record<string, unknown>) {
        responses.push({ id, ok, payload });
      },
      sendEvent(event: string, payload: Record<string, unknown>, seq: number) {
        events.push({ event, payload, seq });
      },
    };
    const account: ResolvedBcsAccount = {
      accountId: 'default',
      enabled: true,
      bcsUrl: 'ws://bcs.test/ws/bot',
      botId: 'bot-1',
      botName: 'Bot 1',
      capabilities: {
        summary: 'test bot',
        domains: [],
        skills: [],
        scopes: [],
      },
      heartbeatIntervalMs: 60000,
      reconnectIntervalMs: 5000,
      connectionTimeoutMs: 10000,
    };
    const runtime = {
      config: {
        async loadConfig() {
          return {};
        },
      },
      events: {
        onAgentEvent(handler: (evt: Record<string, unknown>) => boolean) {
          agentEventHandler = handler;
          return () => {
            agentEventHandler = undefined;
          };
        },
      },
      channel: {
        routing: {
          resolveAgentRoute() {
            return { agentId: 'agent-1', sessionKey: 'bcs:group-1' };
          },
        },
        reply: {
          finalizeInboundContext(ctx: Record<string, unknown>) {
            return ctx;
          },
          async dispatchReplyWithBufferedBlockDispatcher({ replyOptions }: any) {
            const runId = replyOptions.runId;
            agentEventHandler?.({
              runId,
              stream: 'assistant',
              ts: 1,
              data: { delta: 'reply before dispatcher completion' },
            });
            agentEventHandler?.({
              runId,
              stream: 'lifecycle',
              ts: 2,
              data: { phase: 'end' },
            });
            await new Promise<void>(resolve => {
              releaseDispatch = resolve;
            });
          },
        },
        session: {
          resolveStorePath() {
            return '/tmp/openclaw-bcn-test';
          },
          async recordInboundSession() {
            return undefined;
          },
        },
      },
    };
    setBcsRuntime(runtime as any);
    initAgentEventsSubscription();

    const request: RequestFrame = {
      type: 'req',
      id: 'chat-lifecycle-final',
      method: 'chat.send',
      params: {
        bcs_group_id: 'group-1',
        channel: { source: 'api', user_id: 'user-1' },
        session_context: {},
        message: {
          role: 'user',
          content: [{ type: 'text', text: 'finish from lifecycle' }],
          timestamp: Date.now(),
        },
      },
    };

    try {
      const pending = handleChatSend(request, client as any, account);
      await new Promise(resolve => setImmediate(resolve));

      assert.equal(responses.length, 1);
      const runId = responses[0].payload?.run_id;
      const chatEventsBeforeDispatchSettles = events.filter(item => item.event === 'chat.event');
      assert.deepEqual(
        chatEventsBeforeDispatchSettles.map(item => item.payload.state),
        [ 'delta', 'final' ],
      );
      assert.deepEqual(
        chatEventsBeforeDispatchSettles.map(item => (item.payload.message as any).content[0].text),
        [ 'reply before dispatcher completion', 'reply before dispatcher completion' ],
      );
      assert.deepEqual(chatEventsBeforeDispatchSettles.map(item => item.payload.run_id), [ runId, runId ]);

      assert.ok(releaseDispatch, 'dispatcher should still be waiting when lifecycle final is sent');
      releaseDispatch();
      await pending;

      const finalEvents = events
        .filter(item => item.event === 'chat.event')
        .filter(item => item.payload.state === 'final');
      assert.equal(finalEvents.length, 1, 'dispatcher completion must not send a duplicate final');
    } finally {
      cleanupAgentEventsSubscription();
      abortAllStreams();
      releaseDispatch?.();
    }
  });

  it('sends NO_REPLY final when no assistant agent text is observed', async () => {
    const responses: Array<{ id: string; ok: boolean; payload?: Record<string, unknown> }> = [];
    const events: Array<{ event: string; payload: Record<string, unknown>; seq: number }> = [];
    let agentEventHandler: ((evt: Record<string, unknown>) => boolean) | undefined;
    const client = {
      sendResponse(id: string, ok: boolean, payload?: Record<string, unknown>) {
        responses.push({ id, ok, payload });
      },
      sendEvent(event: string, payload: Record<string, unknown>, seq: number) {
        events.push({ event, payload, seq });
      },
    };
    const account: ResolvedBcsAccount = {
      accountId: 'default',
      enabled: true,
      bcsUrl: 'ws://bcs.test/ws/bot',
      botId: 'bot-1',
      botName: 'Bot 1',
      capabilities: {
        summary: 'test bot',
        domains: [],
        skills: [],
        scopes: [],
      },
      heartbeatIntervalMs: 60000,
      reconnectIntervalMs: 5000,
      connectionTimeoutMs: 10000,
    };
    const runtime = {
      config: {
        async loadConfig() {
          return {};
        },
      },
      events: {
        onAgentEvent(handler: (evt: Record<string, unknown>) => boolean) {
          agentEventHandler = handler;
          return () => {
            agentEventHandler = undefined;
          };
        },
      },
      channel: {
        routing: {
          resolveAgentRoute() {
            return { agentId: 'agent-1', sessionKey: 'bcs:group-1' };
          },
        },
        reply: {
          finalizeInboundContext(ctx: Record<string, unknown>) {
            return ctx;
          },
          async dispatchReplyWithBufferedBlockDispatcher({ dispatcherOptions }: any) {
            agentEventHandler?.({
              runId: 'unrelated-run',
              stream: 'assistant',
              ts: 1,
              data: { text: 'ignored text', delta: 'ignored text' },
            });
            await dispatcherOptions.deliver({ text: 'visible part 1' }, { kind: 'block' });
            await dispatcherOptions.deliver({ text: 'visible part 2' }, { kind: 'block' });
          },
        },
        session: {
          resolveStorePath() {
            return '/tmp/openclaw-bcn-test';
          },
          async recordInboundSession() {
            return undefined;
          },
        },
      },
    };
    setBcsRuntime(runtime as any);
    initAgentEventsSubscription();

    const request: RequestFrame = {
      type: 'req',
      id: 'chat-1',
      method: 'chat.send',
      params: {
        bcs_group_id: 'group-1',
        channel: { source: 'api', user_id: 'user-1' },
        session_context: {},
        message: {
          role: 'user',
          content: [{ type: 'text', text: 'run with block reply' }],
          timestamp: Date.now(),
        },
      },
    };

    try {
      await handleChatSend(request, client as any, account);

      assert.equal(responses.length, 1);
      const runId = responses[0].payload?.run_id;
      const chatEvents = events.filter(item => item.event === 'chat.event');
      assert.deepEqual(chatEvents.map(item => item.payload.state), [ 'final' ]);
      assert.deepEqual(
        chatEvents.map(item => (item.payload.message as any).content[0].text),
        [ 'NO_REPLY' ],
      );
      assert.deepEqual(chatEvents.map(item => item.payload.run_id), [ runId ]);
    } finally {
      cleanupAgentEventsSubscription();
      abortAllStreams();
    }
  });

  it('returns route.resolve candidates when bcs_route targets an unknown name', async () => {
    const requests: Array<{ method: string; params: Record<string, unknown>; timeoutMs?: number }> = [];
    const client = {
      sendRequest(method: string, params: Record<string, unknown>, timeoutMs?: number) {
        requests.push({ method, params, timeoutMs });
        return Promise.resolve({
          type: 'res',
          id: 'route-1',
          ok: true,
          payload: {
            ok: false,
            error: 'UNKNOWN_ROUTE_TARGET',
            message: 'No participant matched name "钻石玩家"',
            candidates: [
              {
                bot_uuid: '20260604_m869qsxf:146836',
                bot_name: 'qz的德州钻石玩家bot_v1',
                role: 'consultant',
              },
            ],
          },
        });
      },
    };

    try {
      rememberTaskToolSession('route-miss-session', client as any, 'group-1', {
        session_id: 'group-1:abcdef12',
        participants: [ '20260604_m869qsxf:146836' ],
        originator: '20260512_1fl0038o:437240',
        from: '20260512_1fl0038o:437240',
        you_are_mentioned: true,
        is_sender: false,
        mentions: [],
        message: '轮到我了，先看下手牌。',
      });

      const result = await handleBcsRouteTool('run-route-miss', 'route-miss-session', {
        to: [{ type: 'name', value: '钻石玩家' }],
        reason: 'ask target player to respond',
      });

      assert.equal(result.ok, false);
      assert.equal(result.error, 'UNKNOWN_ROUTE_TARGET');
      assert.deepEqual(result.candidates, [
        {
          bot_uuid: '20260604_m869qsxf:146836',
          bot_name: 'qz的德州钻石玩家bot_v1',
          role: 'consultant',
        },
      ]);
      assert.deepEqual(requests, [
        {
          method: 'route.resolve',
          params: {
            group_id: 'group-1',
            session_id: 'group-1:abcdef12',
            selectors: [{ type: 'name', value: '钻石玩家' }],
          },
          timeoutMs: 10000,
        },
      ]);
    } finally {
      abortAllStreams();
    }
  });

  it('normalizes v2 session-form bcs_group_id before route.resolve', async () => {
    const requests: Array<{ method: string; params: Record<string, unknown>; timeoutMs?: number }> = [];
    const client = {
      sendRequest(method: string, params: Record<string, unknown>, timeoutMs?: number) {
        requests.push({ method, params, timeoutMs });
        return Promise.resolve({
          type: 'res',
          id: 'route-v2-session',
          ok: true,
          payload: {
            ok: true,
            resolved: [
              {
                type: 'bot',
                value: 'bot-target',
                bot_name: 'Target Bot',
                role: 'consultant',
              },
            ],
          },
        });
      },
    };

    try {
      rememberTaskToolSession('route-v2-session', client as any, 'group-1:abcdef12', {
        session_id: 'group-1',
        participants: [ 'bot-target' ],
        originator: 'bot-driver',
        from: 'bot-driver',
        you_are_mentioned: true,
        is_sender: false,
        mentions: [],
        message: 'route this',
      });

      const result = await handleBcsRouteTool('run-route-v2-session', 'route-v2-session', {
        to: [{ type: 'name', value: 'Target Bot' }],
        reason: 'ask target to respond',
      });

      assert.equal(result.ok, true);
      assert.deepEqual(requests, [
        {
          method: 'route.resolve',
          params: {
            group_id: 'group-1',
            session_id: 'group-1:abcdef12',
            selectors: [{ type: 'name', value: 'Target Bot' }],
          },
          timeoutMs: 10000,
        },
      ]);
    } finally {
      abortAllStreams();
    }
  });

  it('captures canonical bot selectors returned by route.resolve', async () => {
    const client = {
      sendRequest() {
        return Promise.resolve({
          type: 'res',
          id: 'route-2',
          ok: true,
          payload: {
            ok: true,
            resolved: [
              {
                type: 'bot',
                value: '20260604_m869qsxf:146836',
                bot_name: 'qz的德州钻石玩家bot_v1',
                role: 'consultant',
              },
            ],
          },
        });
      },
    };

    try {
      rememberTaskToolSession('route-hit-session', client as any, 'group-1', {
        session_id: 'group-1:abcdef12',
        participants: [ '20260604_m869qsxf:146836' ],
        originator: '20260512_1fl0038o:437240',
        from: '20260512_1fl0038o:437240',
        you_are_mentioned: true,
        is_sender: false,
        mentions: [],
        message: '轮到我了，先看下手牌。',
      });

      const result = await handleBcsRouteTool('run-route-hit', 'route-hit-session', {
        to: [{ type: 'name', value: 'qz的德州钻石玩家bot_v1' }],
        reason: 'ask exact target to respond',
      });

      assert.equal(result.ok, true);
      assert.equal(result.captured, true);
      assert.deepEqual(result.resolved, [
        {
          type: 'bot',
          value: '20260604_m869qsxf:146836',
          bot_name: 'qz的德州钻石玩家bot_v1',
          role: 'consultant',
        },
      ]);
    } finally {
      abortAllStreams();
    }
  });

  it('deduplicates and caps accumulated bcs_route intents on the final chat event', async () => {
    const responses: Array<{ id: string; ok: boolean; payload?: Record<string, unknown> }> = [];
    const events: Array<{ event: string; payload: Record<string, unknown>; seq: number }> = [];
    const client = {
      sendResponse(id: string, ok: boolean, payload?: Record<string, unknown>) {
        responses.push({ id, ok, payload });
      },
      sendEvent(event: string, payload: Record<string, unknown>, seq: number) {
        events.push({ event, payload, seq });
      },
      sendRequest(_method: string, params: Record<string, unknown>) {
        const selector = (params.selectors as Array<{ value: string }>)[0];
        return Promise.resolve({
          type: 'res',
          id: 'route-limit',
          ok: true,
          payload: {
            ok: true,
            resolved: [
              {
                type: 'bot',
                value: selector.value,
                bot_name: selector.value,
              },
            ],
          },
        });
      },
    };
    const account: ResolvedBcsAccount = {
      accountId: 'default',
      enabled: true,
      bcsUrl: 'ws://bcs.test/ws/bot',
      botId: 'bot-1',
      botName: 'Bot 1',
      capabilities: {
        summary: 'test bot',
        domains: [],
        skills: [],
        scopes: [],
      },
      heartbeatIntervalMs: 60000,
      reconnectIntervalMs: 5000,
      connectionTimeoutMs: 10000,
    };
    const runtime = {
      config: {
        async loadConfig() {
          return {};
        },
      },
      channel: {
        routing: {
          resolveAgentRoute() {
            return { agentId: 'agent-1', sessionKey: 'route-limit-session' };
          },
        },
        reply: {
          finalizeInboundContext(ctx: Record<string, unknown>) {
            return ctx;
          },
          async dispatchReplyWithBufferedBlockDispatcher({ dispatcherOptions, replyOptions }: any) {
            const runId = String(replyOptions.runId);
            for (let index = 0; index < 25; index += 1) {
              await handleBcsRouteTool(runId, 'route-limit-session', {
                to: [{ type: 'bot', value: `bot-${index}` }],
                reason: `reason-${index}-${'x'.repeat(30)}`,
              });
            }
            await handleBcsRouteTool(runId, 'route-limit-session', {
              to: [{ type: 'bot', value: 'bot-3' }],
              reason: 'duplicate target should not grow responders',
            });
            await dispatcherOptions.deliver({ text: 'final answer' }, { kind: 'final' });
          },
        },
        session: {
          resolveStorePath() {
            return '/tmp/openclaw-bcn-test';
          },
          async recordInboundSession() {
            return undefined;
          },
        },
      },
    };
    setBcsRuntime(runtime as any);

    try {
      await handleChatSend({
        type: 'req',
        id: 'chat-route-limit',
        method: 'chat.send',
        params: {
          bcs_group_id: 'group-route-limit',
          channel: { source: 'api', user_id: 'user-1' },
          session_context: {},
          message: {
            role: 'user',
            content: [{ type: 'text', text: 'route to many bots' }],
            timestamp: Date.now(),
          },
        },
      }, client as any, account);

      assert.equal(responses[0]?.ok, true);
      const finalEvent = events.find(item => item.payload.state === 'final');
      const routing = finalEvent?.payload.routing as any;
      assert.equal(routing.responders.length, 20);
      assert.equal(new Set(routing.responders.map((item: { value: string }) => item.value)).size, 20);
      assert.deepEqual(
        routing.responders.map((item: { value: string }) => item.value),
        Array.from({ length: 20 }, (_item, index) => `bot-${index}`),
      );
      assert.equal(routing.reason.length <= 500, true);
    } finally {
      abortAllStreams();
    }
  });
});
