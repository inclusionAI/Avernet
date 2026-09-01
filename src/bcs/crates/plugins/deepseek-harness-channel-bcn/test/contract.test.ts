import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { test } from 'node:test';
import type { Context } from '@deepseek-ai/cordis';
import type { Config as PluginConfig } from '../src/config.js';
import { Config, apply, inject, name } from '../src/index.js';

test('exports the Cordis bundle contract and a disabled safe default', async () => {
  assert.equal(name, 'deepseek-harness-channel-bcn');
  assert.deepEqual(inject, ['agents', 'credentials', 'sessions', 'sessionPersistence', 'tools']);
  const resolved = Config({} as PluginConfig);
  assert.equal(resolved.enabled, false);
  assert.equal(resolved.onboardingTokenRef, 'BCN_ONBOARDING_TOKEN');
  assert.equal(resolved.botSessionRef, 'BCN_BOT_SESSION');

  const logs: string[] = [];
  const context = {
    logger: () => ({
      info: (message: string) => logs.push(message),
      warn: () => {},
      error: () => {},
      debug: () => {},
    }),
  } as unknown as Context;
  const dispose = await apply(context, resolved);
  await dispose();
  assert.deepEqual(logs, ['BCN channel is installed but disabled']);
});

test('declares an installable DSH bundle manifest and mount row', async () => {
  const packageUrl = new URL('../package.json', import.meta.url);
  const patchUrl = new URL('../cordis.patch.yml', import.meta.url);
  const manifest = JSON.parse(await readFile(packageUrl, 'utf8')) as Record<string, unknown>;
  assert.deepEqual(manifest.dsh, { bundle: { patch: './cordis.patch.yml' } });
  assert.equal((manifest.dependencies as Record<string, string>).ws, '^8.18.3');
  assert.equal(Object.keys(manifest.dependencies as Record<string, string>).some(key => key.startsWith('@deepseek-ai/')), false);
  const patch = await readFile(patchUrl, 'utf8');
  assert.match(patch, /id: deepseek-harness-channel-bcn/);
  assert.match(patch, /name: '@avernet-plugin\/deepseek-harness-channel-bcn'/);
  assert.match(patch, /enabled: false/);
});
