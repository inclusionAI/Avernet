/** @jest-environment jsdom */
import { GroupChatMessageList } from '@/pages/Workspace/components/GroupChatPane/GroupChatMessageList';
import { describe, expect, it, jest } from '@jest/globals';
import '@testing-library/jest-dom/jest-globals';
import { render, screen } from '@testing-library/react';
import React from 'react';

let mockBubbleListProps: Record<string, unknown> = {};
jest.mock('@tc-chat/ui/es/BubbleList', () => ({
  BubbleList: (props: Record<string, unknown>) => {
    mockBubbleListProps = props;
    return <div>{props.footer as React.ReactNode}</div>;
  },
}));

jest.mock('@/pages/Workspace/components/GroupChatPane/GroupChatBubble', () => ({
  GroupChatBubble: () => null,
  ThinkingBubble: () => <div data-testid="thinking-bubble" />,
}));

const group = {
  groupId: 'group-1',
  name: '测试群',
  kind: 'free_chat',
} as never;

const session = {
  sessionId: 'session-1',
  groupId: 'group-1',
  participants: [],
} as never;

const interactionProps = {
  interactions: {
    rootRef: { current: null },
    selection: null,
    unreadCount: 0,
    copyText: jest.fn(),
    markRead: jest.fn(),
  } as never,
  onQuoteSelected: jest.fn(),
  onExplainSelected: jest.fn(),
  onEditMessage: jest.fn(),
};

describe('GroupChatMessageList', () => {
  it('群聊加载更早历史使用锚点包装后的回调，而不是直接跳到新批次最旧消息', () => {
    const onLoadMoreHistory = jest.fn();
    render(
      <GroupChatMessageList
        messages={[{ id: 'message-1', role: 'assistant', content: 'oldest', status: 'history' }]}
        group={group}
        session={session}
        isRequesting={false}
        groupBootstrapProcessing={false}
        hasMoreHistory
        onLoadMoreHistory={onLoadMoreHistory}
        {...interactionProps}
      />,
    );

    const wrapped = mockBubbleListProps.onLoadMore as (() => void) | undefined;
    expect(typeof wrapped).toBe('function');
    expect(wrapped).not.toBe(onLoadMoreHistory);
    wrapped?.();
    expect(onLoadMoreHistory).toHaveBeenCalledTimes(1);
  });

  it('does not render a thinking bubble after the latest assistant output is aborted', () => {
    render(
      <GroupChatMessageList
        messages={[
          {
            id: 'message-1',
            role: 'assistant',
            content: 'partial output',
            status: 'aborted',
            extra: { botUuid: 'bot-1', runId: 'run-1' },
          },
        ]}
        group={group}
        session={session}
        isRequesting
        groupBootstrapProcessing={false}
        {...interactionProps}
      />,
    );

    expect(screen.queryByTestId('thinking-bubble')).not.toBeInTheDocument();
  });

  it('keeps the thinking bubble while a user message is waiting for the first Bot output', () => {
    render(
      <GroupChatMessageList
        messages={[
          {
            id: 'message-1',
            role: 'user',
            content: 'hello',
            status: 'done',
          },
        ]}
        group={group}
        session={session}
        isRequesting
        groupBootstrapProcessing={false}
        {...interactionProps}
      />,
    );

    expect(screen.getByTestId('thinking-bubble')).toBeInTheDocument();
  });
});
