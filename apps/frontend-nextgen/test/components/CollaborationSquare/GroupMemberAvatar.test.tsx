/** @jest-environment jsdom */
import { GroupMembersModal } from '@/components/CollaborationSquare/GroupMembersModal';
import type { PublicGroupMember } from '@/domain/collaborationSquare/types';
import { generateBotAvatar } from '@/services/botWorkshop/botAvatarGenerator';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

const bot: PublicGroupMember = { id: 'bot-1:owner', displayName: '助手', type: 'bot', role: 'driver' };
const modalProps = { open: true, group: null, loading: false, onClose: jest.fn() };

test.each(['bot', 'human'] as const)('成员弹窗使用 %s 的头像 URL', (type) => {
  render(<GroupMembersModal {...modalProps} members={[{ ...bot, type, avatarUrl: 'https://example.com/avatar.png' }]} />);
  expect(screen.getByRole('img', { name: bot.displayName })).toHaveAttribute('src', 'https://example.com/avatar.png');
});

test.each([undefined, null, '', '   '])('Bot 头像为空（%p）时与 Bot 管理的默认头像相同', (avatarUrl) => {
  render(<GroupMembersModal {...modalProps} members={[{ ...bot, avatarUrl }]} />);
  expect(screen.getByRole('img', { name: bot.displayName })).toHaveAttribute('src', generateBotAvatar('bot-1'));
});

test('默认头像只取决于 Bot ID，不受名称、角色或成员顺序影响', () => {
  const secondBot = { ...bot, id: 'bot-2:owner', displayName: '另一个助手' };
  const { rerender } = render(<GroupMembersModal {...modalProps} members={[bot, secondBot]} />);
  const firstSrc = screen.getByRole('img', { name: bot.displayName }).getAttribute('src');
  expect(firstSrc).not.toBe(screen.getByRole('img', { name: secondBot.displayName }).getAttribute('src'));
  rerender(<GroupMembersModal {...modalProps} members={[secondBot, { ...bot, displayName: '改名', role: 'worker' }]} />);
  expect(screen.getByRole('img', { name: '改名' })).toHaveAttribute('src', firstSrc);
});

test('相同 Bot 的不同归属后缀使用管理页的同一默认头像', () => {
  const { rerender } = render(<GroupMembersModal {...modalProps} members={[bot]} />);
  expect(screen.getByRole('img', { name: bot.displayName })).toHaveAttribute('src', generateBotAvatar('bot-1'));
  rerender(<GroupMembersModal {...modalProps} members={[{ ...bot, id: 'bot-1:another-owner' }]} />);
  expect(screen.getByRole('img', { name: bot.displayName })).toHaveAttribute('src', generateBotAvatar('bot-1'));
});

test('用户没有头像时仍显示首字母，而不是生成 Bot 头像', () => {
  render(<GroupMembersModal {...modalProps} members={[{ ...bot, type: 'human', displayName: '张三' }]} />);
  expect(screen.getByText('张')).toBeInTheDocument();
  expect(screen.queryByRole('img')).not.toBeInTheDocument();
});
