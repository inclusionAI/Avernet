import { strict as assert } from 'node:assert';
import { once } from 'node:events';
import { mkdtemp, rm } from 'node:fs/promises';
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { WebSocketServer } from 'ws';
import { setBcsRuntime } from '../src/runtime.js';
import {
  abortAllStreams,
  handleChatHistory,
  handleChatInject,
  handleSessionDelete,
  rememberTaskToolSession,
} from '../src/inbound-handler.js';

const REQUIRED_GATEWAY_PROTOCOL = 4;

function createResponseClient() {
  const responses: Array<{
    id: string;
    ok: boolean;
    payload?: Record<string, unknown>;
    error?: Record<string, unknown>;
  }> = [];

  return {
    responses,
    client: {
      sendResponse(
        id: string,
        ok: boolean,
        payload?: Record<string, unknown>,
        error?: Record<string, unknown>,
      ) {
        responses.push({ id, ok, payload, error });
      },
    },
  };
}

async function startGatewayStub(options?: {
  onChatInject?: (frame: any) => void;
  chatInjectPayload?: Record<string, unknown>;
}) {
  const frames: any[] = [];
  const server = new WebSocketServer({ host: '127.0.0.1', port: 0 });
  await once(server, 'listening');

  server.on('connection', socket => {
    socket.send(JSON.stringify({
      type: 'event',
      event: 'connect.challenge',
      payload: { nonce: 'test-nonce' },
    }));

    socket.on('message', data => {
      const frame = JSON.parse(data.toString());
      frames.push(frame);

      if (frame.method === 'connect') {
        const minProtocol = frame.params?.minProtocol;
        const maxProtocol = frame.params?.maxProtocol;
        if (maxProtocol < REQUIRED_GATEWAY_PROTOCOL || minProtocol > REQUIRED_GATEWAY_PROTOCOL) {
          socket.send(JSON.stringify({
            type: 'res',
            id: frame.id,
            ok: false,
            error: {
              code: 'INVALID_REQUEST',
              message: 'protocol mismatch',
              details: {
                expectedProtocol: REQUIRED_GATEWAY_PROTOCOL,
                minimumProbeProtocol: REQUIRED_GATEWAY_PROTOCOL,
              },
            },
          }));
          return;
        }

        socket.send(JSON.stringify({
          type: 'res',
          id: frame.id,
          ok: true,
          payload: { type: 'hello-ok', protocol: REQUIRED_GATEWAY_PROTOCOL },
        }));
        return;
      }

      if (frame.method === 'chat.history') {
        socket.send(JSON.stringify({
          type: 'res',
          id: frame.id,
          ok: true,
          payload: {
            messages: [
              {
                role: 'user',
                content: [{ type: 'text', text: 'hello from history' }],
                timestamp: 1,
              },
            ],
          },
        }));
        return;
      }

      if (frame.method === 'chat.inject') {
        options?.onChatInject?.(frame);
        socket.send(JSON.stringify({
          type: 'res',
          id: frame.id,
          ok: true,
          payload: options?.chatInjectPayload ?? {},
        }));
        return;
      }

      if (frame.method === 'sessions.delete') {
        socket.send(JSON.stringify({
          type: 'res',
          id: frame.id,
          ok: true,
          payload: {},
        }));
      }
    });
  });

  const address = server.address() as AddressInfo | null;
  assert.equal(typeof address, 'object');
  assert.ok(address);

  return {
    port: address.port,
    frames,
    async close() {
      await new Promise<void>((resolve, reject) => {
        server.close(err => (err ? reject(err) : resolve()));
      });
    },
  };
}

function installRuntime(
  port: number,
  storePath: string,
  capturedInbound?: { value?: Record<string, unknown> },
) {
  setBcsRuntime({
    config: {
      async loadConfig() {
        return {
          gateway: {
            port,
            auth: { token: 'test-token' },
          },
          session: { store: storePath },
        };
      },
    },
    channel: {
      routing: {
        resolveAgentRoute() {
          return { agentId: 'agent-1', sessionKey: 'session-1' };
        },
      },
      reply: {
        finalizeInboundContext(ctx: Record<string, unknown>) {
          if (capturedInbound) capturedInbound.value = ctx;
          return ctx;
        },
      },
      session: {
        resolveStorePath() {
          return storePath;
        },
        async recordInboundSession(opts: { sessionKey: string }) {
          mkdirSync(dirname(storePath), { recursive: true });
          writeFileSync(
            storePath,
            JSON.stringify({ [opts.sessionKey]: { sessionId: 'transcript-1' } }),
          );
        },
      },
    },
  } as any);
}

describe('OpenClaw gateway protocol compatibility', () => {
  let previousGatewayPort: string | undefined;

  beforeEach(() => {
    previousGatewayPort = process.env.OPENCLAW_GATEWAY_PORT;
    delete process.env.OPENCLAW_GATEWAY_PORT;
  });

  afterEach(() => {
    if (previousGatewayPort === undefined) {
      delete process.env.OPENCLAW_GATEWAY_PORT;
    } else {
      process.env.OPENCLAW_GATEWAY_PORT = previousGatewayPort;
    }
  });

  it('uses a protocol range that can call chat.history on OpenClaw 2026.5.12', async () => {
    const gateway = await startGatewayStub();
    const dataDir = await mkdtemp(join(tmpdir(), 'bcn-history-'));
    const storePath = join(dataDir, 'sessions', 'sessions.json');
    installRuntime(gateway.port, storePath);
    const { client, responses } = createResponseClient();

    try {
      await handleChatHistory(
        {
          type: 'req',
          id: 'history-1',
          method: 'chat.history',
          params: { session_key: 'group-1', limit: 5 },
        } as any,
        client as any,
        { accountId: 'default', botId: 'bot-1' } as any,
        undefined,
        dataDir,
      );

      assert.equal(responses.length, 1);
      assert.equal(responses[0].ok, true, JSON.stringify(responses[0].error));
      assert.deepEqual(responses[0].payload?.messages, [
        {
          role: 'user',
          content: [{ type: 'text', text: 'hello from history' }],
          timestamp: 1,
        },
      ]);

      const connectFrame = gateway.frames.find(frame => frame.method === 'connect');
      assert.equal(connectFrame?.params?.minProtocol, 3);
      assert.equal(connectFrame?.params?.maxProtocol, REQUIRED_GATEWAY_PROTOCOL);
      assert.deepEqual(connectFrame?.params?.scopes, [ 'operator.read' ]);
      assert.ok(gateway.frames.some(frame => frame.method === 'chat.history'));
    } finally {
      await gateway.close();
      await rm(dataDir, { recursive: true, force: true });
    }
  });

  it('uses a protocol range that can call chat.inject on OpenClaw 2026.5.12', async () => {
    const dataDir = await mkdtemp(join(tmpdir(), 'bcn-inject-'));
    const storePath = join(dataDir, 'sessions', 'sessions.json');
    const transcriptPath = join(dataDir, 'sessions', 'transcript-1.jsonl');
    const gateway = await startGatewayStub({
      chatInjectPayload: { ok: true, messageId: 'msg-injected' },
      onChatInject() {
        writeFileSync(
          transcriptPath,
          [
            JSON.stringify({ type: 'session', version: 3, id: 'transcript-1' }),
            JSON.stringify({
              type: 'message',
              id: 'msg-injected',
              parentId: null,
              timestamp: '2026-08-05T00:00:00.000Z',
              message: {
                role: 'assistant',
                content: [{ type: 'text', text: 'injected observation' }],
                api: 'openai-responses',
                provider: 'openclaw',
                model: 'gateway-injected',
                stopReason: 'stop',
                usage: { totalTokens: 0 },
              },
            }),
          ].join('\n') + '\n',
        );
      },
    });
    const capturedInbound: { value?: Record<string, unknown> } = {};
    installRuntime(gateway.port, storePath, capturedInbound);
    const { client, responses } = createResponseClient();

    try {
      await handleChatInject(
        {
          type: 'req',
          id: 'inject-1',
          method: 'chat.inject',
          params: {
            session_key: 'group-1',
            bcs_group_id: 'group-1',
            message: { content: [] },
            attachments: [{
              attachment_id: 'att-1',
              type: 'image',
              file_name: 'diagram.png',
              url: 'https://download.dingtalk.example/temporary-image-token',
            }],
            channel: { user_id: 'alice' },
            session_context: {
              from: 'alice',
              participants: [],
            },
          },
        } as any,
        client as any,
        { accountId: 'default', botId: 'bot-1' } as any,
        undefined,
        dataDir,
      );

      assert.equal(responses.length, 1);
      assert.equal(responses[0].ok, true);
      const observationText = '[Image attachment: name=diagram.png; image content is not available in this silent observation]';
      assert.equal(capturedInbound.value?.Body, observationText);
      assert.equal(capturedInbound.value?.MediaUrl, undefined);
      assert.equal(capturedInbound.value?.MediaUrls, undefined);
      assert.equal(capturedInbound.value?.MediaPath, undefined);
      assert.equal(capturedInbound.value?.MediaPaths, undefined);

      const connectFrame = gateway.frames.find(frame => frame.method === 'connect');
      assert.equal(connectFrame?.params?.minProtocol, 3);
      assert.equal(connectFrame?.params?.maxProtocol, REQUIRED_GATEWAY_PROTOCOL);
      assert.deepEqual(connectFrame?.params?.scopes, [ 'operator.admin' ]);
      const injectFrame = gateway.frames.find(frame => frame.method === 'chat.inject');
      assert.equal(injectFrame?.params?.message, observationText);
      const transcriptLines = readFileSync(transcriptPath, 'utf8').trim().split('\n');
      const injectedEntry = JSON.parse(transcriptLines[1]);
      assert.equal(injectedEntry.message.role, 'user');
      assert.deepEqual(injectedEntry.message.content, [{ type: 'text', text: 'injected observation' }]);
      assert.equal(injectedEntry.message.provider, undefined);
      assert.equal(injectedEntry.message.model, undefined);
      assert.equal(injectedEntry.message.api, undefined);
      assert.equal(injectedEntry.message.stopReason, undefined);
      assert.equal(injectedEntry.message.usage, undefined);
    } finally {
      await gateway.close();
      await rm(dataDir, { recursive: true, force: true });
    }
  });

  it('falls back to configured gateway port when OPENCLAW_GATEWAY_PORT is invalid', async () => {
    const gateway = await startGatewayStub();
    const dataDir = await mkdtemp(join(tmpdir(), 'bcn-invalid-port-'));
    const storePath = join(dataDir, 'sessions', 'sessions.json');
    installRuntime(gateway.port, storePath);
    process.env.OPENCLAW_GATEWAY_PORT = '-1';
    const { client, responses } = createResponseClient();

    try {
      await handleChatHistory(
        {
          type: 'req',
          id: 'history-invalid-port',
          method: 'chat.history',
          params: { session_key: 'group-1', limit: 5 },
        } as any,
        client as any,
        { accountId: 'default', botId: 'bot-1' } as any,
        undefined,
        dataDir,
      );

      assert.equal(responses[0]?.ok, true, JSON.stringify(responses[0]?.error));
      assert.ok(gateway.frames.some(frame => frame.method === 'chat.history'));
    } finally {
      await gateway.close();
      await rm(dataDir, { recursive: true, force: true });
    }
  });

  it('uses operator.admin scope for gateway-backed session.delete', async () => {
    const gateway = await startGatewayStub();
    const dataDir = await mkdtemp(join(tmpdir(), 'bcn-session-delete-'));
    const storePath = join(dataDir, 'sessions', 'sessions.json');
    installRuntime(gateway.port, storePath);
    const { client, responses } = createResponseClient();

    try {
      rememberTaskToolSession('session-delete-key', {} as any, 'group-delete', {
        session_id: 'group-delete:abcdef12',
        participants: [],
        originator: 'bot-1',
        from: 'bot-1',
        you_are_mentioned: true,
        is_sender: false,
        mentions: [],
        message: 'delete',
      });

      await handleSessionDelete(
        {
          type: 'req',
          id: 'delete-1',
          method: 'session.delete',
          params: { bcs_group_id: 'group-delete' },
        } as any,
        client as any,
        { accountId: 'default', botId: 'bot-1' } as any,
        undefined,
        dataDir,
      );

      assert.equal(responses[0]?.ok, true, JSON.stringify(responses[0]?.error));
      const connectFrame = gateway.frames.find(frame => frame.method === 'connect');
      assert.deepEqual(connectFrame?.params?.scopes, [ 'operator.admin' ]);
      const deleteFrame = gateway.frames.find(frame => frame.method === 'sessions.delete');
      assert.ok(deleteFrame);
      assert.equal('force' in (deleteFrame.params ?? {}), false);
    } finally {
      abortAllStreams();
      await gateway.close();
      await rm(dataDir, { recursive: true, force: true });
    }
  });
});
