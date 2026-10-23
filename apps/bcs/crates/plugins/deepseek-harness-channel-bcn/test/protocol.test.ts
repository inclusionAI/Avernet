import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseBotSession, parseFrame } from '../src/protocol.js';

test('Bot Session parsing preserves Provider membership and accepts legacy sessions', () => {
  const legacy = { version: 1, endpoint: 'http://127.0.0.1:9000/', botUuid: 'bot-123', botToken: 'test-secret', botName: 'DSH' };
  assert.deepEqual(parseBotSession(JSON.stringify(legacy)), legacy);
  const registration = { providerId: 'prv_dsh', providerBotRef: 'dsh-local-ref', mode: 'plugin', authMode: 'static_bearer' };
  assert.deepEqual(parseBotSession(JSON.stringify({ ...legacy, registration })), { ...legacy, registration });
  assert.throws(() => parseBotSession(JSON.stringify({ ...legacy, registration: { ...registration, mode: 'gateway' } })), /invalid Provider/);
});

test('accepts well-formed BCN response errors and rejects incomplete error frames', () => {
  assert.deepEqual(parseFrame({
    type: 'res',
    id: 'response-1',
    ok: false,
    error: { code: 'DENIED', message: 'Not allowed', retryable: false, details: { reason: 'policy' } },
  }), {
    type: 'res',
    id: 'response-1',
    ok: false,
    error: { code: 'DENIED', message: 'Not allowed', retryable: false, details: { reason: 'policy' } },
  });
  assert.equal(parseFrame({
    type: 'res', id: 'response-2', ok: false,
    error: { code: 'DENIED', message: 'Not allowed' },
  }), undefined);
  assert.equal(parseFrame({ type: 'res', id: 'response-3', ok: false }), undefined);
});

test('accepts BCN events with the protocol-optional sequence field', () => {
  assert.deepEqual(parseFrame({ type: 'event', event: 'bot.notice', payload: { message: 'ready' } }), {
    type: 'event',
    event: 'bot.notice',
    payload: { message: 'ready' },
  });
  assert.equal(parseFrame({ type: 'event', event: 'bot.notice', payload: {}, seq: -1 }), undefined);
});
