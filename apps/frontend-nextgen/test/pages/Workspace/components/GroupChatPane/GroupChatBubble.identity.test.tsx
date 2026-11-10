/** @jest-environment jsdom */
import type { GroupView } from '@/domain/collaboration';
import { GroupChatBubble } from '@/pages/Workspace/components/GroupChatPane/GroupChatBubble';
import type { ChatMessage } from '@tc-chat/core';
import '@testing-library/jest-dom';
import { render, screen } from '@testing-library/react';

jest.mock('@tc-chat/ui/es/Bubble', () => ({
  Bubble: ({ sender }: { sender: { align: string } }) => <div data-testid="bubble" data-align={sender.align} />,
}));
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({ aixUiPlugin: {}, fileRefPlugin: {} }));

const group: GroupView = {
  groupId: 'group', name: '协作群', kind: 'free_chat', status: 'active', participants: [], sessions: [],
  participantCount: 2, isPublic: false, deliveryPolicy: 'send_to_driver', createdAt: 1, lastMessageAt: 1,
};

it.each([
  ['当前用户', 'user', 'human_123', '123', 'right', false],
  ['当前用户原始 ID', 'user', '123', 'human_123', 'right', false],
  ['其他用户', 'user', 'human_456', '123', 'left', true],
  ['Bot', 'assistant', 'bot-a', '123', 'left', false],
  ['Bot ID 不作为用户匹配', 'assistant', '123', '123', 'left', false],
  ['发送者未知', 'user', undefined, '123', 'left', true],
  ['登录身份未知', 'user', 'human_456', undefined, 'left', true],
] as const)('%s 的气泡、元信息和操作行位置正确', (_label, role, senderId, userIdentityId, align, hasBadge) => {
  const message: ChatMessage = {
    id: 'message', role, content: '你好', status: 'history',
    extra: { senderId, senderName: '发送者', displayTime: '10:31' },
  };
  render(<GroupChatBubble message={message} group={group} participants={[]} isLastMessage={false}
    isRequesting={false} userIdentityId={userIdentityId} onCopy={jest.fn()} />);
  expect(screen.getByTestId('bubble')).toHaveAttribute('data-align', align);
  const meta = screen.getByTestId('message-sender-meta');
  expect(meta).toHaveClass(align === 'right' ? 'justify-end' : 'justify-start');
  expect(meta.parentElement?.parentElement?.parentElement).toHaveClass(align === 'right' ? 'justify-end' : 'justify-start');
  expect(screen.getByTestId('message-copy-action-message')).toHaveClass(align === 'right' ? 'justify-end' : 'justify-start');
  expect(screen.queryAllByText('用户')).toHaveLength(hasBadge ? 1 : 0);
});

it.each([
  ['历史消息只带 botName', 'history', { botUuid: 'b1', botName: '旧名' }, '旧名'],
  ['实时消息只带 senderName', 'streaming', { senderId: 'b1', senderName: '新名' }, '新名'],
] as const)('%s 时头像仍按 Bot ID 取 Session 摘要', (_label, status, extra, expectedName) => {
  render(<GroupChatBubble
    message={{ id: 'avatar', role: 'assistant', content: '你好', status, extra }}
    group={group} participants={[]}
    displayParticipants={[{ actorId: 'b1', kind: 'bot', name: 'Bot 一', avatarUrl: '/b1.png' }]}
    isLastMessage={false} isRequesting={false}
  />);
  const img = screen.getByAltText(expectedName);
  expect(img).toBeInTheDocument();
  expect(img.getAttribute('src')).toBe('/b1.png');
});

it.each([
  ['user', '456', '测试用户乙', '用户'],
  ['assistant', 'bot-a', '协作助手', 'Bot'],
] as const)('%s 名称后显示与会话管理一致的类型标签，时间在标签之后', (role, senderId, senderName, label) => {
  render(<GroupChatBubble
    message={{ id: 'other', role, content: '你好', status: 'history',
      extra: { senderId, senderName, displayTime: '10:31' } }}
    group={group} participants={[]} isLastMessage={false} isRequesting={false} userIdentityId="123"
  />);
  const badge = screen.getByText(label);
  expect(badge).toHaveClass('bg-primary/10', 'text-primary', 'rounded-full');
  expect(badge.previousElementSibling).toHaveTextContent(senderName);
  expect(badge.nextElementSibling).toHaveTextContent('10:31');
});

// 2026-11 推翻 dmore「仅末条回复常显」口径：会话详情每条消息的操作行（复制/编辑）
// 一律常显，不再依赖悬停/键盘聚焦才显现；断言不携带 hover 显隐类作为守护。
it.each([
  ['非末条 Bot 回复', 'assistant', false],
  ['用户消息', 'user', false],
] as const)('%s 的操作行常显（不携带 opacity-0 显隐类）', (_label, role, _isLast) => {
  const { unmount } = render(<GroupChatBubble
    message={{ id: 'reveal', role, content: '你好', status: 'history',
      extra: { senderId: 'b1', senderName: '发送者', displayTime: '10:31' } }}
    group={group} participants={[]} isLastMessage={false} isRequesting={false}
    userIdentityId="123" onCopy={jest.fn()} onEdit={jest.fn()} isEditable
  />);
  const action = screen.getByTestId('message-copy-action-reveal');
  expect(action).not.toHaveClass('opacity-0');
  expect(action).not.toHaveClass('group-hover:opacity-100');
  unmount();
});
