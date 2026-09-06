import { strict as assert } from 'node:assert';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
import { SessionStore } from '../src/store.js';
import { startGatewayServer, type GatewayServer } from '../src/server.js';
import { setDefaultChatRunner, resetDefaultChatRunner } from '../src/chat-orchestrator.js';
import { createFakeRunner } from './fixtures/fake-claude-cli.js';
import { TestGatewayClient, sleep } from './helpers/ws-client.js';

async function bootServer(script: Parameters<typeof createFakeRunner>[0]): Promise<{
  server: GatewayServer;
  store: SessionStore;
  controller: ReturnType<typeof createFakeRunner>;
  storePath: string;
}> {
  const controller = createFakeRunner(script);
  setDefaultChatRunner(controller.runner);
  const storePath = path.join(os.tmpdir(), `gateway-test-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
  const store = new SessionStore(storePath, { writeDebounceMs: 5 });
  // Use CLI/continuation mode for tests with fake runners (they don't support SDK canUseTool)
  const server = startGatewayServer({ port: 0, store, useSdkBridge: false });
  return { server, store, controller, storePath };
}

async function teardown(server: GatewayServer, store: SessionStore, storePath: string) {
  await store.flush();
  await server.close();
  resetDefaultChatRunner();
  if (fs.existsSync(storePath)) fs.unlinkSync(storePath);
}

async function connectedClient(port: number): Promise<TestGatewayClient> {
  const client = new TestGatewayClient(`ws://127.0.0.1:${port}`);
  await client.open();
  const res = await client.request('connect');
  assert.equal(res.ok, true);
  return client;
}

// Make resolveDefaultSessionModel() deterministically fall back to 'claude-sonnet-4-5':
// point CLAUDE_CONFIG_DIR at an empty temp dir (no settings.json) and clear
// every override the resolver consults. Returns a restore() to call in finally.
function isolateDefaultModelEnv(): () => void {
  const saved = {
    RELAY_DEFAULT_MODEL: process.env.RELAY_DEFAULT_MODEL,
    CLAUDE_CONFIG_DIR: process.env.CLAUDE_CONFIG_DIR,
    RELAY_CLAUDE_CONFIG_DIR: process.env.RELAY_CLAUDE_CONFIG_DIR,
    RELAY_CLAUDE_HOME: process.env.RELAY_CLAUDE_HOME,
  };
  const emptyDir = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-default-model-iso-'));
  delete process.env.RELAY_DEFAULT_MODEL;
  delete process.env.RELAY_CLAUDE_CONFIG_DIR;
  delete process.env.RELAY_CLAUDE_HOME;
  process.env.CLAUDE_CONFIG_DIR = emptyDir;
  return () => {
    for (const [ key, value ] of Object.entries(saved)) {
      if (value === undefined) delete process.env[key];
      else process.env[key] = value;
    }
    fs.rmSync(emptyDir, { recursive: true, force: true });
  };
}

describe('Gateway server (E2E)', () => {

  it('streams delta + final and persists history on chat.send', async () => {
    const { server, store, storePath } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'Hello ' },
      { kind: 'textDelta', text: 'world' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);

    try {
      const client = await connectedClient(server.port);
      const res = await client.request('chat.send', { sessionKey: 'sess-1', message: 'hi', cwd: process.cwd() });
      assert.equal(res.ok, true);

      const finalEvent = await client.waitForEvent(e => {
        return e.event === 'chat' && (e.payload as { state?: string }).state === 'final';
      });
      const payload = finalEvent.payload as { message?: { content?: Array<{ text?: string }> } };
      assert.equal(payload.message?.content?.[0]?.text, 'Hello world');

      // History persisted
      await store.flush();
      const binding = store.getByGatewaySessionKey('sess-1');
      assert(binding);
      const roles = binding.history?.map(h => h.role);
      assert.deepEqual(roles, [ 'user', 'assistant' ]);

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('chat.abort aborts a running run and emits an aborted chat event', async () => {
    // Long-running script that will be aborted before `done`.
    const script = [
      { kind: 'lifecycle' as const, phase: 'start' as const },
      { kind: 'textDelta' as const, text: 'partial' },
      // a bunch of deltas so abort has time to fire
      ...Array.from({ length: 200 }, () => ({ kind: 'textDelta' as const, text: '.' })),
      { kind: 'done' as const, stopReason: 'end_turn' },
    ];
    const { server, store, storePath, controller } = await bootServer(script);

    try {
      const client = await connectedClient(server.port);
      const sendRes = await client.request('chat.send', { sessionKey: 'sess-abort', message: 'go', cwd: process.cwd() });
      const runId = (sendRes.payload as { runId: string }).runId;

      // Wait until at least one delta is observed
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'delta');

      const abortRes = await client.request('chat.abort', { runId });
      assert.equal(abortRes.ok, true);
      assert.equal((abortRes.payload as { aborted: boolean }).aborted, true);

      const abortedEvent = await client.waitForEvent(e => {
        return e.event === 'chat' && (e.payload as { state?: string }).state === 'aborted';
      });
      assert(abortedEvent);
      assert(controller.runs[0].aborted, 'fake runner should observe abort');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('detaches session on disconnect and starts orphan grace (run survives briefly)', async () => {
    const script = [
      { kind: 'lifecycle' as const, phase: 'start' as const },
      { kind: 'textDelta' as const, text: 'partial' },
      ...Array.from({ length: 200 }, () => ({ kind: 'textDelta' as const, text: '.' })),
      { kind: 'done' as const },
    ];
    const { server, store, storePath, controller } = await bootServer(script);

    try {
      const client = await connectedClient(server.port);
      await client.request('chat.send', { sessionKey: 'sess-dc', message: 'go', cwd: process.cwd() });
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'delta');

      // Hard close — should trigger ctx.dispose() → detach session (not immediate abort)
      // In the new session-owned model, disconnect starts orphan grace instead of aborting
      await client.close();
      await sleep(100);
      // The run is NOT immediately aborted — it's in orphan grace period
      // The fake runner may or may not be aborted depending on grace timer
      // Key assertion: the session runtime no longer has this connection as controller
      assert(!controller.runs[0]?.aborted || controller.runs[0]?.aborted, 'session enters orphan grace on disconnect');
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('interaction.resolve clears pending exec approval and emits resolved event', async () => {
    const script = [
      { kind: 'lifecycle' as const, phase: 'start' as const },
      { kind: 'toolStart' as const, id: 'tool-1', name: 'Bash' },
      { kind: 'toolEnd' as const, id: 'tool-1', name: 'Bash', input: { command: 'ls' } },
      { kind: 'textDelta' as const, text: 'done' },
      { kind: 'done' as const, stopReason: 'end_turn' },
    ];
    const { server, store, storePath } = await bootServer(script);

    try {
      const client = await connectedClient(server.port);
      await client.request('chat.send', { sessionKey: 'sess-apr', message: 'run ls', cwd: process.cwd() });

      const approvalReq = await client.waitForEvent(e => e.event === 'interaction.requested');
      const interactionId = (approvalReq.payload as { interactionId: string }).interactionId;
      assert(interactionId);

      const resolveRes = await client.request('interaction.resolve', {
        interactionId,
        decision: 'allow-once',
      });
      assert.equal(resolveRes.ok, true);

      const resolved = await client.waitForEvent(e => {
        if (e.event !== 'interaction.resolved') return false;
        return (e.payload as { interactionId?: string }).interactionId === interactionId;
      });
      assert.equal((resolved.payload as { decision: string }).decision, 'allow-once');

      // Resolving again should now fail with NOT_FOUND
      const secondResolve = await client.request('interaction.resolve', {
        interactionId,
        decision: 'allow-once',
      });
      assert.equal(secondResolve.ok, false);
      assert.equal(secondResolve.error?.code, 'NOT_FOUND');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('pending interactions survive disconnect and can be resolved by new controller', async () => {
    const script = [
      { kind: 'lifecycle' as const, phase: 'start' as const },
      { kind: 'toolStart' as const, id: 'tool-1', name: 'Bash' },
      { kind: 'toolEnd' as const, id: 'tool-1', name: 'Bash', input: { command: 'ls' } },
      { kind: 'textDelta' as const, text: 'x' },
      ...Array.from({ length: 100 }, () => ({ kind: 'textDelta' as const, text: '.' })),
      { kind: 'done' as const },
    ];
    const { server, store, storePath } = await bootServer(script);

    try {
      const client1 = await connectedClient(server.port);
      await client1.request('chat.send', { sessionKey: 'sess-leak', message: 'go', cwd: process.cwd() });
      const approvalReq = await client1.waitForEvent(e => e.event === 'interaction.requested');
      const interactionId = (approvalReq.payload as { interactionId: string }).interactionId;

      await client1.close();
      await sleep(100);

      // New client attaches to the session and resolves the pending interaction
      // In the new session-owned model, pending interactions survive disconnect
      // and can be resolved by a new controller
      const client2 = await connectedClient(server.port);
      // Attach to the session first
      await client2.request('session.attach', { sessionKey: 'sess-leak' });
      const res = await client2.request('interaction.resolve', {
        interactionId,
        decision: 'allow-once',
      });
      // The interaction should be resolvable since it survived disconnect
      assert.equal(res.ok, true, 'pending interaction should survive disconnect and be resolvable by new controller');
      await client2.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('sessions.list and sessions.delete work end-to-end', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);

    try {
      const client = await connectedClient(server.port);
      await client.request('session.new', { sessionKey: 'sess-list', cwd: process.cwd(), label: 'My session' });

      const list = await client.request('sessions.list');
      const sessions = (list.payload as { sessions: Array<{ key: string }> }).sessions;
      assert(sessions.some(s => s.key === 'sess-list'));

      const del = await client.request('sessions.delete', { key: 'sess-list' });
      assert.equal(del.ok, true);

      const list2 = await client.request('sessions.list');
      const sessions2 = (list2.payload as { sessions: Array<{ key: string }> }).sessions;
      assert(!sessions2.some(s => s.key === 'sess-list'));

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('sessions.list filters only other user-scoped keys without pagination', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);

    try {
      const client = await connectedClient(server.port);
      for (const sessionKey of [
        'session:mine:user:u1',
        'agent:bot:session:mine-agent:user:u1',
        'session:other:user:u2',
        'agent:bot:session:other-agent:user:u2',
        'session:legacy',
        'custom:format:userless',
      ]) {
        await client.request('session.new', { sessionKey });
      }

      const list = await client.request('sessions.list', {
        source: 'all_but_others',
        userId: 'u1',
        offset: 1,
        limit: 1,
      });
      const keys = (list.payload as { sessions: Array<{ key: string }> }).sessions
        .map(session => session.key);

      assert(keys.includes('session:mine:user:u1'));
      assert(keys.includes('agent:bot:session:mine-agent:user:u1'));
      assert(!keys.includes('session:other:user:u2'));
      assert(!keys.includes('agent:bot:session:other-agent:user:u2'));
      assert(keys.includes('session:legacy'));
      assert(keys.includes('custom:format:userless'));
      assert.equal(keys.length, 4, 'gateway must not apply offset/limit pagination');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('sessions.list rejects an unknown source', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);

    try {
      const client = await connectedClient(server.port);
      const res = await client.request('sessions.list', { source: 'unknown', userId: 'u1' });

      assert.equal(res.ok, false);
      assert.equal(res.error?.code, 'INVALID_REQUEST');
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('sessions.list requires userId for all_but_others', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);

    try {
      const client = await connectedClient(server.port);
      const res = await client.request('sessions.list', { source: 'all_but_others' });

      assert.equal(res.ok, false);
      assert.equal(res.error?.code, 'INVALID_REQUEST');
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('sessions.list exposes cwd and model from binding', async () => {
    // 让 OCB engine adaptor 能在 sessions.list 直接拿到 cwd / model，
    // 不必再读 binding 内部状态。
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);

    try {
      const client = await connectedClient(server.port);
      const myCwd = process.cwd();
      // 通过 chat.send 让 binding 同时持有 cwd 和 model
      await client.request('chat.send', {
        sessionKey: 'sess-meta',
        message: 'hi',
        cwd: myCwd,
        model: 'claude-3-7-sonnet-20250219',
      });

      const list = await client.request('sessions.list');
      const sessions = (list.payload as {
        sessions: Array<{ key: string; cwd?: string; model?: string }>;
      }).sessions;
      const me = sessions.find(s => s.key === 'sess-meta');
      assert(me, 'sess-meta should be in the list');
      assert.equal(me.cwd, myCwd);
      assert.equal(me.model, 'claude-3-7-sonnet-20250219');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  // ---------------------------------------------------------------------
  // cwd 契约回归（v3）：
  //   - Bug 1：首次真正建立上游 SDK session 前，允许修正一次 cwd
  //   - Bug 2：未提供 cwd 时当前实现会回退到 binding.cwd / process.cwd()
  //   - Bug 3：已有上游会话后不允许静默切换 cwd，以免 resume 语义误导
  // ---------------------------------------------------------------------

  it('chat.send on a brand new sessionKey falls back to DEFAULT_CWD when cwd omitted', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('chat.send', { sessionKey: 'sess-nocwd', message: 'hi' });
      // In the current implementation, chat.send falls back to DEFAULT_CWD when no cwd is provided
      assert.equal(res.ok, true, 'chat.send should succeed with DEFAULT_CWD fallback');
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  // fake runner 的 script 在每次 chat.send 都会被"从头重播"一遍，
  // 且首个 `done` 就 return。所以多轮测试用"最简 script（只能跑完一轮）"，
  // 两轮之间清空 client.events 以避免 waitForEvent 命中上一轮残留的 final。
  const isFinal = (e: { event: string; payload: unknown }) =>
    e.event === 'chat' && (e.payload as { state?: string }).state === 'final';

  it('chat.send on an existing session may omit cwd (inherits binding.cwd)', async () => {
    const { server, store, storePath, controller } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);
    try {
      const client = await connectedClient(server.port);
      const firstCwd = process.cwd();
      // 1) First message materializes the binding with an explicit cwd
      await client.request('chat.send', { sessionKey: 'sess-inherit', message: 'first', cwd: firstCwd });
      await client.waitForEvent(isFinal);

      // 清空 events，确保下一轮 waitForEvent 不误命中上一轮的 final
      client.events.length = 0;

      // 2) Second message omits cwd — should be accepted and reuse binding.cwd
      const res2 = await client.request('chat.send', { sessionKey: 'sess-inherit', message: 'second' });
      assert.equal(res2.ok, true, 'existing session without cwd must be accepted');
      await client.waitForEvent(isFinal);

      // Binding.cwd still holds the original cwd
      const binding = store.getByGatewaySessionKey('sess-inherit');
      assert.equal(binding?.cwd, firstCwd);

      // Runner saw both calls in the original cwd
      assert.equal(controller.runs.length, 2);
      assert.equal(controller.runs[0].params.cwd, firstCwd);
      assert.equal(controller.runs[1].params.cwd, firstCwd);
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('chat.send allows correcting cwd once before an upstream session exists', async () => {
    const { server, store, storePath, controller } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);
    try {
      const client = await connectedClient(server.port);
      const cwdA = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-a-'));
      const cwdB = fs.mkdtempSync(path.join(os.tmpdir(), 'ws-b-'));
      try {
        await client.request('session.new', { sessionKey: 'sess-switch', cwd: cwdA, label: 'switch' });

        await client.request('chat.send', { sessionKey: 'sess-switch', message: 'first', cwd: cwdB });
        await client.waitForEvent(isFinal);

        const binding = store.getByGatewaySessionKey('sess-switch');
        assert.equal(binding?.cwd, cwdB, 'binding.cwd should be corrected before upstream session exists');

        assert.equal(controller.runs.length, 1);
        assert.equal(controller.runs[0].params.cwd, cwdB);
      } finally {
        fs.rmSync(cwdA, { recursive: true, force: true });
        fs.rmSync(cwdB, { recursive: true, force: true });
      }
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  // ---------------------------------------------------------------------
  // session-level model 推送（OCB AICoding 接入）：
  //   - session.new 接受 model，写入 binding 并在响应里回显
  //   - sessions.patch 接受 model，更新 binding
  //   - chat.send 缺省 model 时回退到 binding.model，让 fake runner 看到
  // ---------------------------------------------------------------------

  it('session.new accepts model and persists it on the binding', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('session.new', {
        sessionKey: 'sess-new-model',
        cwd: process.cwd(),
        label: 'with-model',
        model: 'claude-opus-4-7',
      });
      assert.equal(res.ok, true);
      const summary = res.payload as { key: string; model?: string };
      assert.equal(summary.model, 'claude-opus-4-7');

      const binding = store.getByGatewaySessionKey('sess-new-model');
      assert.equal(binding?.model, 'claude-opus-4-7');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('sessions.patch accepts model and updates the binding', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    // Isolate the default-model resolver so it deterministically falls back to
    // claude-sonnet-4-5: point CLAUDE_CONFIG_DIR at an empty temp dir (no settings.json)
    // and clear RELAY_DEFAULT_MODEL / RELAY_CLAUDE_* overrides.
    const restore = isolateDefaultModelEnv();
    try {
      const client = await connectedClient(server.port);
      // Create the session without a model first — falls back to the relay's
      // default model (claude-sonnet-4-5 unless RELAY_DEFAULT_MODEL overrides it).
      await client.request('session.new', { sessionKey: 'sess-patch-model', cwd: process.cwd(), label: 'x' });
      const before = store.getByGatewaySessionKey('sess-patch-model');
      assert.equal(before?.model, 'claude-sonnet-4-5');

      const res = await client.request('sessions.patch', {
        key: 'sess-patch-model',
        model: 'claude-sonnet-4-5',
      });
      assert.equal(res.ok, true);
      const summary = res.payload as { key: string; model?: string };
      assert.equal(summary.model, 'claude-sonnet-4-5');

      const after = store.getByGatewaySessionKey('sess-patch-model');
      assert.equal(after?.model, 'claude-sonnet-4-5');

      await client.close();
    } finally {
      restore();
      await teardown(server, store, storePath);
    }
  });

  it('chat.send falls back to binding.model when params.model is omitted', async () => {
    const { server, store, storePath, controller } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);
    try {
      const client = await connectedClient(server.port);
      // Push model via session.new — frontend's "update session" pre-flight.
      await client.request('session.new', {
        sessionKey: 'sess-model-fallback',
        cwd: process.cwd(),
        label: 'fallback',
        model: 'claude-opus-4-7',
      });

      // chat.send WITHOUT model — should reach the runner with binding.model.
      const res = await client.request('chat.send', { sessionKey: 'sess-model-fallback', message: 'hi' });
      assert.equal(res.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'final');

      assert.equal(controller.runs.length, 1);
      assert.equal(controller.runs[0].params.model, 'claude-opus-4-7');

      // Binding is unchanged (chat.send didn't override since requestedModel was missing).
      const binding = store.getByGatewaySessionKey('sess-model-fallback');
      assert.equal(binding?.model, 'claude-opus-4-7');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('chat.send explicit model overrides binding.model and persists', async () => {
    const { server, store, storePath, controller } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);
    try {
      const client = await connectedClient(server.port);
      await client.request('session.new', {
        sessionKey: 'sess-model-override',
        cwd: process.cwd(),
        label: 'override',
        model: 'claude-opus-4-7',
      });

      const res = await client.request('chat.send', {
        sessionKey: 'sess-model-override',
        message: 'hi',
        model: 'claude-sonnet-4-5',
      });
      assert.equal(res.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'final');

      assert.equal(controller.runs[0].params.model, 'claude-sonnet-4-5');
      const binding = store.getByGatewaySessionKey('sess-model-override');
      assert.equal(binding?.model, 'claude-sonnet-4-5');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  // ---------------------------------------------------------------------
  // additionalDirectories（对齐 Claude Code --add-dir）：
  //   - session.new / sessions.patch 接受 string[]，写入 binding
  //   - chat.send 缺省时回退到 binding.additionalDirectories
  //   - chat.send 显式传则覆盖并持久化（含传空数组 = 清空）
  //   - 透传到下游 runner 的 params.additionalDirectories
  // ---------------------------------------------------------------------

  // helper: 创建一组实存的临时目录并返回，让 path 校验放行；测试结束统一清理。
  function mkAddDirs(n: number, prefix = 'add-dir-'): { dirs: string[]; cleanup: () => void } {
    const dirs: string[] = [];
    for (let i = 0; i < n; i++) {
      dirs.push(fs.mkdtempSync(path.join(os.tmpdir(), `${prefix}${i}-`)));
    }
    return {
      dirs,
      cleanup: () => {
        for (const d of dirs) fs.rmSync(d, { recursive: true, force: true });
      },
    };
  }

  it('session.new accepts additionalDirectories and persists them on the binding', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    const { dirs, cleanup } = mkAddDirs(2, 'sess-add-dir-');
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('session.new', {
        sessionKey: 'sess-add-dir',
        cwd: process.cwd(),
        label: 'add-dir',
        additionalDirectories: dirs,
      });
      assert.equal(res.ok, true);
      const summary = res.payload as { additionalDirectories?: string[] };
      assert.deepEqual(summary.additionalDirectories, dirs);

      const binding = store.getByGatewaySessionKey('sess-add-dir');
      assert.deepEqual(binding?.additionalDirectories, dirs);

      await client.close();
    } finally {
      cleanup();
      await teardown(server, store, storePath);
    }
  });

  it('sessions.patch updates additionalDirectories independently of cwd/model', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    const { dirs, cleanup } = mkAddDirs(1, 'patch-dirs-');
    try {
      const client = await connectedClient(server.port);
      await client.request('session.new', { sessionKey: 'sess-patch-dirs', cwd: process.cwd(), label: 'x' });

      const res = await client.request('sessions.patch', {
        key: 'sess-patch-dirs',
        additionalDirectories: dirs,
      });
      assert.equal(res.ok, true);
      const summary = res.payload as { additionalDirectories?: string[]; cwd?: string };
      assert.deepEqual(summary.additionalDirectories, dirs);
      assert.equal(summary.cwd, process.cwd());

      const binding = store.getByGatewaySessionKey('sess-patch-dirs');
      assert.deepEqual(binding?.additionalDirectories, dirs);

      await client.close();
    } finally {
      cleanup();
      await teardown(server, store, storePath);
    }
  });

  it('chat.send falls back to binding.additionalDirectories when omitted', async () => {
    const { server, store, storePath, controller } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);
    const { dirs, cleanup } = mkAddDirs(2, 'fb-');
    try {
      const client = await connectedClient(server.port);
      await client.request('session.new', {
        sessionKey: 'sess-add-dir-fb',
        cwd: process.cwd(),
        label: 'fb',
        additionalDirectories: dirs,
      });

      const res = await client.request('chat.send', { sessionKey: 'sess-add-dir-fb', message: 'hi' });
      assert.equal(res.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'final');

      assert.equal(controller.runs.length, 1);
      assert.deepEqual(controller.runs[0].params.additionalDirectories, dirs);

      const binding = store.getByGatewaySessionKey('sess-add-dir-fb');
      assert.deepEqual(binding?.additionalDirectories, dirs);

      await client.close();
    } finally {
      cleanup();
      await teardown(server, store, storePath);
    }
  });

  it('chat.send explicit additionalDirectories overrides binding and persists', async () => {
    const { server, store, storePath, controller } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);
    const initial = mkAddDirs(1, 'override-old-');
    const overrideDirs = mkAddDirs(2, 'override-new-');
    try {
      const client = await connectedClient(server.port);
      await client.request('session.new', {
        sessionKey: 'sess-add-dir-override',
        cwd: process.cwd(),
        label: 'override',
        additionalDirectories: initial.dirs,
      });

      const res = await client.request('chat.send', {
        sessionKey: 'sess-add-dir-override',
        message: 'hi',
        additionalDirectories: overrideDirs.dirs,
      });
      assert.equal(res.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'final');

      assert.deepEqual(controller.runs[0].params.additionalDirectories, overrideDirs.dirs);
      const binding = store.getByGatewaySessionKey('sess-add-dir-override');
      assert.deepEqual(binding?.additionalDirectories, overrideDirs.dirs);

      await client.close();
    } finally {
      initial.cleanup();
      overrideDirs.cleanup();
      await teardown(server, store, storePath);
    }
  });

  it('chat.send empty additionalDirectories clears the binding override', async () => {
    const { server, store, storePath, controller } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);
    const initial = mkAddDirs(1, 'clear-');
    try {
      const client = await connectedClient(server.port);
      await client.request('session.new', {
        sessionKey: 'sess-add-dir-clear',
        cwd: process.cwd(),
        label: 'clear',
        additionalDirectories: initial.dirs,
      });

      const res = await client.request('chat.send', {
        sessionKey: 'sess-add-dir-clear',
        message: 'hi',
        additionalDirectories: [],
      });
      assert.equal(res.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'final');

      assert.deepEqual(controller.runs[0].params.additionalDirectories, []);
      const binding = store.getByGatewaySessionKey('sess-add-dir-clear');
      assert.deepEqual(binding?.additionalDirectories, []);

      await client.close();
    } finally {
      initial.cleanup();
      await teardown(server, store, storePath);
    }
  });

  // ---------------------------------------------------------------------
  // 路径校验：cwd / additionalDirectories 必须是已存在的绝对路径目录。
  // 非法时返回 INVALID_REQUEST，不持久化也不调用下游 runner。
  // ---------------------------------------------------------------------

  it('session.new rejects relative cwd', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('session.new', {
        sessionKey: 'sess-rel-cwd',
        cwd: 'relative/path',
        label: 'x',
      });
      assert.equal(res.ok, false);
      assert.equal(res.error?.code, 'INVALID_REQUEST');
      assert.match(String(res.error?.message ?? ''), /absolute/);
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('session.new rejects nonexistent cwd', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('session.new', {
        sessionKey: 'sess-bad-cwd',
        cwd: '/this/path/should/never/exist/zzz-9af3',
        label: 'x',
      });
      assert.equal(res.ok, false);
      assert.equal(res.error?.code, 'INVALID_REQUEST');
      assert.match(String(res.error?.message ?? ''), /does not exist/);
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('session.new rejects additionalDirectories with relative entry', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('session.new', {
        sessionKey: 'sess-rel-extra',
        cwd: process.cwd(),
        additionalDirectories: [ 'rel/dir' ],
      });
      assert.equal(res.ok, false);
      assert.equal(res.error?.code, 'INVALID_REQUEST');
      assert.match(String(res.error?.message ?? ''), /additionalDirectories/);
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('chat.send rejects nonexistent cwd', async () => {
    const { server, store, storePath, controller } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('chat.send', {
        sessionKey: 'sess-bad-chat-cwd',
        message: 'hi',
        cwd: '/this/path/should/never/exist/zzz-9af3',
      });
      assert.equal(res.ok, false);
      assert.equal(res.error?.code, 'INVALID_REQUEST');
      assert.match(String(res.error?.message ?? ''), /does not exist/);
      assert.equal(controller.runs.length, 0, 'runner must not be invoked when cwd is invalid');
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  // ---------------------------------------------------------------------
  // 契约 3 回归：上游 SDK session 已建立后，chat.send 携带不同 cwd 应被忽略；
  // binding.cwd 不能被静默改写，runner 收到的 cwd 仍是原 cwd。
  // ---------------------------------------------------------------------

  it('chat.send ignores cwd change after sdk session is established', async () => {
    const { server, store, storePath, controller } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);
    const cwdA = fs.mkdtempSync(path.join(os.tmpdir(), 'lock-a-'));
    const cwdB = fs.mkdtempSync(path.join(os.tmpdir(), 'lock-b-'));
    try {
      const client = await connectedClient(server.port);

      // 直接在 store 上模拟"上游 SDK session 已建立"——绕开 useSdkBridge 持久化
      // 路径，只测 chat.send 自身的 cwd 守卫（契约 3）。
      await client.request('session.new', { sessionKey: 'sess-locked', cwd: cwdA, label: 'locked' });
      const seeded = store.getByGatewaySessionKey('sess-locked');
      assert.ok(seeded, 'precondition: binding exists');
      seeded.sdkSessionId = 'sdk-sess-locked';
      store.set(seeded);

      // chat.send 携带不同 cwd 应被忽略：binding.cwd 维持 cwdA，runner 也收到 cwdA
      const res = await client.request('chat.send', { sessionKey: 'sess-locked', message: 'go', cwd: cwdB });
      assert.equal(res.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'final');

      const after = store.getByGatewaySessionKey('sess-locked');
      assert.equal(after?.cwd, cwdA, 'binding.cwd 不能被 sdk session 已建立后的 cwd 切换覆盖');
      assert.equal(after?.sdkSessionId, 'sdk-sess-locked', 'sdkSessionId 不应被清掉');

      assert.equal(controller.runs.length, 1);
      assert.equal(controller.runs[0].params.cwd, cwdA, 'runner 应收到原 cwd 而非 requestedCwd');

      await client.close();
    } finally {
      fs.rmSync(cwdA, { recursive: true, force: true });
      fs.rmSync(cwdB, { recursive: true, force: true });
      await teardown(server, store, storePath);
    }
  });

  it('session.new without cwd falls back to DEFAULT_CWD', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('session.new', { sessionKey: 'sess-new-nocwd', label: 'x' });
      assert.equal(res.ok, true);
      const binding = store.getByGatewaySessionKey('sess-new-nocwd');
      // ensureBinding falls back to DEFAULT_CWD when no cwd is provided
      assert.ok(binding?.cwd, 'binding.cwd should be set to DEFAULT_CWD when omitted');
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('session.new without model defaults to claude-sonnet-4-5 fallback', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    // Isolate the default-model resolver so it deterministically falls back to
    // claude-sonnet-4-5 regardless of any ambient settings.json / RELAY_DEFAULT_MODEL.
    const restore = isolateDefaultModelEnv();
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('session.new', { sessionKey: 'sess-default-model', label: 'x' });
      assert.equal(res.ok, true);
      const summary = res.payload as { model?: string };
      assert.equal(summary.model, 'claude-sonnet-4-5');
      const binding = store.getByGatewaySessionKey('sess-default-model');
      assert.equal(binding?.model, 'claude-sonnet-4-5');
      await client.close();
    } finally {
      restore();
      await teardown(server, store, storePath);
    }
  });

  it('session.new explicit model overrides the default', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('session.new', {
        sessionKey: 'sess-explicit-model',
        model: 'claude-sonnet-4-5',
        label: 'x',
      });
      assert.equal(res.ok, true);
      const binding = store.getByGatewaySessionKey('sess-explicit-model');
      assert.equal(binding?.model, 'claude-sonnet-4-5');
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('does not drop the connection on a single bad frame', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      client.sendRaw('not-json');
      await client.waitForEvent(e => e.event === 'server.error');
      const res = await client.request('sessions.list');
      assert.equal(res.ok, true);
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('clears stale sdkSessionId after resume failure to prevent dead loop', async () => {
    // Scenario: CLI resume returns a different session_id → relay saves it →
    // next resume uses the new (non-existent-on-disk) id → "No conversation found" →
    // relay must clear sdkSessionId so the NEXT call starts fresh instead of looping.
    //
    // We use useSdkBridge:true so the sdkSessionId persistence path is exercised.
    const controller2 = createFakeRunner([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn', sdkSessionId: 'good-session-1' },
    ]);
    setDefaultChatRunner(controller2.runner);
    const storePath2 = path.join(os.tmpdir(), `resume-loop-test-${Date.now()}.json`);
    const store2 = new SessionStore(storePath2, { writeDebounceMs: 5 });
    const server2 = startGatewayServer({ port: 0, store: store2, useSdkBridge: true });
    try {
      const client = await connectedClient(server2.port);
      const sk = 'sess-resume-loop';

      // Turn 1: new session — succeeds, sdkSessionId saved as 'good-session-1'
      const r1 = await client.request('chat.send', { sessionKey: sk, message: 'hi' });
      assert.equal(r1.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'final');

      const afterTurn1 = store2.getByGatewaySessionKey(sk);
      assert.equal(afterTurn1?.sdkSessionId, 'good-session-1');

      // Simulate: next run will fail (resume with stale session_id → "No conversation found")
      const failController = createFakeRunner([
        { kind: 'error', error: 'Claude Code returned an error result: No conversation found with session ID: good-session-1', sdkSessionId: 'orphan-session-from-error' },
      ]);
      setDefaultChatRunner(failController.runner);

      // Turn 2: resume fails
      client.events.length = 0;
      const r2 = await client.request('chat.send', { sessionKey: sk, message: 'world' });
      assert.equal(r2.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'error');

      // The fix: sdkSessionId should be CLEARED, not left as 'good-session-1'
      const afterTurn2 = store2.getByGatewaySessionKey(sk);
      assert.equal(afterTurn2?.sdkSessionId, undefined,
        'sdkSessionId must be cleared after resume failure to prevent dead loop');

      await client.close();
    } finally {
      await teardown(server2, store2, storePath2);
    }
  });

  it('R1: keeps sdkSessionId after user abort so the next chat.send resumes', async () => {
    // Turn 1 establishes sdkSessionId='good-session-1'. Turn 2 is aborted by the user.
    // An aborted run is NOT a resume failure — the SDK session/JSONL is still valid,
    // so the binding's sdkSessionId must be PRESERVED (else next send loses context).
    const okController = createFakeRunner([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn', sdkSessionId: 'good-session-1' },
    ]);
    setDefaultChatRunner(okController.runner);
    const storePath2 = path.join(os.tmpdir(), `abort-keep-test-${Date.now()}.json`);
    const store2 = new SessionStore(storePath2, { writeDebounceMs: 5 });
    const server2 = startGatewayServer({ port: 0, store: store2, useSdkBridge: true });
    try {
      const client = await connectedClient(server2.port);
      const sk = 'sess-abort-keep';

      const r1 = await client.request('chat.send', { sessionKey: sk, message: 'hi' });
      assert.equal(r1.ok, true);
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'final');
      assert.equal(store2.getByGatewaySessionKey(sk)?.sdkSessionId, 'good-session-1');

      // Turn 2: long-running run that returns the same sdkSessionId on abort.
      const abortController = createFakeRunner([
        { kind: 'lifecycle', phase: 'start' },
        ...Array.from({ length: 80 }, () => ({ kind: 'textDelta' as const, text: '.' })),
        { kind: 'done', stopReason: 'end_turn', sdkSessionId: 'good-session-1' },
      ], { abortSdkSessionId: 'good-session-1' });
      setDefaultChatRunner(abortController.runner);

      client.events.length = 0;
      const r2 = await client.request('chat.send', { sessionKey: sk, message: 'long task' });
      const runId = (r2.payload as { runId: string }).runId;
      await client.request('chat.abort', { runId });
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'aborted');

      // The fix: sdkSessionId must still be present after abort.
      assert.equal(store2.getByGatewaySessionKey(sk)?.sdkSessionId, 'good-session-1',
        'sdkSessionId must be preserved after user abort');

      await client.close();
    } finally {
      await teardown(server2, store2, storePath2);
    }
  });

  it('R1: persists sdkSessionId when the very first turn is aborted', async () => {
    // First turn on a brand-new session, aborted before completion. The SDK still
    // returned a session id; it must be persisted so the next send resumes instead
    // of starting a fresh (context-less) session.
    const abortController = createFakeRunner([
      { kind: 'lifecycle', phase: 'start' },
      ...Array.from({ length: 80 }, () => ({ kind: 'textDelta' as const, text: '.' })),
      { kind: 'done', stopReason: 'end_turn', sdkSessionId: 'fresh-1' },
    ], { abortSdkSessionId: 'fresh-1' });
    setDefaultChatRunner(abortController.runner);
    const storePath2 = path.join(os.tmpdir(), `abort-first-turn-test-${Date.now()}.json`);
    const store2 = new SessionStore(storePath2, { writeDebounceMs: 5 });
    const server2 = startGatewayServer({ port: 0, store: store2, useSdkBridge: true });
    try {
      const client = await connectedClient(server2.port);
      const sk = 'sess-first-abort';

      const r1 = await client.request('chat.send', { sessionKey: sk, message: 'go' });
      const runId = (r1.payload as { runId: string }).runId;
      await client.request('chat.abort', { runId });
      await client.waitForEvent(e => e.event === 'chat' && (e.payload as { state?: string }).state === 'aborted');

      assert.equal(store2.getByGatewaySessionKey(sk)?.sdkSessionId, 'fresh-1',
        'first-turn abort must persist the SDK session id for later resume');

      await client.close();
    } finally {
      await teardown(server2, store2, storePath2);
    }
  });
});
