import { strict as assert } from 'node:assert';
import path from 'node:path';
import os from 'node:os';
import fs from 'node:fs';
import { initRouter, resolveEnvForModel, loadProvidersFromFile } from '../src/claude-code-router.js';

describe('claude-code-router', () => {

  describe('loadProvidersFromFile', () => {
    it('returns null when no path given', () => {
      assert.equal(loadProvidersFromFile(undefined), null);
    });

    it('returns null for non-existent file', () => {
      assert.equal(loadProvidersFromFile('/tmp/does-not-exist-abc123.json'), null);
    });

    it('loads providers from valid JSON file', () => {
      const tmpFile = path.join(os.tmpdir(), `router-test-${Date.now()}.json`);
      const config = [
        {
          id: 'test',
          name: 'Test',
          enabled: true,
          models: [
            { id: 'model-a', name: 'A', display_name: 'Model A', env: { ANTHROPIC_MODEL: 'model-a' } },
            { id: 'model-b', name: 'B', display_name: 'Model B', env: { ANTHROPIC_MODEL: 'model-b', EXTRA: 'val' } },
          ],
        },
      ];
      fs.writeFileSync(tmpFile, JSON.stringify(config));
      try {
        const result = loadProvidersFromFile(tmpFile);
        assert(result);
        assert.equal(result.length, 1);
        assert.equal(result[0].models.length, 2);
        assert.deepEqual(result[0].models[1].env, { ANTHROPIC_MODEL: 'model-b', EXTRA: 'val' });
      } finally {
        fs.unlinkSync(tmpFile);
      }
    });

    it('returns null for empty array', () => {
      const tmpFile = path.join(os.tmpdir(), `router-empty-${Date.now()}.json`);
      fs.writeFileSync(tmpFile, '[]');
      try {
        assert.equal(loadProvidersFromFile(tmpFile), null);
      } finally {
        fs.unlinkSync(tmpFile);
      }
    });
  });

  describe('resolveEnvForModel', () => {
    const routeMap = new Map<string, Record<string, string>>([
      [ 'Qwen3.5', { ANTHROPIC_MODEL: 'Qwen3.5-397B-A17B' }],
      [ 'GLM-5', { ANTHROPIC_MODEL: 'GLM-5', EXTRA_VAR: 'glm' }],
    ]);

    it('returns route env for known model', () => {
      assert.deepEqual(resolveEnvForModel(routeMap, 'GLM-5'), { ANTHROPIC_MODEL: 'GLM-5', EXTRA_VAR: 'glm' });
    });

    it('falls back to ANTHROPIC_MODEL for unknown model', () => {
      assert.deepEqual(resolveEnvForModel(routeMap, 'unknown-model'), { ANTHROPIC_MODEL: 'unknown-model' });
    });

    it('returns undefined when no model specified', () => {
      assert.equal(resolveEnvForModel(routeMap, undefined), undefined);
    });
  });

  describe('initRouter', () => {
    it('uses defaults when no file specified and env not set', () => {
      const saved = process.env.RELAY_MODELS_FILE;
      delete process.env.RELAY_MODELS_FILE;
      try {
        const router = initRouter();
        assert(router.providers.length > 0);
        assert(router.routeMap.size > 0);
        assert(typeof router.runner === 'function');
      } finally {
        if (saved) process.env.RELAY_MODELS_FILE = saved;
      }
    });

    it('loads from file when path provided', () => {
      const tmpFile = path.join(os.tmpdir(), `router-init-${Date.now()}.json`);
      const config = [
        {
          id: 'custom',
          name: 'Custom',
          enabled: true,
          models: [
            { id: 'my-model', name: 'My', display_name: 'My Model', env: { ANTHROPIC_MODEL: 'my-model' } },
          ],
        },
      ];
      fs.writeFileSync(tmpFile, JSON.stringify(config));
      try {
        const router = initRouter(tmpFile);
        assert.equal(router.providers.length, 1);
        assert.equal(router.providers[0].id, 'custom');
        assert(router.routeMap.has('my-model'));
        assert.deepEqual(router.routeMap.get('my-model'), { ANTHROPIC_MODEL: 'my-model' });
      } finally {
        fs.unlinkSync(tmpFile);
      }
    });

    it('skips disabled providers', () => {
      const tmpFile = path.join(os.tmpdir(), `router-disabled-${Date.now()}.json`);
      const config = [
        {
          id: 'active',
          name: 'Active',
          enabled: true,
          models: [{ id: 'a', name: 'A', display_name: 'A' }],
        },
        {
          id: 'inactive',
          name: 'Inactive',
          enabled: false,
          models: [{ id: 'b', name: 'B', display_name: 'B' }],
        },
      ];
      fs.writeFileSync(tmpFile, JSON.stringify(config));
      try {
        const router = initRouter(tmpFile);
        assert(router.routeMap.has('a'));
        assert(!router.routeMap.has('b'));
      } finally {
        fs.unlinkSync(tmpFile);
      }
    });

    it('auto-generates ANTHROPIC_MODEL env when model has no env field', () => {
      const tmpFile = path.join(os.tmpdir(), `router-noenv-${Date.now()}.json`);
      const config = [
        {
          id: 'p',
          name: 'P',
          enabled: true,
          models: [{ id: 'bare-model', name: 'Bare', display_name: 'Bare Model' }],
        },
      ];
      fs.writeFileSync(tmpFile, JSON.stringify(config));
      try {
        const router = initRouter(tmpFile);
        assert.deepEqual(router.routeMap.get('bare-model'), { ANTHROPIC_MODEL: 'bare-model' });
      } finally {
        fs.unlinkSync(tmpFile);
      }
    });
  });
});
