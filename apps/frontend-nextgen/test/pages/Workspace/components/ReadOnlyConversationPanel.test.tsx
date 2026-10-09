/** @jest-environment jsdom */
import { ReadOnlyConversationPanel } from '@/pages/Workspace/components/ReadOnlyConversationPanel';
import { beforeEach, describe, expect, it, jest } from '@jest/globals';
import type { ChatMessage, TextBlock } from '@tc-chat/core';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';

jest.mock('@tc-chat/ui/es/Bubble', () => ({
  Bubble: ({ blocks }: { blocks?: Array<{ content?: string }> }) => (
    <div data-testid="bubble">{blocks?.map((block) => block.content).join('')}</div>
  ),
}));
jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const { createElement, Fragment } = require('react');
  const ChatLayout = ({ children }: { children?: unknown }) =>
    createElement('div', { 'data-testid': 'chat-layout' }, children);
  ChatLayout.Header = ({ slotLeft, slotRight }: { slotLeft?: unknown; slotRight?: unknown }) =>
    createElement('div', null, slotLeft, slotRight);
  ChatLayout.List = ({ messages = [], renderItem, emptyPlaceholder }: any) =>
    createElement(
      'div',
      null,
      messages.length
        ? messages.map((item: unknown, index: number) =>
            createElement(Fragment, { key: index }, renderItem(item, index)),
          )
        : emptyPlaceholder,
    );
  return { ChatLayout };
});
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({ aixUiPlugin: () => ({}), fileRefPlugin: () => ({}) }));
jest.mock('@tc-chat/ui/es/SystemNotice', () => ({
  SystemNotice: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

beforeEach(() => {
  Object.defineProperty(globalThis, 'ResizeObserver', {
    configurable: true,
    value: class ResizeObserverMock {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  });
});

const bot = { displayName: '皮皮虾', avatarUrl: undefined };
const friend = { userId: '447147', displayName: '风太' };
const session = {
  sessionId: 'session-1',
  botId: '327325',
  title: '历史会话',
  messageCount: 2,
  gmtCreate: '2026-09-15T10:00:00Z',
  gmtModified: '2026-09-16T10:00:00Z',
};
const messages: ChatMessage[] = [
  {
    id: 'a1',
    role: 'assistant',
    content: 'Bot 回复',
    status: 'history',
    blocks: [{ type: 'text', content: 'Bot 回复' }] as TextBlock[],
  },
  {
    id: 'u1',
    role: 'user',
    content: '用户消息',
    status: 'history',
    blocks: [{ type: 'text', content: '用户消息' }] as TextBlock[],
  },
];
const baseProps = {
  bot,
  friend,
  session,
  messages,
  loading: false,
  error: null,
  hasMore: false,
  isLoadingMore: false,
  loadMoreError: null,
  onRetry: jest.fn(),
  onLoadMore: jest.fn(),
  onOpenSessionList: jest.fn(),
};

describe('ReadOnlyConversationPanel', () => {
  it('renders history senders without composer or connection state', () => {
    render(<ReadOnlyConversationPanel {...baseProps} />);

    expect(screen.getByRole('heading', { name: '历史会话' })).toBeInTheDocument();
    expect(screen.getByText('皮皮虾')).toBeInTheDocument();
    expect(screen.getByText('风太')).toBeInTheDocument();
    expect(screen.getByText('Bot 回复')).toBeInTheDocument();
    expect(screen.getByText('用户消息')).toBeInTheDocument();
    expect(screen.queryByPlaceholderText(/发送消息/)).not.toBeInTheDocument();
    expect(screen.queryByText(/已连接|连接中|已断开|重连/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /编辑消息|追问选文本|解释选中文本/ })).not.toBeInTheDocument();
  });

  it('shows an empty selection state before a Session is selected', () => {
    render(<ReadOnlyConversationPanel {...baseProps} friend={null} session={null} messages={[]} />);
    expect(screen.getByText('请选择一个好友用户会话')).toBeInTheDocument();
  });

  it('shows a retry action when initial history fails', () => {
    const onRetry = jest.fn();
    render(<ReadOnlyConversationPanel {...baseProps} messages={[]} error="历史消息加载失败" onRetry={onRetry} />);
    expect(screen.getByText('历史消息加载失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('shows a load-more retry action when loading earlier pages fails', () => {
    const onLoadMore = jest.fn();
    render(<ReadOnlyConversationPanel {...baseProps} loadMoreError="加载更早消息失败" onLoadMore={onLoadMore} />);
    expect(screen.getByText('加载更早消息失败')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重试' }));
    expect(onLoadMore).toHaveBeenCalledTimes(1);
  });
});
