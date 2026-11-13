/** @jest-environment jsdom */
import { defaultCapabilities, extendCapabilities } from '@/capabilities';
import { appExtension } from '@/extensions/collab';
import SquareBotCard from '@/components/CollaborationSquare/BotCard';
import { SquareNavigation } from '@/components/CollaborationSquare/SquareNavigation';
import type { PublicBot } from '@/domain/collaborationSquare/types';
import { useCollaborationSquareFriendActions } from '@/hooks/useCollaborationSquareFriendActions';
import { collaborationSquareBotService } from '@/services/collaborationSquare/publicDiscoveryRuntime';
import '@testing-library/jest-dom';
import { act, render, renderHook, screen, waitFor } from '@testing-library/react';
import { history } from '@umijs/max';
import type { ReactNode } from 'react';

jest.mock('@umijs/max', () => ({
  history: { push: jest.fn() },
  Link: ({ to, children, ...props }: { to: string; children: ReactNode }) => <a href={to} {...props}>{children}</a>,
}));

const bot: PublicBot = {
  id: 'helper:owner', name: '协作助手', ownerName: 'Owner', description: '',
  capabilities: [], relationshipStatus: 'none',
};
const actor = { type: 'human' as const, id: '7' };

beforeEach(() => {
  // 即使部署配置误开 Backend，collab 的无单聊约束也不能被覆盖。
  Object.defineProperty(window, 'AVERNET_RUNTIME_CONFIG', { configurable: true, value: { PUBLIC_BOT_BACKEND_ENABLED: true } });
  extendCapabilities(appExtension.capabilities);
});
afterEach(() => {
  extendCapabilities(defaultCapabilities);
  Reflect.deleteProperty(window, 'AVERNET_RUNTIME_CONFIG');
  jest.restoreAllMocks();
  jest.clearAllMocks();
});

it('collab 发现导航仅展示公开 Bot/群，Open 仍保留专家', () => {
  const { rerender } = render(<SquareNavigation active="bot" />);
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['公开 Bot', '公开协作群']);
  extendCapabilities(defaultCapabilities);
  rerender(<SquareNavigation active="bot" />);
  expect(screen.getAllByRole('link').map((link) => link.textContent)).toEqual(['公开 Bot', '专家', '公开协作群']);
});

it.each([
  { label: '已是好友', overrides: { relationshipStatus: 'friend' as const } },
  { label: '管理的 Bot', overrides: { isOwnedByLoggedInUser: true } },
])('Human $label 卡片没有会话按钮', ({ overrides }) => {
  render(<SquareBotCard bot={{ ...bot, ...overrides }} activeActor={actor} busy={false}
    onShare={jest.fn()} onPrimaryAction={jest.fn()} />);
  expect(screen.queryByRole('button', { name: '立即开始对话' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '申请好友权限' })).not.toBeInTheDocument();
  expect(screen.getByText('已是好友')).toBeInTheDocument();
});

it('Human 非好友仍可申请', () => {
  render(<SquareBotCard bot={bot} activeActor={actor} busy={false}
    onShare={jest.fn()} onPrimaryAction={jest.fn()} />);
  expect(screen.getByRole('button', { name: '申请好友权限' })).toBeEnabled();
});

function mountFriendActions() {
  const updateBotRelationship = jest.fn();
  const hook = renderHook(() => useCollaborationSquareFriendActions({
    resource: 'bot', activeActor: actor, humanBotContext: { actorId: 'human_7', userId: '7' },
    handleTargetInvalid: jest.fn(), busyKeys: [], setBusy: jest.fn(), updateBotRelationship,
  }));
  return { ...hook, updateBotRelationship };
}

it.each([
  { relationshipStatus: 'friend' as const },
  { isOwnedByLoggedInUser: true },
])('Human 已是好友/管理的 Bot 即使调用 action 也不建聊、不导航', (overrides) => {
  const open = jest.spyOn(collaborationSquareBotService, 'openBotConversation').mockResolvedValue({ sessionId: 'bad' });
  const request = jest.spyOn(collaborationSquareBotService, 'requestBotFriendship');
  const { result } = mountFriendActions();
  act(() => result.current({ ...bot, ...overrides }));
  expect(open).not.toHaveBeenCalled();
  expect(request).not.toHaveBeenCalled();
  expect(history.push).not.toHaveBeenCalled();
});

it('Human 申请立即成为好友时更新关系，但不自动建聊或跳转', async () => {
  const request = jest.spyOn(collaborationSquareBotService, 'requestBotFriendship').mockResolvedValue({ status: 'friend' });
  const open = jest.spyOn(collaborationSquareBotService, 'openBotConversation').mockResolvedValue({ sessionId: 'bad' });
  const { result, updateBotRelationship } = mountFriendActions();
  act(() => result.current(bot));
  await waitFor(() => expect(updateBotRelationship).toHaveBeenCalledWith(bot.id, 'friend'));
  expect(request).toHaveBeenCalledTimes(1);
  expect(open).not.toHaveBeenCalled();
  expect(history.push).not.toHaveBeenCalled();
});
