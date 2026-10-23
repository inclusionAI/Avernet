import assert from 'node:assert/strict';
import { test } from 'node:test';
import type { Context } from '@deepseek-ai/cordis';
import type { CredentialRef } from '@deepseek-ai/dsh-credentials';
import { loadOrRegisterBotSession } from '../src/credentials.js';
import type { ResolvedEndpoint } from '../src/endpoint.js';
import type { HttpTransport } from '../src/http-client.js';
import type { BotSession } from '../src/protocol.js';
import { testConfig } from './fixtures.js';

function token(payload: Record<string, unknown> = {}): string {
  return Buffer.concat([Buffer.from(JSON.stringify({ v: 1, id: 'human_test', exp: 4_102_444_800, ...payload })), Buffer.alloc(32)]).toString('base64url');
}
function providerToken(overrides: Record<string, unknown> = {}): string {
  return token({ v: 2, purpose: 'provider_bot_registration', provider_id: 'prv_dsh',
    allowed_modes: ['plugin'], auth_mode: 'static_bearer', ...overrides });
}

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

  async requestJson(
    _endpoint: ResolvedEndpoint,
    path: string,
    options: RecordedRequest['options'],
  ): Promise<unknown> {
    this.requests.push({ path, options });
    assert.equal(path, 'register', 'registration must not call descriptor onboarding');
    return this.registerResponse;
  }
}

function fakeContext(credentials: MemoryCredentials): Context {
  return { credentials } as unknown as Context;
}

test('automatically exchanges a human registration token and persists only the Bot Session', async () => {
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_ONBOARDING_TOKEN', token());
  const transport = new FakeHttpTransport();
  const result = await loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport });

  assert.equal(result.session.botUuid, 'bot-123');
  assert.equal(transport.requests.length, 1);
  assert.equal(transport.requests[0]?.options.query?.get('token'), token());
  assert.equal(transport.requests[0]?.options.query?.get('bot-name'), 'DSH Test Bot');
  assert.equal(credentials.values.get('BCN_ONBOARDING_TOKEN'), token());
  const stored = JSON.parse(credentials.values.get('BCN_BOT_SESSION') ?? '') as BotSession;
  assert.equal(stored.botToken, 'bot-secret');
  assert.equal(JSON.stringify(stored).includes(token()), false);
});

test('reuses an existing Bot Session without HTTP requests and refuses endpoint substitution', async () => {
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_BOT_SESSION', JSON.stringify({
    version: 1,
    endpoint: 'http://127.0.0.1:9000/',
    botUuid: 'bot-123',
    botToken: 'bot-secret',
    botName: 'Stored Bot',
  }));
  const transport = new FakeHttpTransport();
  await loadOrRegisterBotSession(fakeContext(credentials), testConfig({ botName: '' }), { transport });
  assert.equal(transport.requests.length, 0);

  await assert.rejects(
    loadOrRegisterBotSession(fakeContext(credentials), testConfig({ endpoint: 'http://127.0.0.1:9001/' }), { transport }),
    /differs from the endpoint bound/,
  );
});

test('fails safely for missing token, registration failure, and credential write failure', async () => {
  const missing = new MemoryCredentials();
  await assert.rejects(
    loadOrRegisterBotSession(fakeContext(missing), testConfig(), { transport: new FakeHttpTransport() }),
    /onboarding token credential/,
  );

  const registration = new MemoryCredentials();
  registration.values.set('BCN_ONBOARDING_TOKEN', token());
  const badRegistration = new FakeHttpTransport();
  badRegistration.registerResponse = { error: 'nope' };
  await assert.rejects(
    loadOrRegisterBotSession(fakeContext(registration), testConfig(), { transport: badRegistration }),
    /incomplete Bot Session/,
  );

  const writeFailure = new MemoryCredentials();
  writeFailure.values.set('BCN_ONBOARDING_TOKEN', token());
  writeFailure.failWrites = true;
  const writeTransport = new FakeHttpTransport();
  await assert.rejects(
    loadOrRegisterBotSession(fakeContext(writeFailure), testConfig(), { transport: writeTransport }),
    /write failed/,
  );
  assert.equal(writeTransport.requests.length, 1);
  assert.equal(writeFailure.values.has('BCN_BOT_SESSION'), false);
});

class ProviderTransport extends FakeHttpTransport {
  wrongProvider = false;
  override async requestJson(endpoint: ResolvedEndpoint, path: string, options: RecordedRequest['options']) {
    await super.requestJson(endpoint, path, options);
    return { ...this.registerResponse as object, registration: {
      provider_id: this.wrongProvider ? 'other-provider' : 'prv_dsh',
      provider_bot_ref: options.query?.get('provider_bot_ref'), mode: 'plugin', auth_mode: 'static_bearer',
      webhook_url: null, effective_webhook_url: null,
    } };
  }
}

test('Provider registration persists membership and reuses it after restart and Token rotation', async () => {
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_ONBOARDING_TOKEN', providerToken());
  const transport = new ProviderTransport();
  const result = await loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport });
  const query = transport.requests[0]?.options.query;
  assert.equal(query?.get('mode'), 'plugin');
  assert.match(query?.get('provider_bot_ref') ?? '', /^dsh-[a-f0-9-]+$/);
  assert.deepEqual(result.session.registration, { providerId: 'prv_dsh', providerBotRef: query?.get('provider_bot_ref'),
    mode: 'plugin', authMode: 'static_bearer' });
  credentials.values.delete('BCN_ONBOARDING_TOKEN');
  const resumed = await loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport });
  assert.deepEqual(resumed.session, result.session);
  assert.equal(transport.requests.length, 1);
  assert.equal(credentials.values.get('BCN_BOT_SESSION_REGISTRATION')?.includes(providerToken()), false);
});

test('lost HTTP response keeps pending identity and surfaces a duplicate conflict instead of creating another Bot', async () => {
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_ONBOARDING_TOKEN', providerToken());
  const transport = new ProviderTransport();
  const original = transport.requestJson.bind(transport);
  let failOnce = true;
  transport.requestJson = async (...args) => {
    await original(...args);
    if (failOnce) { failOnce = false; throw new Error('connection lost'); }
    // The real register contract never replays Bot credentials for an existing ref.
    throw new Error('BCN HTTP request failed with status 409');
  };
  await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport }), /connection lost/);
  await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport }), /status 409/);
  assert.equal(transport.requests[0]?.options.query?.get('provider_bot_ref'), transport.requests[1]?.options.query?.get('provider_bot_ref'));
  assert.equal(credentials.values.has('BCN_BOT_SESSION'), false);
  for (const overrides of [{ provider_id: 'another' }, { id: 'human_another' }]) {
    credentials.values.set('BCN_ONBOARDING_TOKEN', providerToken(overrides));
    await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport }), /Pending BCN Provider registration differs/);
  }
  assert.equal(transport.requests.length, 2);
});

test('Provider registration fails closed for unsupported tokens and mismatched responses', async () => {
  for (const overrides of [{ allowed_modes: ['gateway'] }, { auth_mode: 'agentpass' }, { auth_mode: undefined }, { auth_mode: null }, { exp: 1 }, { v: 3 }, { purpose: 'other' }]) {
    const credentials = new MemoryCredentials();
    credentials.values.set('BCN_ONBOARDING_TOKEN', providerToken(overrides));
    const transport = new ProviderTransport();
    await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport }));
    assert.equal(transport.requests.length, 0);
  }
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_ONBOARDING_TOKEN', providerToken());
  const transport = new ProviderTransport();
  transport.wrongProvider = true;
  await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport }), /mismatched Provider/);
  assert.equal(credentials.values.has('BCN_BOT_SESSION'), false);
  const legacy = new FakeHttpTransport();
  await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport: legacy }), /mismatched Provider/);
});

test('Provider identity must be durable before registration HTTP starts', async () => {
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_ONBOARDING_TOKEN', providerToken());
  credentials.failWrites = true;
  const transport = new ProviderTransport();
  await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport }), /write failed/);
  assert.equal(transport.requests.length, 0);
});


test('lost Provider response blocks legacy onboarding without replacing the pending identity', async () => {
  const credentials = new MemoryCredentials();
  credentials.values.set('BCN_ONBOARDING_TOKEN', providerToken());
  const transport = new ProviderTransport();
  const original = transport.requestJson.bind(transport);
  transport.requestJson = async (...args) => {
    await original(...args);
    throw new Error('connection lost');
  };
  await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport }), /connection lost/);
  const pending = credentials.values.get('BCN_BOT_SESSION_REGISTRATION');
  assert.ok(pending);
  credentials.values.set('BCN_ONBOARDING_TOKEN', token());
  const legacy = new FakeHttpTransport();
  await assert.rejects(loadOrRegisterBotSession(fakeContext(credentials), testConfig(), { transport: legacy }),
    /Pending BCN Provider registration cannot be replaced by legacy onboarding/);
  assert.equal(legacy.requests.length, 0);
  assert.equal(credentials.values.get('BCN_BOT_SESSION_REGISTRATION'), pending);
  assert.equal(credentials.values.has('BCN_BOT_SESSION'), false);
});
