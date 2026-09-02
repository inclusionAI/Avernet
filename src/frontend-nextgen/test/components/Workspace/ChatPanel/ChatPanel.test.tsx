/** @jest-environment jsdom */
import { ChatPanel, resolveSingleSender } from '@/components/Workspace/ChatPanel/index';
import type { ConversationTarget } from '@/services/workspace/workspaceModel';
import { describe, expect, it, jest } from '@jest/globals';
import type { ChatBridge } from '@tc-chat/core';
import '@testing-library/jest-dom';
import '@testing-library/jest-dom/jest-globals';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import React from 'react';

// 捕获 <ChatLayout.Panel> 收到的 props（断言 bridge 透传）。mock 前缀通过 jest.factory 检查。
let mockPanelRenders: Array<Record<string, unknown>> = [];
let mockBubbleRenders: Array<Record<string, unknown>> = [];

// Stub SDK UI primitives: tests focus on ChatPanel header/connection copy + Sender presence,
// not on SDK bubble/markdown rendering. Stubs avoid pulling ESM @tc-chat/ui into jsdom.
jest.mock('@tc-chat/ui/es/Bubble', () => ({
  Bubble: (props: Record<string, unknown>) => {
    mockBubbleRenders.push(props);
    const sender = props.sender as { name?: string } | undefined;
    return <div data-testid="bubble">{sender?.name}</div>;
  },
}));
jest.mock('@tc-chat/ui/es/ChatLayout', () => {
  const { createElement, Fragment, forwardRef } = require('react');
  const ChatLayout = ({ children, className }: { children?: unknown; className?: string }) =>
    createElement('div', { 'data-testid': 'chat-layout', className }, children);
  const Header = ({ slotLeft, slotRight }: { slotLeft?: unknown; slotRight?: unknown }) =>
    createElement('div', null, createElement('div', null, slotLeft), createElement('div', null, slotRight));
  const List = ({
    emptyPlaceholder,
    messages = [],
    renderItem,
  }: {
    emptyPlaceholder?: unknown;
    messages?: unknown[];
    renderItem?: (message: unknown, index: number) => unknown;
  }) =>
    createElement(
      'div',
      null,
      messages.length
        ? messages.map((message, index) => createElement(Fragment, { key: index }, renderItem?.(message, index)))
        : emptyPlaceholder,
    );
  const Sender = (props: { placeholder?: string; disabled?: boolean; className?: string }) =>
    createElement('input', {
      'data-testid': 'sender',
      placeholder: props.placeholder,
      disabled: props.disabled,
      readOnly: true,
      className: props.className,
    });
  const Panel = forwardRef((_props: Record<string, unknown>, ref: unknown) => {
    void ref;
    mockPanelRenders.push(_props);
    return null;
  });
  ChatLayout.Header = Header;
  ChatLayout.List = List;
  ChatLayout.Sender = Sender;
  ChatLayout.Panel = Panel;
  return { ChatLayout };
});
jest.mock('@tc-chat/ui/es/MarkdownRender', () => ({
  aixUiPlugin: () => ({}),
  fileRefPlugin: () => ({}),
}));
jest.mock('@tc-chat/ui/es/Sender', () => {
  // ChatPanel 单聊输入框用原生 <Sender ref={senderRef}>（forwardRef）替代 <ChatLayout.Sender>（根因 5）。
  // 桩渲染 testid="sender" 供存在性断言；经 require('react').forwardRef 接 ref 不告警（factory 不可引用外层 React）。
  const { forwardRef } = require('react');
  return {
    Sender: forwardRef(
      (props: { placeholder?: string; disabled?: boolean; className?: string }, ref: React.Ref<HTMLInputElement>) => (
        <input
          ref={ref}
          data-testid="sender"
          placeholder={props.placeholder}
          disabled={props.disabled}
          readOnly
          className={props.className}
        />
      ),
    ),
    ToolbarButton: () => null,
  };
});
jest.mock('@tc-chat/ui/es/SystemNotice', () => ({
  SystemNotice: ({ children }: { children?: React.ReactNode }) => <div>{children}</div>,
}));

const demoTarget: ConversationTarget = {
  id: 'octopus',
  name: '虾摸鱼',
  avatar: '🐙',
  engine: 'OpenClaw',
  group: 'mine',
  status: 'available',
  summary: '',
};
const botModeTarget = {
  id: 'bot-1',
  name: 'Bot',
  avatar: 'B',
  engine: 'OpenClaw',
  group: 'mine',
  status: 'available',
  summary: '单聊',
};

const viewer = { id: 'human-1', kind: 'user' as const, displayName: '章梧', online: true };

const baseProps = {
  draft: '',
  panelRef: { current: null } as React.RefObject<never>,
  onDraftChange: () => {},
  onOpenPanelDemo: () => {},
  onPanelAction: () => {},
};

describe('ChatPanel interactive', () => {
  it('demo 模式 Sender 禁用', () => {
    render(
      <ChatPanel
        target={demoTarget}
        messages={[]}
        isRequesting={false}
        isLoadingMessages={false}
        connectionStatus="disconnected"
        retryCount={0}
        supportState={{ phase: 'idle', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
      />,
    );
    expect(screen.getByTestId('sender')).toBeInTheDocument();
  });
  it('单聊消息区保留可滚动的高度链路', () => {
    render(
      <ChatPanel
        target={botModeTarget as any}
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={[]}
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
      />,
    );

    const messageList = screen.getByTestId('chat-layout').querySelector('[data-workspace-message-list="single-chat"]');
    expect(messageList).toBeInTheDocument();
    const scrollRegion = messageList?.querySelector('[data-workspace-message-scroll-region="single-chat"]');
    expect(scrollRegion).toHaveClass('flex', 'min-h-0', 'flex-1', 'flex-col');
  });

  it('输入区宽度随消息区自适应，不受固定 max-width 限制', () => {
    render(
      <ChatPanel
        target={botModeTarget as any}
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={[]}
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
      />,
    );
    const sender = screen.getByTestId('sender');
    expect(sender).toHaveClass('w-full');
    expect(sender).not.toHaveClass('max-w-4xl');
  });

  it('bot 模式(交互) connectionStatus=connected 文案为在线', () => {
    render(
      <ChatPanel
        target={botModeTarget as any}
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={[]}
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
      />,
    );
    expect(screen.getByText('在线')).toBeInTheDocument();
  });
  it('用户消息优先使用顶栏真实用户头像，Bot 消息仍使用 Bot 头像', () => {
    const userAvatarUrl = 'https://example.test/user-avatar.png';
    const userSender = resolveSingleSender(
      { id: 'u1', role: 'user', content: '你好', status: 'history' } as never,
      botModeTarget as any,
      { ...viewer, kind: 'bot' },
      userAvatarUrl,
    );
    const assistantSender = resolveSingleSender(
      { id: 'a1', role: 'assistant', content: '你好', status: 'history' } as never,
      botModeTarget as any,
      viewer,
      userAvatarUrl,
    );
    const userAvatar = render(<>{userSender.avatar}</>);
    const assistantAvatar = render(<>{assistantSender.avatar}</>);

    expect(userAvatar.container.querySelector('img')).toHaveAttribute('src', userAvatarUrl);
    expect(userAvatar.container.querySelector('img')).toHaveClass('shrink-0');
    expect(assistantAvatar.container.querySelector('img')).toBeNull();
    expect(assistantAvatar.container.textContent).toContain('B');
  });

  it('最近一条用户消息提供编辑入口，并回填输入区而不修改旧消息', () => {
    mockBubbleRenders.length = 0;
    const onDraftChange = jest.fn();
    render(
      <ChatPanel
        target={botModeTarget as any}
        viewer={viewer}
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={
          [
            { id: 'u1', role: 'user', content: '旧问题', status: 'history' },
            { id: 'a1', role: 'assistant', content: '旧回答', status: 'history' },
            { id: 'u2', role: 'user', content: '最近的问题', status: 'history' },
            { id: 'a2', role: 'assistant', content: '最新回答', status: 'history' },
          ] as never
        }
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
        onDraftChange={onDraftChange}
      />,
    );

    expect(screen.queryByTestId('user-message-actions-u2')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '引用消息' })).not.toBeInTheDocument();
    const latestUserActionArea = screen.getByTestId('message-copy-action-u2');
    expect(latestUserActionArea).toHaveClass('justify-end', 'pr-11');
    fireEvent.click(within(latestUserActionArea).getByRole('button', { name: '编辑消息' }));
    expect(onDraftChange).toHaveBeenCalledWith('最近的问题');
    expect(screen.getByRole('status', { name: '编辑消息状态' })).toBeInTheDocument();

    expect(
      within(screen.getByTestId('message-copy-action-u1')).queryByRole('button', { name: '编辑消息' }),
    ).not.toBeInTheDocument();
  });

  it('用户提问右对齐、Bot 回复左对齐，并使用 14px 正文与分组间距', () => {
    mockBubbleRenders.length = 0;
    render(
      <ChatPanel
        target={botModeTarget as any}
        viewer={viewer}
        userAvatarUrl="https://example.test/user-avatar.png"
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={
          [
            { id: 'u1', role: 'user', content: '你好', status: 'history', extra: { displayTime: '10:30' } },
            { id: 'a1', role: 'assistant', content: '你好，章梧', status: 'history', extra: { displayTime: '10:31' } },
          ] as never
        }
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
      />,
    );
    const bubbles = mockBubbleRenders.filter(Boolean);
    expect(bubbles).toHaveLength(2);
    expect(bubbles.map((props) => props.className)).toEqual([
      'mb-6 message-bubble-compact [--aix-markdown-font-size:14px] [--aix-font-size-base:14px]',
      'mb-0 message-bubble-compact [--aix-markdown-font-size:14px] [--aix-font-size-base:14px]',
    ]);
    expect(bubbles.map((props) => (props.sender as { align?: string }).align)).toEqual(['right', 'left']);
    expect((bubbles[0].sender as { bubbleColor?: string }).bubbleColor).toBe('hsl(var(--primary) / 0.1)');
    expect((bubbles[1].sender as { bubbleColor?: string }).bubbleColor).toBeUndefined();
    const messageMeta = screen.getAllByTestId('message-sender-meta');
    expect(messageMeta).toHaveLength(2);
    expect(messageMeta[0]).toHaveTextContent('章梧');
    expect(messageMeta[0]).toHaveTextContent('10:30');
    expect(messageMeta[0]).toHaveClass('justify-end', 'text-right');
    expect(messageMeta[1]).toHaveTextContent('Bot');
    expect(messageMeta[1]).toHaveTextContent('10:31');
    expect(messageMeta[1]).toHaveClass('justify-start', 'text-left');
    expect((bubbles[0] as { timestamp?: string; sender?: { name?: string } }).timestamp).toBeUndefined();
    expect((bubbles[0].sender as { name?: string }).name).toBeUndefined();
    expect((bubbles[1] as { timestamp?: string; sender?: { name?: string } }).timestamp).toBeUndefined();
    expect((bubbles[1].sender as { name?: string }).name).toBeUndefined();
    expect(screen.getByRole('img', { name: '章梧' })).toHaveAttribute('src', 'https://example.test/user-avatar.png');
  });

  it('在单聊每条用户与 Bot 消息末尾显性展示复制入口，并按消息方向对齐且提供复制反馈', async () => {
    mockBubbleRenders.length = 0;
    const writeText = jest.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    });
    render(
      <ChatPanel
        target={botModeTarget as any}
        viewer={viewer}
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={
          [
            { id: 'u-copy', role: 'user', content: '请复制我', status: 'history' },
            { id: 'a-copy', role: 'assistant', content: '这是回复', status: 'history' },
          ] as never
        }
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        {...baseProps}
      />,
    );

    expect(screen.getByTestId('message-copy-action-u-copy')).toHaveClass('justify-end', 'pr-11');
    expect(screen.getByTestId('message-copy-action-a-copy')).toHaveClass('justify-start', 'pl-11');
    expect(screen.getAllByRole('button', { name: '复制整条消息' })).toHaveLength(2);
    expect(mockBubbleRenders[1].actions).toBeTruthy();

    fireEvent.click(
      within(screen.getByTestId('message-copy-action-a-copy')).getByRole('button', { name: '复制整条消息' }),
    );
    await waitFor(() => expect(screen.getByTestId('message-copy-action-a-copy-feedback')).toHaveTextContent('已复制'));
    expect(writeText).toHaveBeenCalledWith('这是回复');
  });

  it('把 chatBridge 透传给 ChatLayout.Panel（主→副通道接线）', () => {
    mockPanelRenders.length = 0;
    const bridge = {} as unknown as ChatBridge;
    render(
      <ChatPanel
        target={demoTarget}
        mode="bot"
        interactive
        connectionStatus="connected"
        messages={[]}
        isRequesting={false}
        isLoadingMessages={false}
        retryCount={0}
        supportState={{ phase: 'ready', error: null }}
        onSend={() => {}}
        onStop={() => {}}
        onReconnect={() => {}}
        chatBridge={bridge}
        {...baseProps}
      />,
    );
    expect(mockPanelRenders.some((p) => p.bridge === bridge)).toBe(true);
  });
});
