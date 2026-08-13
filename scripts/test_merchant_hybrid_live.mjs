#!/usr/bin/env node
// Non-destructive live check for the local 3 OpenClaw + 1 Claude Code group.
// It uses the runtime-only Provider credentials and prints no credentials or
// chat content. The inject marker is then required in the Claude reply.
import assert from 'node:assert/strict';
import { request as requestHttp1 } from 'node:http';
import http2 from 'node:http2';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { randomUUID } from 'node:crypto';

const root = new URL('..', import.meta.url).pathname;
const runtime = join(root, 'scripts/.dependencies');
const state = JSON.parse(readFileSync(join(runtime, 'bcs_baas_provider.merchant_hybrid.state.json'), 'utf8'));
const tokens = JSON.parse(readFileSync(join(runtime, 'bcs_baas_provider.merchant_hybrid.tokens.json'), 'utf8'));
const providerBotRef = state.bots?.[0]?.provider_bot_ref;

assert.equal(typeof state.provider_id, 'string');
assert.equal(typeof providerBotRef, 'string');
assert.equal(typeof tokens.bcs_to_provider_token, 'string');

function request(body, stream = false) {
  return new Promise((resolve, reject) => {
    const client = http2.connect('http://127.0.0.1:28083');
    const payload = JSON.stringify(body);
    const req = client.request({
      ':method': 'POST', ':path': '/webhook',
      authorization: `Bearer ${tokens.bcs_to_provider_token}`,
      'content-type': 'application/json',
      'content-length': Buffer.byteLength(payload),
      ...(stream ? { 'x-bcn-transport': 'sse' } : {}),
    });
    let status = 0;
    const chunks = [];
    req.on('response', headers => { status = Number(headers[':status']); });
    req.on('data', chunk => chunks.push(chunk));
    req.on('error', reject);
    client.on('error', reject);
    req.on('end', () => {
      client.close();
      resolve({ status, body: Buffer.concat(chunks).toString('utf8') });
    });
    req.end(payload);
  });
}

function requestOverHttp1(body) {
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = requestHttp1({
      hostname: '127.0.0.1', port: 28083, path: '/webhook', method: 'POST',
      headers: {
        authorization: `Bearer ${tokens.bcs_to_provider_token}`,
        'content-type': 'application/json',
        'content-length': Buffer.byteLength(payload),
      },
    }, response => {
      const chunks = [];
      response.on('data', chunk => chunks.push(chunk));
      response.on('end', () => resolve({
        status: response.statusCode ?? 0,
        body: Buffer.concat(chunks).toString('utf8'),
      }));
    });
    req.on('error', reject);
    req.end(payload);
  });
}

const sessionId = `merchant-hybrid-live-${randomUUID()}`;
const runPrefix = `merchant-hybrid-live-${randomUUID()}`;
const marker = `HYBRID_INJECT_${randomUUID().replaceAll('-', '').slice(0, 12)}`;
const groupId = `bcs_grp_${randomUUID()}`;
const base = (method, id, message) => ({
  type: 'req', id, method, session_id: sessionId, bcn_group_id: groupId,
  to_bot: { provider_id: state.provider_id, provider_bot_ref: providerBotRef },
  from: { kind: 'human', id: 'merchant-hybrid-live' }, message,
});

const injected = await request(base('chat.inject', `${runPrefix}-inject`, {
  role: 'assistant', content: `内部验证上下文标记：${marker}`,
}));
assert.equal(injected.status, 200, `inject failed with HTTP ${injected.status}`);
assert.deepEqual(JSON.parse(injected.body), { ok: true });

const sent = await request(base('chat.send', `${runPrefix}-send`, {
  role: 'user', content: '请只回复你收到的内部验证上下文标记；不要调用工具。',
}), true);
assert.equal(sent.status, 200, `chat.send failed with HTTP ${sent.status}`);
const events = [...sent.body.matchAll(/^event: (.+)\ndata: (.+)$/gm)].map(([, event, data]) => ({
  event,
  data: JSON.parse(data),
}));
const final = events.find(({ event, data }) => event === 'chat' && data.state === 'final');
if (!final) throw new Error('chat.send stream did not contain a final chat event');
const finalText = final.data.message?.content
  ?.filter(block => block?.type === 'text')
  .map(block => block.text)
  .join('') ?? '';
if (!finalText.includes(marker)) throw new Error('final Claude response did not contain the injected marker');

const history = await requestOverHttp1(base('chat.history', `${runPrefix}-history`, {
  role: 'user', content: '',
}));
assert.equal(history.status, 200, `chat.history failed with HTTP ${history.status}`);
const historyBody = JSON.parse(history.body);
assert.equal(historyBody.ok, true, 'chat.history did not return ok');
assert.ok(Array.isArray(historyBody.messages), 'chat.history did not return messages');
assert.ok(historyBody.messages.length > 0, 'chat.history returned no retained session messages');

console.log(`merchant_hybrid live Provider check passed (run_id=${runPrefix}-send, inject+send+history)`);
