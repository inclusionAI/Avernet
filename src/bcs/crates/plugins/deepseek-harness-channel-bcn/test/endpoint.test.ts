import assert from 'node:assert/strict';
import { test } from 'node:test';
import { canonicalizeEndpoint, isPublicAddress, resolveEndpoint } from '../src/endpoint.js';

test('accepts remote HTTP and HTTPS public endpoints and preserves an API prefix', async () => {
  const resolver = async () => [{ address: '93.184.216.34', family: 4 }];
  const http = await resolveEndpoint('http://bcn.example.test/api', resolver);
  const https = await resolveEndpoint('https://bcn.example.test/bcn/', resolver);
  assert.equal(http.baseUrl.toString(), 'http://bcn.example.test/api/');
  assert.equal(http.webSocketUrl.toString(), 'ws://bcn.example.test/api/ws/bot');
  assert.equal(https.webSocketUrl.toString(), 'wss://bcn.example.test/bcn/ws/bot');
});

test('rejects private, link-local, reserved, and mixed DNS destinations', async () => {
  await assert.rejects(
    resolveEndpoint('http://private.example.test', async () => [{ address: '10.0.0.8', family: 4 }]),
    /private, link-local, or reserved/,
  );
  await assert.rejects(
    resolveEndpoint('https://mixed.example.test', async () => [
      { address: '93.184.216.34', family: 4 },
      { address: '169.254.169.254', family: 4 },
    ]),
    /private, link-local, or reserved/,
  );
  await assert.rejects(
    resolveEndpoint('http://loopback-alias.example.test', async () => [{ address: '127.0.0.1', family: 4 }]),
    /private, link-local, or reserved/,
  );
  assert.equal(isPublicAddress('203.0.113.1'), false);
  assert.equal(isPublicAddress('93.184.216.34'), true);
  assert.equal(isPublicAddress('64:ff9b::a9fe:a9fe'), false);
  assert.equal(isPublicAddress('2001:db8::1'), false);
  assert.equal(isPublicAddress('2606:4700:4700::1111'), true);
});

test('allows exact loopback development and rejects endpoint URL credentials or query data', async () => {
  const endpoint = await resolveEndpoint('http://127.0.0.1:8787/bcn');
  assert.equal(endpoint.loopback, true);
  assert.equal(endpoint.webSocketUrl.toString(), 'ws://127.0.0.1:8787/bcn/ws/bot');
  assert.throws(() => canonicalizeEndpoint('https://user:pass@example.com'), /must not contain credentials/);
  assert.throws(() => canonicalizeEndpoint('https://example.com?token=secret'), /must not contain credentials/);
  assert.throws(() => canonicalizeEndpoint('file:///tmp/bcn'), /must use HTTP or HTTPS/);
});

test('pins network lookups to the validated hostname and addresses', async () => {
  const endpoint = await resolveEndpoint(
    'https://bcn.example.test',
    async () => [{ address: '93.184.216.34', family: 4 }],
  );
  const lookup = endpoint.lookup as unknown as (
    hostname: string,
    options: { family: number },
    callback: (error: Error | null, address?: string, family?: number) => void,
  ) => void;
  const selected = await new Promise<{ address?: string; family?: number }>((resolve, reject) => {
    lookup('bcn.example.test', { family: 4 }, (error, address, family) => {
      if (error) reject(error);
      else if (address && family) resolve({ address, family });
      else reject(new Error('lookup returned no address'));
    });
  });
  assert.deepEqual(selected, { address: '93.184.216.34', family: 4 });
  await assert.rejects(new Promise<void>((resolve, reject) => {
    lookup('other.example.test', { family: 4 }, error => error ? reject(error) : resolve());
  }), /unexpected BCN hostname/);
});
