#!/usr/bin/env node

import { once } from 'node:events';
import { mkdir, writeFile } from 'node:fs/promises';
import { dirname } from 'node:path';
import process from 'node:process';
import { WebSocketServer } from 'ws';

function parseArgs(argv) {
  const values = {
    host: '127.0.0.1',
    port: 0,
    timeoutMs: 120_000,
    expectedText: 'OPENCLAW_COMPAT_OK',
  };
  for (let index = 0; index < argv.length; index += 1) {
    const token = argv[index];
    const next = argv[index + 1];
    if (!token.startsWith('--') || next === undefined) {
      throw new Error(`invalid argument: ${token}`);
    }
    const key = token.slice(2).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    values[key] = next;
    index += 1;
  }
  values.port = Number(values.port);
  values.timeoutMs = Number(values.timeoutMs);
  for (const required of [ 'readyFile', 'resultFile', 'framesFile' ]) {
    if (!values[required]) throw new Error(`--${required.replace(/[A-Z]/g, c => `-${c.toLowerCase()}`)} is required`);
  }
  return values;
}

function responseFrame(id, payload = {}) {
  return { type: 'res', id, ok: true, payload };
}

function textFromChatEvent(frame) {
  const blocks = frame?.payload?.message?.content;
  if (!Array.isArray(blocks)) return '';
  return blocks
    .filter(block => block?.type === 'text' && typeof block.text === 'string')
    .map(block => block.text)
    .join('\n');
}

async function ensureParent(path) {
  await mkdir(dirname(path), { recursive: true });
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  await Promise.all([ args.readyFile, args.resultFile, args.framesFile ].map(ensureParent));

  const frames = [];
  const observations = {
    connected: false,
    chatAck: false,
    agentEvents: 0,
    chatDeltas: 0,
    chatFinals: 0,
    chatErrors: 0,
    finalText: '',
  };
  let finished = false;
  let chatSent = false;
  let timeout;

  const server = new WebSocketServer({ host: args.host, port: args.port });
  await once(server, 'listening');
  const address = server.address();
  if (!address || typeof address === 'string') throw new Error('mock BCS did not expose a TCP address');
  const wsUrl = `ws://${args.host}:${address.port}/ws/bot`;
  await writeFile(args.readyFile, `${JSON.stringify({ ws_url: wsUrl })}\n`, 'utf8');

  async function persistFrames() {
    await writeFile(args.framesFile, frames.map(frame => JSON.stringify(frame)).join('\n') + '\n', 'utf8');
  }

  async function finish(ok, reason) {
    if (finished) return;
    finished = true;
    clearTimeout(timeout);
    const result = {
      ok,
      reason,
      expected_text: args.expectedText,
      observations,
    };
    await persistFrames();
    await writeFile(args.resultFile, `${JSON.stringify(result, null, 2)}\n`, 'utf8');
    for (const client of server.clients) client.close();
    await new Promise(resolve => server.close(resolve));
    process.exitCode = ok ? 0 : 1;
  }

  timeout = setTimeout(() => {
    void finish(false, 'timed out waiting for a final chat.event');
  }, args.timeoutMs);

  server.on('connection', socket => {
    socket.on('message', data => {
      let frame;
      try {
        frame = JSON.parse(data.toString());
      } catch {
        void finish(false, 'plugin sent invalid JSON');
        return;
      }
      frames.push(frame);

      if (frame.type === 'req') {
        if (frame.method === 'bot.connect') {
          observations.connected = true;
          socket.send(JSON.stringify(responseFrame(frame.id, {
            is_new: false,
            bot_uuid: frame.params?.bot_id || 'openclaw-compat-bot',
            token: 'openclaw-compat-token',
            protocol_version: 2,
          })));
          if (!chatSent) {
            chatSent = true;
            setTimeout(() => {
              if (socket.readyState !== 1) return;
              socket.send(JSON.stringify({
                type: 'req',
                id: 'compat-chat-1',
                method: 'chat.send',
                params: {
                  session_key: 'compat-group',
                  bcs_group_id: 'compat-group',
                  bcs_session_id: 'compat-group:session-1',
                  idempotency_key: 'compat-run-1',
                  channel: { source: 'api', user_id: 'compat-user' },
                  message: {
                    role: 'user',
                    content: [{
                      type: 'text',
                      text: `Reply with exactly ${args.expectedText}`,
                    }],
                    timestamp: Date.now(),
                  },
                  session_context: {
                    session_id: 'compat-group:session-1',
                    participants: [ 'openclaw-compat-bot' ],
                    originator: 'compat-user',
                    from: 'compat-user',
                    you_are_mentioned: true,
                    is_sender: false,
                    mentions: [ 'openclaw-compat-bot' ],
                    message: `Reply with exactly ${args.expectedText}`,
                    routing_mode: 'mention',
                    response_directive: {
                      action: 'respond',
                      mode: 'required',
                      reason: 'OpenClaw compatibility probe',
                      request_source: 'legacy_mention',
                    },
                  },
                },
              }));
            }, 750);
          }
          return;
        }

        if (frame.method === 'bot.status') {
          socket.send(JSON.stringify(responseFrame(frame.id)));
          return;
        }

        if (frame.method === 'route.resolve') {
          socket.send(JSON.stringify(responseFrame(frame.id, {
            target: { type: 'broadcast' },
            recipients: [],
          })));
          return;
        }

        socket.send(JSON.stringify(responseFrame(frame.id)));
        return;
      }

      if (frame.type === 'res' && frame.id === 'compat-chat-1') {
        observations.chatAck = frame.ok === true && frame.payload?.run_id === 'compat-run-1';
        if (!observations.chatAck) void finish(false, 'chat.send acknowledgement was invalid');
        return;
      }

      if (frame.type !== 'event') return;
      if (frame.event === 'agent') {
        if (frame.payload?.run_id !== 'compat-run-1') {
          void finish(false, 'agent event had an unexpected run_id');
          return;
        }
        observations.agentEvents += 1;
      }
      if (frame.event !== 'chat.event') return;

      if (frame.payload?.run_id !== 'compat-run-1') {
        void finish(false, 'chat.event had an unexpected run_id');
        return;
      }

      const state = frame.payload?.state;
      if (state === 'delta') observations.chatDeltas += 1;
      if (state === 'error' || state === 'aborted') {
        observations.chatErrors += 1;
        void finish(false, `plugin emitted terminal ${state} chat.event`);
        return;
      }
      if (state !== 'final') return;

      observations.chatFinals += 1;
      observations.finalText = textFromChatEvent(frame);
      setTimeout(() => {
        if (observations.chatFinals !== 1) {
          void finish(false, `expected one final chat.event, received ${observations.chatFinals}`);
        } else if (observations.finalText.trim() !== args.expectedText) {
          void finish(false, `final reply was not exactly ${args.expectedText}`);
        } else if (!observations.chatAck) {
          void finish(false, 'final reply arrived without a valid chat.send acknowledgement');
        } else {
          void finish(true, 'chat.send completed through the real OpenClaw runtime');
        }
      }, 500);
    });

    socket.on('error', error => {
      void finish(false, `websocket error: ${error.message}`);
    });
  });

  const shutdown = () => {
    void finish(false, 'mock BCS was terminated before the scenario completed');
  };
  process.once('SIGTERM', shutdown);
  process.once('SIGINT', shutdown);
}

main().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
