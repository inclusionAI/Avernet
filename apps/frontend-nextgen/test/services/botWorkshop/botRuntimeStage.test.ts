import { resolveBotRuntimeStage } from '@/services/botWorkshop/botRuntimeStage';
import { describe, expect, it } from '@jest/globals';

describe('resolveBotRuntimeStage', () => {
  it.each([
    ['draft', 'draft'],
    ['deploying', 'draft'],
    ['prestable', 'verify'],
    ['running', 'online'],
    ['offline', 'draft'],
    ['unknown', 'draft'],
  ] as const)('maps %s lifecycle to %s IAM stage', (lifecycle, expected) => {
    expect(resolveBotRuntimeStage(lifecycle)).toBe(expected);
  });
});
