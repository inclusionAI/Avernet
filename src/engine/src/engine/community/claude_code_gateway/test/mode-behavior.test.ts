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
  const storePath = path.join(os.tmpdir(), `mode-behavior-${Date.now()}-${Math.random().toString(36).slice(2, 6)}.json`);
  const store = new SessionStore(storePath, { writeDebounceMs: 5 });
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

describe('Mode behavior (gateway params.mode)', () => {
  const supportedModes = [ 'default', 'acceptEdits', 'bypassPermissions', 'plan' ] as const;

  it('chat.send forwards all supported mode values to runner', async () => {
    const script: FakeStep[] = [
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'ok' },
      { kind: 'done', stopReason: 'end_turn' },
    ];
    const { server, store, controller, storePath } = await bootServer(script);

    try {
      const client = await connectedClient(server.port);
      for (const mode of supportedModes) {
        const sessionKey = `sess-mode-${mode}`;
        const res = await client.request('chat.send', {
          sessionKey,
          message: `mode=${mode}`,
          cwd: process.cwd(),
          mode,
        });
        assert.equal(res.ok, true);
        await client.waitForEvent(e =>
          e.event === 'chat' && (e.payload as { state?: string }).state === 'final',
        );
      }

      assert.equal(controller.runs.length, supportedModes.length);
      assert.deepEqual(controller.runs.map(r => r.params.permissionMode), supportedModes);
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('default mode can carry ask_user interactions', async () => {
    const script: FakeStep[] = [
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'toolStart', id: 'ask-1', name: 'AskUserQuestion' },
      { kind: 'toolEnd', id: 'ask-1', name: 'AskUserQuestion', input: {
        questions: [
          {
            question: 'Choose a fruit?',
            header: 'Fruit',
            options: [
              { label: 'Apple', description: 'Red' },
              { label: 'Banana', description: 'Yellow' },
            ],
            multiSelect: false,
          },
        ],
      } },
      { kind: 'done', stopReason: 'end_turn' },
    ];
    const { server, store, storePath } = await bootServer(script);
    try {
      const client = await connectedClient(server.port);
      await client.request('chat.send', {
        sessionKey: 'sess-default-ask',
        message: 'ask me',
        cwd: process.cwd(),
        mode: 'default',
      });
      const requested = await client.waitForEvent(e => e.event === 'interaction.requested');
      const payload = requested.payload as { kind: string; questions?: Array<{ question: string }> };
      assert.equal(payload.kind, 'ask_user');
      assert.equal(payload.questions?.[0]?.question, 'Choose a fruit?');
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('plan mode can carry mode_transition agent stream', async () => {
    const script: FakeStep[] = [
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'toolStart', id: 'plan-1', name: 'ExitPlanMode' },
      { kind: 'toolEnd', id: 'plan-1', name: 'ExitPlanMode', input: {
        fromMode: 'plan',
        toMode: 'execute',
        summary: 'Ready to execute',
      } },
      { kind: 'done', stopReason: 'end_turn' },
    ];
    const { server, store, storePath } = await bootServer(script);
    try {
      const client = await connectedClient(server.port);
      await client.request('chat.send', {
        sessionKey: 'sess-plan-switch',
        message: 'finish plan',
        cwd: process.cwd(),
        mode: 'plan',
      });
      const requested = await client.waitForEvent(e => {
        if (e.event !== 'agent') return false;
        const p = e.payload as { stream?: string; data?: { phase?: string; fromMode?: string; toMode?: string } };
        return p.stream === 'mode_transition' && p.data?.phase === 'requested';
      });
      const payload = (requested.payload as { data?: { fromMode?: string; toMode?: string } }).data;
      assert.equal(payload?.fromMode, 'plan');
      assert.equal(payload?.toMode, 'execute');
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });

  it('acceptEdits and bypassPermissions modes are accepted at protocol level', async () => {
    const script: FakeStep[] = [
      { kind: 'lifecycle', phase: 'start' },
      { kind: 'textDelta', text: 'accepted' },
      { kind: 'done', stopReason: 'end_turn' },
    ];
    const { server, store, controller, storePath } = await bootServer(script);
    try {
      const client = await connectedClient(server.port);
      for (const mode of [ 'acceptEdits', 'bypassPermissions' ] as const) {
        const res = await client.request('chat.send', {
          sessionKey: `sess-${mode}`,
          message: 'hello',
          cwd: process.cwd(),
          mode,
        });
        assert.equal(res.ok, true);
        await client.waitForEvent(e =>
          e.event === 'chat' && (e.payload as { state?: string }).state === 'final',
        );
      }
      assert.deepEqual(controller.runs.map(r => r.params.permissionMode), [ 'acceptEdits', 'bypassPermissions' ]);
      await client.close();
    } finally {
      await teardown(server, store, storePath);
    }
  });
});
