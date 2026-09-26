import { strict as assert } from 'node:assert';
import { applySdkThinkingPolicy } from '../src/claude-sdk-bridge.js';

describe('applySdkThinkingPolicy', () => {
  it('disables thinking in SDK options when the effective token budget is zero', () => {
    const options: Record<string, unknown> = {
      thinking: { type: 'adaptive' },
    };

    applySdkThinkingPolicy(options, { MAX_THINKING_TOKENS: '0' });

    assert.deepEqual(options.thinking, { type: 'disabled' });
  });

  it('preserves SDK options when the token budget allows thinking', () => {
    const thinking = { type: 'enabled', budgetTokens: 8192 };
    const options: Record<string, unknown> = { thinking };

    applySdkThinkingPolicy(options, { MAX_THINKING_TOKENS: '8192' });

    assert.equal(options.thinking, thinking);
  });
});
