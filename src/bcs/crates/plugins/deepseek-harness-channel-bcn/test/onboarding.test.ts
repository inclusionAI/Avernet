import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Context } from '@deepseek-ai/cordis';
import type { CredentialRef } from '@deepseek-ai/dsh-credentials';
import { loadOrOnboardBotSession } from '../src/credentials.js';
import type { ResolvedEndpoint } from '../src/endpoint.js';
import type { HttpTransport } from '../src/http-client.js';
import type { BotSession } from '../src/protocol.js';
import { testConfig } from './fixtures.js';

class MemoryCredentials {
  readonly values = new Map<string, string>();
  failWrites = false;

  async resolve(ref: CredentialRef): Promise<{ value: string; source: string } | undefined> {
    const value = this.values.get(String(ref));
    return value ? { value, source: 'test' } : undefined;
  }

  async set(ref: CredentialRef, value: string): Promise<void> {
    if (this.failWrites) throw new Error('write failed');
    this.values.set(String(ref), value);
  }
}

interface RecordedRequest {
  path: string;
  options: {
    method: 'POST';
    headers?: Record<string, string>;
    body?: unknown;
    query?: URLSearchParams;
  };
}

class FakeHttpTransport implements HttpTransport {
  requests: RecordedRequest[] = [];
  registerResponse: unknown = {
    bot_name: 'DSH Test Bot',
    bot_uuid: 'bot-123',
    bot_token: 'bot-secret',
  };
  onboardResponse: unknown = { onboarded: true, bot_uuid: 'bot-123' };

  async requestJson(
    _endpoint: ResolvedEndpoint,
    path: string,
    options: RecordedRequest['options'],
  ): Promise<unknown> {
    this.requests.push({ path, options });
    return path === 'register' ? this.registerResponse : this.onboardResponse;
  }
}

function fakeContext(credentials: MemoryCredentials): Context {
  return { credentials } as unknown as Context;
}

test('automatically exchanges a human registration token and persists only the Bot Session', async () => {
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_ONBOARDING_TOKEN', 'human-registration-secret');
  const transport = new FakeHttpTransport();
  const result = await loadOrOnboardBotSession(fakeContext(credentials), testConfig(), { transport });

  assert.equal(result.session.botUuid, 'bot-123');
  assert.equal(transport.requests.length, 2);
  assert.equal(transport.requests[0]?.options.query?.get('token'), 'human-registration-secret');
  assert.equal(transport.requests[0]?.options.query?.get('bot-name'), 'DSH Test Bot');
  assert.deepEqual(transport.requests[1]?.options.body, {
    name: 'DSH Test Bot',
    summary: 'Test Bot',
    domains: ['testing'],
    skills: ['verification'],
    scopes: [],
  });
  assert.equal(Object.hasOwn(transport.requests[1]?.options.body as object, 'ownerId'), false);
  assert.equal(Object.hasOwn(transport.requests[1]?.options.body as object, 'owner_id'), false);
  assert.equal(credentials.values.get('BCN_ONBOARDING_TOKEN'), 'human-registration-secret');
  const stored = JSON.parse(credentials.values.get('BCN_BOT_SESSION') ?? '') as BotSession;
  assert.equal(stored.botToken, 'bot-secret');
  assert.equal(JSON.stringify(stored).includes('human-registration-secret'), false);
});

test('uses an existing Bot Session, refreshes descriptor, and refuses endpoint substitution', async () => {
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_BOT_SESSION', JSON.stringify({
    version: 1,
    endpoint: 'http://127.0.0.1:9000/',
    botUuid: 'bot-123',
    botToken: 'bot-secret',
    botName: 'Stored Bot',
  }));
  const transport = new FakeHttpTransport();
  await loadOrOnboardBotSession(fakeContext(credentials), testConfig({ botName: '' }), { transport });
  assert.equal(transport.requests.length, 1);
  assert.equal(transport.requests[0]?.path, 'bots/onboard');
  assert.equal((transport.requests[0]?.options.body as Record<string, unknown>).name, 'Stored Bot');

  await assert.rejects(
    loadOrOnboardBotSession(fakeContext(credentials), testConfig({ endpoint: 'http://127.0.0.1:9001/' }), { transport }),
    /differs from the endpoint bound/,
  );
});

test('fails safely for missing token, registration failure, credential write failure, and onboarding failure', async () => {
  const missing = new MemoryCredentials();
  await assert.rejects(
    loadOrOnboardBotSession(fakeContext(missing), testConfig(), { transport: new FakeHttpTransport() }),
    /onboarding token credential/,
  );

  const registration = new MemoryCredentials();
  registration.values.set('BCN_ONBOARDING_TOKEN', 'token');
  const badRegistration = new FakeHttpTransport();
  badRegistration.registerResponse = { error: 'nope' };
  await assert.rejects(
    loadOrOnboardBotSession(fakeContext(registration), testConfig(), { transport: badRegistration }),
    /incomplete Bot Session/,
  );

  const writeFailure = new MemoryCredentials();
  writeFailure.values.set('BCN_ONBOARDING_TOKEN', 'token');
  writeFailure.failWrites = true;
  const writeTransport = new FakeHttpTransport();
  await assert.rejects(
    loadOrOnboardBotSession(fakeContext(writeFailure), testConfig(), { transport: writeTransport }),
    /write failed/,
  );
  assert.equal(writeTransport.requests.length, 1, 'descriptor onboarding waits until the Bot Session is stored');

  const onboardFailure = new MemoryCredentials();
  onboardFailure.values.set('BCN_ONBOARDING_TOKEN', 'token');
  const onboardTransport = new FakeHttpTransport();
  onboardTransport.onboardResponse = { onboarded: false, bot_uuid: 'bot-123' };
  await assert.rejects(
    loadOrOnboardBotSession(fakeContext(onboardFailure), testConfig(), { transport: onboardTransport }),
    /rejected Bot descriptor onboarding/,
  );
  assert.ok(onboardFailure.values.has('BCN_BOT_SESSION'), 'registration remains recoverable after descriptor failure');
});
