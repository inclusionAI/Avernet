import { strict as assert } from 'node:assert';
import {
  resolveInteractionTimeoutMs,
  DEFAULT_INTERACTION_TIMEOUT_MS,
  EXEC_APPROVAL_TIMEOUT_MS,
} from '../src/interaction/builders.js';

describe('interaction approval timeout', () => {
  let saved: string | undefined;

  beforeEach(() => {
    saved = process.env.RELAY_INTERACTION_TIMEOUT_MS;
    delete process.env.RELAY_INTERACTION_TIMEOUT_MS;
  });

  afterEach(() => {
    if (saved === undefined) {
      delete process.env.RELAY_INTERACTION_TIMEOUT_MS;
    } else {
      process.env.RELAY_INTERACTION_TIMEOUT_MS = saved;
    }
  });

  it('defaults to 5min when env is not set', () => {
    delete process.env.RELAY_INTERACTION_TIMEOUT_MS;
    assert.equal(DEFAULT_INTERACTION_TIMEOUT_MS, 300_000);
    assert.equal(resolveInteractionTimeoutMs(), 300_000);
  });

  it('defaults to 5min when env is empty / whitespace', () => {
    process.env.RELAY_INTERACTION_TIMEOUT_MS = '';
    assert.equal(resolveInteractionTimeoutMs(), DEFAULT_INTERACTION_TIMEOUT_MS);
    process.env.RELAY_INTERACTION_TIMEOUT_MS = '   ';
    assert.equal(resolveInteractionTimeoutMs(), DEFAULT_INTERACTION_TIMEOUT_MS);
  });

  it('uses the configured value when env is a valid positive number', () => {
    process.env.RELAY_INTERACTION_TIMEOUT_MS = '600000';
    assert.equal(resolveInteractionTimeoutMs(), 600_000);
    process.env.RELAY_INTERACTION_TIMEOUT_MS = '300000';
    assert.equal(resolveInteractionTimeoutMs(), 300_000);
  });

  it('falls back to default for invalid values (NaN / negative / zero)', () => {
    for (const bad of [ 'abc', '-1', '0', '12px', 'NaN' ]) {
      process.env.RELAY_INTERACTION_TIMEOUT_MS = bad;
      assert.equal(
        resolveInteractionTimeoutMs(),
        DEFAULT_INTERACTION_TIMEOUT_MS,
        `expected fallback for invalid value: ${bad}`,
      );
    }
  });

  it('exports EXEC_APPROVAL_TIMEOUT_MS resolved at module load (default 5min in test env)', () => {
    // 模块加载时无 env，应等于默认 5min
    assert.equal(EXEC_APPROVAL_TIMEOUT_MS, DEFAULT_INTERACTION_TIMEOUT_MS);
    assert.equal(EXEC_APPROVAL_TIMEOUT_MS, 300_000);
  });
});
