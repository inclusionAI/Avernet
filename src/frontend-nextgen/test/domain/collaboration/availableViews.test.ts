import { clampView, getAvailableViews } from '@/domain/collaboration/availableViews';
import { describe, expect, it } from '@jest/globals';

describe('availableViews', () => {
  it('用户身份:会话+协作群', () => {
    expect(getAvailableViews({ id: 'me', kind: 'user' })).toEqual(['chat', 'group']);
  });
  it('bot 身份:仅协作群', () => {
    expect(getAvailableViews({ id: 'b1', kind: 'bot' })).toEqual(['group']);
  });
  it('测试用户:仅会话', () => {
    expect(getAvailableViews({ id: 'test-user', kind: 'user' })).toEqual(['chat']);
  });
  it('null 身份:仅协作群(安全默认)', () => {
    expect(getAvailableViews(null)).toEqual(['group']);
  });
  it('clampView:当前不在可用集中,取第一个', () => {
    expect(clampView(['group'], 'chat')).toBe('group');
    expect(clampView(['chat'], 'group')).toBe('chat');
    expect(clampView(['chat', 'group'], 'group')).toBe('group');
  });
});
