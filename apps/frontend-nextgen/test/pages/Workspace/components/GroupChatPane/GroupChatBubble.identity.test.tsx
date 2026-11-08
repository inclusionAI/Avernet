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
