import { strict as assert } from 'node:assert';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
import { SessionStore } from '../src/store.js';
import { startGatewayServer, type GatewayServer } from '../src/server.js';
import { setDefaultChatRunner, resetDefaultChatRunner } from '../src/chat-orchestrator.js';
import { createFakeRunner, type FakeStep } from './fixtures/fake-claude-cli.js';
import { TestGatewayClient } from './helpers/ws-client.js';

async function bootServer(script: FakeStep[]): Promise<{
  server: GatewayServer;
  store: SessionStore;
  controller: ReturnType<typeof createFakeRunner>;
  storePath: string;
}> {
  const controller = createFakeRunner(script);
  setDefaultChatRunner(controller.runner);
  const storePath = path.join(os.tmpdir(), `model-test-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
  const store = new SessionStore(storePath, { writeDebounceMs: 5 });
  const server = startGatewayServer({ port: 0, store });
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

describe('Model switching (OpenClaw protocol)', () => {

  it('models.list returns available models', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('models.list');
      assert.equal(res.ok, true);
      const models = (res.payload as { models: Array<{ id: string; provider: string }> }).models;
      assert(Array.isArray(models), 'models should be an array');
      assert(models.length > 0, 'should have at least one model');
      for (const m of models) {
        assert(m.id, 'model should have an id');
        assert(m.provider, 'model should have a provider');
      }
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('providers.list returns provider configs', async () => {
    const { server, store, storePath } = await bootServer([{ kind: 'done' }]);
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('providers.list');
      assert.equal(res.ok, true);
      const providers = (res.payload as { providers: Array<{ id: string; name: string; models: unknown[] }> }).providers;
      assert(Array.isArray(providers), 'providers should be an array');
      assert(providers.length > 0, 'should have at least one provider');
      for (const p of providers) {
        assert(p.id, 'provider should have an id');
        assert(p.name, 'provider should have a name');
        assert(Array.isArray(p.models), 'provider should have models array');
      }
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('chat.send passes model param to runner and returns in response', async () => {
    const { server, store, controller, storePath } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);

    try {
      const client = await connectedClient(server.port);
      const res = await client.request('chat.send', {
        sessionKey: 'sess-model',
        message: 'hello',
        model: 'Qwen3.5-397B-A17B',
        cwd: process.cwd(),
      });
      assert.equal(res.ok, true);

      await client.waitForEvent(e =>
        e.event === 'chat' && (e.payload as { state?: string }).state === 'final',
      );

      assert.equal(controller.runs.length, 1, 'should have one run');
      assert.equal(controller.runs[0].params.model, 'Qwen3.5-397B-A17B', 'model should be passed to runner');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('chat.send works without model param (uses default-model fallback)', async () => {
    const { server, store, controller, storePath } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);

    // Isolate the default-model resolver so it deterministically falls back to
    // claude-sonnet-4-5 regardless of any ambient settings.json / RELAY_DEFAULT_MODEL.
    const restore = isolateDefaultModelEnv();
    try {
      const client = await connectedClient(server.port);
      const res = await client.request('chat.send', {
        sessionKey: 'sess-default',
        message: 'hello',
        cwd: process.cwd(),
      });
      assert.equal(res.ok, true);

      await client.waitForEvent(e =>
        e.event === 'chat' && (e.payload as { state?: string }).state === 'final',
      );

      // session.new wasn't called explicitly, but ensureBinding seeds binding.model
      // with the relay's default (claude-sonnet-4-5 fallback when no env / settings.json).
      assert.equal(controller.runs[0].params.model, 'claude-sonnet-4-5', 'binding default model should reach the runner');

      await client.close();
    } finally {
      restore();
      await teardown(server, store, storePath);
    }
  });

  it('chat.send passes mode param to runner', async () => {
    const { server, store, controller, storePath } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'planning' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);

    try {
      const client = await connectedClient(server.port);
      const res = await client.request('chat.send', {
        sessionKey: 'sess-mode',
        message: 'plan this',
        model: 'GLM-5',
        mode: 'plan',
        cwd: process.cwd(),
      });
      assert.equal(res.ok, true);

      await client.waitForEvent(e =>
        e.event === 'chat' && (e.payload as { state?: string }).state === 'final',
      );

      assert.equal(controller.runs[0].params.model, 'GLM-5');
      assert.equal(controller.runs[0].params.permissionMode, 'plan');

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('chat.send injects sessionKey into runner env as HITL_SESSION_KEY', async () => {
    const { server, store, controller, storePath } = await bootServer([
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ]);

    try {
      const client = await connectedClient(server.port);
      const sessionKey = 'session:test-openclaw-session-id:user:ray';
      const res = await client.request('chat.send', {
        sessionKey,
        message: 'hello',
        cwd: process.cwd(),
      });
      assert.equal(res.ok, true);

      await client.waitForEvent(e =>
        e.event === 'chat' && (e.payload as { state?: string }).state === 'final',
      );

      assert.equal(controller.runs.length, 1);
      assert.equal(controller.runs[0].params.env?.HITL_SESSION_KEY, sessionKey);

      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('can switch models between requests in same session', async () => {
    const script: FakeStep[] = [
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'reply' },
      { kind: 'done', stopReason: 'end_turn' },
    ];
    const controller = createFakeRunner(script);
    setDefaultChatRunner(controller.runner);
    const storePath = path.join(os.tmpdir(), `model-switch-${Date.now()}.json`);
    const store = new SessionStore(storePath, { writeDebounceMs: 5 });
    const server = startGatewayServer({ port: 0, store });

    try {
      const client = await connectedClient(server.port);

      // First request with Qwen
      await client.request('chat.send', {
        sessionKey: 'sess-switch',
        message: 'first',
        model: 'Qwen3.5-397B-A17B',
        cwd: process.cwd(),
      });
      await client.waitForEvent(e =>
        e.event === 'chat' && (e.payload as { state?: string }).state === 'final',
      );

      // Second request with GLM-5
      await client.request('chat.send', {
        sessionKey: 'sess-switch',
        message: 'second',
        model: 'GLM-5',
        cwd: process.cwd(),
      });
      await client.waitForEvent(e => {
        if (e.event !== 'chat') return false;
        const p = e.payload as { state?: string; message?: { content?: Array<{ text?: string }> } };
        return p.state === 'final' && !!(p.message?.content?.[0]?.text);
      });

      assert.equal(controller.runs.length, 2);
      assert.equal(controller.runs[0].params.model, 'Qwen3.5-397B-A17B');
      assert.equal(controller.runs[1].params.model, 'GLM-5');

      await client.close();
    } finally {
      await store.flush();
      await server.close();
      resetDefaultChatRunner();
      if (fs.existsSync(storePath)) fs.unlinkSync(storePath);
    }
  });
});
