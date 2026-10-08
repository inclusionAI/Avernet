import { findSelectedChatBot, mapIdentityViewsToDisplayIdentities } from '@/hooks/workspaceIdentityMapper';
import { expect, it } from '@jest/globals';

const bots = [
  { botId: 'bot-1', displayName: '测试 Bot' },
  { botId: 'bot-2', displayName: '另一个 Bot' },
] as any;

it('没有会话时使用当前展开的 Bot 作为聊天目标', () => {
  expect(findSelectedChatBot(bots, null, { 'bot-1': true })?.displayName).toBe('测试 Bot');
});

it('有选中会话时优先使用会话所属 Bot', () => {
  expect(findSelectedChatBot(bots, 'bot-2', { 'bot-1': true })?.displayName).toBe('另一个 Bot');
});

const IDENTITY_VIEWS = [
  { id: 'human_900003', kind: 'user' as const, displayName: 'mine占位名', online: true },
  { id: 'bot-1:900003', kind: 'bot' as const, displayName: '协作 Bot', online: true },
];

describe('mapIdentityViewsToDisplayIdentities', () => {
  it('基础映射保留 id/kind 并转换展示字段', () => {
    const identities = mapIdentityViewsToDisplayIdentities(IDENTITY_VIEWS, null, false);
    expect(identities).toHaveLength(2);
    expect(identities[0]).toMatchObject({ id: 'human_900003', kind: 'user', name: 'mine占位名' });
    expect(identities[1]).toMatchObject({ id: 'bot-1:900003', kind: 'bot', name: '协作 Bot' });
  });

  it('preferAuthenticatedUserProfile 且 id 匹配登录用户时覆盖用户身份显示名', () => {
    const identities = mapIdentityViewsToDisplayIdentities(
      IDENTITY_VIEWS,
      { userId: '900003', name: '登录用户真名' },
      true,
    );
    expect(identities[0]).toMatchObject({ name: '登录用户真名' });
    expect(identities[1]).toMatchObject({ name: '协作 Bot' });
  });

  it('preferAuthenticatedUserProfile 为 false 时不覆盖显示名', () => {
    const identities = mapIdentityViewsToDisplayIdentities(
      IDENTITY_VIEWS,
      { userId: '900003', name: '登录用户真名' },
      false,
    );
    expect(identities[0]).toMatchObject({ name: 'mine占位名' });
  });

  it('登录用户 id 不匹配时保留原显示名', () => {
    const identities = mapIdentityViewsToDisplayIdentities(
      IDENTITY_VIEWS,
      { userId: 'someone_else', name: '别人' },
      true,
    );
    expect(identities[0]).toMatchObject({ name: 'mine占位名' });
  });
});
