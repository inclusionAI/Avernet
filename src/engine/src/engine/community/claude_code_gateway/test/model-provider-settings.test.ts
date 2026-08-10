import { strict as assert } from 'node:assert';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { loadRelayModelProviderEnv } from '../src/model-provider-settings.js';

describe('loadRelayModelProviderEnv', () => {
  let tmpDir: string;
  let previousSource: string | undefined;

  beforeEach(() => {
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'relay-model-provider-settings-'));
    previousSource = process.env.RELAY_MODEL_SETTINGS_SOURCE;
  });

  afterEach(() => {
    if (previousSource === undefined) delete process.env.RELAY_MODEL_SETTINGS_SOURCE;
    else process.env.RELAY_MODEL_SETTINGS_SOURCE = previousSource;
    fs.rmSync(tmpDir, { recursive: true, force: true });
  });

  it('forwards only Claude model-provider settings without logging or importing arbitrary env keys', () => {
    const sourcePath = path.join(tmpDir, 'settings.json');
    fs.writeFileSync(sourcePath, JSON.stringify({
      env: {
        ANTHROPIC_AUTH_TOKEN: 'test-token',
        ANTHROPIC_BASE_URL: 'https://models.example.test',
        ANTHROPIC_MODEL: 'local-model',
        API_TIMEOUT_MS: '300000',
        PATH: '/unexpected',
        UNRELATED_SETTING: 'ignore-me',
      },
    }));
    process.env.RELAY_MODEL_SETTINGS_SOURCE = sourcePath;

    assert.deepEqual(loadRelayModelProviderEnv(), {
      ANTHROPIC_AUTH_TOKEN: 'test-token',
      ANTHROPIC_BASE_URL: 'https://models.example.test',
      ANTHROPIC_MODEL: 'local-model',
      API_TIMEOUT_MS: '300000',
    });
  });

  it('returns an empty mapping for a missing or invalid source', () => {
    process.env.RELAY_MODEL_SETTINGS_SOURCE = path.join(tmpDir, 'missing.json');
    assert.deepEqual(loadRelayModelProviderEnv(), {});

    const sourcePath = path.join(tmpDir, 'invalid.json');
    fs.writeFileSync(sourcePath, '{invalid');
    process.env.RELAY_MODEL_SETTINGS_SOURCE = sourcePath;
    assert.deepEqual(loadRelayModelProviderEnv(), {});
  });
});
