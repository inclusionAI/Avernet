import assert from 'node:assert/strict';
import { test } from 'node:test';
import { parseFrame } from '../src/protocol.js';

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
  assert.deepEqual(parseFrame({ type: 'event', event: 'chat.abort', payload: { run_id: 'run-1' } }), {
    type: 'event',
    event: 'chat.abort',
    payload: { run_id: 'run-1' },
  });
  assert.equal(parseFrame({ type: 'event', event: 'chat.abort', payload: {}, seq: -1 }), undefined);
});
