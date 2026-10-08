import { publicationSuccessMessage } from '@/hooks/collaborationPrivacyHelpers';
import { describe, expect, it } from '@jest/globals';

describe('collaboration privacy hook helpers', () => {
  it.each([
    ['none', 'Bot 可见性已更新为不可见，当前立即生效'],
    ['all', '可见性变更申请已提交，当前可见性保持不变'],
    ['restricted', '可见性变更申请已提交，当前可见性保持不变'],
  ] as const)('returns the publication success copy for %s', (scope, expected) => {
    expect(publicationSuccessMessage({ scope, organizationPaths: [] })).toBe(expected);
  });
});
